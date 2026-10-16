# Readroom

A local English article reader and TTS comparison MVP. Fetch an article or paste text, save it once, and render the **entire article** with Kokoro, Chatterbox Turbo, both Qwen3-TTS sizes, three Kitten sizes, Pocket TTS, and Paradee. Each voice/delivery setting gets its own saved rendition. The comparison sample includes prose, dialogue, dates/numbers, money, and abbreviations.

## Open the app

Public test URL: https://readroom.remoth.net

Local URL: http://localhost:28765

```bash
./scripts/start.sh
```

For a persistent user service:

```bash
./scripts/install_service.sh
systemctl --user status readroom
systemctl --user restart readroom
systemctl --user stop readroom
journalctl --user -u readroom -f
```

The service binds to loopback port 28765. The existing remotely managed Cloudflare tunnel `tgl` publishes it at `https://readroom.remoth.net` through a proxied CNAME and a public-hostname route to `http://localhost:28765`. This test deployment has no login; anyone with the URL can access the library and generate readings.

## Reading and comparison

1. **Add an article** accepts a public HTML URL or pasted text. The cleaned article, title, source URL, and paragraph boundaries are saved locally. Login/paywall/JavaScript-only pages may require pasted text.
2. Choose a **family**, a model variant where available, and a **voice**. **Save voice** stores the combination for every article; use **☆ / ★** to toggle favorites, which sort first. The × removes a saved selection without deleting its audio. Choose **Render article**, or **Render saved voices** to queue your saved combinations. The first section of every queued voice gets priority. A newly queued voice follows other pending first sections, then remaining sections resume in original queue order. The active section finishes before switching, and only one synthesis job runs at a time. Switching families may require loading a different model.
3. **Listen** starts at the current paragraph range when changing models, making the same passage easy to compare. It also queues a rendition if needed. Saved-reading chips select previous versions; playback positions persist in this browser.
4. The player preloads the next completed chunk. New readings group three paragraphs per range (1,950 character safety limit, with sentence/word splitting for unusually long passages), with no added gap between ranges. Older saved readings remain playable. Playback speed changes preserve pitch.
5. Click a paragraph to jump to its range. The global **Render queue** shows active and waiting jobs across articles. **Stop now** kills the active inference process immediately; **Cancel** removes a queued job; **Stop all** cancels both. Completed audio is retained for resuming. Closing the browser does not stop generation. Restarting the service recovers unfinished queued jobs.
6. **WAV ↓** exports a whole rendition once complete, with the same range boundaries. **Export text** downloads the saved article JSON.

This is progressive paragraph audio, not token-level streaming. If a model generates more slowly than playback, the player waits at a chunk boundary. For uninterrupted listening, render the full article first. Prosody can reset between ranges. Kokoro also splits internally at its token limit; Chatterbox uses smaller internal sentence groups to avoid truncation. Displayed real-time speed measures synthesis only; it excludes model loading, word alignment, and queue wait.

Three original sample articles cover humor, suspense, and science. Add them with ` .venv-server/bin/python scripts/add_samples.py --render` (queues Kokoro).

## Local models

| Model | Voices exposed | Weights | License |
| --- | --- | --- | --- |
| Kitten Mini 80M | Eight voices | ~78 MiB | Apache-2.0 |
| Kitten Micro 40M | Eight voices | ~43 MiB | Apache-2.0 |
| Kitten Nano 15M int8 | Eight voices | ~27 MiB | Apache-2.0 |
| Pocket TTS 100M | 21 English voices | ~367 MiB | MIT; voice terms vary |
| Paradee 8M int8 | Heart | ~9 MiB | Apache-2.0 |
| Kokoro 82M | Heart, Bella, Michael, Emma, George | ~315 MB | Apache-2.0 |
| Chatterbox Turbo 350M | Built-in narrator | ~2.8 GB | MIT |
| Qwen3-TTS 0.6B CustomVoice | Ryan, Aiden | ~2.4 GB | Apache-2.0 |
| Qwen3-TTS 1.7B CustomVoice | Ryan, Aiden; delivery instructions | ~4.3 GB | Apache-2.0 |

The 0.6B Qwen model uses fixed narrator delivery; the installed Qwen adapter only enables delivery instructions for 1.7B.

Sizes above are approximate local model directories, excluding Python environments and the shared ~361 MiB English aligner. Kitten uses the upstream ONNX-only dependency subset, intentionally omitting dependencies for KittenTTS 2.

Model revisions are pinned in `models-manifest.json`. Dependencies are pinned in the four `requirements-*.lock` files. Chatterbox needs a different Transformers version, so it has its own Python environment. The service itself stays lightweight; one separate persistent inference process holds one model, and exits on switching. GPU memory is released after two idle minutes. Kitten, Pocket, Paradee and the shared word aligner use a separate CPU-only `.venv-light` environment. Pocket uses the official public preset-voice checkpoint, without voice cloning. NeuTTS was evaluated for installation but its weights require gated access; it is not installed or exposed as a working model.

Tested on an NVIDIA RTX 3080 Laptop GPU, 16 GB VRAM, using CUDA 12.4 PyTorch wheels with the installed NVIDIA driver. Qwen uses BF16 and PyTorch SDPA; FlashAttention is not required. No reference voice upload/cloning UI is included. The new models were smoke-tested on CPU, including a 150-word, three-paragraph article and word mapping. Chatterbox uses its supplied built-in voice and retains its normal watermarking.

## Install on another Linux/NVIDIA machine

```bash
./scripts/setup.sh
./scripts/start.sh
```

Requires Python/pip to bootstrap uv, Git for pinned source dependencies, network access for setup, and an NVIDIA driver compatible with CUDA 12.4. The installer downloads Python 3.11, four isolated environments, English pronunciation resources, and the model weights. Allow roughly 20–30 GB of free space for environments, caches, and models. Workers can fall back to CPU, but Qwen/Chatterbox performance on CPU has not been tested. The web UI and fonts are local; saved articles/audio play without external requests.

## Data and implementation

- `data/readroom.sqlite3`: article library, rendition queue state, clip metrics, and saved/starred voice selections. Selections are shared across browsers using this server; the last picker choice is remembered per browser.
- `data/articles/*.json`: portable snapshots of cleaned articles, source URLs, and chunks.
- `data/audio/*.wav`: content-addressed chunks and exported full readings. Cache keys include model revision, voice, delivery instructions, and text.
- `data/logs/`: model/download logs. Normal service logs go to the systemd journal.
- `models/`: downloaded model weights and revision markers.
- `app/catalog.py`, `app/worker.py`: model registry and adapters. Add another model here and in the download manifest/script; the UI reads the catalog dynamically.
- `static/`: buildless HTML, CSS, and JavaScript; no frontend package manager required.

Article fetching accepts public HTTP(S) on standard ports only, validates every redirect, pins a validated public IP, rejects private/link-local addresses, and caps response size. Article text is rendered as text, not executable HTML. Cross-origin mutation requests are rejected. There is no telemetry or paid API integration. Fetching an article naturally contacts its source website.

Audio remains on disk until manually removed; there is no automatic cache eviction. Do not delete audio or the database while jobs are running. Back up `data/` to retain the library and readings.

## Validation

```bash
.venv-server/bin/python -m pytest -q
node --check static/app.js
```

Tests cover paragraph-preserving chunking, input validation, URL/private-network restrictions, redirect pinning, saved article deduplication, rendition stop/resume/restart behavior, voice-specific caching, full WAV assembly and paragraph pauses, and cross-origin write rejection. Real GPU generation and desktop/mobile playback are verified separately.

## Upstream

- [Kokoro](https://huggingface.co/hexgrad/Kokoro-82M)
- [Chatterbox](https://github.com/resemble-ai/chatterbox)
- [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS)
- [Trafilatura](https://github.com/adbar/trafilatura)

Bundled fonts are DM Sans and Libre Caslon Text, under the SIL Open Font License; licenses are in `static/fonts/`.

## Word timing and targeted audio effects

Kokoro English exposes token start/end timestamps derived from predicted phoneme durations. Readroom saves these alongside each clip in `.words.json`, merges token pieces into displayed words, and maps chunk-relative offsets back to the original article. The reader’s **Highlight** setting chooses **Word**, **Sentence**, or **None** and is remembered in this browser. Word and sentence highlighting follow the audio clock, including seeking and playback-speed changes. None disables playback highlighting while preserving click-to-seek. Sentence mode groups the source text into sentences and uses available word timings. Click a timed word (or focus it and press Enter/Space) to start playback at its timestamp, across paragraph ranges. Untimed text falls back to the paragraph range. Three-paragraph synthesis stays continuous. Unmatched tokens are skipped rather than assigned guessed positions.

Existing Kokoro audio predates this metadata. **Enable word highlighting** or **Listen** queues regeneration of ranges missing timings; already timed ranges are reused. Other installed models offer **Generate word timings** after the full article has rendered. This explicitly queues the shared local Wav2Vec2 English CTC forced aligner and enables the same word highlighting and click-to-seek support without regenerating audio. The button appears below the voice picker and beside the reader’s Highlight setting for the selected reading. Rendering or listening alone does not start alignment. Existing timings remain available. Low-confidence word matches are omitted, and unmatched words retain paragraph-range seeking. Word mapping is approximate, especially for names, unusual pronunciation and normalization differences. Timestamps are approximate acoustic boundaries, not perfect separation of neighboring sounds.

Research checked 2026-10-16: the official Qwen3-TTS 0.6B/1.7B synthesis interfaces return waveforms and sample rate, not word timings; Chatterbox Turbo returns a waveform without a supported word-timestamp output. This does not mean alignment is impossible. [Qwen3-ForcedAligner-0.6B](https://github.com/QwenLM/Qwen3-ASR#forcedaligner-usage) is a separate local model that takes existing audio plus its text and returns word/character timestamps, including English. It is not installed here; Readroom instead uses `facebook/wav2vec2-base-960h` with the pinned TorchAudio 2.8 forced-alignment API for English.

Future link effects should preserve link destinations and exact text spans during article extraction, then map those spans to word timings. Keep narration continuous and fade effects across selected intervals; let effect tails decay over subsequent audio. Test contractions, numbers, repeated words, and timing at phrase boundaries before relying on these timestamps for precise audio edits. Effect selection remains open.

Upstream timing references: [Kokoro pipeline](https://github.com/hexgrad/kokoro/blob/main/kokoro/pipeline.py), [Qwen TTS interface](https://github.com/QwenLM/Qwen3-TTS/blob/main/qwen_tts/inference/qwen3_tts_model.py), [Chatterbox Turbo interface](https://github.com/resemble-ai/chatterbox/blob/master/src/chatterbox/tts_turbo.py).

## Shared English word alignment

`app/alignment.py` normalizes source words while preserving their Unicode character spans. Numbers and currencies may expand to multiple spoken words but map to the original source span. The aligner analyzes overlapping audio windows to bound memory, then aligns the full character transcript and omits low-confidence matches. It runs in a separate persistent CPU process, supports immediate cancellation, and unloads with the synthesis worker. Alignment is a separate, explicitly requested queue job after audio is saved. It survives restarts and can be cancelled or retried without losing or regenerating audio.

Kokoro continues to use its native phoneme-duration timings. Alignment adds processing time for other models and does not establish perfect acoustic boundaries or prove that every word was spoken.

Upstream: [Kitten ONNX models](https://github.com/KittenML/KittenTTS/blob/main/docs/onnx-models.md), [Pocket TTS](https://github.com/kyutai-labs/pocket-tts), [Paradee](https://github.com/sahilmahendrakar/paradee), [English alignment weights](https://huggingface.co/facebook/wav2vec2-base-960h), [TorchAudio forced alignment](https://docs.pytorch.org/audio/2.8/tutorials/ctc_forced_alignment_api_tutorial.html).

### NeuTTS-2E

The NeuTTS family includes Q4 and Q8 GGUF variants, each with Emily, Paul, Sophie, and Steven. The generation box saves the chosen emotion (neutral, happy, sad, angry, fearful, disgusted, or surprised) with the voice. These fixed-speaker models do not support cloning; NeuTTS Air and Nano are separate cloning models. Word timings can be generated after rendering using the existing English aligner. The bulk button includes each installed variant/speaker once with neutral delivery.

Accept access conditions for `neuphonic/neutts-2e-q4-gguf`, `neuphonic/neutts-2e-q8-gguf`, and `neuphonic/neucodec-onnx-decoder-int8`, then authenticate with `.venv-server/bin/hf auth login` before running setup. Downloads are pinned in `models-manifest.json`. The isolated CPU runtime uses `requirements-neutts.lock`; inference loads local GGUF and ONNX assets offline. Long sections are split into shorter spans to respect NeuTTS's context limit. Model outputs retain the upstream watermark.

### Auditions and comparison tools

Bulk rendering defaults to **First section only**; select **Full article** to render every section. Each voice also has an **Audition first section** button. **Continue full article** creates a full reading that reuses the audition's cached first section. Auditions remain available as separate saved readings.

Switching saved readings or using Listen on another voice seeks to the start of the current sentence when both recordings have word timings. Playback continues if it was playing. Without timings, switching falls back to the corresponding section with an explanatory message.

Personal thumbs-up/down ratings and notes are saved server-side per model, voice, and delivery selection. Pronunciation overrides are global, exact, case-sensitive whole-word/phrase replacements (`original = spoken spelling`). Each new reading snapshots these rules; prior recordings and source article text remain unchanged. Timing metadata is mapped back to source spellings for highlighting and seeking.

Queue estimates use measured synthesis seconds per character from recent cached recordings of the same model; speed is shown as audio duration divided by generation time. Estimates update after completed sections and exclude model loading and alignment. Models without measured samples show an unknown estimate.
