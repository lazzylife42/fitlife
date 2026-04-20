# FitLife

Dashboard fitness personnel — React + Flask + SQLite, self-hosted sur LXC 101.

## Stack

- **Frontend** : React + Vite, servi par Nginx
- **Backend** : Flask + SQLite, Gunicorn
- **Intégrations** : Strava (activités), Google Calendar (rappels séances)
- **Hébergement** : LXC 101 (192.168.1.11), port 3010, exposé via Cloudflare Tunnel

## Structure

```
fitlife/
  docker-compose.yml
  .env                    # non commité
  .env.example
  backend/
    app.py
    requirements.txt
    Dockerfile
  frontend/
    src/
      App.jsx
      api.js
      index.css
      main.jsx
    index.html
    nginx.conf
    vite.config.js
    package.json
    Dockerfile
  delete_fitlife_events.py  # utilitaire suppression events Google Cal
```

## Variables d'environnement (.env)

```env
SECRET_KEY=           # openssl rand -hex 32
APP_TOKEN=            # openssl rand -hex 16
STRAVA_CLIENT_ID=
STRAVA_CLIENT_SECRET=
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
```

## Deploy

```bash
# Depuis la racine du repo
rsync -av --exclude='.env' --exclude='*.zip' --exclude='*.pyc' . root@192.168.1.11:/opt/fitlife/
ssh root@192.168.1.11 "cd /opt/fitlife && docker compose build --no-cache && docker compose up -d"
```

## Cloudflare Tunnel

```yaml
ingress:
  - hostname: fit.sabinomonte.ch
    service: http://192.168.1.11:3010
```

## Strava

1. https://www.strava.com/settings/api
2. Callback URL : `https://fit.sabinomonte.ch/api/strava/callback`
3. Renseigner `STRAVA_CLIENT_ID` et `STRAVA_CLIENT_SECRET` dans `.env`
4. Connecter depuis l'onglet Intégrations

## Google Calendar

1. https://console.cloud.google.com → activer Google Calendar API
2. OAuth consent screen → ajouter email en test user
3. Credentials → OAuth 2.0 → redirect URI : `https://fit.sabinomonte.ch/api/google/callback`
4. Renseigner `GOOGLE_CLIENT_ID` et `GOOGLE_CLIENT_SECRET` dans `.env`
5. Connecter depuis l'onglet Intégrations
6. Cron automatique : chaque lundi 7h00 Europe/Zurich → génère la semaine en cours à 9h

## Utilitaires

```bash
# Supprimer tous les events FitLife du calendrier
docker cp delete_fitlife_events.py fitlife-backend:/tmp/
docker exec -it fitlife-backend python3 /tmp/delete_fitlife_events.py
# Relancer jusqu'à Total deleted: 0

# Reset DB
docker compose down -v && docker compose up -d --build

# Logs
docker logs fitlife-backend --tail 50
docker logs fitlife-frontend --tail 20
```

## Programme

| Jour | Séance |
|------|--------|
| Lundi | Repos |
| Mardi | Full Body A — Pousser + Jambes |
| Mercredi | Course Zone 2 (3→5km) |
| Jeudi | Full Body B — Tirer + Jambes |
| Vendredi | Repos actif / marche |
| Samedi | Full Body C — Mixte + Gainage |
| Dimanche | Course Zone 2 longue (5→8km) |

Objectif cardio : 10km/semaine en Zone 2 (130-140 bpm).