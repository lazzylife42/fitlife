# FitLife v2 — Deploy

## Structure
```
fitlife-v2/
  docker-compose.yml
  .env.example
  backend/
    app.py
    requirements.txt
    Dockerfile
  frontend/
    src/ (App.jsx, api.js, index.css, main.jsx)
    index.html
    nginx.conf
    vite.config.js
    package.json
    Dockerfile
```

## Deploy sur LXC 101

```bash
# 1. Supprimer l'ancienne version
cd /opt/fitlife && docker compose down
docker rm -f fitlife 2>/dev/null || true

# 2. Copier v2
scp -r fitlife-v2/ root@192.168.1.11:/opt/fitlife-v2

# 3. Config
cd /opt/fitlife-v2
cp .env.example .env
nano .env  # remplir SECRET_KEY, APP_TOKEN, Strava, Google

# Générer les secrets :
# openssl rand -hex 32  → SECRET_KEY
# openssl rand -hex 16  → APP_TOKEN

# 4. Build + start
docker compose up -d --build

# 5. Vérifier
docker ps | grep fitlife
curl http://localhost:3010
curl http://localhost:3010/api/health
```

## Strava setup
1. https://www.strava.com/settings/api
2. Créer app : Website = https://fit.sabinomonte.ch, Callback = https://fit.sabinomonte.ch/api/strava/callback
3. Copier Client ID + Secret dans .env
4. Rebuild : docker compose up -d --build
5. Dans l'app : onglet Intégrations → Connecter Strava

## Google Calendar setup
1. https://console.cloud.google.com → New project
2. APIs & Services → Enable → Google Calendar API
3. Credentials → OAuth 2.0 Client ID → Web app
4. Authorized redirect URI : https://fit.sabinomonte.ch/api/google/callback
5. Copier Client ID + Secret dans .env
6. Rebuild + connecter depuis l'app

## Cloudflare Tunnel
Pas de changement — même config que v1 :
```yaml
- hostname: fit.sabinomonte.ch
  service: http://192.168.1.11:3010
```

## Reset données
```bash
docker exec fitlife-backend sqlite3 /data/fitlife.db
# ou supprimer le volume :
docker compose down -v && docker compose up -d --build
```
