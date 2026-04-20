import os
import sqlite3
import json
import logging
import requests
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, request, jsonify, redirect, session
from flask_cors import CORS

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
            CREATE TABLE IF NOT EXISTS state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                value REAL NOT NULL,
                date TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions_done (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                week_num INTEGER NOT NULL,
                year INTEGER NOT NULL,
                day_index INTEGER NOT NULL,
                done INTEGER NOT NULL DEFAULT 1,
                UNIQUE(week_num, year, day_index)
            );
            CREATE TABLE IF NOT EXISTS charges (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS oauth_tokens (
                provider TEXT PRIMARY KEY,
                access_token TEXT,
                refresh_token TEXT,
                expires_at TEXT,
                raw TEXT
            );
        """)
    log.info("DB initialized at %s", DB_PATH)


# --- Auth middleware ---

def require_token(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get('Authorization', '')
        token = auth.replace('Bearer ', '').strip()
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
    state['logs'] = {
        'poids': [dict(r) for r in logs_poids],
        'fc': [dict(r) for r in logs_fc],
        'cigs': [dict(r) for r in logs_cigs],
    }
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
            db.execute(
                'INSERT OR REPLACE INTO state (key, value, updated_at) VALUES (?, ?, ?)',
                (key, json.dumps(value), now)
            )
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
        db.execute('INSERT OR REPLACE INTO state (key, value, updated_at) VALUES (?, ?, ?)',
                   (log_type, json.dumps(float(value)), date))
    return jsonify({'ok': True})


# --- Sessions ---

@app.post('/api/sessions/toggle')
@require_token
def toggle_session():
    data = request.json or {}
    week_num = data.get('week_num')
    year = data.get('year')
    day_index = data.get('day_index')
    done = data.get('done', True)
    if any(v is None for v in [week_num, year, day_index]):
        return jsonify({'error': 'week_num, year, day_index required'}), 400
    with get_db() as db:
        if done:
            db.execute(
                'INSERT OR REPLACE INTO sessions_done (week_num, year, day_index, done) VALUES (?, ?, ?, 1)',
                (week_num, year, day_index)
            )
        else:
            db.execute(
                'DELETE FROM sessions_done WHERE week_num=? AND year=? AND day_index=?',
                (week_num, year, day_index)
            )
    return jsonify({'ok': True})


# --- Charges ---

@app.post('/api/charges')
@require_token
def save_charges():
    data = request.json or {}
    now = datetime.utcnow().isoformat()
    with get_db() as db:
        for key, value in data.items():
            db.execute(
                'INSERT OR REPLACE INTO charges (key, value, updated_at) VALUES (?, ?, ?)',
                (key, str(value), now)
            )
    return jsonify({'ok': True})


# --- Strava OAuth ---

@app.get('/api/strava/auth')
@require_token
def strava_auth():
    if not STRAVA_CLIENT_ID:
        return jsonify({'error': 'Strava not configured'}), 500
    url = (
        f"https://www.strava.com/oauth/authorize"
        f"?client_id={STRAVA_CLIENT_ID}"
        f"&redirect_uri={STRAVA_REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=read,activity:read_all"
    )
    return jsonify({'url': url})


@app.get('/api/strava/callback')
def strava_callback():
    code = request.args.get('code')
    if not code:
        return "Missing code", 400
    resp = requests.post('https://www.strava.com/oauth/token', data={
        'client_id': STRAVA_CLIENT_ID,
        'client_secret': STRAVA_CLIENT_SECRET,
        'code': code,
        'grant_type': 'authorization_code',
    })
    if not resp.ok:
        log.error("Strava token exchange failed: %s", resp.text)
        return "Strava auth failed", 400
    tokens = resp.json()
    expires_at = datetime.utcfromtimestamp(tokens['expires_at']).isoformat()
    with get_db() as db:
        db.execute(
            'INSERT OR REPLACE INTO oauth_tokens (provider, access_token, refresh_token, expires_at, raw) VALUES (?, ?, ?, ?, ?)',
            ('strava', tokens['access_token'], tokens['refresh_token'], expires_at, json.dumps(tokens))
        )
    log.info("Strava tokens saved")
    return redirect('/?strava=connected')


def get_strava_token():
    with get_db() as db:
        row = db.execute("SELECT * FROM oauth_tokens WHERE provider='strava'").fetchone()
    if not row:
        return None
    expires_at = datetime.fromisoformat(row['expires_at'])
    if datetime.utcnow() >= expires_at - timedelta(minutes=5):
        resp = requests.post('https://www.strava.com/oauth/token', data={
            'client_id': STRAVA_CLIENT_ID,
            'client_secret': STRAVA_CLIENT_SECRET,
            'refresh_token': row['refresh_token'],
            'grant_type': 'refresh_token',
        })
        if not resp.ok:
            log.error("Strava token refresh failed: %s", resp.text)
            return None
        tokens = resp.json()
        expires_at = datetime.utcfromtimestamp(tokens['expires_at']).isoformat()
        with get_db() as db:
            db.execute(
                'INSERT OR REPLACE INTO oauth_tokens (provider, access_token, refresh_token, expires_at, raw) VALUES (?, ?, ?, ?, ?)',
                ('strava', tokens['access_token'], tokens['refresh_token'], expires_at, json.dumps(tokens))
            )
        return tokens['access_token']
    return row['access_token']


@app.get('/api/strava/activities')
@require_token
def strava_activities():
    token = get_strava_token()
    if not token:
        return jsonify({'error': 'not_connected'}), 401
    after = int((datetime.utcnow() - timedelta(days=7)).timestamp())
    resp = requests.get(
        'https://www.strava.com/api/v3/athlete/activities',
        headers={'Authorization': f'Bearer {token}'},
        params={'after': after, 'per_page': 20}
    )
    if not resp.ok:
        return jsonify({'error': 'strava_error'}), 502
    activities = resp.json()
    runs = [
        {
            'id': a['id'],
            'name': a['name'],
            'date': a['start_date_local'],
            'distance_km': round(a['distance'] / 1000, 2),
            'duration_s': a['moving_time'],
            'avg_hr': a.get('average_heartrate'),
            'max_hr': a.get('max_heartrate'),
            'type': a['sport_type'],
        }
        for a in activities if a['sport_type'] in ('Run', 'Walk')
    ]
    total_km = round(sum(r['distance_km'] for r in runs), 2)
    return jsonify({'activities': runs, 'total_km_week': total_km})


@app.get('/api/strava/status')
@require_token
def strava_status():
    with get_db() as db:
        row = db.execute("SELECT expires_at FROM oauth_tokens WHERE provider='strava'").fetchone()
    return jsonify({'connected': row is not None})


# --- Google OAuth ---

@app.get('/api/google/auth')
@require_token
def google_auth():
    if not GOOGLE_CLIENT_ID:
        return jsonify({'error': 'Google not configured'}), 500
    scopes = 'https://www.googleapis.com/auth/calendar.events'
    url = (
        f"https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={GOOGLE_CLIENT_ID}"
        f"&redirect_uri={GOOGLE_REDIRECT_URI}"
        f"&response_type=code"
        f"&scope={scopes}"
        f"&access_type=offline"
        f"&prompt=consent"
    )
    return jsonify({'url': url})


@app.get('/api/google/callback')
def google_callback():
    code = request.args.get('code')
    if not code:
        return "Missing code", 400
    resp = requests.post('https://oauth2.googleapis.com/token', data={
        'client_id': GOOGLE_CLIENT_ID,
        'client_secret': GOOGLE_CLIENT_SECRET,
        'code': code,
        'redirect_uri': GOOGLE_REDIRECT_URI,
        'grant_type': 'authorization_code',
    })
    if not resp.ok:
        log.error("Google token exchange failed: %s", resp.text)
        return "Google auth failed", 400
    tokens = resp.json()
    expires_at = (datetime.utcnow() + timedelta(seconds=tokens['expires_in'])).isoformat()
    with get_db() as db:
        db.execute(
            'INSERT OR REPLACE INTO oauth_tokens (provider, access_token, refresh_token, expires_at, raw) VALUES (?, ?, ?, ?, ?)',
            ('google', tokens['access_token'], tokens.get('refresh_token', ''), expires_at, json.dumps(tokens))
        )
    log.info("Google tokens saved")
    return redirect('/?google=connected')


def get_google_token():
    with get_db() as db:
        row = db.execute("SELECT * FROM oauth_tokens WHERE provider='google'").fetchone()
    if not row:
        return None
    expires_at = datetime.fromisoformat(row['expires_at'])
    if datetime.utcnow() >= expires_at - timedelta(minutes=5):
        resp = requests.post('https://oauth2.googleapis.com/token', data={
            'client_id': GOOGLE_CLIENT_ID,
            'client_secret': GOOGLE_CLIENT_SECRET,
            'refresh_token': row['refresh_token'],
            'grant_type': 'refresh_token',
        })
        if not resp.ok:
            log.error("Google token refresh failed: %s", resp.text)
            return None
        tokens = resp.json()
        expires_at = (datetime.utcnow() + timedelta(seconds=tokens['expires_in'])).isoformat()
        with get_db() as db:
            db.execute(
                'INSERT OR REPLACE INTO oauth_tokens (provider, access_token, refresh_token, expires_at, raw) VALUES (?, ?, ?, ?, ?)',
                ('google', tokens['access_token'], row['refresh_token'], expires_at, json.dumps(tokens))
            )
        return tokens['access_token']
    return row['access_token']


@app.post('/api/google/create-events')
@require_token
def create_calendar_events():
    token = get_google_token()
    if not token:
        return jsonify({'error': 'not_connected'}), 401

    SCHEDULE = [
        None,
        {'title': 'Full Body A', 'duration': 60},
        {'title': 'Course Zone 2', 'duration': 45},
        {'title': 'Full Body B', 'duration': 60},
        None,
        {'title': 'Full Body C', 'duration': 60},
        {'title': 'Course Zone 2 longue', 'duration': 75},
    ]

    data = request.json or {}
    start_time = data.get('start_time', '07:00')
    weeks = data.get('weeks', 12)

    created = []
    errors = []
    today = datetime.utcnow().date()
    monday = today - timedelta(days=today.weekday())

    for week in range(weeks):
        for day_idx, session in enumerate(SCHEDULE):
            if session is None:
                continue
            event_date = monday + timedelta(weeks=week, days=day_idx)
            h, m = map(int, start_time.split(':'))
            start_dt = datetime(event_date.year, event_date.month, event_date.day, h, m)
            end_dt = start_dt + timedelta(minutes=session['duration'])
            event = {
                'summary': f"FitLife — {session['title']}",
                'description': 'Séance FitLife',
                'start': {'dateTime': start_dt.isoformat(), 'timeZone': 'Europe/Zurich'},
                'end': {'dateTime': end_dt.isoformat(), 'timeZone': 'Europe/Zurich'},
                'reminders': {'useDefault': False, 'overrides': [{'method': 'popup', 'minutes': 30}]},
            }
            resp = requests.post(
                'https://www.googleapis.com/calendar/v3/calendars/primary/events',
                headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
                json=event,
            )
            if resp.ok:
                created.append(f"{event_date} - {session['title']}")
            else:
                errors.append(f"{event_date} - {resp.text}")

    return jsonify({'created': len(created), 'errors': errors})


@app.get('/api/google/status')
@require_token
def google_status():
    with get_db() as db:
        row = db.execute("SELECT expires_at FROM oauth_tokens WHERE provider='google'").fetchone()
    return jsonify({'connected': row is not None})


@app.get('/api/health')
def health():
    return jsonify({'ok': True})


if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=False)
