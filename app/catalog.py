from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = {
    'kokoro': {
        'name': 'Kokoro', 'size': '82M', 'description': 'Light, clear, and quick. The everyday reader.',
        'license': 'Apache-2.0', 'default_voice': 'af_heart',
        'voices': [{'id': v, 'name': n} for v, n in [('af_heart', 'Heart · American'), ('af_bella', 'Bella · American'), ('am_michael', 'Michael · American'), ('bf_emma', 'Emma · British'), ('bm_george', 'George · British')]],
        'environment': '.venv-tts', 'styles': False,
    },
    'chatterbox': {
        'name': 'Chatterbox Turbo', 'size': '350M', 'description': 'Expressive English with a conversational rhythm.',
        'license': 'MIT', 'default_voice': 'default', 'voices': [{'id': 'default', 'name': 'Built-in narrator'}],
        'environment': '.venv-chatterbox', 'styles': False,
    },
    'qwen': {
        'name': 'Qwen3-TTS', 'size': '1.7B', 'description': 'A more deliberate reader with adjustable delivery.',
        'license': 'Apache-2.0', 'default_voice': 'Ryan',
        'voices': [{'id': 'Ryan', 'name': 'Ryan · Rhythmic'}, {'id': 'Aiden', 'name': 'Aiden · American'}],
        'environment': '.venv-tts', 'styles': True,
    },
}
