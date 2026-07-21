-- FitLife v3 schema (multi-user, reset from scratch)

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
    run_km_target REAL NOT NULL DEFAULT 0,   -- km/semaine cible
    run_days INTEGER NOT NULL DEFAULT 0,     -- jours de course/semaine
    updated_at TEXT NOT NULL
);

-- Pool d'exos importe du dataset (partage entre users)
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
    plan_json TEXT NOT NULL,         -- plan complet genere (exos ou run: km/zone)
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_workouts_user ON workouts(user_id, status);

-- Realise par exo (charges saisies pendant la seance)
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

CREATE TABLE IF NOT EXISTS oauth_tokens (
    user_id INTEGER NOT NULL REFERENCES users(id),
    provider TEXT NOT NULL,          -- strava
    access_token TEXT,
    refresh_token TEXT,
    expires_at TEXT,
    raw TEXT,
    PRIMARY KEY (user_id, provider)
);

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
