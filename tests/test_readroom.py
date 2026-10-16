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
from app.text import chunk_paragraphs, paragraphs_from_text
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
    chunks = chunk_paragraphs(paragraphs)
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
    chunks = chunk_paragraphs([text])
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
    initialize()  # A server restart preserves progress and pauses interrupted work.
    assert client.get('/api/articles/' + aid).json()['renditions'][0]['status'] == 'paused'


def test_complete_download_joins_chunks_in_order_with_paragraph_pause(client):
    aid = client.post('/api/articles', json={'title': 'Audio export', 'text': 'First.\n\nSecond.'}).json()['id']
    rid = client.post('/api/articles/' + aid + '/renditions', json={'model': 'kokoro'}).json()['id']
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
