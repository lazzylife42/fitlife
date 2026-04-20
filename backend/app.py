import os
import sqlite3
import json
import logging
import requests
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, request, jsonify, redirect
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY')
if not app.secret_key:
    raise ValueError("SECRET_KEY env var is required")

CORS(app, origins=os.environ.get('CORS_ORIGINS', 'http://localhost:5173').split(','), supports_credentials=True)

DB_PATH = os.environ.get('DB_PATH', '/data/fitlife.db')
STRAVA_CLIENT_ID = os.environ.get('STRAVA_CLIENT_ID')
STRAVA_CLIENT_SECRET = os.environ.get('STRAVA_CLIENT_SECRET')
STRAVA_REDIRECT_URI = os.environ.get('STRAVA_REDIRECT_URI', 'https://fit.sabinomonte.ch/api/strava/callback')
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET')
GOOGLE_REDIRECT_URI = os.environ.get('GOOGLE_REDIRECT_URI', 'https://fit.sabinomonte.ch/api/google/callback')
APP_TOKEN = os.environ.get('APP_TOKEN')
if not APP_TOKEN:
    raise ValueError("APP_TOKEN env var is required")


# --- DB ---

def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL, value REAL NOT NULL, date TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions_done (id INTEGER PRIMARY KEY AUTOINCREMENT, week_num INTEGER NOT NULL, year INTEGER NOT NULL, day_index INTEGER NOT NULL, done INTEGER NOT NULL DEFAULT 1, UNIQUE(week_num, year, day_index));
            CREATE TABLE IF NOT EXISTS charges (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS oauth_tokens (provider TEXT PRIMARY KEY, access_token TEXT, refresh_token TEXT, expires_at TEXT, raw TEXT);
        """)
    log.info("DB initialized at %s", DB_PATH)


init_db()


# --- Auth ---

def require_token(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization', '').replace('Bearer ', '').strip()
        if token != APP_TOKEN:
            return jsonify({'error': 'unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated


# --- State ---

@app.get('/api/state')
@require_token
def get_state():
    with get_db() as db:
        rows = db.execute('SELECT key, value FROM state').fetchall()
        charges = db.execute('SELECT key, value FROM charges').fetchall()
        sessions = db.execute('SELECT week_num, year, day_index, done FROM sessions_done').fetchall()
        logs_poids = db.execute("SELECT value, date FROM logs WHERE type='poids' ORDER BY date DESC LIMIT 30").fetchall()
        logs_fc = db.execute("SELECT value, date FROM logs WHERE type='fc' ORDER BY date DESC LIMIT 30").fetchall()
        logs_cigs = db.execute("SELECT value, date FROM logs WHERE type='cigs' ORDER BY date DESC LIMIT 30").fetchall()
    state = {r['key']: json.loads(r['value']) for r in rows}
    state['charges'] = {r['key']: r['value'] for r in charges}
    state['sessions'] = [dict(r) for r in sessions]
    state['logs'] = {'poids': [dict(r) for r in logs_poids], 'fc': [dict(r) for r in logs_fc], 'cigs': [dict(r) for r in logs_cigs]}
    return jsonify(state)


@app.post('/api/state')
@require_token
def set_state():
    data = request.json or {}
    now = datetime.utcnow().isoformat()
    with get_db() as db:
        for key, value in data.items():
            if key in ('charges', 'sessions', 'logs'):
                continue
            db.execute('INSERT OR REPLACE INTO state (key, value, updated_at) VALUES (?, ?, ?)', (key, json.dumps(value), now))
    return jsonify({'ok': True})


# --- Logs ---

@app.post('/api/log/<log_type>')
@require_token
def add_log(log_type):
    if log_type not in ('poids', 'fc', 'cigs', 'km'):
        return jsonify({'error': 'invalid type'}), 400
    data = request.json or {}
    value = data.get('value')
    if value is None:
        return jsonify({'error': 'value required'}), 400
    date = data.get('date', datetime.utcnow().isoformat())
    with get_db() as db:
        db.execute('INSERT INTO logs (type, value, date) VALUES (?, ?, ?)', (log_type, float(value), date))
        db.execute('INSERT OR REPLACE INTO state (key, value, updated_at) VALUES (?, ?, ?)', (log_type, json.dumps(float(value)), date))
    return jsonify({'ok': True})


# --- Sessions ---

@app.post('/api/sessions/toggle')
@require_token
def toggle_session():
    data = request.json or {}
    week_num, year, day_index = data.get('week_num'), data.get('year'), data.get('day_index')
    done = data.get('done', True)
    if any(v is None for v in [week_num, year, day_index]):
        return jsonify({'error': 'week_num, year, day_index required'}), 400
    with get_db() as db:
        if done:
            db.execute('INSERT OR REPLACE INTO sessions_done (week_num, year, day_index, done) VALUES (?, ?, ?, 1)', (week_num, year, day_index))
        else:
            db.execute('DELETE FROM sessions_done WHERE week_num=? AND year=? AND day_index=?', (week_num, year, day_index))
    return jsonify({'ok': True})


# --- Charges ---

@app.post('/api/charges')
@require_token
def save_charges():
    data = request.json or {}
    now = datetime.utcnow().isoformat()
    with get_db() as db:
        for key, value in data.items():
            db.execute('INSERT OR REPLACE INTO charges (key, value, updated_at) VALUES (?, ?, ?)', (key, str(value), now))
    return jsonify({'ok': True})


# --- Strava ---

@app.get('/api/strava/auth')
@require_token
def strava_auth():
    if not STRAVA_CLIENT_ID:
        return jsonify({'error': 'Strava not configured'}), 500
    url = (f"https://www.strava.com/oauth/authorize?client_id={STRAVA_CLIENT_ID}"
           f"&redirect_uri={STRAVA_REDIRECT_URI}&response_type=code&scope=read,activity:read_all")
    return jsonify({'url': url})


@app.get('/api/strava/callback')
def strava_callback():
    code = request.args.get('code')
    if not code:
        return "Missing code", 400
    resp = requests.post('https://www.strava.com/oauth/token', data={
        'client_id': STRAVA_CLIENT_ID, 'client_secret': STRAVA_CLIENT_SECRET,
        'code': code, 'grant_type': 'authorization_code',
    })
    if not resp.ok:
        return "Strava auth failed", 400
    tokens = resp.json()
    expires_at = datetime.utcfromtimestamp(tokens['expires_at']).isoformat()
    with get_db() as db:
        db.execute('INSERT OR REPLACE INTO oauth_tokens VALUES (?, ?, ?, ?, ?)',
                   ('strava', tokens['access_token'], tokens['refresh_token'], expires_at, json.dumps(tokens)))
    log.info("Strava tokens saved")
    return redirect('/?strava=connected')


def get_strava_token():
    with get_db() as db:
        row = db.execute("SELECT * FROM oauth_tokens WHERE provider='strava'").fetchone()
    if not row:
        return None
    if datetime.utcnow() >= datetime.fromisoformat(row['expires_at']) - timedelta(minutes=5):
        resp = requests.post('https://www.strava.com/oauth/token', data={
            'client_id': STRAVA_CLIENT_ID, 'client_secret': STRAVA_CLIENT_SECRET,
            'refresh_token': row['refresh_token'], 'grant_type': 'refresh_token',
        })
        if not resp.ok:
            return None
        tokens = resp.json()
        expires_at = datetime.utcfromtimestamp(tokens['expires_at']).isoformat()
        with get_db() as db:
            db.execute('INSERT OR REPLACE INTO oauth_tokens VALUES (?, ?, ?, ?, ?)',
                       ('strava', tokens['access_token'], tokens['refresh_token'], expires_at, json.dumps(tokens)))
        return tokens['access_token']
    return row['access_token']


@app.get('/api/strava/activities')
@require_token
def strava_activities():
    token = get_strava_token()
    if not token:
        return jsonify({'error': 'not_connected'}), 401
    today = datetime.utcnow().date()
    monday = today - timedelta(days=today.weekday())
    after = int(datetime(monday.year, monday.month, monday.day).timestamp())
    resp = requests.get('https://www.strava.com/api/v3/athlete/activities',
                        headers={'Authorization': f'Bearer {token}'},
                        params={'after': after, 'per_page': 20})
    if not resp.ok:
        return jsonify({'error': 'strava_error'}), 502
    activities = resp.json()
    runs = [{'id': a['id'], 'name': a['name'], 'date': a['start_date_local'],
              'distance_km': round(a['distance'] / 1000, 2), 'duration_s': a['moving_time'],
              'avg_hr': a.get('average_heartrate'), 'max_hr': a.get('max_heartrate'), 'type': a['sport_type']}
             for a in activities if a['sport_type'] in ('Run', 'Walk')]
    total_km = round(sum(r['distance_km'] for r in runs if r['type'] == 'Run'), 2)
    return jsonify({'activities': runs, 'total_km_week': total_km})


@app.get('/api/strava/status')
@require_token
def strava_status():
    with get_db() as db:
        row = db.execute("SELECT expires_at FROM oauth_tokens WHERE provider='strava'").fetchone()
    return jsonify({'connected': row is not None})


# --- Google ---

@app.get('/api/google/auth')
@require_token
def google_auth():
    if not GOOGLE_CLIENT_ID:
        return jsonify({'error': 'Google not configured'}), 500
    url = (f"https://accounts.google.com/o/oauth2/v2/auth?client_id={GOOGLE_CLIENT_ID}"
           f"&redirect_uri={GOOGLE_REDIRECT_URI}&response_type=code"
           f"&scope=https://www.googleapis.com/auth/calendar.events&access_type=offline&prompt=consent")
    return jsonify({'url': url})


@app.get('/api/google/callback')
def google_callback():
    code = request.args.get('code')
    if not code:
        return "Missing code", 400
    resp = requests.post('https://oauth2.googleapis.com/token', data={
        'client_id': GOOGLE_CLIENT_ID, 'client_secret': GOOGLE_CLIENT_SECRET,
        'code': code, 'redirect_uri': GOOGLE_REDIRECT_URI, 'grant_type': 'authorization_code',
    })
    if not resp.ok:
        return "Google auth failed", 400
    tokens = resp.json()
    expires_at = (datetime.utcnow() + timedelta(seconds=tokens['expires_in'])).isoformat()
    with get_db() as db:
        db.execute('INSERT OR REPLACE INTO oauth_tokens VALUES (?, ?, ?, ?, ?)',
                   ('google', tokens['access_token'], tokens.get('refresh_token', ''), expires_at, json.dumps(tokens)))
    log.info("Google tokens saved")
    return redirect('/?google=connected')


def get_google_token():
    with get_db() as db:
        row = db.execute("SELECT * FROM oauth_tokens WHERE provider='google'").fetchone()
    if not row:
        return None
    if datetime.utcnow() >= datetime.fromisoformat(row['expires_at']) - timedelta(minutes=5):
        resp = requests.post('https://oauth2.googleapis.com/token', data={
            'client_id': GOOGLE_CLIENT_ID, 'client_secret': GOOGLE_CLIENT_SECRET,
            'refresh_token': row['refresh_token'], 'grant_type': 'refresh_token',
        })
        if not resp.ok:
            return None
        tokens = resp.json()
        expires_at = (datetime.utcnow() + timedelta(seconds=tokens['expires_in'])).isoformat()
        with get_db() as db:
            db.execute('INSERT OR REPLACE INTO oauth_tokens VALUES (?, ?, ?, ?, ?)',
                       ('google', tokens['access_token'], row['refresh_token'], expires_at, json.dumps(tokens)))
        return tokens['access_token']
    return row['access_token']


WEEK_SCHEDULE = [
    None,
    {
        'title': 'Full Body A — Pousser + Jambes',
        'duration': 60,
        'color': '9',
        'description': (
            "Warmup : 7 min gainage\n"
            "1. Leg Press 3×12 (60-80kg)\n"
            "2. Chest Press machine 3×12 (30-40kg)\n"
            "3. Élévations latérales 3×15 (5-8kg)\n"
            "4. Triceps câble pushdown 3×15 (15-20kg)\n"
            "5. Crunch machine 3×15\n\n"
            "Surcharge progressive : +5% si 3×12 terminés sans forcer"
        ),
    },
    {
        'title': 'Course Zone 2',
        'duration': 45,
        'color': '2',
        'description': (
            "Objectif : 3-5km en Zone 2 (130-140 bpm)\n"
            "Alerte FC > 140 bpm activée sur Garmin\n"
            "Allure libre, priorité FC — ralentir si besoin\n"
            "Progression : +0.5km/semaine"
        ),
    },
    {
        'title': 'Full Body B — Tirer + Jambes',
        'duration': 60,
        'color': '9',
        'description': (
            "Warmup : 7 min gainage\n"
            "1. Leg Curl couché 3×12 (25-35kg)\n"
            "2. Low Row machine 3×12 (30-40kg)\n"
            "3. Lat Pulldown 3×12 (35-45kg)\n"
            "4. Curl biceps machine 3×15 (15-20kg)\n"
            "5. Adducteurs/Abducteurs 3×15 (30-40kg)\n\n"
            "Surcharge progressive : +5% si 3×12 terminés sans forcer"
        ),
    },
    None,
    {
        'title': 'Full Body C — Mixte + Gainage',
        'duration': 60,
        'color': '9',
        'description': (
            "Warmup : 7 min gainage\n"
            "1. Leg Press 3×10 (70-90kg) — plus lourd\n"
            "2. Pec Deck papillon 3×15 (25-35kg)\n"
            "3. Lat Pulldown prise serrée 3×12 (35-40kg)\n"
            "4. Shoulder Press machine 3×12 (20-30kg)\n"
            "5. Planche gainage 3×30-45sec\n\n"
            "Surcharge progressive : +5% si terminé sans forcer"
        ),
    },
    {
        'title': 'Course Zone 2 longue',
        'duration': 75,
        'color': '2',
        'description': (
            "Objectif : 5-8km en Zone 2 (130-140 bpm)\n"
            "Alerte FC > 140 bpm activée sur Garmin\n"
            "Allure libre, priorité FC — ralentir si besoin\n"
            "Progression : +0.5km/semaine\n"
            "Séance longue de la semaine — pas de pression sur le chrono"
        ),
    },
]


def _create_week_events(token, monday, start_time='09:00'):
    created, errors = [], []
    h, m = map(int, start_time.split(':'))
    for day_idx, session in enumerate(WEEK_SCHEDULE):
        if session is None:
            continue
        event_date = monday + timedelta(days=day_idx)
        start_dt = datetime(event_date.year, event_date.month, event_date.day, h, m)
        end_dt = start_dt + timedelta(minutes=session['duration'])
        event = {
            'summary': f"FitLife — {session['title']}",
            'description': session.get('description', 'Séance FitLife'),
            'colorId': session['color'],
            'start': {'dateTime': start_dt.isoformat(), 'timeZone': 'Europe/Zurich'},
            'end': {'dateTime': end_dt.isoformat(), 'timeZone': 'Europe/Zurich'},
            'reminders': {'useDefault': False, 'overrides': [{'method': 'popup', 'minutes': 30}]},
        }
        resp = requests.post('https://www.googleapis.com/calendar/v3/calendars/primary/events',
                             headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
                             json=event)
        if resp.ok:
            created.append(f"{event_date} - {session['title']}")
        else:
            errors.append(f"{event_date} - {resp.text}")
    return created, errors


def scheduled_create_week_events():
    log.info("Cron: creating week events")
    token = get_google_token()
    if not token:
        log.warning("Cron: Google token not available, skipping")
        return
    today = datetime.utcnow().date()
    monday = today - timedelta(days=today.weekday())
    created, errors = _create_week_events(token, monday)
    log.info("Cron: created %d events, %d errors", len(created), len(errors))


@app.post('/api/google/create-events')
@require_token
def create_calendar_events():
    token = get_google_token()
    if not token:
        return jsonify({'error': 'not_connected'}), 401
    data = request.json or {}
    start_time = data.get('start_time', '09:00')
    weeks = data.get('weeks', 12)
    today = datetime.utcnow().date()
    monday = today - timedelta(days=today.weekday())
    all_created, all_errors = [], []
    for week in range(weeks):
        created, errors = _create_week_events(token, monday + timedelta(weeks=week), start_time)
        all_created.extend(created)
        all_errors.extend(errors)
    return jsonify({'created': len(all_created), 'errors': all_errors})


@app.get('/api/google/status')
@require_token
def google_status():
    with get_db() as db:
        row = db.execute("SELECT expires_at FROM oauth_tokens WHERE provider='google'").fetchone()
    return jsonify({'connected': row is not None})


@app.get('/api/health')
def health():
    return jsonify({'ok': True})


scheduler = BackgroundScheduler(timezone='Europe/Zurich')
scheduler.add_job(scheduled_create_week_events, 'cron', day_of_week='mon', hour=7, minute=0)
scheduler.start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)