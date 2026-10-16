"""Local English CTC forced alignment, keeping offsets into the original text."""
import json
import re
import unicodedata

from .catalog import ROOT


def alignment_words(text):
    """Normalize each source word separately so expansions retain its exact span."""
    from kittenml import normalize_text
    result = []
    for match in re.finditer(r'\S+', text):
        raw = match.group().replace('’', "'")
        # Preserve contractions: generic text normalization can split "don't" into "don t".
        word = raw if re.fullmatch(r"[A-Za-z]+('[A-Za-z]+)+[.,!?;:]?", raw) else normalize_text(raw)
        word = unicodedata.normalize('NFKD', word).encode('ascii', 'ignore').decode().upper()
        word = re.sub(r"[^A-Z' ]", ' ', word)
        word = ' '.join(word.split()).strip("'")
        if word:
            result.append({'start': match.start(), 'end': match.end(), 'normalized': word})
    return result


class EnglishAligner:
    def __init__(self):
        from transformers import Wav2Vec2ForCTC
        self.model = Wav2Vec2ForCTC.from_pretrained(str(ROOT / 'models/aligner'), local_files_only=True).eval()
        self.vocab = json.loads((ROOT / 'models/aligner/vocab.json').read_text())

    def align(self, text, filename):
        import numpy as np
        import soundfile as sf
        import torch
        import torchaudio.functional as F
        spans = alignment_words(text)
        if not spans:
            return []
        samples, sr = sf.read(filename, dtype='float32', always_2d=True)
        waveform = torch.from_numpy(samples.mean(axis=1))
        waveform = F.resample(waveform, sr, 16000)
        # Overlapping windows bound memory while retaining acoustic context at joins.
        emissions, frame_times = [], []
        window, margin, rate = 20 * 16000, 16000, 16000
        with torch.inference_mode():
            for start in range(0, len(waveform), window):
                left, right = max(0, start - margin), min(len(waveform), start + window + margin)
                segment = waveform[left:right]
                if len(segment) < 400:
                    continue
                segment = (segment - segment.mean()) / (segment.var(unbiased=False) + 1e-7).sqrt()
                logits = self.model(segment[None]).logits[0].log_softmax(-1)
                times = (torch.arange(len(logits)) * 320 + 200 + left) / rate
                keep = (times >= start / rate) & (times < min(start + window, len(waveform)) / rate)
                emissions.append(logits[keep]); frame_times.extend(times[keep].tolist())
        emission = torch.cat(emissions)[None]
        tokens, ranges = [], []
        for span in spans:
            if tokens:
                tokens.append(self.vocab['|'])
            begin = len(tokens)
            tokens.extend(self.vocab[c] for c in span['normalized'].replace(' ', '|'))
            ranges.append((begin, len(tokens)))
        targets = torch.tensor([tokens], dtype=torch.int32)
        aligned, scores = F.forced_align(emission, targets, blank=self.vocab['<pad>'])
        merged = F.merge_tokens(aligned[0], scores[0].exp(), blank=self.vocab['<pad>'])
        if len(merged) != len(tokens):
            raise ValueError('Alignment did not preserve the transcript token sequence.')
        duration = len(samples) / sr
        result = []
        for span, (begin, end) in zip(spans, ranges):
            letters = [t for t in merged[begin:end] if t.token != self.vocab['|']]
            confidence = float(np.mean([float(t.score) for t in letters])) if letters else 0
            # Forced alignment can place words that were never spoken. Omit weak matches.
            if confidence < 0.15:
                continue
            first, last = merged[begin], merged[end - 1]
            a = max(0, frame_times[first.start] - 0.01)
            b = min(duration, frame_times[last.end - 1] + 0.01)
            if b > a:
                result.append({'start': span['start'], 'end': span['end'], 'start_time': a, 'end_time': b, 'confidence': round(confidence, 3)})
        return result
