"""Pre-traduit toutes les instructions d'exercices en francais via Ollama.

Usage (dans le container backend, Ollama up):
    python3 translate_all.py

Resumable : skip les exos deja traduits. Interruptible sans perte (commit par exo).
"""

import logging
import sqlite3
import sys
import time

sys.path.insert(0, '/app')
import coach

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

DB_PATH = '/data/fitlife.db'


def main():
    if not coach.ollama_available():
        raise ValueError("Ollama injoignable, abandonne")

    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    todo = db.execute(
        "SELECT id, name, instructions FROM exercises "
        "WHERE instructions IS NOT NULL AND instructions_fr IS NULL "
        "ORDER BY id").fetchall()
    log.info("A traduire: %d exercices", len(todo))

    ok, failed = 0, 0
    start = time.time()
    for i, row in enumerate(todo, 1):
        try:
            fr = coach.translate_instructions(row['instructions'])
            db.execute('UPDATE exercises SET instructions_fr=? WHERE id=?', (fr, row['id']))
            db.commit()
            ok += 1
        except Exception as exc:
            failed += 1
            log.warning("Echec %s (%s): %s", row['id'], row['name'], exc)
        if i % 25 == 0:
            elapsed = time.time() - start
            rate = i / elapsed
            eta_min = (len(todo) - i) / rate / 60
            log.info("%d/%d — %.1f/s — ETA %.0f min", i, len(todo), rate, eta_min)

    db.close()
    log.info("Termine: %d traduits, %d echecs", ok, failed)


if __name__ == '__main__':
    main()
