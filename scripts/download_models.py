"""Download the English comparison models; resumable, no GPU required."""
import json
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

os.environ.setdefault('HF_HUB_DISABLE_XET', '1')
from huggingface_hub import snapshot_download

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / 'models-manifest.json').read_text())
SPECS = {
    'kokoro': ('hexgrad/Kokoro-82M', ['config.json', 'kokoro-v1_0.pth', 'voices/af_heart.pt', 'voices/af_bella.pt', 'voices/am_michael.pt', 'voices/bf_emma.pt', 'voices/bm_george.pt', 'LICENSE', 'README.md']),
    'qwen': ('Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice', ['*.json', '*.safetensors', '*.txt', '*.model', '*.tiktoken', 'README.md', 'LICENSE']),
    'qwen-small': ('Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice', ['*.json', '*.safetensors', '*.txt', '*.model', '*.tiktoken', 'README.md', 'LICENSE']),
    'chatterbox': ('ResembleAI/chatterbox-turbo', ['*.json', '*.txt', 'conds.pt', 've.safetensors', 's3gen_meanflow.safetensors', 't3_turbo_v1.safetensors', 'README.md', 'LICENSE']),
}
for key in ('kitten-mini', 'kitten-micro', 'kitten-nano'):
    SPECS[key] = (MANIFEST[key]['repo'], ['*.json', '*.onnx', '*.npz', 'README.md', 'LICENSE*'])
SPECS.update({
    'supertonic': (MANIFEST['supertonic']['repo'], ['onnx/*', 'voice_styles/*', 'config.json', 'README.md', 'LICENSE']),
    'pocket': (MANIFEST['pocket']['repo'], ['languages/english/*', 'README.md', 'LICENSE*']),
    'paradee': (MANIFEST['paradee']['repo'], ['config.json', 'onnx/paradee_int8.onnx', 'README.md', 'LICENSE*']),
    'aligner': (MANIFEST['aligner']['repo'], ['*.json', 'model.safetensors', 'README.md']),
})

for key in ('neutts-q4', 'neutts-q8', 'neutts-codec'):
    SPECS[key] = (MANIFEST[key]['repo'], ['*.gguf', '*.onnx', 'meta.yaml', 'README.md', 'LICENSE'])

def download(item):
    key, (repo, patterns) = item
    folder = ROOT / 'models' / key
    print(f'Downloading {repo}', flush=True)
    revision = MANIFEST[key]['revision']
    snapshot_download(repo, revision=revision, local_dir=folder, allow_patterns=patterns, max_workers=4)
    (folder / '.ready.json').write_text(json.dumps({'repo': repo, 'revision': revision}))
    print(f'Ready: {key} ({revision})', flush=True)

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', choices=SPECS, help='Download only this model')
    args = parser.parse_args()
    selected = {args.model: SPECS[args.model]} if args.model else SPECS
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(download, selected.items()))
