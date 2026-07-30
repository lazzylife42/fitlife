# FitLife — Guide complet : architecture, concepts et code annoté

> Objectif : comprendre chaque brique assez profondément pour recoder l'app entière solo,
> sans copier-coller. Le document est en deux temps : d'abord les CONCEPTS par phase
> (le pourquoi, les pièges réels, les défis de rebuild), puis le CODE RÉEL annoté
> bloc par bloc (Parties 1 à 6 + checklist finale).
>
> Prérequis supposés : Python, SQL (Redshift), bases React, Docker, homelab Proxmox.

---

# VOLET A — Architecture & concepts

## Vue d'ensemble

```
                    ┌─────────────────────────────────────────────┐
                    │              LXC 205 (Proxmox Tower)         │
   Internet         │  ┌────────────────┐   ┌──────────────────┐  │
      │             │  │ fitlife-frontend│   │ fitlife-backend  │  │
 Cloudflare Tunnel ─┼─►│ Nginx :80       │──►│ Flask/Gunicorn   │  │
 fit.sabinomonte.ch │  │ - SPA React     │/api│ :5000            │  │
      (:3010)       │  │ - /media (gifs) │   │ - REST + JWT     │  │
                    │  └────────────────┘   │ - APScheduler     │  │
                    │                        │ - SQLite /data    │  │
                    │                        └────────┬─────────┘  │
                    └─────────────────────────────────┼────────────┘
                                                      │ HTTP :11434
                                            ┌─────────▼─────────┐
                                            │ VM 202 (Tower GPU) │
                                            │ Ollama + RTX 3060  │
                                            │ qwen3:14b (coach)  │
                                            │ mistral:7b (trad)  │
                                            └───────────────────┘
```

**Le flux central de l'app** (tout le reste est du support autour de ça) :

```
Terminer la séance
  ├─> séance marquée done (charges réalisées persistées)
  ├─> plan FALLBACK créé immédiatement (déterministe, l'app marche toujours)
  ├─> job ajouté dans une QUEUE (table SQL)
  └─> worker (toutes les 2 min) : si Ollama joignable
        ├─> construit le CONTEXTE (profil + historique + métriques)
        ├─> appelle le LLM avec un schéma JSON forcé
        ├─> VALIDE strictement la réponse (sinon retry)
        └─> remplace le plan fallback → badge "coach IA"
```

💡 **Rationale globale** : le serveur GPU est on-demand. Toute l'architecture découle de
cette contrainte : l'app doit être 100% fonctionnelle sans IA (fallback), et rattraper
quand l'IA revient (queue + worker). C'est un pattern général : *graceful degradation +
eventual consistency*. Tu le retrouveras partout en data engineering (retry queues,
dead letter queues, circuit breakers).

---

## Phase 0 — Infra & environnement

### 📍 Concepts clés
- LXC vs VM : le LXC partage le kernel de l'hôte (léger, boot instantané), la VM a le
  sien (isolation forte, nécessaire pour le GPU passthrough d'Ollama).
- `nesting=1` sur le LXC : requis pour faire tourner Docker *dans* un container LXC.
- Bind mounts vs volumes Docker : volumes (`fitlife-data`) gérés par Docker, survivent
  aux rebuilds ; bind mounts (`/opt/fitlife-media`) exposent un chemin de l'hôte.

### 🎯 Best practices appliquées
- Un LXC par service applicatif (convention : 1xx = NUC, 2xx = Tower).
- DNS forcé (`1.1.1.1`) : Tailscale écrase `resolv.conf` → `tailscale set --accept-dns=false`.
- MTU 1400 partout (réseau 4G) : `/etc/docker/daemon.json` → `{"mtu": 1400}`.
- IP statique + `onboot: 1` + `pct set --nameserver`.

### ⚠️ Pièges rencontrés (vécus, pas théoriques)
1. Tailscale DNS a cassé apt ET les builds Docker ET git — deux fois (Tower puis LXC).
2. Le zip de déploiement a écrasé `docker-compose.yml` et ses chemins de mounts →
   905 gifs "disparus" alors qu'ils étaient sur le disque. Leçon : **une seule source de
   vérité = git**. Le scp de fichiers à la main finit toujours par diverger
   (doublons `coach(1).py` dans Downloads → vieux code déployé sans s'en rendre compte).

### 📝 Défi de rebuild
Crée un LXC Debian, installe Docker avec MTU custom, et écris un `docker-compose.yml`
avec deux services vides (nginx + python) qui communiquent par nom de service.

---

## Phase 1 — Modèle de données (SQLite)

### 📍 Concepts clés

| Table | Rôle | Notion illustrée |
|---|---|---|
| `users` | comptes (email + hash) | jamais de mot de passe en clair |
| `profiles` | réponses du QCM, 1-1 avec users | `user_id` PK = relation 1-1 |
| `exercises` | pool importé du dataset | données de référence partagées |
| `workouts` | séances (plan JSON + statut) | hybride relationnel/document |
| `workout_sets` | réalisé par exo (charges, done) | détail 1-N, cible vs réalisé |
| `logs` | poids/FC par jour | série temporelle simple |
| `generation_jobs` | queue IA | une queue N'EST QU'une table + un statut |

### 💡 Rationale des choix
- **SQLite en prod ?** Oui pour un PoC self-hosted mono-instance : zéro process
  supplémentaire, un fichier, backups triviaux. La migration PostgreSQL reste le bon
  exercice suivant.
- **`plan_json` en TEXT** : le plan est un document (structure variable gym/run/travel).
  MAIS les charges réalisées sont dans `workout_sets` en colonnes typées : c'est ce
  qu'on requête et agrège. Règle : *ce que tu requêtes → colonnes ; ce que tu
  affiches → JSON*. (En Redshift : SUPER vs colonnes.)
- **Queue = table** : `status='pending'` + `attempts` + retry = 90% de ce que fait une
  vraie message queue, sans Redis/Celery.
- **Migrations "pauvres"** : `CREATE TABLE IF NOT EXISTS` + `ALTER ... ADD COLUMN`
  dans un try/except au boot. Suffisant en solo ; Alembic quand l'équipe grandit.

### ⚠️ Piège rencontré
`INSERT OR REPLACE` sur `exercises` lors d'un ré-import aurait **écrasé les 905
traductions** (REPLACE = DELETE + INSERT, les colonnes absentes reprennent leur
DEFAULT). Fix : upsert `ON CONFLICT(id) DO UPDATE SET ...` en listant les colonnes,
`instructions_fr` volontairement absente. REPLACE vs UPSERT : ça mord fort.

### 📝 Défi de rebuild
Écris le schéma de mémoire, puis compare. Ensuite : une requête qui sort, pour un user,
la dernière charge réalisée par exercice (pas de `QUALIFY` en SQLite → sous-requête).

---

## Phase 2 — Backend Flask : API REST + auth JWT

### 📍 Concepts clés
- **Stateless auth** : aucun état de session serveur. JWT `{uid, exp}` signé HMAC,
  décodé par un décorateur `@require_auth` qui pose `g.user_id`.
- **Hash de mot de passe** : `werkzeug.security` (PBKDF2 salé). Jamais de hash maison.
- **Scoping multi-user** : CHAQUE requête SQL filtre par `user_id` ; vérifier la
  propriété avant toute écriture. Un `WHERE user_id=?` oublié = fuite de données.

### 🎯 Design de l'API
```
POST /api/auth/register, /api/auth/login      → {token}
GET  /api/me                                  → user + profile
POST /api/profile                             → QCM (validation par sets de valeurs)
POST /api/profile/exclusions                  → toggles matériel + régénération
GET  /api/exercises?q=&category=              → bibliothèque (LIMIT 100)
GET  /api/exercises/<id>/fr                   → trad FR lazy + cache
GET  /api/workouts                            → next + runs + history + ai_pending
POST /api/workouts/generate                   → première séance
POST /api/workouts/<id>/sets/<set_id>         → saisie charge/done + note
POST /api/workouts/<id>/complete              → done + fallback + job (note, distance_km pour les courses)
POST /api/workouts/mode                       → gym <-> travel
POST /api/log/<type>                          → log daté (rétroactif)
GET  /api/progress                            → km course + séances salle (8 semaines)
GET  /api/health                              → {ok, ollama}
```

### 💡 Rationales à retenir
- **Validation stricte en entrée**, jamais de fallback silencieux.
- **Les codes HTTP portent du sens côté client** : un endpoint métier qui répond 401
  alors que le vrai problème est un état ("service tiers non connecté") fait purger
  le JWT côté front par erreur → déconnexion sauvage. Réserver 401 à "QUI es-tu",
  403 à "pas le DROIT", et un 4xx métier dédié (ex. 409) à un état applicatif.

### 📝 Défi de rebuild
Register/login/`@require_auth` sans regarder. Un token modifié d'un caractère → 401 ;
un token d'un autre user ne lit jamais tes données.

---

## Phase 3 — Le coach : intégration LLM locale

### 3.1 → 3.7 en résumé (le détail annoté est au Volet B, Partie 4)
- **Sortie structurée** : `format: <json-schema>` (pas la string "json") — la
  structure devient garantie, le contenu reste à valider.
- **⚠️ num_ctx, la leçon n°1** : Ollama tronque silencieusement à 4096 tokens par
  défaut. Prompt trop grand → le modèle voyait un fragment → plans absurdes
  (ids 0001-0003, "Butt to the floor" en reps). Fix : `num_ctx: 8192` + prompt
  compacté. *Un LLM qui répond n'importe quoi voit peut-être n'importe quoi.*
- **Prompt engineering efficace** : pool en lignes compactes, sections nommées, tâche
  répétée en FIN de prompt, `think: false` pour qwen3, deux modèles pour deux jobs
  (qwen3:14b programme / mistral:7b traduction).
- **Validation en couches** (generate → validate → retry) : JSON, structure, ids
  normalisés (`zfill`), **ids restreints au POOL envoyé** (pas à toute la DB — sinon
  le modèle recopie l'historique), dédup, diversité ≥3 groupes, max 2 câble. Chaque
  rejet → retry par le worker. La fiabilité vient de la validation, pas du prompt.
- **Filtrage amont du pool** : blacklist d'exos exotiques, whitelist câble
  (simple-poulie seulement), exclusions configurables par user, tri machines d'abord.
- **Fallback déterministe** : rotation A/B/C par modulo du nombre de séances done,
  continuité par slot, surcharge progressive +5% arrondie au 2.5 kg.

### 📝 Défis de rebuild
1. Client Ollama minimal : schéma JSON + log de la réponse brute sur échec.
2. Provoque le bug num_ctx : prompt 10k tokens avec num_ctx=2048, observe.
3. Rotation A/B/C + surcharge progressive en pur Python.

---

## Phase 4 — Queue & worker (résilience)

- APScheduler `BackgroundScheduler` dans le process Gunicorn, tick 2 min.
- Cheap guard `ollama_available()` (timeout 3s) → serveur down = skip sans consommer
  les attempts. Retry jusqu'à 30, `last_error` persisté.

### ⚠️ Pièges
1. **`--workers 1` obligatoire** : chaque worker Gunicorn = un scheduler → doublons.
2. Passer en mode travel devait ANNULER les jobs pending (sinon le worker écrasait le
   plan travel avec un plan salle). Les races existent même à petite échelle.
3. `maximum number of running instances reached (1)` : un tick > 2 min fait sauter le
   suivant — comportement voulu d'APScheduler, pas un bug.

### 📝 Défi
Queue sqlite + APScheduler from scratch ; simule le serveur IA down et vérifie que
rien ne se perd.

---

## Phase 5 — Frontend React (SPA mobile-first)

- **Pas de router** : la navigation = un state `tab`. 4 onglets, react-router serait
  du poids mort.
- **Machine à états** : `loading → !me (Auth) → !me.profile (Onboarding) → Main`.
- **Optimistic updates** : state local mis à jour AVANT le POST, toast sur échec.
- **Debounce** 300 ms sur la recherche ; save au blur pour les charges.
- **JWT client** : localStorage + wrapper `req()` unique ; 401 hors /auth/ → purge.
- Détails UI : `onError` sur les img, champ kg masqué en poids du corps, labels FR
  mappés à l'affichage (data brute en DB), traduction lazy avec fallback EN.

### 📝 Défi
Recode LogInput + l'optimistic update sans regarder ; casse le backend et vérifie que
chaque interaction toaste au lieu de crasher.

---

## Phase 6 — Build & serving

- **Multi-stage** : image finale Nginx ~25 MB sans Node.
- **Nginx façade unique** : SPA (`try_files ... /index.html`), proxy `/api/` par nom
  de service Docker, `/media/` en cache 1 an, `proxy_read_timeout 180s`.
- ⚠️ Rebuild backend ≠ rebuild frontend (App.jsx est compilé DANS l'image Nginx).

---

## Phase 7 — Intégrations externes

- **Import dataset** : filtre équipement, copie médias, upsert idempotent.
- **Traduction batch** : commit par exo (resumable), ETA loggée. Pattern de backfill.
- **Course sans source externe** : la distance parcourue est saisie manuellement par
  l'utilisateur (`POST /workouts/<id>/complete` avec `distance_km`), stockée en colonne
  typée (`workouts.actual_km`) plutôt que dans un champ texte libre — *ce que tu
  requêtes → colonnes*.

  ⚠️ **Historique** : une intégration Strava (OAuth2 + auto-validation des courses
  ≥60% du km cible) a existé ici, mais Strava a fermé son API aux comptes gratuits
  (403 "Application Inactive" sans abonnement payant) — elle a été retirée du code.
  Leçon : une dépendance à une API tierce gratuite n'est jamais garantie dans le temps ;
  prévoir un chemin de repli manuel dès le départ évite de tout recâbler en urgence.

---

## Phase 8 — Debug méthodique : les 5 vraies pannes du projet

| Symptôme | Cause réelle | Leçon |
|---|---|---|
| Plans IA débiles (ids 0001-0003) | prompt > num_ctx 4096, tronqué silencieusement | vérifier ce que le modèle VOIT vraiment |
| "model qwen3:14bb not found" | typo dans .env | lire le message d'erreur littéralement avant de théoriser |
| Cross-over revenu malgré le filtre | validation contre toute la DB, le modèle recopiait l'historique | valider contre ce qu'on a PROPOSÉ, pas contre ce qui EXISTE |
| Vieux code déployé en boucle | `coach(1).py` dans Downloads, scp du mauvais fichier | une seule source de vérité (git), vérifier ce qui TOURNE |
| 905 gifs "manquants" | zip a écrasé le compose → mounts vers un dossier vide | config = code, à versionner |

Méthode : reproduire → isoler la couche → vérifier l'état RÉEL (grep dans le
container, SELECT, logs bruts) → fix minimal → vérifier → commit.

---

## Ordre de rebuild recommandé (5 semaines)

1. **Socle** : LXC + Docker, schéma DB, Flask + auth JWT. Tests au curl.
2. **Métier sans IA** : import, CRUD workouts/sets/logs, fallback A/B/C. Utilisable
   en salle SANS IA ni front.
3. **Front** : SPA, auth flow, onboarding, séance optimistic, build + Nginx.
4. **IA** : client Ollama + schéma + num_ctx, validation en couches, queue + retry.
5. **Intégrations** : traduction, exclusions, tunnel.

Règle : à chaque étape, écris D'ABORD sans regarder, puis diffe avec l'original.
C'est le diff qui t'apprend.

---

## 🔗 Docs de référence

| Sujet | Lien |
|---|---|
| Flask | https://flask.palletsprojects.com/ |
| JWT (concept) | https://jwt.io/introduction |
| PyJWT | https://pyjwt.readthedocs.io/ |
| SQLite UPSERT | https://www.sqlite.org/lang_upsert.html |
| APScheduler | https://apscheduler.readthedocs.io/ |
| Ollama API + structured outputs | https://github.com/ollama/ollama/blob/main/docs/api.md |
| React (hooks) | https://react.dev/reference/react |
| Vite | https://vitejs.dev/guide/ |
| Nginx reverse proxy | https://docs.nginx.com/nginx/admin-guide/web-server/reverse-proxy/ |
| Docker multi-stage | https://docs.docker.com/build/building/multi-stage/ |


---

# VOLET B — Le code annoté

# Partie 1 — Infra as code

## docker-compose.yml

```yaml
services:
  fitlife-backend:
    dns:
      - 1.1.1.1
      - 8.8.8.8
    build: ./backend
    container_name: fitlife-backend
    restart: unless-stopped
    environment:
      SECRET_KEY: ${SECRET_KEY}
      DB_PATH: /data/fitlife.db
      CORS_ORIGINS: https://fit.sabinomonte.ch
      OLLAMA_URL: ${OLLAMA_URL:-http://192.168.1.14:11434}
      OLLAMA_MODEL: ${OLLAMA_MODEL:-mistral:7b}
      OLLAMA_TRANSLATE_MODEL: ${OLLAMA_TRANSLATE_MODEL:-mistral:7b}
    volumes:
      - fitlife-data:/data
      - /opt/fitlife-dataset:/dataset:ro
      - /opt/fitlife-media:/media-out
    expose:
      - "5000"

  fitlife-frontend:
    dns:
      - 1.1.1.1
      - 8.8.8.8
    build:
      context: ./frontend
    container_name: fitlife-frontend
    restart: unless-stopped
    ports:
      - "3010:80"
    volumes:
      - /opt/fitlife-media:/usr/share/nginx/html/media:ro
    depends_on:
      - fitlife-backend

volumes:
  fitlife-data:
```

**Explication ligne par ligne des choix :**

- `dns: [1.1.1.1, 8.8.8.8]` — force le DNS DANS les containers. Sans ça, ils héritent
  du resolv.conf de l'hôte, que Tailscale casse régulièrement. Doublonné avec le
  `daemon.json` de l'hôte : ceinture + bretelles.
- `${SECRET_KEY}` sans défaut — si absent du `.env`, compose passe une chaîne vide et
  le backend **crash volontairement au boot** (voir app.py). Fail fast : mieux qu'une
  app qui tourne avec une clé vide.
- `expose: 5000` vs `ports: 3010:80` — le backend n'est PAS publié sur l'hôte. Seul
  Nginx le joint via le réseau Docker interne (par son nom `fitlife-backend`). Un seul
  point d'entrée = surface d'attaque minimale, et c'est ce port unique que le tunnel
  Cloudflare expose.
- `fitlife-data` (volume nommé) pour la DB : survit à `docker compose down` (sans -v)
  et aux rebuilds. `/opt/fitlife-media` (bind mount) pour les gifs : gérés par un
  script hors Docker, montés en `:ro` côté Nginx (le serveur web n'a aucune raison
  d'écrire).
- `depends_on` — ordonne le démarrage, mais n'attend PAS que le backend soit "prêt"
  (juste démarré). Suffisant ici car Nginx ne proxifie qu'à la première requête.

## backend/Dockerfile

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py coach.py schema.sql ./
EXPOSE 5000
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--timeout", "180", "app:app"]
```

- **Ordre des COPY = cache Docker.** `requirements.txt` copié seul AVANT le
  `pip install` : tant que les deps ne changent pas, cette layer est réutilisée et un
  rebuild après modif de `app.py` prend 2 secondes au lieu de 2 minutes.
- `--workers 1` — critique et contre-intuitif. Chaque worker Gunicorn est un process
  Python complet, donc chaque worker lancerait SON APScheduler → jobs IA traités en
  double. Pour scaler les workers HTTP un jour, il faudra d'abord sortir le scheduler
  dans un process séparé.
- `--timeout 180` — Gunicorn tue un worker silencieux après 120s par défaut. La
  génération IA au premier chargement du modèle peut dépasser → aligné sur le timeout
  Ollama (180s).

## frontend/Dockerfile (multi-stage)

```dockerfile
FROM node:20-alpine AS builder
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
```

- Stage 1 : Node + toutes les deps → `vite build` produit `dist/` (HTML + JS/CSS
  minifiés et hashés).
- Stage 2 : image finale = Nginx + `dist/` + conf. **Node n'existe plus dans l'image
  finale** (~25 MB vs ~400 MB). Le `--from=builder` est le cœur du pattern.
- Même astuce de cache que côté Python : `package.json` copié avant `npm install`.

## frontend/nginx.conf

```nginx
server {
    listen 80;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;

    gzip on;
    gzip_types text/plain text/css application/json application/javascript text/javascript;

    location /api/ {
        proxy_pass http://fitlife-backend:5000/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 180s;
    }

    location /media/ {
        expires 1y;
        add_header Cache-Control "public, immutable";
    }

    location / {
        try_files $uri $uri/ /index.html;
    }

    location ~* \.(js|css|png|jpg|ico|svg|woff2)$ {
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
}
```

- `proxy_pass http://fitlife-backend:5000` — `fitlife-backend` est résolu par le DNS
  interne de Docker (le nom du service). Zéro IP en dur.
- `proxy_read_timeout 180s` — sans lui, Nginx coupe à 60s et renvoie un 504 pendant
  les générations lentes, même si Flask travaille encore.
- `try_files $uri $uri/ /index.html` — LA ligne indispensable pour une SPA : un
  refresh du navigateur sur n'importe quel chemin retombe sur index.html, et React
  reprend la main. Sans ça : 404 sur tout sauf `/`.
- Cache 1 an sur `/media/` et les assets : les gifs ne changent jamais, et les JS/CSS
  de Vite ont un hash dans le nom (un nouveau build = un nouveau nom = pas de cache
  périmé). `index.html` lui n'est PAS caché → il pointe toujours vers le bon hash.

---

# Partie 2 — Le schéma (backend/schema.sql)

Code complet, puis lecture guidée :

```sql
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profiles (
    user_id INTEGER PRIMARY KEY REFERENCES users(id),
    goal TEXT NOT NULL,              -- recomp | strength | endurance
    gym_days INTEGER NOT NULL,       -- 2 | 3 | 4
    focus TEXT NOT NULL,             -- balanced | upper | lower
    level TEXT NOT NULL,             -- beginner | intermediate
    equipment_pref TEXT NOT NULL,    -- machines | machines_dumbbells | all
    run_km_target REAL NOT NULL DEFAULT 0,
    run_days INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS exercises (
    id TEXT PRIMARY KEY,             -- id dataset, ex "0025"
    name TEXT NOT NULL,
    category TEXT NOT NULL,          -- body part
    equipment TEXT NOT NULL,
    target TEXT,
    muscle_group TEXT,
    secondary_muscles TEXT,          -- JSON array
    instructions TEXT,               -- EN
    instructions_fr TEXT,            -- traduit par Ollama, cache
    image TEXT,                      -- /media/images/xxx.jpg
    gif TEXT                         -- /media/videos/xxx.gif
);

CREATE TABLE IF NOT EXISTS workouts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    kind TEXT NOT NULL,              -- gym | run
    status TEXT NOT NULL DEFAULT 'planned',  -- planned | done | skipped
    scheduled_date TEXT,             -- YYYY-MM-DD
    completed_at TEXT,
    source TEXT NOT NULL,            -- ai | fallback | manual
    plan_json TEXT NOT NULL,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_workouts_user ON workouts(user_id, status);

CREATE TABLE IF NOT EXISTS workout_sets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workout_id INTEGER NOT NULL REFERENCES workouts(id),
    exercise_id TEXT NOT NULL REFERENCES exercises(id),
    position INTEGER NOT NULL,
    target_sets INTEGER NOT NULL,
    target_reps TEXT NOT NULL,       -- "12" ou "30-45s"
    target_weight REAL,
    actual_weight REAL,
    actual_reps TEXT,
    done INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sets_workout ON workout_sets(workout_id);

CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    type TEXT NOT NULL,              -- poids | fc
    value REAL NOT NULL,
    date TEXT NOT NULL               -- YYYY-MM-DD (retroactif via date picker)
);
CREATE INDEX IF NOT EXISTS idx_logs_user ON logs(user_id, type, date);

CREATE TABLE IF NOT EXISTS generation_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | done | failed
    created_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    result_workout_id INTEGER REFERENCES workouts(id)
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON generation_jobs(status);
```

**Lecture guidée :**

- `profiles.user_id INTEGER PRIMARY KEY REFERENCES users(id)` — la PK EST la FK :
  c'est comme ça qu'on encode une relation **1-1** en SQL. Impossible d'avoir deux
  profils pour un user.
- `exercises.id TEXT` — on garde les ids du dataset ("0025") plutôt que de générer les
  nôtres : les médias sur disque sont nommés avec, et un ré-import reste idempotent.
- Le couple `workouts.plan_json` / `workout_sets` illustre la règle *"ce que tu
  requêtes → colonnes, ce que tu affiches → JSON"*. La progression (charge précédente,
  séries complétées) se calcule en SQL sur `workout_sets` ; la structure libre du plan
  (advice, run_advice, mode) vit dans le JSON.
- `target_reps TEXT` et pas INTEGER — "8-10" et "30-45s" sont des valeurs légitimes.
  Typage guidé par le domaine, pas par le réflexe.
- Les index suivent les requêtes réelles : `workouts(user_id, status)` sert le "next
  planned de ce user", `logs(user_id, type, date)` sert les historiques par métrique.
  Règle : un index par pattern d'accès, pas un par colonne.
- `generation_jobs` — quatre colonnes suffisent à faire une queue : `status` (état),
  `attempts` (retry budget), `last_error` (debuggabilité), `result_workout_id`
  (traçabilité). Compare avec ce que t'offre SQS/Celery : conceptuellement identique.
- SQLite : les FK ne sont PAS vérifiées par défaut → `PRAGMA foreign_keys=ON` à chaque
  connexion (voir get_db plus bas). Piège classique.

---

# Partie 3 — backend/app.py

## 3.1 Boot, config, connexions

```python
app = Flask(__name__)
SECRET_KEY = os.environ.get('SECRET_KEY')
if not SECRET_KEY:
    raise ValueError("SECRET_KEY env var is required")
app.secret_key = SECRET_KEY

CORS(app, origins=os.environ.get('CORS_ORIGINS', 'http://localhost:5173').split(','))

DB_PATH = os.environ.get('DB_PATH', '/data/fitlife.db')

def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db
```

- **Fail fast** sur SECRET_KEY : mieux vaut un crash au boot qu'une app qui signe des
  JWT avec une clé vide (= tokens forgeables par n'importe qui).
- `row_factory = sqlite3.Row` : les résultats deviennent accessibles par nom
  (`row['email']`) et convertibles en dict — sinon tu manipules des tuples par index,
  illisible et fragile.
- Une connexion PAR requête (pas de pool, pas de connexion globale) : SQLite est un
  fichier, ouvrir/fermer est quasi gratuit, et ça évite tous les problèmes de partage
  de connexion entre threads (Gunicorn + APScheduler = plusieurs threads).
- Le défaut CORS `localhost:5173` = le port du dev server Vite : l'app est développable
  en local sans toucher à la conf.

```python
def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')
    with open(schema_path) as f, get_db() as db:
        db.executescript(f.read())
        try:
            db.execute('ALTER TABLE exercises ADD COLUMN instructions_fr TEXT')
        except sqlite3.OperationalError:
            pass  # colonne deja presente
        try:
            db.execute("ALTER TABLE generation_jobs ADD COLUMN mode TEXT NOT NULL DEFAULT 'gym'")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE profiles ADD COLUMN excluded_equipment TEXT "
                       "NOT NULL DEFAULT '[\"dual_cable\"]'")
        except sqlite3.OperationalError:
            pass
    log.info("DB initialized at %s", DB_PATH)

init_db()
```

- `executescript(schema.sql)` : tout le schéma est en `IF NOT EXISTS` → idempotent, on
  peut le rejouer à chaque boot sans risque.
- Les `ALTER ... ADD COLUMN` dans des try/except = **migrations du pauvre**. SQLite
  lève `OperationalError: duplicate column` si la colonne existe → on ignore. Chaque
  nouvelle colonne ajoutée en cours de vie du projet a son bloc. Simple, traçable dans
  git, suffisant en solo. (Le jour où tu as des down-migrations ou plusieurs
  environnements : Alembic.)
- Le DEFAULT de `excluded_equipment` = `'["dual_cable"]'` : les users existants
  héritent du comportement voulu (double poulie exclue) sans backfill manuel.

## 3.2 Auth JWT

```python
JWT_ALGO = 'HS256'
JWT_TTL_DAYS = 30

def make_token(user_id, ttl_days=JWT_TTL_DAYS):
    return jwt.encode(
        {'uid': user_id, 'exp': datetime.utcnow() + timedelta(days=ttl_days)},
        SECRET_KEY, algorithm=JWT_ALGO)

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization', '').replace('Bearer ', '').strip()
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[JWT_ALGO])
        except jwt.PyJWTError:
            return jsonify({'error': 'unauthorized'}), 401
        g.user_id = payload['uid']
        return f(*args, **kwargs)
    return decorated
```

- Le payload minimal `{uid, exp}` : PyJWT vérifie `exp` automatiquement au decode
  (token expiré → `ExpiredSignatureError`, sous-classe de `PyJWTError` → 401). On ne
  met RIEN de sensible dans le payload : un JWT est signé, pas chiffré — n'importe qui
  peut le lire sur jwt.io.
- `algorithms=[JWT_ALGO]` explicite au decode : bloque l'attaque classique
  "alg: none" où un attaquant forge un token non signé.
- `@wraps(f)` préserve le nom de la fonction décorée — sans lui, Flask voit toutes les
  routes avec le même nom `decorated` et refuse de démarrer.
- `g` est le contexte par-requête de Flask : `g.user_id` posé ici est lisible dans le
  handler, thread-safe, remis à zéro à chaque requête. C'est LE canal propre pour
  passer l'identité.

```python
@app.post('/api/auth/register')
def register():
    data = request.json or {}
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''
    if not email or '@' not in email or len(password) < 8:
        return jsonify({'error': 'email invalide ou mot de passe < 8 caracteres'}), 400
    with get_db() as db:
        try:
            cur = db.execute(
                'INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)',
                (email, generate_password_hash(password), datetime.utcnow().isoformat()))
        except sqlite3.IntegrityError:
            return jsonify({'error': 'email deja utilise'}), 409
        return jsonify({'token': make_token(cur.lastrowid)})

@app.post('/api/auth/login')
def login():
    data = request.json or {}
    email = (data.get('email') or '').strip().lower()
    with get_db() as db:
        row = db.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
    if not row or not check_password_hash(row['password_hash'], data.get('password') or ''):
        return jsonify({'error': 'identifiants invalides'}), 401
    return jsonify({'token': make_token(row['id'])})
```

- Email normalisé (`strip().lower()`) AVANT tout : sinon "Sab@x.ch" et "sab@x.ch"
  sont deux comptes.
- L'unicité n'est PAS vérifiée par un SELECT préalable mais en **laissant la contrainte
  UNIQUE lever** `IntegrityError` → 409. Un SELECT-puis-INSERT a une race condition
  (deux requêtes simultanées passent le SELECT) ; la contrainte DB est atomique.
- Au login, même message d'erreur que l'email existe ou pas ("identifiants invalides") :
  ne pas révéler quels emails ont un compte (user enumeration).
- `check_password_hash` compare en temps constant — jamais de `==` sur des hashes.

## 3.3 Endpoints métier : le pattern de scoping

Tous les handlers suivent la même discipline. Exemple représentatif :

```python
@app.post('/api/workouts/<int:workout_id>/sets/<int:set_id>')
@require_auth
def update_set(workout_id, set_id):
    data = request.json or {}
    with get_db() as db:
        owner = db.execute('SELECT user_id FROM workouts WHERE id=?', (workout_id,)).fetchone()
        if not owner or owner['user_id'] != g.user_id:
            return jsonify({'error': 'not_found'}), 404
        db.execute(
            'UPDATE workout_sets SET actual_weight=?, actual_reps=?, done=? '
            'WHERE id=? AND workout_id=?',
            (data.get('actual_weight'), data.get('actual_reps'),
             1 if data.get('done') else 0, set_id, workout_id))
    return jsonify({'ok': True})
```

- **Vérifier la propriété avant d'écrire** : le set appartient à un workout, le
  workout à un user. On remonte la chaîne et on compare à `g.user_id`.
- Répondre **404 et pas 403** quand la ressource appartient à quelqu'un d'autre : un
  403 confirme que la ressource EXISTE (fuite d'information). 404 = "rien à voir ici".
- Le `AND workout_id=?` dans l'UPDATE : même si quelqu'un devine un `set_id` d'un
  autre workout, la clause le neutralise. Défense en profondeur.
- `with get_db() as db:` — le context manager sqlite3 COMMIT en sortie normale et
  ROLLBACK sur exception. C'est pour ça qu'il n'y a presque aucun `db.commit()`
  explicite dans le code.

## 3.4 Création / remplacement de plan

```python
def _create_gym_workout(db, user_id, plan, source):
    cur = db.execute(
        "INSERT INTO workouts (user_id, kind, status, source, plan_json) "
        "VALUES (?, 'gym', 'planned', ?, ?)", (user_id, source, json.dumps(plan)))
    workout_id = cur.lastrowid
    for i, ex in enumerate(plan['exercises']):
        db.execute(
            'INSERT INTO workout_sets (workout_id, exercise_id, position, '
            'target_sets, target_reps, target_weight) VALUES (?, ?, ?, ?, ?, ?)',
            (workout_id, ex['id'], i, ex['sets'], ex['reps'], ex.get('weight')))
    return workout_id

def _replace_plan(db, workout_id, plan, source):
    db.execute('UPDATE workouts SET plan_json=?, source=? WHERE id=?',
               (json.dumps(plan), source, workout_id))
    db.execute('DELETE FROM workout_sets WHERE workout_id=?', (workout_id,))
    for i, ex in enumerate(plan['exercises']):
        db.execute(...)  # memes INSERT que ci-dessus
```

- Le plan (JSON) et ses sets (lignes) sont créés **dans la même transaction** — pas
  d'état intermédiaire où le workout existe sans ses sets.
- `_replace_plan` = ce que fait le worker quand l'IA répond : le workout garde son id
  (le front n'a rien à re-résoudre), seuls le contenu et `source` changent
  ('fallback' → 'ai'). DELETE + re-INSERT des sets plutôt qu'un diff : à 5 lignes,
  la simplicité gagne.

## 3.5 Compléter une séance = le déclencheur central

```python
@app.post('/api/workouts/<int:workout_id>/complete')
@require_auth
def complete_workout(workout_id):
    with get_db() as db:
        w = db.execute('SELECT * FROM workouts WHERE id=?', (workout_id,)).fetchone()
        if not w or w['user_id'] != g.user_id:
            return jsonify({'error': 'not_found'}), 404
        if w['status'] == 'done':
            return jsonify({'error': 'deja terminee'}), 400
        db.execute("UPDATE workouts SET status='done', completed_at=? WHERE id=?",
                   (datetime.utcnow().isoformat(), workout_id))
        profile = dict(db.execute('SELECT * FROM profiles WHERE user_id=?', (g.user_id,)).fetchone())
        next_id = None
        if w['kind'] == 'gym':
            plan = coach.fallback_gym_plan(db, g.user_id, profile)
            next_id = _create_gym_workout(db, g.user_id, plan, 'fallback')
            db.execute('INSERT INTO generation_jobs (user_id, created_at) VALUES (?, ?)',
                       (g.user_id, datetime.utcnow().isoformat()))
        coach.plan_missing_runs(db, g.user_id, profile)
    return jsonify({'ok': True, 'next_workout_id': next_id})
```

Relis le flux central de ARCHITECTURE.md : le voilà en 15 lignes. Trois écritures dans
UNE transaction : (1) la séance passe done, (2) la suivante existe immédiatement en
fallback — l'utilisateur n'attend jamais l'IA, (3) le job est en queue. Le guard
`status == 'done'` rend l'endpoint idempotent côté effet (double-clic sur le bouton =
un seul enchaînement).

## 3.6 Validation manuelle des courses

Pas de source externe fiable (voir Phase 7) : l'utilisateur saisit lui-même la
distance parcourue.

```python
@app.post('/api/workouts/<int:workout_id>/complete')
@require_auth
def complete_workout(workout_id):
    data = request.json or {}
    note = (data.get('note') or '').strip()
    distance_km = data.get('distance_km')
    with get_db() as db:
        w = db.execute('SELECT * FROM workouts WHERE id=?', (workout_id,)).fetchone()
        if not w or w['user_id'] != g.user_id:
            return jsonify({'error': 'not_found'}), 404
        if w['status'] == 'done':
            return jsonify({'error': 'deja terminee'}), 400
        actual_km = float(distance_km) if (w['kind'] == 'run' and distance_km) else None
        db.execute("UPDATE workouts SET status='done', completed_at=?, notes=?, actual_km=? WHERE id=?",
                   (datetime.utcnow().isoformat(), note or None, actual_km, workout_id))
        ...
```

- Même endpoint pour gym et run : `distance_km` n'est appliqué que si `kind == 'run'`,
  `note` est libre dans les deux cas (ex: "fait aux haltères, pas de machine dispo").
- `actual_km` est une colonne typée (pas du texte dans `notes`) : c'est ce qui permet
  d'agréger par semaine pour `/api/progress` sans parser de string.

## 3.7 Le worker

```python
def process_generation_jobs():
    if not coach.ollama_available():
        return
    db = get_db()
    try:
        jobs = db.execute(
            "SELECT * FROM generation_jobs WHERE status='pending' ORDER BY id LIMIT 5").fetchall()
        for job in jobs:
            try:
                plan = coach.ollama_generate_gym_plan(
                    db, job['user_id'],
                    mode=job['mode'] if 'mode' in job.keys() else 'gym')
                target = db.execute(
                    "SELECT id FROM workouts WHERE user_id=? AND status='planned' "
                    "AND kind='gym' ORDER BY id LIMIT 1", (job['user_id'],)).fetchone()
                if target:
                    _replace_plan(db, target['id'], plan, 'ai')
                    db.execute("UPDATE generation_jobs SET status='done', result_workout_id=? "
                               "WHERE id=?", (target['id'], job['id']))
                else:
                    db.execute("UPDATE generation_jobs SET status='done' WHERE id=?", (job['id'],))
                db.commit()
                log.info("Job %d done (user %d)", job['id'], job['user_id'])
            except Exception as exc:
                attempts = job['attempts'] + 1
                status = 'failed' if attempts >= MAX_JOB_ATTEMPTS else 'pending'
                db.execute(
                    'UPDATE generation_jobs SET attempts=?, last_error=?, status=? WHERE id=?',
                    (attempts, str(exc)[:500], status, job['id']))
                db.commit()
                log.warning("Job %d attempt %d failed: %s", job['id'], attempts, exc)
    finally:
        db.close()

scheduler = BackgroundScheduler(timezone='Europe/Zurich')
scheduler.add_job(process_generation_jobs, 'interval', minutes=2)
scheduler.start()
```

Anatomie du pattern retry-queue, dans l'ordre :
1. **Cheap guard** : `ollama_available()` (GET /api/tags, timeout 3s). Serveur down →
   on sort en 3s sans consommer les attempts. C'est un circuit breaker minimal.
2. `LIMIT 5` : borne le travail par tick — un tick ne doit jamais durer indéfiniment.
3. Try/except PAR job : un job qui explose n'empêche pas les suivants.
4. À l'échec : `attempts += 1`, l'erreur tronquée à 500 chars persistée
   (`last_error` = ton meilleur ami en debug — c'est lui qui a révélé "qwen3:14bb",
   "Plan invalide: pas assez varié", les timeouts...), et le job reste `pending`
   jusqu'à `MAX_JOB_ATTEMPTS` (30 × 2 min = 1h de retries).
5. Commits explicites ici (pas de context manager) car on veut committer par job,
   pas tout le batch d'un coup.

⚠️ Rappel de la Partie 1 : ce scheduler vit dans le process Gunicorn → `--workers 1`
obligatoire, sinon N schedulers concurrents.

---

# Partie 4 — backend/coach.py (le cerveau)

## 4.1 Configuration et filtres d'exercices

```python
OLLAMA_URL = os.environ.get('OLLAMA_URL', 'http://192.168.1.14:11434')
OLLAMA_MODEL = os.environ.get('OLLAMA_MODEL', 'mistral:7b')
OLLAMA_TRANSLATE_MODEL = os.environ.get('OLLAMA_TRANSLATE_MODEL', OLLAMA_MODEL)
OLLAMA_TIMEOUT = int(os.environ.get('OLLAMA_TIMEOUT', '180'))

EQUIPMENT_MAP = {
    'machines': ['leverage machine', 'cable', 'smith machine', 'body weight'],
    'machines_dumbbells': ['leverage machine', 'cable', 'smith machine', 'body weight', 'dumbbell'],
    'all': ['leverage machine', 'cable', 'smith machine', 'body weight', 'dumbbell',
            'barbell', 'kettlebell'],
}

EQUIP_PRIORITY = ['leverage machine', 'dumbbell', 'cable', 'smith machine',
                  'barbell', 'kettlebell', 'body weight']
NAME_BLACKLIST = ('balance board', 'bosu', 'stability', 'wheel', 'rope', 'suspended',
                  'run', 'walk', 'stepmill', 'elliptical', 'burpee', 'handstand',
                  'muscle up', 'planche push', 'one arm', 'single arm push')
DUAL_CABLE_KEYWORDS = ('cross-over', 'crossover', 'cable fly', 'cable alternate')
CABLE_ALLOWED_KEYWORDS = ('pushdown', 'pulldown', 'pull-down', 'row', 'curl',
                          'face pull', 'kickback', 'pull-through', 'crunch',
                          'triceps extension')

def _is_excluded(e, allow_dual_cable=False):
    name = e['name'].lower()
    if any(b in name for b in NAME_BLACKLIST):
        return True
    if e['equipment'] == 'cable' and not allow_dual_cable:
        if any(k in name for k in DUAL_CABLE_KEYWORDS):
            return True
        if not any(k in name for k in CABLE_ALLOWED_KEYWORDS):
            return True  # raises, flys, press, bends... = station double poulie
    return False
```

- `OLLAMA_TRANSLATE_MODEL` défaut = `OLLAMA_MODEL` : la séparation des deux modèles
  est opt-in, la config minimale reste valide.
- La logique câble est une **whitelist, pas une blacklist**. Historique du choix :
  blacklister "cross-over" n'a pas suffi ("cable lateral raise", "cable fly", "cable
  bench press"... se font tous sur la tour vis-à-vis). Énumérer le mauvais est sans
  fin ; énumérer le bon (pushdown, pulldown, row, curl... = mouvements simple-poulie)
  est fini et stable. Règle générale : quand la liste des cas valides est plus courte
  et plus stable que celle des invalides → whitelist.
- Tout est piloté par des tuples de keywords en tête de fichier : la connaissance
  métier ("qu'est-ce qui est faisable dans MA salle") est localisée, lisible,
  modifiable sans toucher à la logique.

## 4.2 Le pool : ce que le modèle a le DROIT de voir

```python
EXCLUDABLE_EQUIPMENT = {'cable', 'dumbbell', 'smith machine', 'barbell',
                        'kettlebell', 'dual_cable'}

def _excluded_set(profile):
    try:
        return set(json.loads(profile.get('excluded_equipment') or '["dual_cable"]'))
    except (json.JSONDecodeError, TypeError):
        return {'dual_cable'}

def exercise_pool(db, profile, per_category=10):
    excluded = _excluded_set(profile)
    allow_dual = 'dual_cable' not in excluded
    eq = [e for e in _allowed_equipment(profile) if e not in excluded]
    if not eq:
        eq = ['leverage machine', 'body weight']  # garde-fou
    skip = {'neck', 'cardio', 'lower arms'}
    placeholders = ','.join('?' * len(eq))
    rows = db.execute(
        f"SELECT id, name, category, equipment, target FROM exercises "
        f"WHERE equipment IN ({placeholders}) ORDER BY category, id", eq).fetchall()
    by_cat = {}
    for r in rows:
        c = r['category']
        if c in skip:
            continue
        e = dict(r)
        if _is_excluded(e, allow_dual):
            continue
        by_cat.setdefault(c, []).append(e)
    pool = []
    for c, items in by_cat.items():
        items.sort(key=lambda e: (_equip_rank(e), e['name']))  # machines/halteres d'abord
        pool.extend(items[:per_category])
    return pool
```

- **Principe garbage-in/garbage-out inversé** : plutôt que d'espérer que le LLM évite
  les mauvais exos, on ne les met pas dans le pool. Le filtrage amont est la première
  ligne de défense, la validation aval la seconde.
- `placeholders = ','.join('?' * len(eq))` : la façon sûre de faire un `IN (...)` de
  taille variable en SQL paramétré. JAMAIS de f-string avec les valeurs dedans
  (injection).
- `per_category=10` avec tri par priorité AVANT le cap : les 10 gardés par catégorie
  sont les 10 "meilleurs" (machines d'abord), pas les 10 premiers par id — c'était un
  bug réel (certaines catégories saturées de câble parce que triées par id).
- Le garde-fou `if not eq` : un user qui exclut TOUT garde machines + poids du corps.
  Toujours prévoir l'état dégradé des inputs user.
- `per_category` limite aussi la TAILLE DU PROMPT — lien direct avec la leçon num_ctx.

## 4.3 Fallback déterministe : templates + surcharge progressive

```python
TEMPLATES = [
    {'title': 'Full Body A — Pousser + Jambes', 'slots': [
        ('upper legs', 'quads'), ('chest', None), ('shoulders', 'delts'),
        ('upper arms', 'triceps'), ('waist', 'abs')]},
    {'title': 'Full Body B — Tirer + Jambes', 'slots': [
        ('upper legs', 'hamstrings'), ('back', 'lats'), ('back', 'upper back'),
        ('upper arms', 'biceps'), ('waist', 'abs')]},
    {'title': 'Full Body C — Mixte + Gainage', 'slots': [
        ('upper legs', 'glutes'), ('chest', 'pectorals'), ('shoulders', None),
        ('back', None), ('waist', 'abs')]},
]
REPS_BY_GOAL = {'recomp': '12', 'strength': '8', 'endurance': '15'}
SETS_BY_LEVEL = {'beginner': 3, 'intermediate': 4}

def fallback_gym_plan(db, user_id, profile):
    done_count = db.execute(
        "SELECT COUNT(*) c FROM workouts WHERE user_id=? AND kind='gym' AND status='done'",
        (user_id,)).fetchone()['c']
    template = TEMPLATES[done_count % len(TEMPLATES)]
    pool = exercise_pool(db, profile, per_category=50)
    prev = _last_same_template(db, user_id, template['title'])
    reps = REPS_BY_GOAL.get(profile['goal'], '12')
    n_sets = SETS_BY_LEVEL.get(profile['level'], 3)

    exercises = []
    for i, (category, hint) in enumerate(template['slots']):
        prev_set = prev.get(i)
        ex = _pick_exercise(pool, category, hint,
                            prefer_id=prev_set['exercise_id'] if prev_set else None)
        if not ex:
            continue
        weight = None
        if prev_set and prev_set['exercise_id'] == ex['id']:
            base = prev_set['actual_weight'] or prev_set['target_weight']
            if base:
                weight = round(base * 1.05 / 2.5) * 2.5 if prev_set['done'] else base
        exercises.append({'id': ex['id'], 'name': ex['name'],
                          'sets': n_sets, 'reps': reps, 'weight': weight})
    return {'title': template['title'], 'exercises': exercises,
            'advice': 'Plan fallback (Ollama indisponible). +5% si toutes les series passent.'}
```

Quatre mécanismes à comprendre :
1. **Rotation par modulo** : `done_count % 3` → A, B, C, A, B, C... Zéro état à
   stocker : le compteur de séances done EST l'état. Élégant et incassable.
2. **Continuité** : `_last_same_template` retrouve la dernière séance du même titre
   (via `json_extract(plan_json, '$.title')` — SQLite sait requêter DANS le JSON) et
   `prefer_id` fait re-choisir le même exo pour le même slot → la progression a un
   sens (même machine d'une fois sur l'autre).
3. **Surcharge progressive** : `actual_weight` (priorité au réalisé) × 1.05, arrondi
   au multiple de 2.5 via `round(x/2.5)*2.5` (les machines n'ont pas de plaque de
   1.37 kg). Si les séries n'étaient pas complétées (`done=0`) : même charge.
4. `hint` de muscle : "quads" vs "hamstrings" distingue leg press et leg curl dans la
   même catégorie 'upper legs' — le matching se fait sur le champ `target` du dataset.

## 4.4 L'appel Ollama : prompt, schéma, options

```python
SYSTEM_PROMPT = (
    "Tu es un coach fitness francophone. [...]"
    "REGLES STRICTES:\n"
    "- title, advice et run_advice REDIGES EN FRANCAIS. [...]\n"
    "- Exactement 5 exercices, uniquement des id presents dans le pool.\n"
    "- VARIETE OBLIGATOIRE: couvrir au moins 4 groupes differents [...] "
    "JAMAIS plus de 2 exercices du meme groupe.\n"
    "- reps = format simple: '12' ou '8-10' ou '30-45s'.\n"
    "- Surcharge progressive ~5% [...]\n"
    "- Privilegie les machines guidees (leverage machine) et les halteres. "
    "MAXIMUM 2 exercices au cable par seance. [...]"
)

PLAN_SCHEMA = {
    'type': 'object',
    'properties': {
        'title': {'type': 'string'},
        'exercises': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'id': {'type': 'string'}, 'sets': {'type': 'integer'},
            'reps': {'type': 'string'}, 'weight': {'type': ['number', 'null']}},
            'required': ['id', 'sets', 'reps']}},
        'run_advice': {'type': 'string'},
        'advice': {'type': 'string'}},
    'required': ['title', 'exercises', 'advice'],
}

def ollama_generate_gym_plan(db, user_id, mode='gym'):
    profile = dict(db.execute('SELECT * FROM profiles WHERE user_id=?', (user_id,)).fetchone())
    context = build_context(db, user_id)
    # ... pool selon mode (gym filtre / travel = body weight only)

    pool_lines = '\n'.join(
        f"{e['id']} | {e['name']} | {e['category']} | {e['equipment']}" for e in pool)
    user_msg = (
        f"PROFIL:\n{json.dumps(context['profile'], ensure_ascii=False)}\n\n"
        f"HISTORIQUE SEANCES (recentes d'abord, cible vs realise):\n"
        f"{json.dumps(context['history'][:4], ensure_ascii=False)}\n\n"
        f"METRIQUES (poids, fc repos):\n{json.dumps(context['logs'][:14], ensure_ascii=False)}\n\n"
        f"POOL D'EXERCICES (id | nom | groupe | equipement):\n{pool_lines}\n\n"
        f"{constraint}"
        "TACHE: genere la prochaine seance en JSON. Choisis EXACTEMENT 5 exercices du POOL "
        "ci-dessus par leur id, couvrant au moins 4 groupes musculaires differents. [...]"
    )

    resp = requests.post(f'{OLLAMA_URL}/api/chat', json={
        'model': OLLAMA_MODEL,
        'stream': False,
        'think': False,
        'format': PLAN_SCHEMA,
        'options': {'temperature': 0.4, 'num_ctx': 8192},
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': user_msg},
        ],
    }, timeout=OLLAMA_TIMEOUT)
```

Chaque paramètre a une histoire :
- `format: PLAN_SCHEMA` (un schéma, pas la string "json") : Ollama contraint le
  décodage token par token — la STRUCTURE devient garantie. Avant ce fix, mistral
  renvoyait du JSON valide mais avec ses propres clés.
- `num_ctx: 8192` : LE fix du bug le plus vicieux du projet. Défaut Ollama = 4096
  tokens, dépassement = troncature SILENCIEUSE. Le modèle voyait un fragment du
  prompt → ids 0001-0003 et titre générique. Symptôme d'un LLM qui déraille ? Vérifie
  d'abord ce qu'il VOIT. (Coût : ~+1.5 GB de KV cache sur un 14B — à budgéter en VRAM.)
- `think: False` : qwen3 raisonne longuement par défaut (>120s → timeouts). Inutile
  pour une tâche de sélection contrainte.
- `temperature: 0.4` : assez bas pour la cohérence, assez haut pour varier les
  séances. Pour la traduction : 0 (une trad n'a pas à être créative).
- Le message user : sections nommées, pool en **lignes compactes** (`id | nom |
  groupe | équipement` — moitié moins de tokens que du JSON), historique **borné**
  ([:4], [:14]) pour maîtriser la taille, et la TÂCHE répétée en fin de prompt (les
  modèles pondèrent davantage la fin du contexte).

## 4.5 La validation en couches (la partie la plus importante du projet)

```python
    content = resp.json().get('message', {}).get('content', '')
    try:
        plan = json.loads(content)                                    # couche 1
    except json.JSONDecodeError:
        log.warning("Ollama raw response (invalid JSON): %s", content[:500])
        raise ValueError('Reponse non-JSON')

    if not isinstance(plan.get('exercises'), list) or not plan['exercises']:  # couche 2
        log.warning("Ollama raw response (bad structure): %s", content[:500])
        raise ValueError('Plan invalide: exercises manquant')

    pool_ids = {e['id'] for e in pool}                                # couche 4 (prep)
    meta = {e['id']: e for e in pool}
    cleaned = []
    seen = set()
    for ex in plan['exercises'][:8]:
        ex_id = str(ex.get('id', '')).strip().zfill(4)                # couche 3
        if ex_id not in pool_ids or ex_id in seen:                    # couches 4 + 5
            continue
        seen.add(ex_id)
        cleaned.append({
            'id': ex_id,
            'name': meta[ex_id]['name'],
            'sets': int(ex.get('sets', 3)),
            'reps': str(ex.get('reps', '12')),
            'weight': float(ex['weight']) if ex.get('weight') else None,
        })
    if not cleaned:
        log.warning("Ollama raw response (ids invalides): %s", content[:500])
        raise ValueError('Plan invalide: aucun exercice valide')

    categories = {meta[e['id']]['category'] for e in cleaned}         # couche 6
    if mode != 'travel' and len(categories) < 3:
        log.warning("Ollama plan pas assez varie (%s): %s", categories, content[:300])
        raise ValueError(f'Plan invalide: pas assez varie ({len(categories)} groupes)')

    n_cable = sum(1 for e in cleaned if meta[e['id']].get('equipment') == 'cable')  # couche 7
    if n_cable > 2:
        raise ValueError(f'Plan invalide: trop de cable ({n_cable})')
```

Pourquoi chaque couche existe (chacune vient d'un échec observé en prod) :
1. **JSON parseable** — même avec format-schema, un timeout partiel peut tronquer.
2. **Structure minimale** — mistral renvoyait parfois `{}` valide.
3. **`zfill(4)`** — qwen3 strippait les zéros de tête : "585" au lieu de "0585".
   Normaliser AVANT de comparer.
4. **`pool_ids`, pas toute la DB** — la faille la plus instructive : valider contre
   `SELECT id FROM exercises` laissait passer les exos que le modèle RECOPIAIT depuis
   l'HISTORIQUE du contexte (dont les exclus). Valider contre ce qu'on a PROPOSÉ,
   jamais contre ce qui EXISTE.
5. **Dedup `seen`** — "Lever chest press" proposé deux fois dans le même plan.
6. **Diversité** — le plan "6 exercices d'abdos" de mistral. Une contrainte métier
   qu'aucun schéma JSON ne peut exprimer → code.
7. **Max câble** — renforce côté sortie ce que le pool filtre côté entrée.

Et le contrat avec le worker : chaque violation → `raise` → attempt++ → retry au tick
suivant. Le log du 23/07 montre le système s'auto-corriger : attempt 2 rejeté ("pas
assez varié: shoulders, chest"), attempt 3 → `Job 15 done`. **La fiabilité vient de la
validation + retry, pas du prompt.** Grave ça quelque part.

Enfin, le nom est TOUJOURS réinjecté depuis `meta` (nos données), jamais repris de la
réponse du modèle : le LLM choisit des ids, nous fournissons les faits.

## 4.6 Traduction

```python
def translate_instructions(text):
    resp = requests.post(f'{OLLAMA_URL}/api/chat', json={
        'model': OLLAMA_TRANSLATE_MODEL,
        'stream': False,
        'think': False,
        'options': {'temperature': 0},
        'messages': [
            {'role': 'system', 'content':
                'Traduis ces instructions d\'exercice de musculation en francais, '
                'style clair et direct, tutoiement. Reponds uniquement avec la traduction.'},
            {'role': 'user', 'content': text},
        ],
    }, timeout=60)
    if not resp.ok:
        raise ValueError(f'Ollama HTTP {resp.status_code}')
    out = resp.json().get('message', {}).get('content', '').strip()
    if not out:
        raise ValueError('Traduction vide')
    return out
```

Consommé par deux chemins avec le même code :
- **Lazy** (endpoint `/api/exercises/<id>/fr`) : traduit à la première ouverture,
  cache en DB, fallback EN si Ollama down. Latence payée une fois par exo.
- **Batch** (`translate_all.py`) : boucle sur les non-traduits, commit PAR exo
  (interruptible sans perte = resumable), ETA loggée toutes les 25. Pattern de
  backfill classique — le même que tu écrirais pour rattraper une colonne en Redshift.

Deux modèles, deux jobs : traduire ne demande pas un 14B — mistral:7b est 2-3× plus
rapide pour un résultat équivalent. Ollama swappe les modèles en VRAM automatiquement
si les deux ne tiennent pas ensemble (~15s de reload, acceptable).

---

# Partie 5 — Frontend

## 5.1 frontend/src/api.js (complet — 46 lignes)

```javascript
const BASE = import.meta.env.VITE_API_URL || '/api'

export function getToken() { return localStorage.getItem('fitlife_token') }
export function setToken(t) { localStorage.setItem('fitlife_token', t) }
export function clearToken() { localStorage.removeItem('fitlife_token') }

async function req(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } }
  const token = getToken()
  if (token) opts.headers['Authorization'] = `Bearer ${token}`
  if (body !== undefined) opts.body = JSON.stringify(body)
  const r = await fetch(`${BASE}${path}`, opts)
  if (r.status === 401 && !path.startsWith('/auth/')) {
    clearToken()
    window.location.reload()
  }
  const data = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(data.error || `${method} ${path} → ${r.status}`)
  return data
}

export const api = {
  register: (email, password) => req('POST', '/auth/register', { email, password }),
  login: (email, password) => req('POST', '/auth/login', { email, password }),
  me: () => req('GET', '/me'),
  saveProfile: (p) => req('POST', '/profile', p),
  saveExclusions: (excluded) => req('POST', '/profile/exclusions', { excluded }),
  exerciseFr: (id) => req('GET', `/exercises/${id}/fr`),
  exercises: (params = {}) => {
    const qs = new URLSearchParams(params).toString()
    return req('GET', `/exercises${qs ? '?' + qs : ''}`)
  },
  workouts: () => req('GET', '/workouts'),
  generateWorkout: () => req('POST', '/workouts/generate'),
  setMode: (mode) => req('POST', '/workouts/mode', { mode }),
  updateSet: (workoutId, setId, data) => req('POST', `/workouts/${workoutId}/sets/${setId}`, data),
  completeWorkout: (workoutId, data = {}) => req('POST', `/workouts/${workoutId}/complete`, data),
  log: (type, value, date) => req('POST', `/log/${type}`, { value, date }),
  logs: () => req('GET', '/logs'),
  progress: () => req('GET', '/progress'),
}
```

- **Un seul wrapper `req()`** pour tous les appels : le token, le JSON, la gestion
  d'erreur sont écrits UNE fois. Chaque méthode de `api` est une ligne déclarative —
  ajouter un endpoint = une ligne.
- `if (r.status === 401 && !path.startsWith('/auth/'))` : un 401 = session invalide →
  purge + reload (retour à l'écran de login). L'exception `/auth/` évite la boucle :
  un mauvais mot de passe au login est AUSSI un 401 mais ne doit pas reload. Un endpoint
  métier qui répond 401 pour un état applicatif (plutôt qu'un vrai problème d'identité)
  déclenche cette purge par erreur : le contrat des codes HTTP se lit DES DEUX côtés.
- `throw new Error(data.error || ...)` : le message d'erreur du backend (en français)
  remonte tel quel dans les toasts.
- localStorage vs cookie httpOnly : localStorage est vulnérable au XSS mais simple ;
  acceptable pour un PoC self-hosted. La version durcie (cookie httpOnly + CSRF token)
  est un bon exercice d'extension.

## 5.2 La machine à états racine (App)

```javascript
export default function App() {
  const [me, setMe] = useState(null)
  const [loading, setLoading] = useState(true)
  const [toast, setToast] = useState(null)

  const refreshMe = useCallback(async () => {
    try { setMe(await api.me()) } catch { setMe(null) }
  }, [])

  useEffect(() => {
    if (!getToken()) { setLoading(false); return }
    refreshMe().finally(() => setLoading(false))
  }, [refreshMe])

  if (loading) return <Center>Chargement...</Center>
  return (
    <div style={{ maxWidth: 480, margin: '0 auto', paddingBottom: 40 }}>
      {toast && <Toast .../>}
      {!me
        ? <AuthScreen onAuthed={refreshMe} showToast={showToast} />
        : !me.profile
          ? <Onboarding onDone={refreshMe} showToast={showToast} />
          : <Main me={me} refreshMe={refreshMe} showToast={showToast} />}
    </div>
  )
}
```

- Toute l'UX tient dans une expression ternaire à 3 branches : pas de token/me →
  login ; me sans profil → onboarding ; sinon → app. Chaque transition = un
  `refreshMe()`. Quand tu recodes, commence PAR cette machine à états, le reste
  s'accroche dessus.
- `maxWidth: 480` : l'app est mobile-first par construction — sur desktop c'est une
  colonne centrée, sur téléphone c'est plein écran. Un seul layout à maintenir.

## 5.3 L'optimistic update (SetRow + saveSet)

```javascript
const saveSet = async (setId, patch) => {
  setData(prev => ({
    ...prev,
    next: {
      ...prev.next,
      sets: prev.next.sets.map(s => s.id === setId
        ? { ...s, actual_weight: patch.actual_weight, done: patch.done ? 1 : 0 }
        : s),
    },
  }))
  try { await api.updateSet(w.id, setId, patch) } catch { showToast('Erreur sync', 'err') }
}
```

- Le state local est mis à jour AVANT l'appel réseau : la coche et la barre de
  progression réagissent instantanément, même sur une 4G qui rame. En cas d'échec :
  toast (choix assumé de ne pas rollback pour un PoC — un vrai rollback stockerait
  l'état précédent et le restaurerait dans le catch : bon exercice).
- Note la mise à jour **immutable** : spread à chaque niveau (`prev` → `next` →
  `sets.map`). React compare par référence ; muter l'objet existant ne redessine rien.
  C'est LE réflexe React à automatiser.

Dans `SetRow`, le champ kg n'apparaît pas pour le poids du corps :

```javascript
const bodyweight = s.equipment === 'body weight'
// ...
{!bodyweight && (
  <input type="number" step="0.5" value={weight} placeholder="kg"
    onChange={e => setWeight(e.target.value)} onBlur={blurWeight} ... />
)}
```

Le save se fait au `onBlur` (pas à chaque frappe) : une saisie de charge = un seul
POST. Même philosophie que le debounce, adaptée à un champ numérique.

## 5.4 Le debounce de recherche (ExercisesTab)

```javascript
useEffect(() => {
  const t = setTimeout(() => {
    api.exercises({ ...(q && { q }), ...(category && { category }) }).then(setData).catch(() => {})
  }, 300)
  return () => clearTimeout(t)
}, [q, category])
```

Le pattern canonique : chaque frappe re-déclenche l'effet, le **cleanup** annule le
timer précédent → seule la dernière frappe (après 300 ms de silence) fetch. Sans ça :
un appel réseau PAR caractère tapé. `{...(q && { q })}` n'ajoute la clé que si la
valeur est non vide → l'URL reste propre (`?q=press` et pas `?q=&category=`).

## 5.5 InstructionsFR : fetch lazy avec garde anti-course

```javascript
function InstructionsFR({ exerciseId, fallback }) {
  const [text, setText] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    api.exerciseFr(exerciseId)
      .then(d => { if (alive) setText(d.instructions_fr) })
      .catch(() => {})
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [exerciseId])

  if (loading) return <div style={...}>Traduction...</div>
  return (
    <div style={...}>
      {text || fallback}
      {!text && fallback && <div style={...}>(VF indisponible — serveur IA down)</div>}
    </div>
  )
}
```

Le flag `alive` (mis à false dans le cleanup) empêche le setState sur un composant
démonté : si l'utilisateur ouvre l'exo A puis l'exo B avant la fin du fetch de A, la
réponse de A est ignorée. Sans ce guard : warnings React et affichages incohérents
(la réponse lente écrase la récente). Version pro du même pattern : AbortController.
Et la dégradation est explicite : FR si dispo, sinon EN + mention — jamais un trou.

## 5.6 Les toggles d'exclusion (ProfileTab)

```javascript
const toggleExclusion = async (key) => {
  const next = excluded.includes(key) ? excluded.filter(k => k !== key) : [...excluded, key]
  setExcluded(next)          // optimistic
  setSavingExcl(true)
  try {
    await api.saveExclusions(next)
    showToast('Programme mis à jour')
  } catch (e) {
    showToast(e.message, 'err')
    setExcluded(excluded)    // rollback (ici on le fait : l'état est trivial)
  } finally { setSavingExcl(false) }
}
```

Même pattern optimistic que saveSet, mais AVEC rollback cette fois — parce que l'état
(une liste) est trivial à restaurer. Côté backend, `POST /profile/exclusions` ne se
contente pas de sauver : il **régénère la séance planifiée** (fallback + nouveau job
IA). Un toggle a donc un effet visible immédiat dans l'onglet Séance — une préférence
qui ne change rien d'observable est une préférence morte.

Le switch est fait maison (deux divs + transition CSS sur `left`) : pas besoin d'une
lib de composants pour un toggle.

---

# Partie 6 — Les scripts

## import_exercises.py — l'essentiel

```python
kept = [e for e in exercises if e.get('equipment', '').lower() in ALLOWED_EQUIPMENT]
# ... copie des medias ...
db.execute(
    """INSERT INTO exercises (...)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
       ON CONFLICT(id) DO UPDATE SET
         name=excluded.name, category=excluded.category, ...
         image=excluded.image, gif=excluded.gif""",
    # note: instructions_fr volontairement absent -> traductions conservees
    ...)
```

L'upsert `ON CONFLICT ... DO UPDATE` liste EXPLICITEMENT les colonnes à rafraîchir ;
`instructions_fr` n'y est pas → un ré-import (nouvel équipement, dataset mis à jour)
préserve les traductions. La v1 utilisait `INSERT OR REPLACE` : REPLACE = DELETE +
INSERT, les colonnes absentes reprennent leur DEFAULT → les 905 trads auraient été
détruites. **REPLACE ≠ UPSERT** : la différence a failli coûter 1h de GPU.

## translate_all.py — le backfill resumable

```python
todo = db.execute(
    "SELECT id, name, instructions FROM exercises "
    "WHERE instructions IS NOT NULL AND instructions_fr IS NULL ORDER BY id").fetchall()
for i, row in enumerate(todo, 1):
    try:
        fr = coach.translate_instructions(row['instructions'])
        db.execute('UPDATE exercises SET instructions_fr=? WHERE id=?', (fr, row['id']))
        db.commit()                       # commit PAR ligne = interruptible
    except Exception as exc:
        failed += 1
        log.warning("Echec %s (%s): %s", row['id'], row['name'], exc)
    if i % 25 == 0:
        rate = i / (time.time() - start)
        log.info("%d/%d — %.1f/s — ETA %.0f min", i, len(todo), rate, (len(todo)-i)/rate/60)
```

Trois propriétés qui définissent un bon script de backfill : (1) la SÉLECTION est le
checkpoint (`WHERE instructions_fr IS NULL` → relancer = reprendre), (2) commit par
unité de travail (un kill -9 ne perd que la ligne en cours), (3) observabilité
(progression + ETA + erreurs nommées). Le script réimporte `coach` (`sys.path.insert`)
pour réutiliser `translate_instructions` : un seul code de traduction, deux usages.

---

# Checklist finale de rebuild

Coche au fur et à mesure — chaque item doit être écrit SANS regarder ce document,
puis diffé contre lui :

- [ ] compose + 2 Dockerfiles + nginx.conf (Partie 1) — vérifie tailles d'images et proxy
- [ ] schema.sql de mémoire (Partie 2) — diff, note les oublis
- [ ] get_db / init_db + une migration ALTER (3.1)
- [ ] make_token / require_auth / register / login (3.2) — teste les 401 au curl
- [ ] un endpoint scoped avec vérification de propriété (3.3)
- [ ] _create/_replace plan + complete_workout (3.4, 3.5)
- [ ] validation manuelle des courses, note libre (3.6)
- [ ] worker + retry + last_error (3.7) — simule Ollama down
- [ ] filtres + pool + fallback avec surcharge progressive (4.1-4.3)
- [ ] appel Ollama : schéma + num_ctx + think:false (4.4)
- [ ] les 7 couches de validation (4.5) — sans regarder, puis compte celles oubliées
- [ ] api.js wrapper + machine à états App (5.1, 5.2)
- [ ] optimistic update + debounce + fetch lazy avec guard (5.3-5.5)
- [ ] import upsert + backfill resumable (Partie 6)

*Companion de ARCHITECTURE.md — dataset hasaneyldrm/exercises-dataset (éducatif/non-commercial).*
