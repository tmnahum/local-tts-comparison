import hashlib
import json
import re
import wave
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .catalog import ROOT, MODELS
from .pronunciation import apply_rules
from .engine import engine, rendition_info, queue_rank, estimate_queue
from .fetcher import fetch_article
from .storage import DATA, database, initialize, article
from .text import paragraphs_from_text, chunk_paragraphs


@asynccontextmanager
async def lifespan(app):
    initialize()
    engine.start()
    yield
    engine.close()


app = FastAPI(title='Readroom', lifespan=lifespan)


@app.middleware('http')
async def local_only_mutations(request: Request, call_next):
    # This is a local app: reject cross-origin writes from arbitrary websites.
    origin = request.headers.get('origin')
    if request.method not in ('GET', 'HEAD', 'OPTIONS') and origin and urlsplit(origin).netloc != request.headers.get('host'):
        return JSONResponse({'detail': 'Cross-origin requests are not allowed.'}, status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


class ArticleInput(BaseModel):
    url: str = Field(default='', max_length=2048)
    title: str = Field(default='', max_length=300)
    text: str = Field(default='', max_length=250_000)


class RenditionInput(BaseModel):
    model: str
    voice: str = ''
    style: str = Field(default='', max_length=400)
    audition: bool = False


class FeedbackInput(RenditionInput):
    rating: int = Field(default=0, ge=-1, le=1)
    notes: str = Field(default='', max_length=1000)


class PronunciationRule(BaseModel):
    word: str = Field(min_length=1, max_length=100)
    replacement: str = Field(min_length=1, max_length=200)


class PronunciationsInput(BaseModel):
    rules: list[PronunciationRule] = Field(max_length=100)


@app.get('/api/pronunciations')
def pronunciations():
    with database() as db:
        row = db.execute("SELECT value FROM preferences WHERE key='pronunciations'").fetchone()
    return json.loads(row['value']) if row else []


@app.post('/api/pronunciations')
def set_pronunciations(body: PronunciationsInput):
    rules = [r.model_dump() for r in body.rules]
    if any(not r['word'].strip() or not r['replacement'].strip() for r in rules) or len({r['word'] for r in rules}) != len(rules):
        raise HTTPException(422, 'Use nonempty, unique words for pronunciation overrides.')
    with database() as db:
        db.execute("INSERT OR REPLACE INTO preferences VALUES ('pronunciations', ?)", (json.dumps(rules),))
    return rules


@app.post('/api/voices/feedback')
def voice_feedback(body: FeedbackInput):
    saved = save_voice(body)
    with database() as db:
        db.execute('UPDATE voice_selections SET rating=?,notes=? WHERE id=?', (body.rating, body.notes, saved['id']))
    return {'id': saved['id'], 'rating': body.rating, 'notes': body.notes}


class StarInput(BaseModel):
    starred: bool


def validate_selection(body):
    spec = MODELS.get(body.model)
    if not spec:
        raise HTTPException(422, 'Unknown model.')
    voice = body.voice or spec['default_voice']
    if voice not in [v['id'] for v in spec['voices']]:
        raise HTTPException(422, 'Unknown voice for this model.')
    style = body.style.strip() if spec['styles'] else ''
    if spec.get('style_options') and style not in spec['style_options']:
        raise HTTPException(422, 'Unknown performance cue for this model.')
    if spec.get('emotions'):
        style = style or 'neutral'
        if style not in spec['emotions']:
            raise HTTPException(422, 'Unknown delivery for this model.')
    return voice, style


@app.get('/api/voices')
def saved_voices():
    with database() as db:
        return [dict(r) for r in db.execute('SELECT * FROM voice_selections ORDER BY starred DESC, created, id')]


@app.post('/api/voices')
def save_voice(body: RenditionInput):
    voice, style = validate_selection(body)
    key = hashlib.sha256(json.dumps([body.model, voice, style]).encode()).hexdigest()[:24]
    with database() as db:
        db.execute('INSERT OR IGNORE INTO voice_selections (id,model,voice,style,created) VALUES (?,?,?,?,?)', (key, body.model, voice, style, datetime.now(timezone.utc).isoformat()))
        return dict(db.execute('SELECT * FROM voice_selections WHERE id=?', (key,)).fetchone())


@app.post('/api/voices/{key}/star')
def star_voice(key: str, body: StarInput):
    with database() as db:
        if not db.execute('UPDATE voice_selections SET starred=? WHERE id=?', (int(body.starred), key)).rowcount:
            raise HTTPException(404, 'Saved voice not found.')
    return {'starred': body.starred}


@app.delete('/api/voices/{key}')
def remove_voice(key: str):
    with database() as db:
        if not db.execute('DELETE FROM voice_selections WHERE id=?', (key,)).rowcount:
            raise HTTPException(404, 'Saved voice not found.')
    return {'deleted': True}


def require_article(article_id):
    doc = article(article_id)
    if not doc:
        raise HTTPException(404, 'Article not found.')
    return doc


@app.get('/api/status')
def status():
    models = []
    for key, spec in MODELS.items():
        ready_file = ROOT / 'models' / key / '.ready.json'
        models.append({'id': key, **{k: v for k, v in spec.items() if k != 'environment'}, 'downloaded': ready_file.exists() and all((ROOT / 'models' / dep / '.ready.json').exists() for dep in spec.get('requires_models', [])), 'installed': (ROOT / spec['environment'] / 'bin/python').exists()})
    return {'models': models, 'engine': {'model': engine.loaded_model, 'phase': engine.phase, 'rendition': engine.current, 'load_seconds': engine.load_seconds}}


@app.get('/api/articles')
def articles():
    with database() as db:
        rows = db.execute('SELECT id,title,url,byline,paragraphs,created FROM articles ORDER BY created DESC').fetchall()
    return [dict(r) | {'paragraphs': len(json.loads(r['paragraphs'])), 'words': len(' '.join(json.loads(r['paragraphs'])).split())} for r in rows]


@app.get('/api/queue')
def render_queue():
    with database() as db:
        rows = db.execute("SELECT r.*, a.title FROM renditions r JOIN articles a ON a.id=r.article_id WHERE r.status IN ('queued','loading','generating') ORDER BY r.queued_at,r.rowid").fetchall()
    jobs = [rendition_info(row) for row in sorted(rows, key=queue_rank)]
    active = next((r for r in jobs if r['id'] == engine.current), None)
    return {'active': active, 'queued': [r for r in jobs if r is not active], **estimate_queue(jobs)}


@app.post('/api/queue/cancel')
def cancel_queue():
    return {'cancelled': engine.cancel_all()}


@app.post('/api/renditions/{rid}/cancel')
def cancel_rendition(rid: str):
    return {'cancelled': engine.cancel(rid)}


@app.post('/api/articles')
async def create_article(body: ArticleInput):
    if body.url.strip():
        try:
            doc = await fetch_article(body.url.strip())
        except (ValueError, httpx.HTTPError, OSError) as exc:
            raise HTTPException(422, f'Could not fetch this article: {exc}') from exc
    else:
        paragraphs = paragraphs_from_text(body.text)
        if not paragraphs:
            raise HTTPException(422, 'Paste some text or enter an article URL.')
        doc = {'title': body.title.strip() or 'Untitled reading', 'byline': '', 'url': '', 'paragraphs': paragraphs}
    if sum(map(len, doc['paragraphs'])) > 250_000:
        raise HTTPException(422, 'This article is too long (250,000 character limit).')
    doc['chunks'] = chunk_paragraphs(doc['paragraphs'])
    doc['id'] = hashlib.sha256(json.dumps([doc['title'], doc['url'], doc['paragraphs']]).encode()).hexdigest()[:20]
    doc['created'] = datetime.now(timezone.utc).isoformat()
    with database() as db:
        db.execute('INSERT OR IGNORE INTO articles VALUES (?,?,?,?,?,?,?)', (doc['id'], doc['title'], doc['url'], doc['byline'], json.dumps(doc['paragraphs']), json.dumps(doc['chunks']), doc['created']))
    saved = article(doc['id'])
    (DATA / 'articles' / (doc['id'] + '.json')).write_text(json.dumps(saved, indent=2, ensure_ascii=False))
    return saved


@app.get('/api/articles/{article_id}')
def get_article(article_id: str):
    doc = require_article(article_id)
    with database() as db:
        rows = db.execute('SELECT * FROM renditions WHERE article_id=? ORDER BY created', (article_id,)).fetchall()
    return doc | {'renditions': [rendition_info(row) for row in rows]}


@app.get('/api/articles/{article_id}/text')
def article_text(article_id: str):
    doc = require_article(article_id)
    return JSONResponse(doc, headers={'Content-Disposition': 'attachment; filename="article.json"'})


@app.post('/api/articles/{article_id}/renditions')
def create_rendition(article_id: str, body: RenditionInput):
    doc = require_article(article_id)
    voice, style = validate_selection(body)
    if not (ROOT / 'models' / body.model / '.ready.json').exists() or any(not (ROOT / 'models' / dep / '.ready.json').exists() for dep in MODELS[body.model].get('requires_models', [])):
        raise HTTPException(409, 'This model is still downloading.')
    rules = pronunciations()
    identity = [article_id, body.model, voice, style, 'three-paragraphs-v1']
    if rules:
        identity.append(rules)
    if body.audition:
        identity.append('audition')
    rid = hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24]
    chunks = chunk_paragraphs(doc['paragraphs'])
    if body.audition:
        chunks = chunks[:1]
    for chunk in chunks:
        spoken, edits = apply_rules(chunk['text'], rules)
        if edits:
            chunk['source_text'], chunk['text'], chunk['pronunciation_edits'] = chunk['text'], spoken, edits
    with engine.lock, database() as db:
        db.execute('BEGIN IMMEDIATE')
        existing = db.execute('SELECT * FROM renditions WHERE id=?', (rid,)).fetchone()
        saved = rendition_info(existing) if existing else None
        enqueue = not existing or (existing['status'] in ('error', 'paused') and (not existing['timing_requested'] or saved['ready'] < saved['total'])) or (existing['status'] == 'complete' and body.model == 'kokoro' and not saved['has_word_timings'])
        if not existing:
            now = datetime.now(timezone.utc).isoformat()
            db.execute('INSERT INTO renditions (id,article_id,model,voice,style,status,error,created,chunks,queued_at,audition,pronunciations) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', (rid, article_id, body.model, voice, style, 'queued', None, now, json.dumps(chunks), now, int(body.audition), json.dumps(rules)))
        elif enqueue:
            db.execute("UPDATE renditions SET status='queued', error=NULL,timing_requested=0,queued_at=? WHERE id=?", (datetime.now(timezone.utc).isoformat(), rid))
        db.commit()
        if enqueue:
            engine.enqueue(rid)
    return {'id': rid}


@app.post('/api/renditions/{rid}/timings')
def generate_timings(rid: str):
    with engine.lock, database() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM renditions WHERE id=?', (rid,)).fetchone()
        if not row:
            raise HTTPException(404, 'Reading not found.')
        if row['model'] == 'kokoro':
            raise HTTPException(409, 'Kokoro produces native word timings when rendering.')
        reading = rendition_info(row)
        if reading['ready'] != reading['total']:
            raise HTTPException(409, 'Finish rendering the article before generating word timings.')
        if reading['has_word_timings']:
            return {'id': rid, 'status': 'ready'}
        if row['status'] in ('queued', 'loading', 'generating'):
            if row['timing_requested']:
                return {'id': rid, 'status': 'queued'}
            raise HTTPException(409, 'Wait for this reading to finish rendering.')
        if not (ROOT / 'models/aligner/.ready.json').exists() or not (ROOT / '.venv-light/bin/python').exists():
            raise HTTPException(409, 'The word aligner is not installed.')
        db.execute("UPDATE renditions SET timing_requested=1,status='queued',error=NULL,queued_at=? WHERE id=?", (datetime.now(timezone.utc).isoformat(), rid))
        db.commit()
        engine.enqueue(rid)
    return {'id': rid, 'status': 'queued'}


@app.post('/api/renditions/{rid}/pause')
def pause_rendition(rid: str):
    return {'paused': engine.cancel(rid)}


@app.get('/api/audio/{filename}')
def audio(filename: str):
    if not re.fullmatch(r'[a-f0-9]{64}\.wav', filename):
        raise HTTPException(404)
    path = DATA / 'audio' / filename
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type='audio/wav', headers={'Cache-Control': 'private, max-age=31536000, immutable'})


@app.get('/api/renditions/{rid}/download')
def download_rendition(rid: str):
    with database() as db:
        row = db.execute('SELECT * FROM renditions WHERE id=?', (rid,)).fetchone()
    if not row:
        raise HTTPException(404)
    reading = rendition_info(row)
    if reading['ready'] != reading['total']:
        raise HTTPException(409, 'The full reading is not ready yet.')
    doc = require_article(row['article_id'])
    path = DATA / 'audio' / (rid + '-full.wav')
    if not path.exists():
        # Atomic rename makes simultaneous downloads safe.
        import tempfile
        with tempfile.NamedTemporaryFile(dir=DATA / 'audio', suffix='.wav', delete=False) as tmp:
            temporary = tmp.name
        try:
            with wave.open(temporary, 'wb') as out:
                for index, clip in enumerate(reading['clips']):
                    with wave.open(str(DATA / 'audio' / (clip['key'] + '.wav')), 'rb') as part:
                        if index == 0:
                            out.setparams(part.getparams())
                        out.writeframes(part.readframes(part.getnframes()))
                        chunk = reading['chunks'][index]
                        pause_ms = chunk.get('pause_ms', 250 if chunk['paragraph_end'] else 0)
                        if index < len(reading['clips']) - 1 and pause_ms:
                            out.writeframes(b'\x00' * int(part.getframerate() * pause_ms / 1000) * part.getsampwidth() * part.getnchannels())
            from pathlib import Path
            Path(temporary).replace(path)
        finally:
            from pathlib import Path
            Path(temporary).unlink(missing_ok=True)
    return FileResponse(path, media_type='audio/wav', filename=f"{row['model']}-{row['voice']}-{doc['id']}.wav")


app.mount('/', StaticFiles(directory=ROOT / 'static', html=True), name='static')
