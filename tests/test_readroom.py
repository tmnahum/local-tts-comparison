import asyncio
import io
import json
import os
import socket
import tempfile
import wave

os.environ['READROOM_DATA'] = tempfile.mkdtemp(prefix='readroom-tests-')
import pytest
from fastapi.testclient import TestClient
from app.text import chunk_paragraphs, split_paragraphs, paragraphs_from_text
from app.fetcher import public_address, fetch_article, extract_readable
from app.engine import engine, clip_key
from app.main import app
from app.storage import DATA, database, initialize


@pytest.fixture(scope='module')
def client():
    original = engine.enqueue
    engine.enqueue = lambda rid: None  # API tests must not start GPU inference.
    with TestClient(app) as client:
        yield client
    engine.enqueue = original


def test_chunking_preserves_text_and_paragraph_boundaries():
    paragraphs = ["Dr. Morgan paid $125.50. " + 'A complete sentence deserves room. ' * 25, 'A second paragraph, with a different thought.']
    chunks = split_paragraphs(paragraphs)
    assert len(chunks) > 2
    for i, original in enumerate(paragraphs):
        parts = [c for c in chunks if c['paragraph'] == i]
        assert ' '.join(c['text'] for c in parts) == original.strip()
        assert sum(c['paragraph_end'] for c in parts) == 1
        assert parts[-1]['paragraph_end']
    assert all(len(c['text']) <= 650 for c in chunks)
    assert chunks[0]['text'].startswith('Dr. Morgan paid $125.50.')


def test_overlong_sentence_preserves_words():
    text = ' '.join(['uninterrupted'] * 300)
    chunks = split_paragraphs([text])
    assert ' '.join(c['text'] for c in chunks) == text
    assert all(len(c['text']) <= 650 for c in chunks)


def test_blank_lines_define_pasted_paragraphs():
    assert paragraphs_from_text(' First.\n\n Second.\n\n\nThird. ') == ['First.', 'Second.', 'Third.']
    assert paragraphs_from_text('A softly wrapped\nline.\n\nAnother paragraph.') == ['A softly wrapped line.', 'Another paragraph.']


def test_inline_formatting_does_not_split_paragraphs():
    source = '''<html><head><title>A reading test</title></head><body><article>
    <p>There is a dish called <i>saltimbocca</i>, which is an interesting name.
    We should read this sentence together, with no pause around the italic word.</p>
    <p>This second paragraph contains <a href="/somewhere">a link</a> and some <strong>bold words</strong>.
    Neither formatting choice should interrupt the reader's rhythm.</p></article></body></html>'''
    doc = extract_readable(source, 'https://example.org/article')
    assert doc['title'] == 'A reading test'
    assert len(doc['paragraphs']) == 2
    assert 'called saltimbocca, which' in doc['paragraphs'][0]
    assert 'contains a link and some bold words.' in doc['paragraphs'][1]


def test_old_style_line_break_paragraphs():
    source = '<html><head><title>Old page</title></head><body><table><tr><td><font>' + ('An old page can have <i>italic words</i> inside a sentence, while real paragraphs use line breaks.<br><br>' * 3) + '</font></td></tr></table></body></html>'
    doc = extract_readable(source, 'https://example.org/article')
    assert len(doc['paragraphs']) == 3
    assert all('have italic words inside a sentence' in p for p in doc['paragraphs'])


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'http://user:pass@example.com', 'http://example.com:8000'])
def test_reject_non_public_url_shapes(url):
    with pytest.raises(ValueError):
        asyncio.run(public_address(url))


@pytest.mark.parametrize('ip', ['127.0.0.1', '10.0.0.1', '169.254.169.254', '::1', '192.168.0.1'])
def test_reject_private_dns(monkeypatch, ip):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 80))])
    with pytest.raises(ValueError):
        asyncio.run(public_address('https://article.example'))


def test_redirect_cannot_escape_to_private_network(monkeypatch):
    import httpx
    from app import fetcher
    original = httpx.AsyncClient
    monkeypatch.setattr(socket, 'getaddrinfo', lambda host, *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1' if host == 'localhost' else '93.184.216.34', 443))])
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={'location': 'http://localhost/secret'})
    monkeypatch.setattr(fetcher.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(ValueError):
        asyncio.run(fetch_article('https://article.example'))
    assert len(requests) == 1
    assert requests[0].url.host == '93.184.216.34'
    assert requests[0].headers['host'] == 'article.example'
    assert requests[0].extensions['sni_hostname'] == 'article.example'


def test_article_saved_and_deduplicated(client):
    payload = {'title': 'Persistent article', 'text': 'First paragraph.\n\nSecond paragraph.'}
    first = client.post('/api/articles', json=payload)
    assert first.status_code == 200
    doc = first.json()
    assert client.post('/api/articles', json=payload).json()['id'] == doc['id']
    saved = client.get('/api/articles/' + doc['id']).json()
    assert saved['paragraphs'] == ['First paragraph.', 'Second paragraph.']
    assert json.loads((DATA / 'articles' / (doc['id'] + '.json')).read_text())['paragraphs'] == saved['paragraphs']
    assert client.get('/api/articles/' + doc['id'] + '/text').headers['content-disposition'].startswith('attachment')


def test_rendition_validation_dedup_pause_resume(client):
    aid = client.post('/api/articles', json={'title': 'Queue test', 'text': 'A test paragraph.'}).json()['id']
    url = '/api/articles/' + aid + '/renditions'
    assert client.post(url, json={'model': 'unknown'}).status_code == 422
    assert client.post(url, json={'model': 'kokoro', 'voice': '../bad'}).status_code == 422
    rid = client.post(url, json={'model': 'kokoro'}).json()['id']
    assert client.post(url, json={'model': 'kokoro'}).json()['id'] == rid
    assert client.get('/api/renditions/' + rid + '/download').status_code == 409
    assert client.post('/api/renditions/' + rid + '/pause').json()['paused']
    assert client.get('/api/articles/' + aid).json()['renditions'][0]['status'] == 'paused'
    assert client.post(url, json={'model': 'kokoro'}).json()['id'] == rid
    assert client.get('/api/articles/' + aid).json()['renditions'][0]['status'] == 'queued'
    initialize()  # A server restart preserves the queue.
    assert client.get('/api/articles/' + aid).json()['renditions'][0]['status'] == 'queued'


def test_complete_download_joins_chunks_in_order_with_paragraph_pause(client):
    aid = client.post('/api/articles', json={'title': 'Audio export', 'text': 'First.\n\nSecond.'}).json()['id']
    rid = client.post('/api/articles/' + aid + '/renditions', json={'model': 'kokoro'}).json()['id']
    with database() as db:
        db.execute('UPDATE articles SET chunks=? WHERE id=?', (json.dumps(split_paragraphs(['First.', 'Second.'])), aid))
        db.execute('UPDATE renditions SET chunks=NULL WHERE id=?', (rid,))
    for text, value in [('First.', 100), ('Second.', 200)]:
        key = clip_key('kokoro', 'af_heart', '', text)
        with wave.open(str(DATA / 'audio' / (key + '.wav')), 'wb') as wav:
            wav.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
            wav.writeframes(value.to_bytes(2, 'little') * 2400)
        with database() as db:
            db.execute('INSERT INTO clips VALUES (?,?,?,?,?)', (key, .1, .01, .5, 24000))
    result = client.get('/api/renditions/' + rid + '/download')
    assert result.status_code == 200
    with wave.open(io.BytesIO(result.content)) as wav:
        assert wav.getnframes() == 2400 + 6000 + 2400
        assert wav.readframes(1) == (100).to_bytes(2, 'little')
        wav.setpos(8400)
        assert wav.readframes(1) == (200).to_bytes(2, 'little')


def test_cross_origin_writes_blocked(client):
    assert client.post('/api/articles', json={'text': 'No'}, headers={'origin': 'https://unrelated.example'}).status_code == 403


def test_empty_input_and_missing_article(client):
    assert client.post('/api/articles', json={}).status_code == 422
    assert client.get('/api/articles/missing').status_code == 404


def test_cache_separates_voice_and_style():
    assert len({clip_key('kokoro', 'af_heart', '', 'Text'), clip_key('kokoro', 'af_bella', '', 'Text'), clip_key('qwen', 'Ryan', '', 'Text'), clip_key('qwen', 'Ryan', 'Whisper', 'Text')}) == 4


def test_three_paragraph_ranges_preserve_every_word_and_map_boundaries():
    paragraphs = [f'Paragraph {i} has a thought of its own.' for i in range(7)]
    chunks = chunk_paragraphs(paragraphs)
    assert [(c['paragraph'], c['paragraph_last']) for c in chunks] == [(0, 2), (3, 5), (6, 6)]
    assert ' '.join(c['text'] for c in chunks) == ' '.join(paragraphs)
    assert all(c['pause_ms'] == 0 for c in chunks)


def test_oversized_three_paragraph_range_still_has_bounded_chunks():
    paragraphs = [('A sentence with room to breathe. ' * 100).strip(), 'Second.', 'Third.', 'Fourth.']
    chunks = chunk_paragraphs(paragraphs)
    assert all(len(c['text']) <= 1950 for c in chunks)
    assert ' '.join(c['text'] for c in chunks) == ' '.join(paragraphs)
    assert chunks[-1]['paragraph'] == 3


def test_queue_visible_across_articles_and_cancellable(client):
    client.post('/api/queue/cancel')
    ids = []
    for title in ('First queued article', 'Second queued article'):
        aid = client.post('/api/articles', json={'title': title, 'text': 'One.\n\nTwo.\n\nThree.\n\nFour.'}).json()['id']
        ids.append(client.post('/api/articles/' + aid + '/renditions', json={'model': 'kokoro'}).json()['id'])
    queue = client.get('/api/queue').json()
    assert queue['active'] is None
    assert [r['id'] for r in queue['queued']] == ids
    assert queue['queued'][0]['total'] == 2
    assert queue['queued'][0]['chunks'][0]['paragraph_last'] == 2
    assert client.post('/api/renditions/' + ids[0] + '/cancel').json()['cancelled']
    assert [r['id'] for r in client.get('/api/queue').json()['queued']] == [ids[1]]
    assert client.post('/api/queue/cancel').json()['cancelled'] == 1
    assert client.get('/api/queue').json() == {'active': None, 'queued': []}


def test_active_process_is_killed_and_immediate_retry_survives(client):
    import subprocess
    import sys
    import time
    from app.engine import Engine

    class BlockingEngine(Engine):
        starts = 0
        def load(self, model, ticket):
            with self.lock:
                self.check(ticket)
                self.process = subprocess.Popen([sys.executable, '-u', '-c', 'import time; time.sleep(60)'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
                self.loaded_model = model
                self.starts += 1

    aid = client.post('/api/articles', json={'title': 'Cancellation process test', 'text': 'Uncached test speech.'}).json()['id']
    rid = client.post('/api/articles/' + aid + '/renditions', json={'model': 'kokoro'}).json()['id']
    e = BlockingEngine()
    e.thread.start()
    e.enqueue(rid)
    def wait_for(condition):
        deadline = time.monotonic() + 3
        while not condition():
            assert time.monotonic() < deadline
            time.sleep(.01)
    try:
        wait_for(lambda: e.starts == 1)
        process = e.process
        start = time.monotonic()
        with e.lock:
            assert e.cancel(rid)
            with database() as db:
                db.execute("UPDATE renditions SET status='queued' WHERE id=?", (rid,))
            e.enqueue(rid)  # Retry before the cancelled worker has unwound.
        wait_for(lambda: process.poll() is not None)
        assert time.monotonic() - start < 2
        wait_for(lambda: e.starts == 2)
        assert e.cancel(rid)
        wait_for(lambda: e.current is None)
        with database() as db:
            row = db.execute('SELECT status,error FROM renditions WHERE id=?', (rid,)).fetchone()
        assert tuple(row) == ('paused', None)
    finally:
        e.close()


def test_grouped_download_has_no_inserted_silence(client):
    aid = client.post('/api/articles', json={'title': 'Continuous export', 'text': 'Alpha.\n\nBeta.\n\nGamma.\n\nDelta.'}).json()['id']
    rid = client.post('/api/articles/' + aid + '/renditions', json={'model': 'kokoro'}).json()['id']
    reading = client.get('/api/articles/' + aid).json()['renditions'][0]
    assert reading['grouped']
    for chunk in reading['chunks']:
        key = clip_key('kokoro', 'af_heart', '', chunk['text'])
        with wave.open(str(DATA / 'audio' / (key + '.wav')), 'wb') as wav:
            wav.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
            wav.writeframes(b'\x01\x00' * 2400)
        with database() as db:
            db.execute('INSERT INTO clips VALUES (?,?,?,?,?)', (key, .1, .01, .5, 24000))
    response = client.get('/api/renditions/' + rid + '/download')
    assert response.status_code == 200
    with wave.open(io.BytesIO(response.content)) as wav:
        assert wav.getnframes() == 4800


def test_kokoro_timings_merge_contractions_and_offset_internal_audio():
    from types import SimpleNamespace as Obj
    from app.timing import kokoro_words
    def token(text, start, end):
        return Obj(text=text, start_ts=start, end_ts=end)
    outputs = [Obj(audio=[0] * 24000, tokens=[token('Don', .1, .2), token("'t", .2, .3), token('go', .4, .6), token('.', None, None)]),
               Obj(audio=[0] * 24000, tokens=[token('Go', .1, .4), token('now', .5, .8), token('.', None, None)])]
    words = kokoro_words("Don't go. Go now.", outputs)
    assert [(w['start'], w['end']) for w in words] == [(0, 5), (6, 9), (10, 12), (13, 17)]
    assert words[0]['start_time'] == .1 and words[0]['end_time'] == .3
    assert words[2]['start_time'] == 1.1
    assert words[-1]['end_time'] == 1.8


def test_word_offsets_handle_repeated_text_and_unicode():
    from app.timing import chunk_offsets
    paragraphs = ['Hello 🪴 world.', 'Hello 🪴 world.', 'Third.']
    chunks = [{'text': p} for p in paragraphs]
    assert chunk_offsets(paragraphs, chunks) == [0, 15, 30]
    assert chunk_offsets(paragraphs, [{'text': 'Missing text.'}]) == [None]


def test_kokoro_timings_skip_unmapped_tokens_and_invalid_times():
    from types import SimpleNamespace as Obj
    from app.timing import kokoro_words
    output = Obj(audio=[0] * 24000, tokens=[Obj(text='unmatched', start_ts=0, end_ts=.1), Obj(text='Hello', start_ts=float('nan'), end_ts=.3), Obj(text='world', start_ts=.4, end_ts=20)])
    assert kokoro_words('Hello world', [output]) == [{'start':6, 'end':11, 'start_time':.4, 'end_time':1.0}]


def test_existing_kokoro_reading_can_upgrade_word_timings(client, monkeypatch):
    from app.engine import clip_key
    doc = client.post('/api/articles', json={'title':'Timing upgrade', 'text':'Hello again.'}).json()
    rid = client.post(f"/api/articles/{doc['id']}/renditions", json={'model':'kokoro'}).json()['id']
    key = clip_key('kokoro', 'af_heart', '', 'Hello again.')
    with wave.open(str(DATA / 'audio' / (key + '.wav')), 'wb') as wav:
        wav.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        wav.writeframes(b'\0' * 4800)
    with database() as db:
        db.execute('INSERT INTO clips VALUES (?,?,?,?,?)', (key, .1, .01, .5, 24000))
        db.execute("UPDATE renditions SET status='complete' WHERE id=?", (rid,))
    queued = []
    monkeypatch.setattr(engine, 'enqueue', queued.append)
    client.post(f"/api/articles/{doc['id']}/renditions", json={'model':'kokoro'})
    assert queued == [rid]
    words = [{'start':0, 'end':5, 'start_time':0, 'end_time':.1}]
    (DATA / 'audio' / (key + '.words.json')).write_text(json.dumps(words))
    with database() as db:
        db.execute("UPDATE renditions SET status='complete' WHERE id=?", (rid,))
    reading = client.get(f"/api/articles/{doc['id']}").json()['renditions'][0]
    assert reading['has_word_timings']
    assert reading['clips'][0]['words'] == words
    assert reading['clips'][0]['url'].endswith('?timing=1')
    assert reading['chunk_offsets'] == [0]
    client.post(f"/api/articles/{doc['id']}/renditions", json={'model':'kokoro'})
    assert queued == [rid]  # Timed audio is reused.


def test_saved_voices_deduplicate_and_persist_favorites(client):
    first = client.post('/api/voices', json={'model': 'kokoro', 'voice': 'af_heart'}).json()
    second = client.post('/api/voices', json={'model': 'kokoro', 'voice': 'af_bella'}).json()
    assert first['id'] != second['id']
    assert client.post('/api/voices', json={'model': 'kokoro', 'voice': 'af_heart', 'style': 'ignored'}).json()['id'] == first['id']
    assert client.post(f"/api/voices/{second['id']}/star", json={'starred': True}).status_code == 200
    initialize()  # Durable selections survive service initialization.
    saved = client.get('/api/voices').json()
    assert len(saved) == 2 and saved[0]['id'] == second['id'] and saved[0]['starred'] == 1
    assert client.post('/api/voices', json={'model': 'kokoro', 'voice': 'af_bella'}).json()['starred'] == 1
    assert client.post(f"/api/voices/{second['id']}/star", json={'starred': False}).status_code == 200
    assert client.delete(f"/api/voices/{first['id']}").status_code == 200
    assert client.delete(f"/api/voices/{second['id']}").status_code == 200


def test_saved_voice_validation_and_style_identity(client):
    assert client.post('/api/voices', json={'model': 'invalid'}).status_code == 422
    assert client.post('/api/voices', json={'model': 'kokoro', 'voice': 'Ryan'}).status_code == 422
    one = client.post('/api/voices', json={'model': 'qwen', 'style': ' Calm '}).json()
    two = client.post('/api/voices', json={'model': 'qwen', 'style': 'Calm'}).json()
    three = client.post('/api/voices', json={'model': 'qwen', 'style': 'Excited'}).json()
    assert one['id'] == two['id'] != three['id']
    assert client.post(f"/api/voices/{one['id']}/star", json={'starred': True}, headers={'origin': 'https://evil.example'}).status_code == 403
    assert client.delete(f"/api/voices/{one['id']}", headers={'origin': 'https://evil.example'}).status_code == 403
    assert client.post('/api/voices/missing/star', json={'starred': True}).status_code == 404


def test_existing_other_model_aligns_without_regenerating_audio(client):
    import time
    from app.engine import Engine
    doc = client.post('/api/articles', json={'title': 'Alignment reuse', 'text': 'Existing saved speech.'}).json()
    rid = client.post(f"/api/articles/{doc['id']}/renditions", json={'model': 'qwen', 'voice': 'Ryan'}).json()['id']
    key = clip_key('qwen', 'Ryan', '', doc['chunks'][0]['text'])
    path = DATA / 'audio' / (key + '.wav')
    with wave.open(str(path), 'wb') as wav:
        wav.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        wav.writeframes(b'\0' * 4800)
    original = path.read_bytes()
    with database() as db:
        db.execute('INSERT INTO clips VALUES (?,?,?,?,?)', (key, .1, .01, .5, 24000))
        db.execute("UPDATE renditions SET status='complete' WHERE id=?", (rid,))
    assert client.post(f"/api/articles/{doc['id']}/renditions", json={'model': 'qwen'}).json()['id'] == rid
    assert client.get(f"/api/articles/{doc['id']}").json()['renditions'][0]['status'] == 'complete'
    response = client.post(f'/api/renditions/{rid}/timings')
    assert response.status_code == 200 and response.json()['status'] == 'queued'
    assert client.post(f'/api/renditions/{rid}/timings').status_code == 200
    class AlignmentOnlyEngine(Engine):
        def load(self, model, ticket):
            raise AssertionError('Cached speech must not be regenerated')
        def align_clip(self, text, cache_key, ticket):
            assert text == doc['chunks'][0]['text']
            (DATA / 'audio' / (cache_key + '.words.json')).write_text(json.dumps([{'start': 0, 'end': 8, 'start_time': 0, 'end_time': .1}]))
    e = AlignmentOnlyEngine()
    e.thread.start(); e.enqueue(rid)
    try:
        deadline = time.monotonic() + 3
        while True:
            with database() as db:
                status = db.execute('SELECT status FROM renditions WHERE id=?', (rid,)).fetchone()[0]
            if status == 'complete':
                break
            assert status != 'error' and time.monotonic() < deadline
            time.sleep(.01)
        assert path.read_bytes() == original
        reading = client.get(f"/api/articles/{doc['id']}").json()['renditions'][0]
        assert reading['has_word_timings']
        assert '?' not in reading['clips'][0]['url']  # Keep currently playing audio matched to new timings.
    finally:
        e.close()


def test_alignment_cancel_keeps_cached_audio(client):
    import subprocess
    import sys
    import time
    from app.engine import Engine
    doc = client.post('/api/articles', json={'title': 'Cancel word mapping', 'text': 'Keep this existing recording.'}).json()
    rid = client.post(f"/api/articles/{doc['id']}/renditions", json={'model': 'qwen'}).json()['id']
    key = clip_key('qwen', 'Ryan', '', doc['chunks'][0]['text'])
    path = DATA / 'audio' / (key + '.wav')
    path.write_bytes(b'cached audio')
    with database() as db:
        db.execute('INSERT INTO clips VALUES (?,?,?,?,?)', (key, 1, .1, 0, 24000))
    with database() as db:
        db.execute("UPDATE renditions SET status='complete' WHERE id=?", (rid,))
    assert client.post(f'/api/renditions/{rid}/timings').status_code == 200
    class BlockingAligner(Engine):
        def align_clip(self, text, key, ticket):
            with self.lock:
                self.check(ticket)
                self.aligner = subprocess.Popen([sys.executable, '-u', '-c', 'import time; time.sleep(60)'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            self.receive(process=self.aligner)
    e = BlockingAligner(); e.thread.start(); e.enqueue(rid)
    try:
        deadline = time.monotonic() + 3
        while not e.aligner:
            assert time.monotonic() < deadline
            time.sleep(.01)
        process = e.aligner
        assert e.cancel(rid)
        process.wait(timeout=2)
        assert path.read_bytes() == b'cached audio'
        with database() as db:
            assert db.execute('SELECT status FROM renditions WHERE id=?', (rid,)).fetchone()[0] == 'paused'
    finally:
        e.close()


def test_first_sections_precede_continuations_and_new_arrivals(client):
    import threading
    import time
    from unittest.mock import MagicMock
    from app.engine import Engine
    jobs = []
    for label in ('Alpha', 'Beta', 'Gamma'):
        text = '\n\n'.join(f'{label} paragraph {i}.' for i in range(7))
        doc = client.post('/api/articles', json={'title': label + ' scheduler check', 'text': text}).json()
        jobs.append(client.post(f"/api/articles/{doc['id']}/renditions", json={'model': 'qwen'}).json()['id'])
    first_started, release = threading.Event(), threading.Event()
    order = []
    class SectionEngine(Engine):
        def load(self, model, ticket):
            if self.process is None:
                self.process = MagicMock()
                self.process.poll.return_value = None
                self.process.stdin = io.StringIO()
                self.process.stdout = io.StringIO()
            self.loaded_model = model
        def receive(self, timeout=600, process=None):
            request = json.loads(self.process.stdin.getvalue().splitlines()[-1])
            order.append((request['text'].split()[0], request['text'].split()[2]))
            if len(order) == 1:
                first_started.set()
                assert release.wait(3)
            with wave.open(request['output'], 'wb') as wav:
                wav.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
                wav.writeframes(b'\0' * 480)
            return {'ok': True, 'duration': .01, 'generation_seconds': .01, 'peak_vram_gb': 0, 'sample_rate': 24000, 'words': None}
        def align_clip(self, *args):
            raise AssertionError('Rendering must not implicitly generate word timings')
    e = SectionEngine()
    e.enqueue(jobs[0]); e.enqueue(jobs[1]); e.thread.start()
    try:
        assert first_started.wait(3)
        e.enqueue(jobs[2])  # A new voice arrives while Alpha's first section is running.
        release.set()
        deadline = time.monotonic() + 5
        while True:
            with database() as db:
                statuses = [db.execute('SELECT status FROM renditions WHERE id=?', (rid,)).fetchone()[0] for rid in jobs]
            if statuses == ['complete'] * 3:
                break
            assert 'error' not in statuses and time.monotonic() < deadline
            time.sleep(.01)
        assert order == [('Alpha', '0.'), ('Beta', '0.'), ('Gamma', '0.'), ('Alpha', '3.'), ('Alpha', '6.'), ('Beta', '3.'), ('Beta', '6.'), ('Gamma', '3.'), ('Gamma', '6.')]
    finally:
        release.set(); e.close()


def test_explicit_timing_request_requires_saved_audio(client):
    doc = client.post('/api/articles', json={'title': 'No audio yet', 'text': 'Render this before aligning.'}).json()
    rid = client.post(f"/api/articles/{doc['id']}/renditions", json={'model': 'qwen'}).json()['id']
    assert client.post(f'/api/renditions/{rid}/timings').status_code == 409
    assert client.post('/api/renditions/missing/timings').status_code == 404
    assert client.post(f'/api/renditions/{rid}/timings', headers={'origin': 'https://evil.example'}).status_code == 403


def test_performance_cue_is_validated_saved_and_separates_cache(client):
    plain = client.post('/api/voices', json={'model': 'chatterbox'}).json()
    cue = client.post('/api/voices', json={'model': 'chatterbox', 'style': '[chuckle]'}).json()
    assert cue['style'] == '[chuckle]'
    assert cue['id'] != plain['id']
    assert client.post('/api/voices', json={'model': 'chatterbox', 'style': 'Speak happily'}).status_code == 422
    assert clip_key('chatterbox', 'default', '', 'Hello.') != clip_key('chatterbox', 'default', '[chuckle]', 'Hello.')


def test_neutts_emotion_defaults_and_saved_identity(client):
    plain = client.post('/api/voices', json={'model': 'neutts-q4'}).json()
    neutral = client.post('/api/voices', json={'model': 'neutts-q4', 'style': 'neutral'}).json()
    happy = client.post('/api/voices', json={'model': 'neutts-q4', 'style': 'happy'}).json()
    assert plain['style'] == 'neutral'
    assert plain['id'] == neutral['id'] != happy['id']
    assert client.post('/api/voices', json={'model': 'neutts-q4', 'style': 'whisper'}).status_code == 422
    assert client.post('/api/voices', json={'model': 'neutts-q8', 'voice': 'unknown'}).status_code == 422
