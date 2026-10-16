"""One persistent model per subprocess; stdout is a JSON-line protocol."""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.text import split_paragraphs
from app.timing import kokoro_words
protocol = sys.stdout
sys.stdout = sys.stderr
os.environ.setdefault('HF_HUB_DISABLE_XET', '1')
os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')
os.environ.setdefault('HF_HOME', str(ROOT / 'models' / '.hf'))
os.environ.setdefault('HF_HUB_OFFLINE', '1')
os.environ.setdefault('TRANSFORMERS_OFFLINE', '1')


def send(value):
    protocol.write(json.dumps(value) + '\n')
    protocol.flush()


def main():
    import numpy as np
    import soundfile as sf
    import torch
    torch.set_num_threads(4)
    kind = sys.argv[1]
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    path = ROOT / 'models' / kind
    started = time.monotonic()
    if kind == 'aligner':
        from app.alignment import EnglishAligner
        device = 'cpu'
        model = EnglishAligner()
    elif kind == 'kokoro':
        import espeakng_loader
        from phonemizer.backend.espeak.wrapper import EspeakWrapper
        EspeakWrapper.set_library(espeakng_loader.get_library_path())
        os.environ['ESPEAK_DATA_PATH'] = espeakng_loader.get_data_path()
        from kokoro import KModel, KPipeline
        model = KModel(config=str(path / 'config.json'), model=str(path / 'kokoro-v1_0.pth')).to(device).eval()
        pipelines = {lang: KPipeline(lang_code=lang, model=model, device=device) for lang in ('a', 'b')}
    elif kind in ('qwen', 'qwen-small'):
        from qwen_tts import Qwen3TTSModel
        model = Qwen3TTSModel.from_pretrained(str(path), device_map=device, dtype=torch.bfloat16 if device == 'cuda' else torch.float32, attn_implementation='sdpa')
    elif kind == 'chatterbox':
        from chatterbox.tts_turbo import ChatterboxTurboTTS
        model = ChatterboxTurboTTS.from_local(str(path), device=device)
    elif kind.startswith('kitten-'):
        from kittenml import KittenTTS
        device = 'cpu'
        model = KittenTTS(str(path), backend='cpu')
    elif kind == 'paradee':
        import espeakng_loader
        from phonemizer.backend.espeak.wrapper import EspeakWrapper
        EspeakWrapper.set_library(espeakng_loader.get_library_path())
        os.environ['ESPEAK_DATA_PATH'] = espeakng_loader.get_data_path()
        from paradee import Paradee
        device = 'cpu'
        model = Paradee(model_path=str(path / 'onnx/paradee_int8.onnx'), config_path=str(path / 'config.json'), threads=4)
    elif kind == 'pocket':
        import tempfile
        import yaml
        from pocket_tts import TTSModel
        from pocket_tts.utils.config import CONFIGS_DIR
        device = 'cpu'
        config = yaml.safe_load((CONFIGS_DIR / 'english.yaml').read_text())
        config['weights_path'] = str(path / 'languages/english/model.safetensors')
        config['weights_path_without_voice_cloning'] = None
        config['flow_lm']['lookup_table']['tokenizer_path'] = str(path / 'languages/english/tokenizer.json')
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml') as temp:
            yaml.safe_dump(config, temp)
            temp.flush()
            model = TTSModel.load_model(config=temp.name)
        voices = {}
    else:
        raise ValueError('Unknown model')
    send({'ready': True, 'load_seconds': time.monotonic() - started, 'device': device})
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if kind == 'aligner':
                send({'ok': True, 'words': model.align(request['text'], request['audio'])})
                continue
            started = time.monotonic()
            torch.manual_seed(42)
            if device == 'cuda':
                torch.cuda.reset_peak_memory_stats()
            words = None
            with torch.no_grad():
                if kind == 'kokoro':
                    voice = str(path / 'voices' / (request['voice'] + '.pt'))
                    outputs = list(pipelines[request['voice'][0]](request['text'], voice=voice, speed=1))
                    samples = np.concatenate([o.audio.detach().cpu().numpy() for o in outputs])
                    sr = 24000
                    words = kokoro_words(request['text'], outputs, sr)
                elif kind in ('qwen', 'qwen-small'):
                    audio, sr = model.generate_custom_voice(text=request['text'], language='English', speaker=request['voice'], instruct=(request.get('style') or 'Read naturally and clearly, with calm pacing. Read the text exactly.') if kind == 'qwen' else None, max_new_tokens=2048)
                    samples = audio[0]
                elif kind.startswith('kitten-'):
                    samples = model.generate(request['text'], voice=request['voice'], clean_text=True)
                    sr = 24000
                elif kind == 'paradee':
                    samples = model(request['text'])
                    sr = 24000
                elif kind == 'pocket':
                    voice = request['voice']
                    if voice not in voices:
                        voices[voice] = model.get_state_for_audio_prompt(str(path / 'languages/english/embeddings' / (voice + '.safetensors')))
                    samples = model.generate_audio(voices[voice], request['text']).detach().cpu().numpy()
                    sr = model.sample_rate
                else:
                    # Turbo has a shorter speech-token limit: synthesize safe spans
                    # inside the three-paragraph range, then concatenate continuously.
                    spans = split_paragraphs([request['text']], limit=500)
                    samples = np.concatenate([model.generate(span['text']).squeeze().detach().cpu().numpy() for span in spans])
                    sr = model.sr
            samples = np.asarray(samples, dtype=np.float32).reshape(-1)
            if not len(samples) or not np.isfinite(samples).all():
                raise ValueError('The model produced invalid audio.')
            peak = float(np.max(np.abs(samples)))
            if peak > 1:
                samples = samples * (0.98 / peak)
            sf.write(request['output'], samples, sr, subtype='PCM_16')
            send({'ok': True, 'duration': len(samples) / sr, 'generation_seconds': time.monotonic() - started, 'peak_vram_gb': torch.cuda.max_memory_allocated() / 1024**3 if device == 'cuda' else 0, 'sample_rate': sr, 'words': words})
        except Exception as exc:
            import traceback
            traceback.print_exc()
            send({'ok': False, 'error': str(exc)})


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        import traceback
        traceback.print_exc()
        send({'ready': False, 'error': str(exc)})
        sys.exit(1)
