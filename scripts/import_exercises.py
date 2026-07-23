"""Import du dataset exercises vers la DB FitLife + copie des medias.

Usage (dans le container backend):
    python3 import_exercises.py

Attend:
    /dataset          -> clone de hasaneyldrm/exercises-dataset (bind mount ro)
    /media-out        -> dossier servi par nginx sous /media/ (bind mount rw)
    DB_PATH           -> env, defaut /data/fitlife.db
"""

import json
import logging
import os
import shutil
import sqlite3
import sys

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

DATASET_DIR = os.environ.get('DATASET_DIR', '/dataset')
MEDIA_OUT = os.environ.get('MEDIA_OUT', '/media-out')
DB_PATH = os.environ.get('DB_PATH', '/data/fitlife.db')

# Equipement dispo NonStop Gym (Malley — MATRIX)
ALLOWED_EQUIPMENT = {
    'leverage machine',
    'cable',
    'dumbbell',
    'smith machine',
    'body weight',
    'barbell',
    'kettlebell',
}


def main():
    src_json = os.path.join(DATASET_DIR, 'data', 'exercises.json')
    if not os.path.isfile(src_json):
        raise ValueError(f"Dataset introuvable: {src_json}")
    if not os.path.isdir(MEDIA_OUT):
        raise ValueError(f"MEDIA_OUT introuvable: {MEDIA_OUT}")

    with open(src_json, encoding='utf-8') as f:
        exercises = json.load(f)
    log.info("Dataset charge: %d exercices", len(exercises))

    kept = [e for e in exercises if e.get('equipment', '').lower() in ALLOWED_EQUIPMENT]
    log.info("Apres filtre equipement: %d exercices", len(kept))

    os.makedirs(os.path.join(MEDIA_OUT, 'images'), exist_ok=True)
    os.makedirs(os.path.join(MEDIA_OUT, 'videos'), exist_ok=True)

    db = sqlite3.connect(DB_PATH)
    inserted, media_missing = 0, 0
    for e in kept:
        img_rel = e.get('image', '')       # "images/0001-xxx.jpg"
        gif_rel = e.get('gif_url', '')     # "videos/0001-xxx.gif"
        img_src = os.path.join(DATASET_DIR, img_rel)
        gif_src = os.path.join(DATASET_DIR, gif_rel)

        img_url, gif_url = None, None
        if os.path.isfile(img_src):
            shutil.copy2(img_src, os.path.join(MEDIA_OUT, img_rel))
            img_url = f"/media/{img_rel}"
        else:
            media_missing += 1
        if os.path.isfile(gif_src):
            shutil.copy2(gif_src, os.path.join(MEDIA_OUT, gif_rel))
            gif_url = f"/media/{gif_rel}"

        db.execute(
            """INSERT INTO exercises
               (id, name, category, equipment, target, muscle_group,
                secondary_muscles, instructions, image, gif)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 name=excluded.name, category=excluded.category,
                 equipment=excluded.equipment, target=excluded.target,
                 muscle_group=excluded.muscle_group,
                 secondary_muscles=excluded.secondary_muscles,
                 instructions=excluded.instructions,
                 image=excluded.image, gif=excluded.gif""",
            # note: instructions_fr volontairement absent -> traductions conservees
            (
                e['id'],
                e['name'],
                e.get('category', ''),
                e.get('equipment', '').lower(),
                e.get('target'),
                e.get('muscle_group'),
                json.dumps(e.get('secondary_muscles', [])),
                (e.get('instructions') or {}).get('en'),
                img_url,
                gif_url,
            ),
        )
        inserted += 1

    db.commit()
    db.close()
    log.info("Import termine: %d inseres, %d medias manquants", inserted, media_missing)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        log.error("Import failed: %s", exc)
        sys.exit(1)
