# FitLife

Coach fitness self-hosted avec IA locale. Multi-user, mobile-first, génération de séances par LLM (Ollama) avec fallback déterministe.

React + Vite / Flask + SQLite / Ollama — déployé sur LXC 205 (Proxmox Tower).

## Features

- **Multi-user** : inscription email + mot de passe, sessions JWT, données isolées par utilisateur
- **Onboarding QCM** : objectif (recomposition / force / endurance), séances par semaine, focus, niveau, équipement, objectifs course → le programme est généré à partir de ça
- **Coach IA (Ollama)** : à chaque séance terminée, le backend envoie profil + historique complet (charges réalisées, séances, métriques, activités Strava) au LLM local qui génère la prochaine séance (exos, séries, reps, charges cibles, conseils en français)
- **Queue résiliente** : si le serveur Ollama est down (node GPU on-demand), un plan fallback déterministe est affiché immédiatement (rotation Full Body A/B/C, surcharge progressive +5%) et remplacé par le plan IA dès que le serveur répond (worker, retry toutes les 2 min)
- **Mode déplacement** : bascule en un tap vers une séance 100% poids du corps (pas de salle dispo), générée par l'IA aussi
- **Bibliothèque de 905 exercices** : dataset filtré sur l'équipement salle (machines guidées, câbles, haltères, Smith, poids du corps), GIFs animés + instructions traduites en français par Ollama (cache en DB)
- **Suivi de séance** : saisie des charges par exo, check de progression, GIF + instructions consultables pendant la séance
- **Course** : planification hebdo selon les objectifs du profil (km/semaine, jours), conseils Z2 par le coach
- **Strava** : OAuth par utilisateur, import de toutes les activités de la semaine (courses, marches, FC) — injectées dans le contexte du coach
- **Métriques** : poids et FC de repos, saisie rétroactive via date picker, 1 valeur/jour/métrique

## Stack

- **Frontend** : React 18 + Vite, servi par Nginx (GIFs/images en local)
- **Backend** : Flask + Gunicorn, SQLite, APScheduler (worker de génération)
- **IA** : Ollama sur le réseau local (VM GPU), JSON schema forcé, modèle configurable
- **Auth** : JWT (PyJWT), hash werkzeug
- **Infra** : Docker Compose sur LXC Proxmox, exposé via Cloudflare Tunnel

## Structure

```
fitlife/
  docker-compose.yml
  .env                      # non commité
  backend/
    app.py                  # API Flask : auth, profil, workouts, logs, Strava, worker
    coach.py                # génération : Ollama + fallback déterministe + traduction FR
    schema.sql              # schéma DB (chargé au boot, migrations auto)
    requirements.txt
    Dockerfile
  frontend/
    src/
      App.jsx               # auth, QCM, séance, exos, métriques, profil
      api.js                # client API (JWT)
      index.css
      main.jsx
    index.html
    nginx.conf              # proxy /api + /media
    vite.config.js
    package.json
    Dockerfile
  scripts/
    import_exercises.py     # import dataset + copie médias
```

## Data model (SQLite)

`users`, `profiles` (QCM), `exercises` (pool importé + trad FR cachée), `workouts` (plan JSON, gym/run, planned/done), `workout_sets` (cibles vs réalisé), `logs` (poids/fc, date), `oauth_tokens` (Strava par user), `generation_jobs` (queue IA, mode gym/travel).

## Variables d'environnement (.env)

```env
SECRET_KEY=               # openssl rand -hex 32 (JWT + Flask)
STRAVA_CLIENT_ID=
STRAVA_CLIENT_SECRET=
OLLAMA_URL=http://192.168.1.14:11434
OLLAMA_MODEL=qwen3:14b    # ou phi4, mistral:7b...
```

## Déploiement

Prérequis sur l'hôte (LXC avec nesting) : Docker, MTU 1400 si réseau 4G (`/etc/docker/daemon.json` : `{"mtu": 1400, "dns": ["1.1.1.1"]}`).

```bash
git clone git@github.com:lazzylife42/fitlife.git /opt/fitlife
git clone --depth 1 https://github.com/hasaneyldrm/exercises-dataset.git /opt/fitlife-dataset
mkdir -p /opt/fitlife-media

cd /opt/fitlife
cp .env.example .env && nano .env

docker compose build && docker compose up -d

# Import du dataset (une fois)
docker cp scripts/import_exercises.py fitlife-backend:/tmp/
docker exec fitlife-backend python3 /tmp/import_exercises.py

curl -s localhost:3010/api/health   # {"ok":true,"ollama":true}
```

Mounts attendus (docker-compose) : `/opt/fitlife-dataset` → `/dataset` (ro, backend), `/opt/fitlife-media` → `/media-out` (backend) et `/usr/share/nginx/html/media` (ro, frontend).

## Strava

1. https://www.strava.com/settings/api — callback : `https://<domaine>/api/strava/callback`
2. `STRAVA_CLIENT_ID` / `STRAVA_CLIENT_SECRET` dans `.env`
3. Connexion depuis l'onglet Profil (OAuth par utilisateur)

## Ollama

Serveur Ollama accessible depuis le container backend (`OLLAMA_HOST=0.0.0.0` côté serveur). Le node GPU peut être éteint : l'app fonctionne en mode fallback et rattrape dès qu'il est up. Modèles testés : mistral:7b (JSON fragile), phi4, qwen3:14b (recommandé, ~9.5 GB VRAM).

## Flow de génération

```
Terminer la séance
  ├─> séance marquée done (charges réalisées en DB)
  ├─> plan fallback créé immédiatement (rotation A/B/C, +5% si complété)
  ├─> job ajouté à la queue
  └─> worker (2 min) : si Ollama up
        ├─> contexte = profil + 6 dernières séances + logs + Strava semaine
        ├─> génération JSON (schema forcé) + validation stricte
        │     (ids du pool, diversité min 3 groupes, titre FR)
        └─> remplace le plan fallback → badge "coach IA"
```

## Utilitaires

```bash
docker logs -f fitlife-backend            # suivi worker / jobs
docker compose down -v && docker compose up -d   # reset DB complet (re-import dataset requis)

# Etat de la queue
docker exec fitlife-backend python3 -c "
import sqlite3; db = sqlite3.connect('/data/fitlife.db'); db.row_factory = sqlite3.Row
[print(dict(j)) for j in db.execute('SELECT * FROM generation_jobs').fetchall()]"
```

## Crédits

Dataset exercices : [hasaneyldrm/exercises-dataset](https://github.com/hasaneyldrm/exercises-dataset) (usage éducatif et non-commercial uniquement — les médias appartiennent à leurs ayants droit respectifs).
