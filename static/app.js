const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const state = { models: [], articles: [], article: null, selected: null, chunk: 0, playing: false, waiting: false, source: '', loadToken: 0, importMode: 'url', choices: {}, refresh: false };
const audio = new Audio();
const preload = new Audio();
audio.preload = 'auto';
preload.preload = 'auto';
let nextTimer, toastTimer;
const escape = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const formatTime = s => `${Math.floor((s || 0) / 60)}:${String(Math.floor((s || 0) % 60)).padStart(2, '0')}`;
const minutes = words => Math.max(1, Math.round(words / 170));

async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Please check the article or voice settings.');
  return data;
}
function toast(message) {
  $('#toast').textContent = message; $('#toast').hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => $('#toast').hidden = true, 6000);
}
function choice(model) {
  return state.choices[model.id] ||= { voice: model.default_voice, style: '' };
}
function matching(model) {
  const c = choice(model);
  return state.article?.renditions.find(r => r.model === model.id && r.voice === c.voice && r.style === c.style.trim());
}
function selectedReading() { return state.article?.renditions.find(r => r.id === state.selected); }
function voiceName(r) { return state.models.find(m => m.id === r.model)?.voices.find(v => v.id === r.voice)?.name.split(' · ')[0] || r.voice; }
function savePosition() {
  if (state.selected) localStorage.setItem(`position:${state.selected}`, JSON.stringify({ chunk: state.chunk, time: audio.currentTime || 0 }));
}
function stopPlayback() {
  savePosition(); state.loadToken++; state.playing = false; state.waiting = false;
  clearTimeout(nextTimer); audio.pause(); state.source = '';
}

function renderLibrary() {
  $('#library-count').textContent = state.articles.length;
  $('#library').innerHTML = state.articles.length ? state.articles.map(a => `<button class="library-item ${state.article?.id === a.id ? 'active' : ''}" data-article="${a.id}"><strong>${escape(a.title)}</strong><small>${minutes(a.words)} MIN READ · ${a.paragraphs} PARAGRAPHS</small></button>`).join('') : '<p class="empty-library">A home for the things you’ve<br>been meaning to read.</p>';
  $$('[data-article]').forEach(b => b.onclick = () => openArticle(b.dataset.article).catch(e => toast(e.message)));
}
async function refreshLibrary() { state.articles = await api('/api/articles'); renderLibrary(); }
function renderCards() {
  $('#model-cards').innerHTML = state.models.map((model, i) => {
    const c = choice(model);
    return `<article class="model-card" data-model="${model.id}"><div class="card-top"><span class="model-symbol">${['≋', '⌁', '✳'][i]}</span><span class="model-size">${model.size} · LOCAL</span></div><h3>${model.name}</h3><p class="description">${model.description}</p><label for="voice-${model.id}">NARRATOR</label><select id="voice-${model.id}" data-voice="${model.id}">${model.voices.map(v => `<option value="${v.id}" ${v.id === c.voice ? 'selected' : ''}>${v.name}</option>`).join('')}</select>${model.styles ? `<details class="style-details"><summary>Adjust delivery</summary><input aria-label="Qwen delivery instructions" id="style-${model.id}" maxlength="400" value="${escape(c.style)}" placeholder="e.g. Warm, calm audiobook narration"></details>` : ''}<div class="model-actions"><button class="button listen-button" data-listen="${model.id}">▶ Listen</button><button class="button render-button" data-render="${model.id}">Render article</button></div><div class="card-status"><span class="render-status">Ready to read</span><a class="download" hidden>WAV ↓</a></div><div class="card-progress"><div></div></div><div class="card-metrics"></div><div class="card-error" hidden></div></article>`;
  }).join('');
  $$('[data-voice]').forEach(input => input.onchange = () => { state.choices[input.dataset.voice].voice = input.value; updateCards(); });
  state.models.filter(m => m.styles).forEach(m => $(`#style-${m.id}`).onchange = e => { choice(m).style = e.target.value; updateCards(); });
  $$('[data-listen]').forEach(b => b.onclick = () => listenModel(b.dataset.listen).catch(e => toast(e.message)));
  $$('[data-render]').forEach(b => b.onclick = () => renderModel(b.dataset.render).catch(e => toast(e.message)));
  updateCards();
}
function updateCards() {
  for (const model of state.models) {
    const card = $(`[data-model="${model.id}"]`); if (!card) continue;
    const r = matching(model), available = model.downloaded && model.installed;
    card.classList.toggle('selected', selectedReading()?.model === model.id);
    const label = card.querySelector('.render-status');
    const render = card.querySelector('[data-render]');
    const working = r && ['queued', 'loading', 'generating'].includes(r.status);
    label.classList.toggle('busy', !!working);
    label.textContent = !available ? 'Model not installed' : !r ? 'Ready to read' : ({ queued: 'Queued · waiting for GPU', loading: 'Loading voice into memory…', generating: `${r.ready}/${r.total} chunks saved`, complete: `✓ Full reading saved · ${formatTime(r.audio_seconds)}`, paused: `Paused · ${r.ready}/${r.total} saved`, error: 'Needs a retry' }[r.status]);
    render.textContent = !r ? 'Render article' : working ? 'Stop rendering' : r.status === 'complete' ? 'Saved ✓' : 'Resume article';
    render.disabled = !available || r?.status === 'complete';
    card.querySelector('[data-listen]').disabled = !available;
    card.querySelector('.card-progress div').style.width = `${r ? r.ready / r.total * 100 : 0}%`;
    card.querySelector('.card-metrics').textContent = r?.ready ? `${r.generation_seconds ? (r.audio_seconds / r.generation_seconds).toFixed(1) + '× real-time' : ''} · ${r.peak_vram_gb.toFixed(1)} GB peak VRAM` : model.license + ' · English';
    const link = card.querySelector('.download'); link.hidden = r?.status !== 'complete'; if (r) link.href = `/api/renditions/${r.id}/download`;
    const error = card.querySelector('.card-error'); error.hidden = !r?.error; error.textContent = r?.error || '';
  }
  const readings = state.article?.renditions || [];
  const saved = $('#saved-readings');
  const markup = readings.length ? '<span class="saved-label">READINGS</span>' + readings.map(r => `<button class="reading-chip ${r.id === state.selected ? 'active' : ''}" data-reading="${r.id}">${escape(state.models.find(m => m.id === r.model)?.name)} · ${escape(voiceName(r))} ${r.status === 'complete' ? '✓' : r.ready + '/' + r.total}</button>`).join('') : '';
  if (saved.innerHTML !== markup) {
    saved.innerHTML = markup;
    $$('[data-reading]').forEach(b => b.onclick = () => {
      const r = state.article.renditions.find(x => x.id === b.dataset.reading);
      state.choices[r.model] = { voice: r.voice, style: r.style };
      selectReading(r.id); renderCards(); updatePlayer();
    });
  }
}

async function openArticle(id) {
  const token = state.articleLoad = (state.articleLoad || 0) + 1;
  stopPlayback(); state.selected = null; state.chunk = 0;
  const doc = await api(`/api/articles/${id}`);
  if (token !== state.articleLoad) return;
  state.article = doc;
  localStorage.setItem('readroom:article', id);
  $('#welcome').hidden = true; $('#workspace').hidden = false; $('#player').hidden = true;
  $('#reading-progress').textContent = 'Select a voice to start listening';
  $('#article-title').textContent = doc.title;
  $('#article-source').textContent = doc.url ? new URL(doc.url).hostname.replace(/^www\./, '') + ' / SAVED ARTICLE' : 'FROM YOUR LIBRARY / SAVED TEXT';
  const words = doc.paragraphs.join(' ').split(/\s+/).length;
  $('#article-meta').textContent = `${doc.byline ? doc.byline + ' · ' : ''}${words.toLocaleString()} words · ${minutes(words)} min read · ${doc.paragraphs.length} paragraphs`;
  $('#source-link').hidden = !doc.url; $('#source-link').href = doc.url || '#';
  $('#save-text').href = `/api/articles/${doc.id}/text`;
  $('#article-body').innerHTML = doc.paragraphs.map((p, i) => `<p class="article-paragraph" data-paragraph="${i}" tabindex="0" title="Listen from this paragraph">${escape(p)}</p>`).join('');
  $$('[data-paragraph]').forEach(p => {
    const seek = () => {
      if (!state.selected) return toast('Choose a narrator with Listen first.');
      state.chunk = doc.chunks.findIndex(c => c.paragraph === Number(p.dataset.paragraph));
      state.resumeTime = 0;
      startPlayback();
    };
    p.onclick = seek; p.onkeydown = e => { if (e.key === 'Enter') seek(); };
  });
  renderLibrary(); renderCards();
  if (doc.renditions.length) selectReading(doc.renditions[0].id);
  updatePlayer();
}
async function refreshArticle() {
  if (!state.article) return;
  const id = state.article.id;
  const doc = await api(`/api/articles/${id}`);
  if (id !== state.article?.id) return;
  state.article = doc; updateCards(); updatePlayer();
  if (state.playing && state.waiting) loadChunk();
  prefetchNext();
}
function selectReading(id, samePosition = false) {
  const oldChunk = state.chunk;
  stopPlayback(); state.selected = id;
  let stored = {};
  try { stored = JSON.parse(localStorage.getItem(`position:${id}`) || '{}'); } catch {}
  state.chunk = samePosition ? oldChunk : Math.min(stored.chunk || 0, state.article.chunks.length - 1);
  state.resumeTime = samePosition ? 0 : stored.time || 0;
  $('#seek').value = 0;
  $('#time-label').textContent = `${formatTime(state.resumeTime)} / ${formatTime(selectedReading()?.clips[state.chunk]?.duration)}`;
  $('#player').hidden = false; updateCards(); updatePlayer();
}
async function queueModel(id, articleId = state.article.id) {
  const model = state.models.find(m => m.id === id);
  if (model.styles) choice(model).style = $(`#style-${id}`).value;
  const result = await api(`/api/articles/${articleId}/renditions`, { model: id, ...choice(model) });
  await refreshArticle();
  return result.id;
}
async function renderModel(id) {
  const r = matching(state.models.find(m => m.id === id));
  if (r && ['queued', 'loading', 'generating'].includes(r.status)) {
    await api(`/api/renditions/${r.id}/pause`, {});
    toast('Rendering stopped after the current chunk. Saved audio is kept.');
    await refreshArticle();
  } else {
    await queueModel(id); toast('Full article queued. You can listen while it renders.');
  }
}
async function listenModel(id) {
  // Unlock this media element during the click, before waiting for generation.
  unlockAudio();
  const articleId = state.article.id;
  const rid = await queueModel(id);
  if (articleId !== state.article.id) return;
  if (rid !== state.selected) selectReading(rid, !!state.selected);
  startPlayback();
}
function unlockAudio() {
  if (audio.src) return;
  audio.src = '/silence.wav';
  const silentSource = audio.src;
  audio.play().then(() => { if (audio.src === silentSource) audio.pause(); }).catch(() => {});
}
function startPlayback() {
  clearTimeout(nextTimer); audio.pause(); state.source = ''; state.loadToken++;
  state.playing = true; state.waiting = true; loadChunk(); updatePlayer();
}
async function loadChunk() {
  const r = selectedReading(); if (!r || !state.playing) return;
  const clip = r.clips[state.chunk];
  if (!clip) {
    state.waiting = true;
    if (['paused', 'error'].includes(r.status)) { state.playing = false; toast('Resume rendering this reading to continue.'); }
    updatePlayer(); return;
  }
  if (state.source === clip.url && !state.waiting) return;
  state.waiting = false; state.source = clip.url;
  const token = ++state.loadToken;
  audio.src = clip.url; audio.playbackRate = Number($('#speed').value); audio.preservesPitch = true;
  if (state.resumeTime) {
    const time = state.resumeTime; state.resumeTime = 0;
    audio.addEventListener('loadedmetadata', () => { if (token === state.loadToken) audio.currentTime = Math.min(time, audio.duration - .1); }, { once: true });
  }
  try { await audio.play(); } catch (e) {
    if (token === state.loadToken) { state.playing = false; if (e.name !== 'AbortError') toast('Audio is ready. Press Play to start listening.'); }
  }
  if (token === state.loadToken) { updatePlayer(); prefetchNext(); }
}
function prefetchNext() {
  const clip = selectedReading()?.clips[state.chunk + 1];
  if (clip && preload.dataset.source !== clip.url) { preload.dataset.source = clip.url; preload.src = clip.url; preload.load(); }
}
function updatePlayer() {
  const r = selectedReading(); if (!r) return;
  const model = state.models.find(m => m.id === r.model);
  $('#player').hidden = false;
  $('#player-voice').textContent = `${model.name} · ${voiceName(r)}`;
  $('#player-state').textContent = state.playing ? state.waiting ? 'Waiting for the next saved chunk…' : 'Reading from your saved audio' : r.status === 'complete' ? 'Full reading saved locally' : `${r.ready}/${r.total} chunks saved · ${r.status}`;
  $('#play-toggle').textContent = state.playing ? 'Ⅱ' : '▶'; $('#play-toggle').setAttribute('aria-label', state.playing ? 'Pause' : 'Play');
  const p = state.article.chunks[state.chunk]?.paragraph || 0;
  $('#chunk-label').textContent = `Paragraph ${p + 1} of ${state.article.paragraphs.length}`;
  $('#reading-progress').textContent = `${model.name} · paragraph ${p + 1} of ${state.article.paragraphs.length}`;
  $$('[data-paragraph]').forEach(el => el.classList.toggle('active', Number(el.dataset.paragraph) === p));
  $('#previous').disabled = state.chunk === 0; $('#next').disabled = state.chunk === state.article.chunks.length - 1;
}
audio.onended = () => {
  if (!state.playing || !state.source) return;
  if (state.chunk + 1 >= state.article.chunks.length) {
    state.playing = false; state.chunk = 0; state.source = ''; audio.currentTime = 0; savePosition(); updatePlayer(); toast('Reading complete. A little more room in your day.'); return;
  }
  const pause = state.article.chunks[state.chunk].paragraph_end ? 250 : 0;
  nextTimer = setTimeout(() => { if (state.playing) { state.chunk++; state.resumeTime = 0; state.source = ''; state.waiting = true; loadChunk(); } }, pause);
};
audio.ontimeupdate = () => {
  $('#seek').value = audio.duration ? audio.currentTime / audio.duration * 100 : 0;
  $('#time-label').textContent = `${formatTime(audio.currentTime)} / ${formatTime(audio.duration)}`;
  if (state.source) savePosition();
};
audio.onerror = () => { if (state.source) { state.playing = false; state.source = ''; updatePlayer(); toast('Could not play this audio. Try pressing Play again.'); } };
$('#play-toggle').onclick = () => {
  if (!selectedReading()) return;
  if (state.playing) { state.playing = false; audio.pause(); clearTimeout(nextTimer); savePosition(); updatePlayer(); }
  else { unlockAudio(); if (state.source && audio.currentTime < audio.duration) { state.playing = true; audio.play().catch(e => toast(e.message)); updatePlayer(); } else startPlayback(); }
};
$('#previous').onclick = () => { if (state.chunk > 0) { state.chunk--; state.resumeTime = 0; startPlayback(); } };
$('#next').onclick = () => { if (state.chunk < state.article.chunks.length - 1) { state.chunk++; state.resumeTime = 0; startPlayback(); } };
$('#seek').oninput = e => { if (Number.isFinite(audio.duration)) audio.currentTime = Number(e.target.value) / 100 * audio.duration; };
$('#speed').onchange = () => { audio.playbackRate = Number($('#speed').value); };
$('#render-all').onclick = async () => {
  $('#render-all').disabled = true;
  const articleId = state.article.id;
  try { for (const m of state.models.filter(m => m.downloaded && m.installed)) await queueModel(m.id, articleId); toast('All three readings queued. Each model will finish before the next starts.'); }
  catch (e) { toast(e.message); }
  finally { $('#render-all').disabled = false; }
};

function openImport() { $('#import-error').hidden = true; $('#import-dialog').showModal(); }
$('#new-article').onclick = openImport; $('#welcome-add').onclick = openImport;
$('#close-dialog').onclick = () => $('#import-dialog').close();
function importMode(mode) {
  state.importMode = mode; $('#url-fields').hidden = mode !== 'url'; $('#text-fields').hidden = mode !== 'text';
  for (const m of ['url', 'text']) { $(`#${m}-tab`).classList.toggle('active', mode === m); $(`#${m}-tab`).setAttribute('aria-selected', mode === m); }
  $('#article-url').required = mode === 'url'; $('#paste-text').required = mode === 'text';
}
$('#url-tab').onclick = () => importMode('url'); $('#text-tab').onclick = () => importMode('text'); importMode('url');
$('#import-form').onsubmit = async e => {
  e.preventDefault(); $('#import-error').hidden = true; $('#import-submit').disabled = true; $('#import-submit').textContent = state.importMode === 'url' ? 'Fetching article…' : 'Saving…';
  try {
    const article = await api('/api/articles', state.importMode === 'url' ? { url: $('#article-url').value } : { title: $('#paste-title').value, text: $('#paste-text').value });
    await refreshLibrary(); await openArticle(article.id); $('#import-dialog').close(); $('#import-form').reset(); toast('Article saved. Pick a narrator to hear it.');
  } catch (e) { $('#import-error').textContent = e.message; $('#import-error').hidden = false; }
  finally { $('#import-submit').disabled = false; $('#import-submit').innerHTML = 'Save to library <span>↗</span>'; }
};
const sample = {
  title: 'The quiet pleasure of paying attention',
  text: `On a quiet morning, before the messages arrive, there is a small opportunity to notice the world. A kettle begins to hum. Light moves across the kitchen wall. Somewhere outside, a bicycle rolls over uneven pavement. None of these things asks for our attention. That may be exactly why they deserve it.

Reading has a similar rhythm. A good sentence invites us to slow down, while a surprising idea makes us pause altogether. When someone reads aloud, the spaces between the words become part of the story. A voice can turn an ordinary paragraph into a place we want to stay.

“How much time do we really need?” asked Dr. Morgan. The answer was surprisingly modest: fifteen minutes, twice a day. In the first week, 24 people took part. By Friday, they had read 3.5 hours each, and nobody had bought a new device. The experiment cost $125.50, including coffee.

The point is not to finish everything. It is to choose something worth returning to, and give it enough room to be heard. Close the extra tabs. Take a walk. Let a good voice carry the next few pages.`
};
$('#load-sample').onclick = async () => {
  try { const doc = await api('/api/articles', sample); await refreshLibrary(); await openArticle(doc.id); }
  catch (e) { toast(e.message); }
};
async function poll() {
  if (state.refresh) return; state.refresh = true;
  try {
    const status = await api('/api/status'); state.models = status.models;
    $('#machine-status').textContent = status.engine.phase !== 'idle' ? `${status.models.find(m => m.id === status.engine.model)?.name || 'Model'} · ${status.engine.phase}` : `${status.models.filter(m => m.downloaded).length} models on this machine`;
    await refreshArticle();
  } catch (e) { $('#machine-status').textContent = 'Connection lost · retrying…'; }
  finally { state.refresh = false; }
}
async function init() {
  try {
    const status = await api('/api/status'); state.models = status.models;
    await refreshLibrary();
    const last = localStorage.getItem('readroom:article');
    if (state.articles.length) await openArticle(state.articles.find(a => a.id === last)?.id || state.articles[0].id);
    await poll();
  } catch (e) { toast(e.message); }
  setInterval(poll, 1500);
}
window.addEventListener('beforeunload', savePosition);
init();
