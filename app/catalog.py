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
        'environment': '.venv-chatterbox', 'styles': True,
        'style_label': 'Performance cue',
        'style_options': ['', '[laugh]', '[chuckle]', '[cough]'],
        'style_help': 'Adds this sound at the start of each section. Inline cues in the article are also supported. This model does not accept written delivery instructions.',
    },
    'qwen': {
        'name': 'Qwen3-TTS 1.7B', 'size': '1.7B', 'description': 'A more deliberate reader with adjustable delivery.',
        'license': 'Apache-2.0', 'default_voice': 'Ryan',
        'voices': [{'id': 'Ryan', 'name': 'Ryan · Rhythmic'}, {'id': 'Aiden', 'name': 'Aiden · American'}],
        'environment': '.venv-tts', 'styles': True,
        'style_label': 'Delivery instructions',
        'style_help': 'Describe tone, emotion, or pacing, for example: Warm, curious narration with a measured pace. Saved with this voice.',
    },
    'qwen-small': {
        'name': 'Qwen3-TTS 0.6B', 'size': '0.6B', 'description': 'Smaller Qwen, with the same English narrator choices.',
        'license': 'Apache-2.0', 'default_voice': 'Ryan',
        'voices': [{'id': 'Ryan', 'name': 'Ryan · Rhythmic'}, {'id': 'Aiden', 'name': 'Aiden · American'}],
        'environment': '.venv-tts', 'styles': False,
    },
}

for key, spec in MODELS.items():
    spec['family'] = 'Qwen3-TTS' if key.startswith('qwen') else ('Chatterbox' if key == 'chatterbox' else 'Kokoro')
    spec['variant'] = spec['size']

for variant, size in [('mini', '80M'), ('micro', '40M'), ('nano', '15M · int8')]:
    MODELS['kitten-' + variant] = {
        'name': 'Kitten ' + variant.title(), 'family': 'Kitten', 'variant': variant.title() + ' · ' + size,
        'size': size, 'description': 'Eight English narrators in a compact CPU model.',
        'license': 'Apache-2.0', 'default_voice': 'Bella',
        'voices': [{'id': v, 'name': v} for v in ['Bella', 'Jasper', 'Luna', 'Bruno', 'Rosie', 'Hugo', 'Kiki', 'Leo']],
        'environment': '.venv-light', 'styles': False,
    }
MODELS.update({
    'pocket': {
        'name': 'Pocket TTS', 'family': 'Pocket TTS', 'variant': '100M · English', 'size': '100M',
        'description': 'CPU speech with a broad collection of English narrators.', 'license': 'MIT',
        'default_voice': 'alba', 'voices': [{'id': v, 'name': v.replace('_', ' ').title()} for v in ['alba', 'anna', 'azelma', 'bill_boerst', 'caro_davy', 'charles', 'cosette', 'eponine', 'eve', 'fantine', 'george', 'jane', 'javert', 'jean', 'marius', 'mary', 'michael', 'paul', 'peter_yearsley', 'stuart_bell', 'vera']],
        'environment': '.venv-light', 'styles': False,
    },
    'paradee': {
        'name': 'Paradee', 'family': 'Paradee', 'variant': '8M · int8', 'size': '8M',
        'description': 'A tiny distillation of Kokoro’s Heart voice.', 'license': 'Apache-2.0',
        'default_voice': 'af_heart', 'voices': [{'id': 'af_heart', 'name': 'Heart · American'}],
        'environment': '.venv-light', 'styles': False,
    },

})

MODELS['supertonic'] = {
    'name': 'Supertonic 3', 'family': 'Supertonic', 'variant': '3 · 99M', 'size': '99M',
    'description': 'Fast local speech with ten preset voices and expression cues.',
    'license': 'OpenRAIL-M', 'default_voice': 'F1',
    'voices': [{'id': v, 'name': v + (' · Female' if v.startswith('F') else ' · Male')} for v in ['F1', 'F2', 'F3', 'F4', 'F5', 'M1', 'M2', 'M3', 'M4', 'M5']],
    'environment': '.venv-light', 'styles': True,
    'style_label': 'Expression cue', 'style_options': ['', '<laugh>', '<breath>', '<sigh>'],
    'style_help': 'Adds this sound at the start of each section. Inline expression tags are also supported. No freeform delivery instructions.',
}

for key, spec in MODELS.items():
    spec['capabilities'] = {
        'Kokoro': 'Preset voices · No voice cloning · Native word timings',
        'Kitten': 'Preset voices · No voice cloning · Optional generated word timings',
        'Paradee': 'One preset voice · No voice cloning · Optional generated word timings',
        'Pocket TTS': 'Preset voices here · Family supports cloning, but this installed checkpoint excludes it · Optional generated word timings',
        'Chatterbox': 'Voice cloning supported by the model; reference-audio upload is not enabled here · Performance cues · Optional generated word timings',
        'Qwen3-TTS': 'CustomVoice presets here · Cloning requires the separate Base model; voice design requires VoiceDesign · ' + ('Delivery instructions' if key == 'qwen' else 'No delivery instructions') + ' · Optional generated word timings',
        'Supertonic': '31-language model; English generation here · Custom voices require Voice Builder embeddings; no local voice cloning or embedding import here · Expression cues · Optional generated word timings',
    }[spec['family']]

for quant in ('q4', 'q8'):
    MODELS['neutts-' + quant] = {
        'name': 'NeuTTS-2E ' + quant.upper(), 'family': 'NeuTTS', 'variant': '2E · ' + quant.upper(),
        'size': '236M · ' + quant.upper(), 'description': 'Compact English narration with four speakers and selectable emotion.',
        'license': 'NeuTTS Open License 1.0', 'default_voice': 'emily',
        'voices': [{'id': v, 'name': v.title()} for v in ['emily', 'paul', 'sophie', 'steven']],
        'environment': '.venv-neutts', 'styles': True, 'style_label': 'Emotion',
        'emotions': ['neutral', 'happy', 'sad', 'angry', 'fearful', 'disgusted', 'surprised'],
        'style_help': 'Choose an emotion for this reading. Saved with the voice. No freeform delivery instructions.',
        'capabilities': '2E has four fixed speakers · Cloning is available in the separate Air/Nano models, not 2E · Seven emotions · Optional generated word timings',
        'requires_models': ['neutts-codec'],
    }
