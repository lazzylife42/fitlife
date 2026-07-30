import os
import re
import sqlite3
import json
import logging
from datetime import datetime, timedelta
from functools import wraps

import jwt
import requests
from flask import Flask, request, jsonify, redirect, g
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from apscheduler.schedulers.background import BackgroundScheduler

import coach

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

app = Flask(__name__)
SECRET_KEY = os.environ.get('SECRET_KEY')
if not SECRET_KEY:
    raise ValueError("SECRET_KEY env var is required")
app.secret_key = SECRET_KEY

CORS(app, origins=os.environ.get('CORS_ORIGINS', 'http://localhost:5173').split(','))

DB_PATH = os.environ.get('DB_PATH', '/data/fitlife.db')
MEDIA_ROOT = os.environ.get('MEDIA_ROOT', '/media-out')


def _media_if_exists(url):
    """Retourne l'URL seulement si le fichier existe sur le disque (sinon None)."""
    if not url:
        return None
    rel = url.replace('/media/', '', 1)
    return url if os.path.isfile(os.path.join(MEDIA_ROOT, rel)) else None
STRAVA_CLIENT_ID = os.environ.get('STRAVA_CLIENT_ID')
STRAVA_CLIENT_SECRET = os.environ.get('STRAVA_CLIENT_SECRET')
STRAVA_REDIRECT_URI = os.environ.get('STRAVA_REDIRECT_URI', 'https://fit.sabinomonte.ch/api/strava/callback')

JWT_ALGO = 'HS256'
JWT_TTL_DAYS = 30
MAX_JOB_ATTEMPTS = 30


# --- DB ---

def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


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
        try:
            db.execute('ALTER TABLE workout_sets ADD COLUMN note TEXT')
        except sqlite3.OperationalError:
            pass
        try:
            db.execute('ALTER TABLE workouts ADD COLUMN actual_km REAL')
        except sqlite3.OperationalError:
            pass
        else:
            rows = db.execute(
                "SELECT id, notes FROM workouts WHERE kind='run' AND status='done' "
                "AND notes IS NOT NULL").fetchall()
            for row in rows:
                m = re.search(r'(\d+(?:\.\d+)?)\s*km', row['notes'])
                if m:
                    db.execute('UPDATE workouts SET actual_km=? WHERE id=?',
                               (float(m.group(1)), row['id']))
    log.info("DB initialized at %s", DB_PATH)


init_db()


# --- Auth ---

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


@app.get('/api/me')
@require_auth
def me():
    with get_db() as db:
        user = db.execute('SELECT id, email FROM users WHERE id=?', (g.user_id,)).fetchone()
        profile = db.execute('SELECT * FROM profiles WHERE user_id=?', (g.user_id,)).fetchone()
        strava = db.execute(
            "SELECT 1 FROM oauth_tokens WHERE user_id=? AND provider='strava'",
            (g.user_id,)).fetchone()
    return jsonify({'user': dict(user), 'profile': dict(profile) if profile else None,
                    'strava_connected': strava is not None})


# --- Profil (QCM) ---

VALID_PROFILE = {
    'goal': {'recomp', 'strength', 'endurance'},
    'gym_days': {2, 3, 4},
    'focus': {'balanced', 'upper', 'lower'},
    'level': {'beginner', 'intermediate'},
    'equipment_pref': {'machines', 'machines_dumbbells', 'all'},
}


@app.post('/api/profile')
@require_auth
def save_profile():
    data = request.json or {}
    for field, valid in VALID_PROFILE.items():
        if data.get(field) not in valid:
            return jsonify({'error': f'{field} invalide'}), 400
    run_km = float(data.get('run_km_target') or 0)
    run_days = int(data.get('run_days') or 0)
    with get_db() as db:
        prev = db.execute('SELECT excluded_equipment FROM profiles WHERE user_id=?',
                          (g.user_id,)).fetchone()
        excluded = prev['excluded_equipment'] if prev else '["dual_cable"]'
        db.execute(
            """INSERT OR REPLACE INTO profiles
               (user_id, goal, gym_days, focus, level, equipment_pref,
                run_km_target, run_days, excluded_equipment, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (g.user_id, data['goal'], data['gym_days'], data['focus'], data['level'],
             data['equipment_pref'], run_km, run_days, excluded,
             datetime.utcnow().isoformat()))
    return jsonify({'ok': True})


# --- Exercices ---

@app.get('/api/exercises')
@require_auth
def list_exercises():
    q = request.args.get('q', '').strip().lower()
    category = request.args.get('category', '').strip()
    equipment = request.args.get('equipment', '').strip()
    sql = 'SELECT * FROM exercises WHERE 1=1'
    params = []
    if q:
        sql += ' AND lower(name) LIKE ?'
        params.append(f'%{q}%')
    if category:
        sql += ' AND category=?'
        params.append(category)
    if equipment:
        sql += ' AND equipment=?'
        params.append(equipment)
    sql += ' ORDER BY name LIMIT 100'
    with get_db() as db:
        rows = db.execute(sql, params).fetchall()
        cats = db.execute('SELECT DISTINCT category FROM exercises ORDER BY category').fetchall()
        eqs = db.execute('SELECT DISTINCT equipment FROM exercises ORDER BY equipment').fetchall()
    exercises = []
    for r in rows:
        d = dict(r)
        d['image'] = _media_if_exists(d.get('image'))
        d['gif'] = _media_if_exists(d.get('gif'))
        exercises.append(d)
    return jsonify({'exercises': exercises,
                    'categories': [c['category'] for c in cats],
                    'equipments': [e['equipment'] for e in eqs]})


# --- Workouts ---

def _workout_with_sets(db, workout_id):
    w = db.execute('SELECT * FROM workouts WHERE id=?', (workout_id,)).fetchone()
    if not w:
        return None
    out = dict(w)
    out['plan'] = json.loads(out.pop('plan_json'))
    sets = db.execute(
        'SELECT ws.*, e.name, e.image, e.gif, e.instructions, e.equipment '
        'FROM workout_sets ws '
        'JOIN exercises e ON e.id = ws.exercise_id '
        'WHERE ws.workout_id=? ORDER BY ws.position', (workout_id,)).fetchall()
    out['sets'] = []
    for s in sets:
        d = dict(s)
        d['image'] = _media_if_exists(d.get('image'))
        d['gif'] = _media_if_exists(d.get('gif'))
        out['sets'].append(d)
    return out


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
        db.execute(
            'INSERT INTO workout_sets (workout_id, exercise_id, position, '
            'target_sets, target_reps, target_weight) VALUES (?, ?, ?, ?, ?, ?)',
            (workout_id, ex['id'], i, ex['sets'], ex['reps'], ex.get('weight')))


@app.get('/api/workouts')
@require_auth
def list_workouts():
    with get_db() as db:
        planned = db.execute(
            "SELECT id FROM workouts WHERE user_id=? AND status='planned' "
            "AND kind='gym' ORDER BY id LIMIT 1", (g.user_id,)).fetchone()
        runs = db.execute(
            "SELECT * FROM workouts WHERE user_id=? AND status='planned' AND kind='run'",
            (g.user_id,)).fetchall()
        history = db.execute(
            "SELECT id, kind, status, completed_at, source, plan_json FROM workouts "
            "WHERE user_id=? AND status='done' ORDER BY completed_at DESC LIMIT 10",
            (g.user_id,)).fetchall()
        pending = db.execute(
            "SELECT COUNT(*) c FROM generation_jobs WHERE user_id=? AND status='pending'",
            (g.user_id,)).fetchone()['c']
        next_workout = _workout_with_sets(db, planned['id']) if planned else None
    hist = []
    for h in history:
        d = dict(h)
        d['plan'] = json.loads(d.pop('plan_json'))
        hist.append(d)
    run_list = []
    for r in runs:
        d = dict(r)
        d['plan'] = json.loads(d.pop('plan_json'))
        run_list.append(d)
    return jsonify({'next': next_workout, 'runs': run_list,
                    'history': hist, 'ai_pending': pending > 0})


@app.post('/api/workouts/generate')
@require_auth
def generate_first_workout():
    """Premiere seance apres le QCM (fallback direct + job Ollama)."""
    with get_db() as db:
        profile = db.execute('SELECT * FROM profiles WHERE user_id=?', (g.user_id,)).fetchone()
        if not profile:
            return jsonify({'error': 'profil manquant'}), 400
        existing = db.execute(
            "SELECT id FROM workouts WHERE user_id=? AND status='planned' AND kind='gym'",
            (g.user_id,)).fetchone()
        if existing:
            return jsonify({'ok': True, 'workout_id': existing['id']})
        plan = coach.fallback_gym_plan(db, g.user_id, dict(profile))
        workout_id = _create_gym_workout(db, g.user_id, plan, 'fallback')
        coach.plan_missing_runs(db, g.user_id, dict(profile))
        db.execute(
            'INSERT INTO generation_jobs (user_id, created_at) VALUES (?, ?)',
            (g.user_id, datetime.utcnow().isoformat()))
    return jsonify({'ok': True, 'workout_id': workout_id})


@app.post('/api/profile/exclusions')
@require_auth
def save_exclusions():
    """Toggles d'exclusion materiel. Regenere la seance planifiee."""
    excluded = (request.json or {}).get('excluded', [])
    if not isinstance(excluded, list) or not set(excluded) <= coach.EXCLUDABLE_EQUIPMENT:
        return jsonify({'error': 'exclusions invalides'}), 400
    with get_db() as db:
        db.execute('UPDATE profiles SET excluded_equipment=?, updated_at=? WHERE user_id=?',
                   (json.dumps(excluded), datetime.utcnow().isoformat(), g.user_id))
        profile = db.execute('SELECT * FROM profiles WHERE user_id=?', (g.user_id,)).fetchone()
        target = db.execute(
            "SELECT id, plan_json FROM workouts WHERE user_id=? AND status='planned' "
            "AND kind='gym' ORDER BY id LIMIT 1", (g.user_id,)).fetchone()
        if profile and target:
            is_travel = json.loads(target['plan_json']).get('mode') == 'travel'
            if not is_travel:
                plan = coach.fallback_gym_plan(db, g.user_id, dict(profile))
                _replace_plan(db, target['id'], plan, 'fallback')
                db.execute("UPDATE generation_jobs SET status='done' "
                           "WHERE user_id=? AND status='pending'", (g.user_id,))
                db.execute('INSERT INTO generation_jobs (user_id, created_at) VALUES (?, ?)',
                           (g.user_id, datetime.utcnow().isoformat()))
    return jsonify({'ok': True})


@app.post('/api/workouts/mode')
@require_auth
def switch_mode():
    """Bascule la seance planifiee : salle <-> sans materiel (deplacement)."""
    mode = (request.json or {}).get('mode')
    if mode not in ('travel', 'gym'):
        return jsonify({'error': 'mode invalide'}), 400
    with get_db() as db:
        profile = db.execute('SELECT * FROM profiles WHERE user_id=?', (g.user_id,)).fetchone()
        target = db.execute(
            "SELECT id FROM workouts WHERE user_id=? AND status='planned' "
            "AND kind='gym' ORDER BY id LIMIT 1", (g.user_id,)).fetchone()
        if not profile or not target:
            return jsonify({'error': 'aucune seance planifiee'}), 400
        if mode == 'travel':
            plan = coach.fallback_bodyweight_plan(db, g.user_id, dict(profile))
            _replace_plan(db, target['id'], plan, 'fallback')
            # remplace les jobs en attente par un job mode travel
            db.execute("UPDATE generation_jobs SET status='done' "
                       "WHERE user_id=? AND status='pending'", (g.user_id,))
            db.execute("INSERT INTO generation_jobs (user_id, created_at, mode) "
                       "VALUES (?, ?, 'travel')", (g.user_id, datetime.utcnow().isoformat()))
        else:
            plan = coach.fallback_gym_plan(db, g.user_id, dict(profile))
            _replace_plan(db, target['id'], plan, 'fallback')
            db.execute("UPDATE generation_jobs SET status='done' "
                       "WHERE user_id=? AND status='pending'", (g.user_id,))
            db.execute('INSERT INTO generation_jobs (user_id, created_at) VALUES (?, ?)',
                       (g.user_id, datetime.utcnow().isoformat()))
    return jsonify({'ok': True})


@app.post('/api/workouts/<int:workout_id>/sets/<int:set_id>')
@require_auth
def update_set(workout_id, set_id):
    data = request.json or {}
    with get_db() as db:
        owner = db.execute('SELECT user_id FROM workouts WHERE id=?', (workout_id,)).fetchone()
        if not owner or owner['user_id'] != g.user_id:
            return jsonify({'error': 'not_found'}), 404
        db.execute(
            'UPDATE workout_sets SET actual_weight=?, actual_reps=?, done=?, note=? '
            'WHERE id=? AND workout_id=?',
            (data.get('actual_weight'), data.get('actual_reps'),
             1 if data.get('done') else 0, data.get('note') or None, set_id, workout_id))
    return jsonify({'ok': True})


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
        profile = dict(db.execute('SELECT * FROM profiles WHERE user_id=?', (g.user_id,)).fetchone())
        next_id = None
        if w['kind'] == 'gym':
            plan = coach.fallback_gym_plan(db, g.user_id, profile)
            next_id = _create_gym_workout(db, g.user_id, plan, 'fallback')
            db.execute('INSERT INTO generation_jobs (user_id, created_at) VALUES (?, ?)',
                       (g.user_id, datetime.utcnow().isoformat()))
        coach.plan_missing_runs(db, g.user_id, profile)
    return jsonify({'ok': True, 'next_workout_id': next_id})


# --- Logs (avec date retroactive) ---

@app.get('/api/progress')
@require_auth
def progress():
    since = (datetime.utcnow() - timedelta(days=56)).date().isoformat()
    with get_db() as db:
        runs = db.execute(
            "SELECT completed_at, actual_km FROM workouts WHERE user_id=? AND kind='run' "
            "AND status='done' AND completed_at >= ? ORDER BY completed_at",
            (g.user_id, since)).fetchall()
        gym = db.execute(
            "SELECT completed_at FROM workouts WHERE user_id=? AND kind='gym' "
            "AND status='done' AND completed_at >= ? ORDER BY completed_at",
            (g.user_id, since)).fetchall()
    return jsonify({
        'runs': [{'date': r['completed_at'], 'km': r['actual_km']} for r in runs],
        'gym_sessions': [{'date': r['completed_at']} for r in gym],
    })


@app.post('/api/log/<log_type>')
@require_auth
def add_log(log_type):
    if log_type not in ('poids', 'fc'):
        return jsonify({'error': 'invalid type'}), 400
    data = request.json or {}
    value = data.get('value')
    if value is None:
        return jsonify({'error': 'value required'}), 400
    date = data.get('date') or datetime.utcnow().date().isoformat()
    with get_db() as db:
        # 1 valeur par jour par type : remplace si existe
        db.execute('DELETE FROM logs WHERE user_id=? AND type=? AND date=?',
                   (g.user_id, log_type, date))
        db.execute('INSERT INTO logs (user_id, type, value, date) VALUES (?, ?, ?, ?)',
                   (g.user_id, log_type, float(value), date))
    return jsonify({'ok': True})


@app.get('/api/logs')
@require_auth
def get_logs():
    with get_db() as db:
        rows = db.execute(
            'SELECT type, value, date FROM logs WHERE user_id=? '
            'ORDER BY date DESC LIMIT 200', (g.user_id,)).fetchall()
    out = {'poids': [], 'fc': []}
    for r in rows:
        if r['type'] in out:
            out[r['type']].append({'value': r['value'], 'date': r['date']})
    return jsonify(out)


@app.get('/api/exercises/<ex_id>/fr')
@require_auth
def exercise_fr(ex_id):
    """Instructions en francais, traduites par Ollama et cachees en DB."""
    with get_db() as db:
        row = db.execute(
            'SELECT instructions, instructions_fr FROM exercises WHERE id=?',
            (ex_id,)).fetchone()
    if not row:
        return jsonify({'error': 'not_found'}), 404
    if row['instructions_fr']:
        return jsonify({'instructions_fr': row['instructions_fr'], 'cached': True})
    if not row['instructions']:
        return jsonify({'instructions_fr': None, 'fallback': True})
    try:
        fr = coach.translate_instructions(row['instructions'])
    except Exception as exc:
        log.warning("Translation failed for %s: %s", ex_id, exc)
        return jsonify({'instructions_fr': None, 'fallback': True})
    with get_db() as db:
        db.execute('UPDATE exercises SET instructions_fr=? WHERE id=?', (fr, ex_id))
    return jsonify({'instructions_fr': fr, 'cached': False})


# --- Strava (par user) ---

@app.get('/api/strava/auth')
@require_auth
def strava_auth():
    if not STRAVA_CLIENT_ID:
        return jsonify({'error': 'Strava not configured'}), 500
    state = make_token(g.user_id, ttl_days=1)
    url = (f"https://www.strava.com/oauth/authorize?client_id={STRAVA_CLIENT_ID}"
           f"&redirect_uri={STRAVA_REDIRECT_URI}&response_type=code"
           f"&scope=read,activity:read_all&state={state}")
    return jsonify({'url': url})


@app.get('/api/strava/callback')
def strava_callback():
    code = request.args.get('code')
    state = request.args.get('state', '')
    try:
        user_id = jwt.decode(state, SECRET_KEY, algorithms=[JWT_ALGO])['uid']
    except jwt.PyJWTError:
        return "Invalid state", 400
    if not code:
        return "Missing code", 400
    resp = requests.post('https://www.strava.com/oauth/token', data={
        'client_id': STRAVA_CLIENT_ID, 'client_secret': STRAVA_CLIENT_SECRET,
        'code': code, 'grant_type': 'authorization_code'})
    if not resp.ok:
        return "Strava auth failed", 400
    tokens = resp.json()
    expires_at = datetime.utcfromtimestamp(tokens['expires_at']).isoformat()
    with get_db() as db:
        db.execute('INSERT OR REPLACE INTO oauth_tokens VALUES (?, ?, ?, ?, ?, ?)',
                   (user_id, 'strava', tokens['access_token'], tokens['refresh_token'],
                    expires_at, json.dumps(tokens)))
    return redirect('/?strava=connected')


def get_strava_token(user_id):
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM oauth_tokens WHERE user_id=? AND provider='strava'",
            (user_id,)).fetchone()
    if not row:
        return None
    if datetime.utcnow() >= datetime.fromisoformat(row['expires_at']) - timedelta(minutes=5):
        resp = requests.post('https://www.strava.com/oauth/token', data={
            'client_id': STRAVA_CLIENT_ID, 'client_secret': STRAVA_CLIENT_SECRET,
            'refresh_token': row['refresh_token'], 'grant_type': 'refresh_token'})
        if not resp.ok:
            return None
        tokens = resp.json()
        expires_at = datetime.utcfromtimestamp(tokens['expires_at']).isoformat()
        with get_db() as db:
            db.execute('INSERT OR REPLACE INTO oauth_tokens VALUES (?, ?, ?, ?, ?, ?)',
                       (user_id, 'strava', tokens['access_token'], tokens['refresh_token'],
                        expires_at, json.dumps(tokens)))
        return tokens['access_token']
    return row['access_token']


def fetch_strava_week(user_id):
    token = get_strava_token(user_id)
    if not token:
        return None
    today = datetime.utcnow().date()
    monday = today - timedelta(days=today.weekday())
    after = int(datetime(monday.year, monday.month, monday.day).timestamp())
    resp = requests.get('https://www.strava.com/api/v3/athlete/activities',
                        headers={'Authorization': f'Bearer {token}'},
                        params={'after': after, 'per_page': 30})
    if not resp.ok:
        return None
    activities = [{'id': a['id'], 'name': a['name'], 'date': a['start_date_local'],
                   'distance_km': round(a['distance'] / 1000, 2),
                   'duration_s': a['moving_time'], 'avg_hr': a.get('average_heartrate'),
                   'max_hr': a.get('max_heartrate'), 'type': a['sport_type']}
                  for a in resp.json()]
    total_km = round(sum(a['distance_km'] for a in activities if a['type'] == 'Run'), 2)
    return {'activities': activities, 'total_km_week': total_km}


def _auto_validate_runs(user_id, activities):
    """Valide les courses planifiees de la semaine avec les runs Strava (>= 60% du km cible)."""
    validated = 0
    runs = [a for a in activities if a['type'] == 'Run']
    with get_db() as db:
        planned = db.execute(
            "SELECT id, plan_json FROM workouts WHERE user_id=? AND kind='run' "
            "AND status='planned' ORDER BY id",
            (user_id,)).fetchall()
        used = set()
        for w in planned:
            target_km = json.loads(w['plan_json']).get('km', 0)
            for a in runs:
                if a['id'] in used:
                    continue
                if a['distance_km'] >= target_km * 0.6:
                    db.execute(
                        "UPDATE workouts SET status='done', completed_at=?, "
                        "notes=?, actual_km=? WHERE id=?",
                        (a['date'], f"strava:{a['id']}", a['distance_km'], w['id']))
                    used.add(a['id'])
                    validated += 1
                    break
    return validated


@app.get('/api/strava/activities')
@require_auth
def strava_activities():
    data = fetch_strava_week(g.user_id)
    if data is None:
        # 409 et pas 401 : un 401 ferait purger le JWT cote front
        return jsonify({'error': 'not_connected'}), 409
    data['validated_runs'] = _auto_validate_runs(g.user_id, data['activities'])
    return jsonify(data)


@app.get('/api/health')
def health():
    return jsonify({'ok': True, 'ollama': coach.ollama_available()})


# --- Worker queue Ollama ---

def process_generation_jobs():
    if not coach.ollama_available():
        return
    db = get_db()
    try:
        jobs = db.execute(
            "SELECT * FROM generation_jobs WHERE status='pending' ORDER BY id LIMIT 5").fetchall()
        for job in jobs:
            try:
                strava = fetch_strava_week(job['user_id'])
                plan = coach.ollama_generate_gym_plan(
                    db, job['user_id'],
                    strava_activities=strava['activities'] if strava else [],
                    mode=job['mode'] if 'mode' in job.keys() else 'gym')
                target = db.execute(
                    "SELECT id FROM workouts WHERE user_id=? AND status='planned' "
                    "AND kind='gym' ORDER BY id LIMIT 1", (job['user_id'],)).fetchone()
                if target:
                    _replace_plan(db, target['id'], plan, 'ai')
                    db.execute(
                        "UPDATE generation_jobs SET status='done', result_workout_id=? "
                        "WHERE id=?", (target['id'], job['id']))
                else:
                    db.execute("UPDATE generation_jobs SET status='done' WHERE id=?",
                               (job['id'],))
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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
