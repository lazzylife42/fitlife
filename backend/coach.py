"""FitLife coach — generation de seances (Ollama + fallback deterministe)."""

import json
import logging
import os
from datetime import datetime, timedelta

import requests

log = logging.getLogger(__name__)

OLLAMA_URL = os.environ.get('OLLAMA_URL', 'http://192.168.1.14:11434')
OLLAMA_MODEL = os.environ.get('OLLAMA_MODEL', 'mistral:7b')
OLLAMA_TRANSLATE_MODEL = os.environ.get('OLLAMA_TRANSLATE_MODEL', OLLAMA_MODEL)
OLLAMA_TIMEOUT = int(os.environ.get('OLLAMA_TIMEOUT', '180'))

EQUIPMENT_MAP = {
    'machines': ['leverage machine', 'cable', 'smith machine', 'body weight'],
    'machines_dumbbells': ['leverage machine', 'cable', 'smith machine', 'body weight', 'dumbbell'],
    'all': ['leverage machine', 'cable', 'smith machine', 'body weight', 'dumbbell'],
}

# Templates de seance : cycle A/B/C, slots = (category, mot-cle target optionnel)
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


def _allowed_equipment(profile):
    return EQUIPMENT_MAP.get(profile['equipment_pref'], EQUIPMENT_MAP['machines'])


def exercise_pool(db, profile, per_category=10):
    """Pool filtre par equipement, limite par categorie pour le prompt."""
    eq = _allowed_equipment(profile)
    skip = {'neck', 'cardio', 'lower arms'}
    placeholders = ','.join('?' * len(eq))
    rows = db.execute(
        f"SELECT id, name, category, equipment, target FROM exercises "
        f"WHERE equipment IN ({placeholders}) ORDER BY category, id", eq).fetchall()
    pool, counts = [], {}
    for r in rows:
        c = r['category']
        if c in skip:
            continue
        if any(b in r['name'].lower() for b in NAME_BLACKLIST):
            continue
        if counts.get(c, 0) < per_category:
            pool.append(dict(r))
            counts[c] = counts.get(c, 0) + 1
    return pool


def build_context(db, user_id):
    """Agrege tout ce que le modele doit savoir."""
    profile = dict(db.execute('SELECT * FROM profiles WHERE user_id=?', (user_id,)).fetchone())
    workouts = db.execute(
        "SELECT id, kind, status, scheduled_date, completed_at, source, plan_json "
        "FROM workouts WHERE user_id=? AND status='done' "
        "ORDER BY completed_at DESC LIMIT 6", (user_id,)).fetchall()
    history = []
    for w in workouts:
        entry = {'kind': w['kind'], 'date': w['completed_at'],
                 'plan': json.loads(w['plan_json'])}
        sets = db.execute(
            'SELECT exercise_id, target_sets, target_reps, target_weight, '
            'actual_weight, actual_reps, done FROM workout_sets WHERE workout_id=?',
            (w['id'],)).fetchall()
        entry['sets'] = [dict(s) for s in sets]
        history.append(entry)
    logs = db.execute(
        "SELECT type, value, date FROM logs WHERE user_id=? "
        "ORDER BY date DESC LIMIT 30", (user_id,)).fetchall()
    return {'profile': profile, 'history': history, 'logs': [dict(l) for l in logs]}


# --- Fallback deterministe ---

# Priorite equipement (machines guidees d'abord) + exclusion des exos exotiques
EQUIP_PRIORITY = ['leverage machine', 'cable', 'smith machine', 'dumbbell', 'body weight']
NAME_BLACKLIST = ('balance board', 'bosu', 'stability', 'wheel', 'rope', 'suspended',
                  'run', 'walk', 'stepmill', 'elliptical', 'burpee', 'handstand',
                  'muscle up', 'planche push', 'one arm', 'single arm push')


def _equip_rank(e):
    try:
        return EQUIP_PRIORITY.index(e['equipment'])
    except ValueError:
        return len(EQUIP_PRIORITY)


def _pick_exercise(pool, category, target_hint, prefer_id=None):
    candidates = [e for e in pool if e['category'] == category
                  and not any(b in e['name'].lower() for b in NAME_BLACKLIST)]
    if prefer_id:
        for e in candidates:
            if e['id'] == prefer_id:
                return e
    if target_hint:
        hinted = [e for e in candidates if target_hint in (e.get('target') or '')]
        if hinted:
            candidates = hinted
    preferred = ('lever ', 'cable ', 'smith ', 'sled ')
    candidates.sort(key=lambda e: (
        _equip_rank(e),
        0 if e['name'].lower().startswith(preferred) else 1,
        e['name']))
    return candidates[0] if candidates else None


def _last_same_template(db, user_id, title):
    row = db.execute(
        "SELECT w.id FROM workouts w WHERE w.user_id=? AND w.kind='gym' "
        "AND w.status='done' AND json_extract(w.plan_json,'$.title')=? "
        "ORDER BY w.completed_at DESC LIMIT 1", (user_id, title)).fetchone()
    if not row:
        return {}
    sets = db.execute(
        'SELECT exercise_id, target_sets, target_weight, actual_weight, done '
        'FROM workout_sets WHERE workout_id=? ORDER BY position', (row['id'],)).fetchall()
    return {i: dict(s) for i, s in enumerate(sets)}


def fallback_gym_plan(db, user_id, profile):
    """Prochaine seance salle : rotation A/B/C + surcharge progressive +5%."""
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


# Mode deplacement : slots poids du corps avec mots-cles preferes
BODYWEIGHT_SLOTS = [
    ('upper legs', ['squat', 'lunge']),
    ('chest', ['push-up', 'push up']),
    ('back', ['superman', 'bridge', 'pull-up']),
    ('waist', ['plank', 'crunch', 'sit-up']),
    ('cardio', ['jumping jack', 'mountain climber']),
]


def fallback_bodyweight_plan(db, user_id, profile):
    """Seance sans materiel (deplacement / pas de salle)."""
    rows = db.execute(
        "SELECT id, name, category, equipment, target FROM exercises "
        "WHERE equipment='body weight' ORDER BY name").fetchall()
    pool = [dict(r) for r in rows]
    reps = {'recomp': '15', 'strength': '12', 'endurance': '20'}.get(profile['goal'], '15')
    n_sets = SETS_BY_LEVEL.get(profile['level'], 3)

    exercises = []
    for category, keywords in BODYWEIGHT_SLOTS:
        candidates = [e for e in pool if e['category'] == category]
        pick = None
        for kw in keywords:
            matches = [e for e in candidates
                       if kw in e['name'].lower()
                       and not any(b in e['name'].lower() for b in NAME_BLACKLIST)]
            if matches:
                pick = matches[0]
                break
        if not pick and candidates:
            pick = candidates[0]
        if pick:
            exercises.append({'id': pick['id'], 'name': pick['name'],
                              'sets': n_sets, 'reps': reps, 'weight': None})
    return {'title': 'Sans matériel — déplacement', 'mode': 'travel',
            'exercises': exercises,
            'advice': "Séance poids du corps. Tempo lent, amplitude complète pour compenser l'absence de charge."}


def fallback_run_plan(profile, km):
    return {'title': 'Course Zone 2', 'km': round(km, 1),
            'zone': 'Z2 130-140 bpm',
            'advice': 'Priorite FC, ralentir si > 140 bpm.'}


def plan_missing_runs(db, user_id, profile):
    """Cree les seances course manquantes pour la semaine courante."""
    if not profile['run_days'] or not profile['run_km_target']:
        return []
    today = datetime.utcnow().date()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    existing = db.execute(
        "SELECT COUNT(*) c FROM workouts WHERE user_id=? AND kind='run' "
        "AND scheduled_date BETWEEN ? AND ?",
        (user_id, monday.isoformat(), sunday.isoformat())).fetchone()['c']
    created = []
    missing = profile['run_days'] - existing
    km_each = profile['run_km_target'] / profile['run_days']
    for _ in range(max(missing, 0)):
        plan = fallback_run_plan(profile, km_each)
        cur = db.execute(
            "INSERT INTO workouts (user_id, kind, status, scheduled_date, source, plan_json) "
            "VALUES (?, 'run', 'planned', ?, 'fallback', ?)",
            (user_id, sunday.isoformat(), json.dumps(plan)))
        created.append(cur.lastrowid)
    return created


# --- Ollama ---

SYSTEM_PROMPT = (
    "Tu es un coach fitness francophone. Tu recois le profil d'un utilisateur, son historique "
    "de seances (charges cibles vs realisees), ses metriques (poids, FC repos), "
    "ses activites Strava recentes, et un pool d'exercices disponibles.\n"
    "Genere la PROCHAINE seance. Reponds UNIQUEMENT en JSON valide avec cette structure:\n"
    '{"title": "string", "exercises": [{"id": "id du pool", "sets": int, '
    '"reps": "string", "weight": number ou null}], '
    '"run_advice": "string", "advice": "string"}\n'
    "REGLES STRICTES:\n"
    "- title, advice et run_advice REDIGES EN FRANCAIS. title = nom court de seance "
    "(ex: 'Full Body — Pousser + Jambes'), jamais un nom generique.\n"
    "- Exactement 5 exercices, uniquement des id presents dans le pool.\n"
    "- VARIETE OBLIGATOIRE: couvrir au moins 4 groupes differents parmi jambes (upper legs), "
    "pectoraux (chest), dos (back), epaules (shoulders), bras (upper arms), abdos (waist). "
    "JAMAIS plus de 2 exercices du meme groupe.\n"
    "- reps = format simple: '12' ou '8-10' ou '30-45s'.\n"
    "- Surcharge progressive ~5% sur les exos completes la fois precedente, "
    "sinon reprendre la meme charge.\n"
    "- Privilegie les machines guidees (leverage machine) et cables, "
    "evite les exercices exotiques ou acrobatiques.\n"
    "- advice = 2-3 phrases d'analyse personnalisee basee sur l'historique et les metriques.\n"
    "- Si la FC de repos monte ou fatigue visible dans Strava, reduis le volume."
)


def translate_instructions(text):
    """Traduit les instructions d'un exo en francais. Raise si Ollama down."""
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


def ollama_available():
    try:
        r = requests.get(f'{OLLAMA_URL}/api/tags', timeout=3)
        return r.ok
    except requests.RequestException:
        return False


PLAN_SCHEMA = {
    'type': 'object',
    'properties': {
        'title': {'type': 'string'},
        'exercises': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'id': {'type': 'string'},
                    'sets': {'type': 'integer'},
                    'reps': {'type': 'string'},
                    'weight': {'type': ['number', 'null']},
                },
                'required': ['id', 'sets', 'reps'],
            },
        },
        'run_advice': {'type': 'string'},
        'advice': {'type': 'string'},
    },
    'required': ['title', 'exercises', 'advice'],
}


def ollama_generate_gym_plan(db, user_id, strava_activities=None, mode='gym'):
    """Appelle Ollama. Raise en cas d'echec (le worker gere le retry)."""
    profile = dict(db.execute('SELECT * FROM profiles WHERE user_id=?', (user_id,)).fetchone())
    context = build_context(db, user_id)
    context['strava'] = strava_activities or []
    if mode == 'travel':
        rows = db.execute(
            "SELECT id, name, category, equipment, target FROM exercises "
            "WHERE equipment='body weight' ORDER BY category, id").fetchall()
        pool, counts = [], {}
        for r in rows:
            if counts.get(r['category'], 0) < 10:
                pool.append(dict(r))
                counts[r['category']] = counts.get(r['category'], 0) + 1
        constraint = ("CONTRAINTE: utilisateur en deplacement SANS materiel, "
                      "uniquement des exercices au poids du corps du pool.\n")
    else:
        pool = exercise_pool(db, profile)
        constraint = ''

    pool_lines = '\n'.join(
        f"{e['id']} | {e['name']} | {e['category']} | {e['equipment']}" for e in pool)
    user_msg = (
        f"PROFIL:\n{json.dumps(context['profile'], ensure_ascii=False)}\n\n"
        f"HISTORIQUE SEANCES (recentes d'abord, cible vs realise):\n"
        f"{json.dumps(context['history'][:4], ensure_ascii=False)}\n\n"
        f"METRIQUES (poids, fc repos):\n{json.dumps(context['logs'][:14], ensure_ascii=False)}\n\n"
        f"ACTIVITES STRAVA CETTE SEMAINE:\n{json.dumps(context['strava'], ensure_ascii=False)}\n\n"
        f"POOL D'EXERCICES (id | nom | groupe | equipement):\n{pool_lines}\n\n"
        f"{constraint}"
        "TACHE: genere la prochaine seance en JSON. Choisis EXACTEMENT 5 exercices du POOL "
        "ci-dessus par leur id, couvrant au moins 4 groupes musculaires differents. "
        "Applique la surcharge progressive sur les exos deja realises. "
        "title et advice en francais."
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
    if not resp.ok:
        raise ValueError(f'Ollama HTTP {resp.status_code}: {resp.text[:200]}')

    content = resp.json().get('message', {}).get('content', '')
    try:
        plan = json.loads(content)
    except json.JSONDecodeError:
        log.warning("Ollama raw response (invalid JSON): %s", content[:500])
        raise ValueError('Reponse non-JSON')

    # Validation stricte
    if not isinstance(plan.get('exercises'), list) or not plan['exercises']:
        log.warning("Ollama raw response (bad structure): %s", content[:500])
        raise ValueError('Plan invalide: exercises manquant')
    valid_ids = {r['id'] for r in db.execute('SELECT id FROM exercises').fetchall()}
    meta = {r['id']: r for r in db.execute('SELECT id, name, category FROM exercises').fetchall()}
    cleaned = []
    for ex in plan['exercises'][:6]:
        ex_id = str(ex.get('id', '')).strip().zfill(4)  # qwen strip parfois les zeros de tete
        if ex_id not in valid_ids:
            continue
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
    # Diversite : au moins 3 groupes musculaires differents (sauf mode travel, pool restreint)
    categories = {meta[e['id']]['category'] for e in cleaned}
    if mode != 'travel' and len(categories) < 3:
        log.warning("Ollama plan pas assez varie (%s): %s", categories, content[:300])
        raise ValueError(f'Plan invalide: pas assez varie ({len(categories)} groupes)')
    if plan.get('title', '').lower().strip() in ('exercise database', 'seance', 'workout', ''):
        plan['title'] = 'Séance du coach'
    plan['exercises'] = cleaned
    plan.setdefault('title', 'Seance salle')
    if mode == 'travel':
        plan['mode'] = 'travel'
    return plan
