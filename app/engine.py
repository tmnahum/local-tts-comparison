import hashlib
import json
import os
import queue
import selectors
import subprocess
import threading
import time
from itertools import count

from .catalog import ROOT, MODELS
from .storage import DATA, database, article
from .timing import chunk_offsets


def clip_key(model, voice, style, text):
    revision = (ROOT / 'models' / model / '.ready.json').read_text() if (ROOT / 'models' / model / '.ready.json').exists() else ''
    return hashlib.sha256(json.dumps([2, model, voice, style, text, revision], ensure_ascii=False).encode()).hexdigest()


def word_timings(key):
    path = DATA / 'audio' / (key + '.words.json')
    return json.loads(path.read_text()) if path.exists() else None


def clip_info(key):
    with database() as db:
        row = db.execute('SELECT * FROM clips WHERE key=?', (key,)).fetchone()
    if row and (DATA / 'audio' / (key + '.wav')).exists():
        return dict(row) | {'url': '/api/audio/' + key + '.wav'}
    return None


def rendition_info(row):
    result = dict(row)
    doc = article(result['article_id'])
    result['grouped'] = bool(result.get('chunks'))
    result['chunks'] = json.loads(result['chunks']) if result.get('chunks') else doc['chunks']
    keys = [clip_key(result['model'], result['voice'], result['style'], chunk['text']) for chunk in result['chunks']]
    cached = {}
    with database() as db:
        for offset in range(0, len(keys), 500):
            batch = keys[offset:offset + 500]
            placeholders = ','.join('?' for _ in batch)
            for clip in db.execute(f'SELECT * FROM clips WHERE key IN ({placeholders})', batch):
                if (DATA / 'audio' / (clip['key'] + '.wav')).exists():
                    cached[clip['key']] = dict(clip) | {'url': '/api/audio/' + clip['key'] + '.wav'}
    result['clips'] = [cached.get(key) for key in keys]
    for clip in result['clips']:
        if clip:
            clip['words'] = word_timings(clip['key'])
            # Only Kokoro timing upgrades regenerate the waveform. Alignment adds
            # metadata to unchanged audio, so keep its URL stable during playback.
            if clip['words'] is not None and result['model'] == 'kokoro':
                clip['url'] += '?timing=1'
    result['chunk_offsets'] = chunk_offsets(doc['paragraphs'], result['chunks'])
    result['has_word_timings'] = bool(result['clips']) and all(c and c['words'] is not None for c in result['clips'])
    result['ready'] = sum(c is not None for c in result['clips'])
    result['total'] = len(result['clips'])
    result['audio_seconds'] = sum(c['duration'] for c in result['clips'] if c)
    result['generation_seconds'] = sum(c['generation_seconds'] for c in result['clips'] if c)
    result['peak_vram_gb'] = max([c['peak_vram_gb'] for c in result['clips'] if c] or [0])
    return result


class Cancelled(Exception):
    pass


def queue_rank(row):
    """First audio sections precede continuations; preserve arrival order within each lane."""
    chunks = json.loads(row['chunks']) if row['chunks'] else article(row['article_id'])['chunks']
    first = clip_key(row['model'], row['voice'], row['style'], chunks[0]['text']) if chunks else None
    priority = 0 if first and not clip_info(first) and not row['timing_requested'] else 1
    return priority, row['queued_at'] or row['created']


class Engine:
    def __init__(self):
        self.queue = queue.PriorityQueue()
        self.sequence = count()
        self.lock = threading.RLock()
        self.tickets = {}
        self.process = None
        self.aligner = None
        self.aligner_log = None
        self.log = None
        self.loaded_model = None
        self.current = None
        self.phase = 'idle'
        self.load_seconds = 0
        self.stopping = False
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        with database() as db:
            pending = db.execute("SELECT id FROM renditions WHERE status='queued' ORDER BY queued_at, rowid").fetchall()
        for row in pending:
            self.enqueue(row['id'])
        self.thread.start()

    def enqueue(self, rendition_id):
        with self.lock:
            with database() as db:
                row = db.execute('SELECT * FROM renditions WHERE id=?', (rendition_id,)).fetchone()
            if not row:
                return
            ticket = threading.Event()
            self.tickets[rendition_id] = ticket
            self.queue.put((*queue_rank(row), next(self.sequence), rendition_id, ticket))

    def cancel(self, rid):
        with self.lock:
            with database() as db:
                changed = db.execute("UPDATE renditions SET status='paused', error=NULL WHERE id=? AND status IN ('queued','loading','generating')", (rid,)).rowcount
            if changed:
                ticket = self.tickets.get(rid)
                if ticket:
                    ticket.set()
                if self.current == rid and self.process and self.process.poll() is None:
                    self.process.kill()  # Wake the reader immediately; only the worker thread closes pipes.
                if self.current == rid and self.aligner and self.aligner.poll() is None:
                    self.aligner.kill()
            return bool(changed)

    def cancel_all(self):
        with self.lock:
            with database() as db:
                rows = db.execute("SELECT id FROM renditions WHERE status IN ('queued','loading','generating')").fetchall()
            return sum(self.cancel(row['id']) for row in rows)

    def unload(self):
        self.unload_aligner()
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            for pipe in (self.process.stdin, self.process.stdout):
                pipe.close()
            self.process = None
        if self.log:
            self.log.close()
            self.log = None
        self.loaded_model = None

    def close(self):
        # Preserve queued/running statuses for durable recovery on restart.
        with self.lock:
            self.stopping = True
            for ticket in self.tickets.values():
                ticket.set()
            if self.process and self.process.poll() is None:
                self.process.kill()
            if self.aligner and self.aligner.poll() is None:
                self.aligner.kill()
            self.queue.put((-1, '', -1, None, None))
        self.thread.join(timeout=10)
        self.unload()

    def check(self, ticket):
        if self.stopping or ticket.is_set():
            raise Cancelled()

    def receive(self, timeout=600, process=None):
        process = process or self.process
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout):
                raise RuntimeError('Model timed out. Retry this reading; details are in data/logs/.')
            line = process.stdout.readline()
        if not line:
            raise RuntimeError('Model process exited. See data/logs/' + str(self.loaded_model) + '.log.')
        return json.loads(line)

    def unload_aligner(self):
        if self.aligner:
            if self.aligner.poll() is None:
                self.aligner.kill()
            self.aligner.wait(timeout=5)
            self.aligner.stdin.close()
            self.aligner.stdout.close()
            self.aligner = None
        if self.aligner_log:
            self.aligner_log.close()
            self.aligner_log = None

    def align_clip(self, text, key, ticket):
        with self.lock:
            self.check(ticket)
            fresh = not self.aligner or self.aligner.poll() is not None
            if fresh:
                self.unload_aligner()
                self.aligner_log = open(DATA / 'logs/aligner.log', 'a')
                self.aligner = subprocess.Popen([str(ROOT / '.venv-light/bin/python'), str(ROOT / 'app/worker.py'), 'aligner'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.aligner_log, text=True, bufsize=1, cwd=ROOT)
        if fresh:
            message = self.receive(process=self.aligner)
            self.check(ticket)
            if not message.get('ready'):
                raise RuntimeError(message.get('error', 'Word aligner failed to load.'))
        with self.lock, database() as db:
            self.check(ticket)
            self.phase = 'aligning'
            db.execute("UPDATE renditions SET status='generating' WHERE id=?", (self.current,))
            self.aligner.stdin.write(json.dumps({'text': text, 'audio': str(DATA / 'audio' / (key + '.wav'))}) + '\n')
            self.aligner.stdin.flush()
        result = self.receive(process=self.aligner)
        with self.lock:
            self.check(ticket)
            if not result.get('ok'):
                raise RuntimeError(result.get('error', 'Word alignment failed.'))
            metadata = DATA / 'audio' / (key + '.words.partial.json')
            metadata.write_text(json.dumps(result['words']))
            metadata.replace(DATA / 'audio' / (key + '.words.json'))

    def load(self, model, ticket):
        with self.lock:
            self.check(ticket)
            if self.loaded_model == model and self.process and self.process.poll() is None:
                return
            self.unload()
            self.phase = 'loading'
            env = os.environ.copy()
            env['PYTHONUNBUFFERED'] = '1'
            self.log = open(DATA / 'logs' / (model + '.log'), 'a')
            self.process = subprocess.Popen([str(ROOT / MODELS[model]['environment'] / 'bin/python'), str(ROOT / 'app/worker.py'), model], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log, text=True, bufsize=1, cwd=ROOT, env=env)
            self.loaded_model = model
        message = self.receive()
        self.check(ticket)
        if not message.get('ready'):
            raise RuntimeError(message.get('error', 'Model failed to load.'))
        self.load_seconds = message['load_seconds']

    def run(self):
        while not self.stopping:
            try:
                job = self.queue.get(timeout=120)
            except queue.Empty:
                with self.lock:
                    self.unload()
                continue
            _, _, _, rid, ticket = job
            if rid is None:
                return
            output = None
            yielded = False
            worked = False
            try:
                with self.lock:
                    self.check(ticket)
                    with database() as db:
                        row = db.execute('SELECT * FROM renditions WHERE id=?', (rid,)).fetchone()
                    if not row or row['status'] != 'queued':
                        continue
                    rank = queue_rank(row)
                    if rank != job[:2]:
                        self.queue.put((*rank, next(self.sequence), rid, ticket))
                        yielded = True
                        continue
                    self.current = rid
                chunks = json.loads(row['chunks']) if row['chunks'] else article(row['article_id'])['chunks']
                for chunk in chunks:
                    self.check(ticket)
                    # Reconsider priority after each completed section. Keep the
                    # same cancellation ticket and original arrival time on yield.
                    with self.lock:
                        if worked and not self.queue.empty():
                            self.check(ticket)
                            with database() as db:
                                db.execute("UPDATE renditions SET status='queued' WHERE id=?", (rid,))
                            self.queue.put((*queue_rank(row), next(self.sequence), rid, ticket))
                            yielded = True
                            break
                    key = clip_key(row['model'], row['voice'], row['style'], chunk['text'])
                    if clip_info(key):
                        if word_timings(key) is not None:
                            continue
                        if row['model'] != 'kokoro':
                            if row['timing_requested']:
                                self.align_clip(chunk['text'], key, ticket)
                                worked = True
                            continue
                    if row['timing_requested']:
                        raise RuntimeError('Saved audio is missing. Render the article again before generating timings.')
                    with self.lock, database() as db:
                        self.check(ticket)
                        db.execute("UPDATE renditions SET status=? WHERE id=?", ('loading' if self.loaded_model != row['model'] else 'generating', rid))
                    self.load(row['model'], ticket)
                    with self.lock, database() as db:
                        self.check(ticket)
                        self.phase = 'generating'
                        db.execute("UPDATE renditions SET status='generating' WHERE id=?", (rid,))
                    output = DATA / 'audio' / (key + '.partial.wav')
                    self.process.stdin.write(json.dumps({'text': chunk['text'], 'voice': row['voice'], 'style': row['style'], 'output': str(output)}) + '\n')
                    self.process.stdin.flush()
                    result = self.receive()
                    with self.lock, database() as db:
                        self.check(ticket)
                        if not result.get('ok'):
                            raise RuntimeError(result.get('error', 'Audio generation failed.'))
                        output.replace(DATA / 'audio' / (key + '.wav'))
                        if result.get('words') is not None:
                            metadata = DATA / 'audio' / (key + '.words.partial.json')
                            metadata.write_text(json.dumps(result['words']))
                            metadata.replace(DATA / 'audio' / (key + '.words.json'))
                        (DATA / 'audio' / (rid + '-full.wav')).unlink(missing_ok=True)
                        db.execute('INSERT OR REPLACE INTO clips VALUES (?,?,?,?,?)', (key, result['duration'], result['generation_seconds'], result['peak_vram_gb'], result['sample_rate']))
                    worked = True
                if not yielded:
                    with self.lock, database() as db:
                        self.check(ticket)
                        db.execute("UPDATE renditions SET status='complete', error=NULL,timing_requested=0 WHERE id=?", (rid,))
            except Exception as exc:
                with self.lock:
                    if not ticket.is_set() and not self.stopping:
                        with database() as db:
                            db.execute("UPDATE renditions SET status='error', error=? WHERE id=?", (str(exc), rid))
                    self.unload()
            finally:
                if output:
                    output.unlink(missing_ok=True)
                with self.lock:
                    if not yielded and self.tickets.get(rid) is ticket:
                        del self.tickets[rid]
                    self.phase = 'idle'
                    self.current = None


engine = Engine()
