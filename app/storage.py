import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .catalog import ROOT

DATA = Path(os.environ.get('READROOM_DATA', ROOT / 'data'))
for folder in ('articles', 'audio', 'logs'):
    (DATA / folder).mkdir(parents=True, exist_ok=True)


@contextmanager
def database():
    connection = sqlite3.connect(DATA / 'readroom.sqlite3', timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def initialize():
    with database() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS articles (id TEXT PRIMARY KEY, title TEXT NOT NULL, url TEXT, byline TEXT, paragraphs TEXT NOT NULL, chunks TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS renditions (id TEXT PRIMARY KEY, article_id TEXT NOT NULL, model TEXT NOT NULL, voice TEXT NOT NULL, style TEXT NOT NULL, status TEXT NOT NULL, error TEXT, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS clips (key TEXT PRIMARY KEY, duration REAL, generation_seconds REAL, peak_vram_gb REAL, sample_rate INTEGER);
        CREATE TABLE IF NOT EXISTS voice_selections (id TEXT PRIMARY KEY, model TEXT NOT NULL, voice TEXT NOT NULL, style TEXT NOT NULL, starred INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL);
        ''')
        columns = {r[1] for r in db.execute('PRAGMA table_info(renditions)')}
        for name, kind in [('chunks', 'TEXT'), ('queued_at', 'TEXT'), ('timing_requested', 'INTEGER NOT NULL DEFAULT 0'), ('audition', 'INTEGER NOT NULL DEFAULT 0'), ('pronunciations', "TEXT NOT NULL DEFAULT '[]'")]:
            if name not in columns:
                db.execute(f'ALTER TABLE renditions ADD COLUMN {name} {kind}')
        db.execute("CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        voice_columns = {r[1] for r in db.execute('PRAGMA table_info(voice_selections)')}
        for name, kind in [('rating', 'INTEGER NOT NULL DEFAULT 0'), ('notes', "TEXT NOT NULL DEFAULT ''")]:
            if name not in voice_columns:
                db.execute(f'ALTER TABLE voice_selections ADD COLUMN {name} {kind}')
        db.execute('UPDATE renditions SET queued_at=created WHERE queued_at IS NULL')
        db.execute("UPDATE renditions SET status='queued' WHERE status IN ('loading','generating')")


def article(article_id):
    with database() as db:
        row = db.execute('SELECT * FROM articles WHERE id=?', (article_id,)).fetchone()
    if row is None:
        return None
    result = dict(row)
    result['paragraphs'] = json.loads(result['paragraphs'])
    result['chunks'] = json.loads(result['chunks'])
    return result
