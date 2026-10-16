const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const state = { models: [], articles: [], article: null, selected: null, chunk: 0, playing: false, waiting: false, source: '', loadToken: 0, importMode: 'url', choices: {}, savedVoices: [], pickerModel: 'kokoro', refresh: false };
try { const saved = JSON.parse(localStorage.getItem('readroom:picker') || '{}'); state.pickerModel = saved.model || 'kokoro'; state.choices = saved.choices || {}; } catch {}
state.highlightMode = ['word', 'sentence', 'none'].includes(localStorage.getItem('readroom:highlight')) ? localStorage.getItem('readroom:highlight') : 'word';
$('#highlight-mode').value = state.highlightMode;
$('#highlight-mode').onchange = e => {
  state.highlightMode = e.target.value;
  localStorage.setItem('readroom:highlight', state.highlightMode);
  updatePlayer(); updateWordHighlight();
};
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
  return state.choices[model.id] ||= { voice: model.default_voice, style: model.emotions?.includes('neutral') ? 'neutral' : '' };
}
function matching(model) {
  const c = choice(model);
  return state.article?.renditions.find(r => r.model === model.id && r.voice === c.voice && r.style === c.style.trim() && r.grouped);
}
function playbackChunks() { return selectedReading()?.chunks || state.article?.chunks || []; }
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

async function refreshQueue() {
  const queue = await api('/api/queue');
  const jobs = [...(queue.active ? [queue.active] : []), ...queue.queued];
  $('#queue-count').textContent = jobs.length;
  $('#cancel-all').disabled = !jobs.length;
  const markup = jobs.length ? jobs.map((r, index) => {
    const active = r.id === queue.active?.id;
    const model = state.models.find(m => m.id === r.model);
    return `<div class="queue-job ${active ? 'active' : ''}"><span class="queue-badge">${active ? r.status === 'loading' ? 'LOADING' : 'RENDERING' : `QUEUED ${index + (queue.active ? 0 : 1)}`}</span><div class="queue-job-info"><button class="queue-job-title" data-open-job="${r.article_id}">${escape(r.title)}</button><small>${escape(model?.name || r.model)} · ${escape(voiceName(r))} · ${r.ready}/${r.total} ranges saved</small><div class="card-progress"><div style="width:${r.ready / r.total * 100}%"></div></div></div><button class="button outline small queue-cancel" data-cancel-job="${r.id}">${active ? 'Stop now' : 'Cancel'}</button></div>`;
  }).join('') : '<p class="queue-empty">Nothing rendering. Your GPU can take a breath.</p>';
  if ($('#queue-jobs').innerHTML !== markup) {
    $('#queue-jobs').innerHTML = markup;
    $$('[data-open-job]').forEach(b => b.onclick = () => openArticle(b.dataset.openJob).catch(e => toast(e.message)));
    $$('[data-cancel-job]').forEach(b => b.onclick = async () => {
      b.disabled = true;
      try {
        await api(`/api/renditions/${b.dataset.cancelJob}/cancel`, {});
        $('#queue-feedback').textContent = 'Render cancelled. Completed audio is kept.';
        await Promise.all([refreshQueue(), refreshArticle()]);
      } catch (e) { toast(e.message); b.disabled = false; }
    });
  }
}
$('#cancel-all').onclick = async () => {
  $('#cancel-all').disabled = true;
  try {
    const result = await api('/api/queue/cancel', {});
    $('#queue-feedback').textContent = `${result.cancelled} render${result.cancelled === 1 ? '' : 's'} cancelled. Completed audio is kept.`;
    await Promise.all([refreshQueue(), refreshArticle()]);
  } catch (e) { toast(e.message); $('#cancel-all').disabled = false; }
};
function pickerModel() {
  return state.models.find(m => m.id === state.pickerModel) || state.models[0];
}
function selectionLabel(s) {
  const m = state.models.find(m => m.id === s.model);
  return `${m?.name || s.model} · ${voiceName(s)}${s.style ? ' · ' + s.style : ''}`;
}
function renderSavedVoices() {
  const active = pickerModel();
  const c = active && choice(active);
  const markup = state.savedVoices.length ? state.savedVoices.map(s => {
    const selected = s.model === active?.id && s.voice === c.voice && s.style === c.style.trim();
    const label = selectionLabel(s);
    return `<div class="saved-voice ${selected ? 'active' : ''}"><button class="star-button ${s.starred ? 'starred' : ''}" data-star="${s.id}" aria-label="${s.starred ? 'Unstar' : 'Star'} ${escape(label)}" aria-pressed="${!!s.starred}">${s.starred ? '★' : '☆'}</button><button class="saved-voice-select" data-preset="${s.id}" aria-pressed="${selected}">${escape(label)}</button><button class="remove-voice" data-remove-voice="${s.id}" aria-label="Remove saved voice ${escape(label)}">×</button></div>`;
  }).join('') : '<p class="field-note">Save a family and voice below to use it on any article. Star your favorites to keep them first.</p>';
  if ($('#voice-selections').innerHTML !== markup) {
    $('#voice-selections').innerHTML = markup;
    $$('[data-preset]').forEach(b => b.onclick = () => {
      const s = state.savedVoices.find(v => v.id === b.dataset.preset);
      state.pickerModel = s.model;
      state.choices[s.model] = { voice: s.voice, style: s.style };
      persistPicker(); renderCards();
    });
    $$('[data-star]').forEach(b => b.onclick = async () => {
      b.disabled = true;
      try {
        const s = state.savedVoices.find(v => v.id === b.dataset.star);
        await api(`/api/voices/${s.id}/star`, { starred: !s.starred });
        await refreshVoices();
      } catch (e) { toast(e.message); b.disabled = false; }
    });
    $$('[data-remove-voice]').forEach(b => b.onclick = async () => {
      b.disabled = true;
      try {
        const response = await fetch(`/api/voices/${b.dataset.removeVoice}`, { method: 'DELETE' });
        if (!response.ok) throw new Error('Could not remove this saved voice.');
        await refreshVoices(); updateCards();
      } catch (e) { toast(e.message); b.disabled = false; }
    });
  }
  const count = state.models.filter(m => m.downloaded && m.installed).reduce((n, m) => n + m.voices.length, 0);
  $('#render-combinations').disabled = Boolean(state.bulkRendering) || !count || !state.article;
  $('#render-combinations').textContent = state.bulkRendering ? 'Queuing combinations…' : `Render every combination (${count})`;
  $('#render-all').disabled = Boolean(state.bulkRendering) || !state.savedVoices.some(s => state.models.some(m => m.id === s.model && m.downloaded && m.installed));
}
async function refreshVoices() {
  state.savedVoices = await api('/api/voices');
  renderSavedVoices();
}
function persistPicker() {
  localStorage.setItem('readroom:picker', JSON.stringify({ model: state.pickerModel, choices: state.choices }));
}
function renderCards() {
  const model = pickerModel(); if (!model) return;
  state.pickerModel = model.id;
  const c = choice(model);
  const families = [...new Set(state.models.map(m => m.family))];
  const variants = state.models.filter(m => m.family === model.family);
  $('#model-cards').innerHTML = `<article class="model-card voice-picker" data-model="${model.id}">
    <div class="picker-fields">
      <div><label for="family-picker">Family</label><select id="family-picker">${families.map(f => `<option ${f === model.family ? 'selected' : ''}>${escape(f)}</option>`).join('')}</select></div>
      <div ${variants.length === 1 ? 'hidden' : ''}><label for="variant-picker">Model</label><select id="variant-picker">${variants.map(m => `<option value="${m.id}" ${m.id === model.id ? 'selected' : ''}>${escape(m.variant)}</option>`).join('')}</select></div>
      <div><label for="voice-${model.id}">Voice</label><select id="voice-${model.id}" data-voice="${model.id}">${model.voices.map(v => `<option value="${v.id}" ${v.id === c.voice ? 'selected' : ''}>${escape(v.name)}</option>`).join('')}</select></div>
    </div>
    <p class="description">${escape(model.description)} <span class="model-size">${escape(model.size)}</span></p>
    <p class="field-note capabilities">${escape(model.capabilities || '')}</p>
    ${model.styles ? `<div class="style-details"><label for="style-${model.id}">${escape(model.style_label || 'Delivery')}</label>${model.style_options || model.emotions ? `<select id="style-${model.id}">${(model.style_options || model.emotions).map(v => `<option value="${escape(v)}" ${v === c.style ? 'selected' : ''}>${escape(v || 'None — natural narration')}</option>`).join('')}</select>` : `<textarea aria-label="Delivery instructions" id="style-${model.id}" maxlength="400" placeholder="e.g. Warm, calm audiobook narration">${escape(c.style)}</textarea>`}<p class="field-note">${escape(model.style_help || '')}</p></div>` : ''}
    <div class="model-actions"><button class="button primary" id="save-voice">Save voice</button><button class="button listen-button" data-listen="${model.id}">▶ Listen</button><button class="button render-button" data-render="${model.id}">Render article</button></div><button class="button outline small timing-button" data-timings="${model.id}" hidden>Generate word timings</button>
    <div class="card-status"><span class="render-status"></span><a class="download" hidden>WAV ↓</a></div><div class="card-progress"><div></div></div><div class="card-metrics"></div><div class="card-error" hidden></div>
  </article>`;
  $('#family-picker').onchange = e => {
    state.pickerModel = state.models.find(m => m.family === e.target.value).id;
    persistPicker(); renderCards();
  };
  $('#variant-picker').onchange = e => {
    const next = state.models.find(m => m.id === e.target.value);
    if (next.voices.some(v => v.id === c.voice)) choice(next).voice = c.voice;
    state.pickerModel = next.id; persistPicker(); renderCards();
  };
  $(`[data-voice]`).onchange = e => { c.voice = e.target.value; persistPicker(); updateCards(); };
  if (model.styles) $(`#style-${model.id}`).oninput = e => { c.style = e.target.value; persistPicker(); updateCards(); };
  $('#save-voice').onclick = async () => {
    $('#save-voice').disabled = true;
    try { await api('/api/voices', { model: model.id, ...c }); await refreshVoices(); updateCards(); toast('Voice saved. Use it on any article, or star it above.'); }
    catch (e) { toast(e.message); $('#save-voice').disabled = false; }
  };
  $('[data-listen]').onclick = () => listenModel(model.id).catch(e => toast(e.message));
  $('[data-render]').onclick = () => renderModel(model.id).catch(e => toast(e.message));
  $('[data-timings]').onclick = e => generateTimings(matching(model), e.currentTarget).catch(err => toast(err.message));
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
    label.textContent = !available ? 'Model not installed' : !r ? 'Ready to read' : ({ queued: 'Queued · waiting for its turn', loading: 'Loading voice into memory…', generating: `${r.ready}/${r.total} chunks saved`, complete: `✓ Full reading saved · ${formatTime(r.audio_seconds)}`, paused: `Stopped · ${r.ready}/${r.total} saved`, error: 'Needs a retry' }[r.status]);
    if (r && state.engine?.rendition === r.id && state.engine.phase === 'aligning') label.textContent = 'Mapping words to saved audio…';
    const needsTimings = model.id === 'kokoro' && r?.status === 'complete' && !r.has_word_timings;
    render.textContent = needsTimings ? 'Enable word highlighting' : !r ? 'Render article' : working ? 'Stop rendering' : r.status === 'complete' ? 'Saved ✓' : 'Resume article';
    render.disabled = !available || (r?.status === 'complete' && !needsTimings);
    if (model.id !== 'kokoro' && r && r.ready === r.total && !working) {
      render.textContent = 'Audio saved ✓'; render.disabled = true;
    }
    card.querySelector('[data-listen]').disabled = !available;
    updateTimingButton(card.querySelector('[data-timings]'), r, model.id);
    card.querySelector('.card-progress div').style.width = `${r ? r.ready / r.total * 100 : 0}%`;
    card.querySelector('.card-metrics').textContent = r?.ready ? `${r.generation_seconds ? (r.audio_seconds / r.generation_seconds).toFixed(1) + '× real-time' : ''} · ${r.peak_vram_gb.toFixed(1)} GB peak VRAM` : model.license + ' · English';
    const link = card.querySelector('.download'); link.hidden = r?.status !== 'complete'; if (r) link.href = `/api/renditions/${r.id}/download`;
    const error = card.querySelector('.card-error'); error.hidden = !r?.error; error.textContent = r?.error || '';
  }
  updateTimingButton($('#generate-timings'), selectedReading());
  renderSavedVoices();
  const current = pickerModel();
  if (current && $('#save-voice')) {
    const c = choice(current);
    const saved = state.savedVoices.some(s => s.model === current.id && s.voice === c.voice && s.style === c.style.trim());
    $('#save-voice').disabled = saved; $('#save-voice').textContent = saved ? 'Voice saved ✓' : 'Save voice';
  }
  const readings = state.article?.renditions || [];
  const saved = $('#saved-readings');
  const markup = readings.length ? '<span class="saved-label">READINGS</span>' + readings.map(r => `<button class="reading-chip ${r.id === state.selected ? 'active' : ''}" data-reading="${r.id}">${escape(state.models.find(m => m.id === r.model)?.name)} · ${escape(voiceName(r))}${r.grouped ? ' · 3¶' : ' · original'} ${r.status === 'complete' ? '✓' : r.ready + '/' + r.total}</button>`).join('') : '';
  if (saved.innerHTML !== markup) {
    saved.innerHTML = markup;
    $$('[data-reading]').forEach(b => b.onclick = () => {
      const r = state.article.renditions.find(x => x.id === b.dataset.reading);
      state.choices[r.model] = { voice: r.voice, style: r.style };
      state.pickerModel = r.model; persistPicker();
      selectReading(r.id); renderCards(); updatePlayer();
    });
  }
}

function updateTimingButton(button, reading, modelId = reading?.model) {
  button.hidden = !modelId || modelId === 'kokoro';
  const busy = reading?.timing_requested && ['queued', 'loading', 'generating'].includes(reading.status);
  const audioReady = reading && reading.ready === reading.total;
  button.disabled = !audioReady || !!reading?.has_word_timings || !!busy || ['queued', 'loading', 'generating'].includes(reading?.status);
  button.textContent = reading?.has_word_timings ? 'Word timings ready ✓' : busy ? 'Generating word timings…' : 'Generate word timings';
  button.title = !audioReady ? 'Render the full article first.' : 'Map words to this saved recording for highlighting and click-to-seek.';
}
async function generateTimings(reading, button) {
  if (!reading) return;
  button.disabled = true;
  try {
    await api(`/api/renditions/${reading.id}/timings`, {});
    await Promise.all([refreshArticle(), refreshQueue()]);
    toast('Word timings queued. Your saved audio stays unchanged.');
  } finally { updateCards(); }
}
$('#generate-timings').onclick = e => generateTimings(selectedReading(), e.currentTarget).catch(err => toast(err.message));

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
  let sourceOffset = 0;
  $('#article-body').innerHTML = doc.paragraphs.map((p, i) => {
    const segments = sentenceSegments(p);
    const content = segments.map(segment => {
      const words = segment.segment.replace(/\S+|\s+/gu, (word, index) => {
        if (/^\s/u.test(word)) return word;
        const start = sourceOffset + Array.from(p.slice(0, segment.index + index)).length;
        return `<span class="article-word" data-word-start="${start}">${escape(word)}</span>`;
      });
      return `<span class="article-sentence">${words}</span>`;
    }).join('');
    sourceOffset += Array.from(p).length + 1;
    return `<p class="article-paragraph" data-paragraph="${i}" tabindex="0" title="Listen from this paragraph">${content}</p>`;
  }).join('');
  $$('[data-paragraph]').forEach(p => {
    const seek = event => {
      if (!state.selected) return toast('Choose a narrator with Listen first.');
      const word = event.target.closest('[data-word-start]');
      const target = word && state.wordTargets?.get(Number(word.dataset.wordStart));
      unlockAudio();
      if (target) {
        state.chunk = target.chunk;
        state.resumeTime = target.time;
      } else {
        state.chunk = Math.max(0, playbackChunks().findIndex(c => c.paragraph <= Number(p.dataset.paragraph) && (c.paragraph_last ?? c.paragraph) >= Number(p.dataset.paragraph)));
        state.resumeTime = 0;
        if (word) toast('Word timing is unavailable here. Starting at the paragraph range.');
      }
      startPlayback();
    };
    p.onclick = seek;
    p.onkeydown = e => {
      if (e.key === 'Enter' || (e.key === ' ' && e.target.matches('[data-word-start]'))) {
        e.preventDefault(); seek(e);
      }
    };
  });
  renderLibrary(); renderCards();
  if (doc.renditions.length) selectReading((doc.renditions.find(r => r.grouped && r.model === 'kokoro') || doc.renditions.find(r => r.grouped) || doc.renditions[0]).id);
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
  const oldParagraph = playbackChunks()[state.chunk]?.paragraph || 0;
  stopPlayback(); state.selected = id;
  let stored = {};
  try { stored = JSON.parse(localStorage.getItem(`position:${id}`) || '{}'); } catch {}
  state.chunk = samePosition ? Math.max(0, playbackChunks().findIndex(c => c.paragraph <= oldParagraph && (c.paragraph_last ?? c.paragraph) >= oldParagraph)) : Math.min(stored.chunk || 0, playbackChunks().length - 1);
  state.resumeTime = samePosition ? 0 : stored.time || 0;
  $('#seek').value = 0;
  $('#time-label').textContent = `${formatTime(state.resumeTime)} / ${formatTime(selectedReading()?.clips[state.chunk]?.duration)}`;
  $('#player').hidden = false; updateCards(); updatePlayer();
}
async function queueModel(id, articleId = state.article.id, savedChoice = null) {
  const model = state.models.find(m => m.id === id);
  if (!savedChoice && model.styles && $(`#style-${id}`)) choice(model).style = $(`#style-${id}`).value;
  const result = await api(`/api/articles/${articleId}/renditions`, { model: id, ...(savedChoice || choice(model)) });
  await Promise.all([refreshArticle(), refreshQueue()]);
  return result.id;
}
async function renderModel(id) {
  const r = matching(state.models.find(m => m.id === id));
  if (r && ['queued', 'loading', 'generating'].includes(r.status)) {
    await api(`/api/renditions/${r.id}/cancel`, {});
    toast('Render cancelled. Completed audio is kept.'); await refreshQueue();
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
  const chunk = playbackChunks()[state.chunk];
  const p = chunk?.paragraph || 0;
  const last = chunk?.paragraph_last ?? p;
  const range = last > p ? `Paragraphs ${p + 1}–${last + 1}` : `Paragraph ${p + 1}`;
  $('#chunk-label').textContent = `${range} of ${state.article.paragraphs.length}`;
  $('#reading-progress').textContent = `${model.name} · ${range.toLowerCase()} of ${state.article.paragraphs.length}`;
  $$('[data-paragraph]').forEach(el => el.classList.toggle('active', state.highlightMode !== 'none' && Number(el.dataset.paragraph) >= p && Number(el.dataset.paragraph) <= last));
  updateWordTargets();
  updateWordHighlight();
  $('#previous').disabled = state.chunk === 0; $('#next').disabled = state.chunk === playbackChunks().length - 1;
}
audio.onended = () => {
  if (!state.playing || !state.source) return;
  if (state.chunk + 1 >= playbackChunks().length) {
    state.playing = false; state.chunk = 0; state.source = ''; audio.currentTime = 0; savePosition(); updatePlayer(); toast('Reading complete. A little more room in your day.'); return;
  }
  const chunk = playbackChunks()[state.chunk];
  const pause = chunk.pause_ms ?? (chunk.paragraph_end ? 250 : 0);
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
$('#next').onclick = () => { if (state.chunk < playbackChunks().length - 1) { state.chunk++; state.resumeTime = 0; startPlayback(); } };
$('#seek').oninput = e => { if (Number.isFinite(audio.duration)) audio.currentTime = Number(e.target.value) / 100 * audio.duration; };
$('#speed').onchange = () => { audio.playbackRate = Number($('#speed').value); };
$('#render-combinations').onclick = async () => {
  if (state.bulkRendering || !state.article) return;
  state.bulkRendering = true;
  const articleId = state.article.id;
  const combinations = state.models.filter(m => m.downloaded && m.installed).flatMap(m => m.voices.map(v => ({ model: m.id, voice: v.id, style: '' })));
  renderSavedVoices();
  let queued = 0;
  const failures = [];
  try {
    for (const combination of combinations) {
      try { await api(`/api/articles/${articleId}/renditions`, combination); queued++; }
      catch (e) { failures.push(e.message); }
    }
    await Promise.all([refreshArticle(), refreshQueue()]);
    toast(`${queued} combinations queued or already saved, using default delivery.${failures.length ? ` ${failures.length} failed: ${failures[0]}` : ''}`);
  } finally { state.bulkRendering = false; renderSavedVoices(); }
};
$('#render-all').onclick = async () => {
  $('#render-all').disabled = true;
  const articleId = state.article.id;
  try { for (const s of [...state.savedVoices]) { if (state.models.some(m => m.id === s.model && m.downloaded && m.installed)) await queueModel(s.model, articleId, { voice: s.voice, style: s.style }); } toast('Saved voices queued for this article.'); }
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
    const status = await api('/api/status'); state.models = status.models; state.engine = status.engine;
    $('#machine-status').textContent = status.engine.phase !== 'idle' ? `${status.models.find(m => m.id === status.engine.model)?.name || 'Model'} · ${status.engine.phase}` : `${status.models.filter(m => m.downloaded).length} models on this machine`;
    await Promise.all([refreshArticle(), refreshQueue(), refreshVoices()]);
  } catch (e) { $('#machine-status').textContent = 'Connection lost · retrying…'; }
  finally { state.refresh = false; }
}
async function init() {
  try {
    const status = await api('/api/status'); state.models = status.models; state.engine = status.engine;
    await Promise.all([refreshLibrary(), refreshVoices()]);
    const last = localStorage.getItem('readroom:article');
    if (state.articles.length) await openArticle(state.articles.find(a => a.id === last)?.id || state.articles[0].id);
    await poll();
  } catch (e) { toast(e.message); }
  setInterval(poll, 1500);
}
window.addEventListener('beforeunload', savePosition);
init();

// Read the media clock every frame: follows seeking and playback-rate changes.
let highlightedWord = null;
let highlightedSentence = null;
function sentenceSegments(text) {
  const segments = typeof Intl.Segmenter === 'function'
    ? [...new Intl.Segmenter('en', { granularity: 'sentence' }).segment(text)]
    : [...text.matchAll(/[^.!?]+[.!?]+(?:[”"']*)\s*|.+$/gu)].map(m => ({ segment: m[0], index: m.index }));
  const merged = [];
  for (const segment of segments) {
    const previous = merged[merged.length - 1];
    if (previous && /(?:\b(?:Dr|Mr|Mrs|Ms|Prof|St|vs|No)|\b[A-Z]|\be\.g|\bi\.e)\.\s*$/u.test(previous.segment)) previous.segment += segment.segment;
    else merged.push({ segment: segment.segment, index: segment.index });
  }
  return merged;
}
function updateWordHighlight() {
  const reading = selectedReading();
  const clip = reading?.clips[state.chunk];
  const words = clip?.words;
  const offset = reading?.chunk_offsets?.[state.chunk];
  let target = null, sentence = null;
  if (state.highlightMode !== 'none' && state.source === clip?.url && !state.waiting && words?.length && offset != null) {
    const time = audio.currentTime;
    let low = 0, high = words.length;
    while (low < high) {
      const mid = (low + high) >> 1;
      if (words[mid].start_time <= time) low = mid + 1; else high = mid;
    }
    const word = words[low - 1];
    if (word) {
      const el = document.querySelector(`[data-word-start="${offset + word.start}"]`);
      if (state.highlightMode === 'word' && time < word.end_time) target = el;
      if (state.highlightMode === 'sentence' && el) {
        const current = el.closest('.article-sentence');
        const next = words[low] && document.querySelector(`[data-word-start="${offset + words[low].start}"]`);
        // Keep the sentence lit across pauses between its words, but clear it at its end.
        if (time < word.end_time || next?.closest('.article-sentence') === current) sentence = current;
      }
    }
  }
  if (target !== highlightedWord) {
    highlightedWord?.classList.remove('spoken-word');
    target?.classList.add('spoken-word');
    highlightedWord = target;
  }
  if (sentence !== highlightedSentence) {
    highlightedSentence?.classList.remove('spoken-sentence');
    sentence?.classList.add('spoken-sentence');
    highlightedSentence = sentence;
  }
}
function animateWordHighlight() {
  updateWordHighlight();
  requestAnimationFrame(animateWordHighlight);
}
// Media events also update while a browser throttles background animation frames.
for (const event of ['timeupdate', 'seeked', 'loadedmetadata', 'pause', 'ended']) audio.addEventListener(event, updateWordHighlight);
requestAnimationFrame(animateWordHighlight);

function updateWordTargets() {
  const reading = selectedReading();
  state.wordTargets = new Map();
  reading?.clips.forEach((clip, chunk) => {
    const offset = reading.chunk_offsets?.[chunk];
    if (offset == null) return;
    for (const word of clip?.words || []) state.wordTargets.set(offset + word.start, { chunk, time: word.start_time });
  });
  for (const el of $$('.article-word')) {
    const timed = state.wordTargets.has(Number(el.dataset.wordStart));
    if (el.classList.contains('seekable-word') === timed) continue;
    el.classList.toggle('seekable-word', timed);
    if (timed) {
      el.setAttribute('role', 'button');
      el.setAttribute('tabindex', '0');
      el.setAttribute('title', 'Read from this word');
      el.setAttribute('aria-label', `Read from “${el.textContent}”`);
    } else {
      for (const attr of ['role', 'tabindex', 'title', 'aria-label']) el.removeAttribute(attr);
    }
  }
}
