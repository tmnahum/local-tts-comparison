"""Map Kokoro's timed tokens to verbatim source words without changing audio."""
import math
import re


def kokoro_words(text, outputs, sample_rate=24000):
    spans = list(re.finditer(r'\S+', text))
    timings = {}
    cursor = 0
    offset = 0.0
    for output in outputs:
        duration = len(output.audio) / sample_rate
        for token in output.tokens or []:
            value = token.text
            position = text.find(value, cursor) if value else -1
            if position < 0:
                continue  # Never guess a location for unmatched/normalized tokens.
            end = position + len(value)
            cursor = end
            start_ts, end_ts = token.start_ts, token.end_ts
            if start_ts is None or end_ts is None or not all(math.isfinite(t) for t in (start_ts, end_ts)):
                continue
            start_time = offset + max(0, min(duration, start_ts))
            end_time = offset + max(0, min(duration, end_ts))
            if end_time <= start_time:
                continue
            for i, span in enumerate(spans):
                if span.start() < end and span.end() > position:
                    previous = timings.get(i)
                    timings[i] = {'start': span.start(), 'end': span.end(), 'start_time': min(previous['start_time'], start_time) if previous else start_time, 'end_time': max(previous['end_time'], end_time) if previous else end_time}
        offset += duration
    return [timings[i] for i in sorted(timings)]


def chunk_offsets(paragraphs, chunks):
    """Sequential matching disambiguates repeated words and safety-split paragraphs."""
    source = ' '.join(paragraphs)
    cursor = 0
    result = []
    for chunk in chunks:
        start = source.find(chunk['text'], cursor)
        result.append(start if start >= 0 else None)
        if start >= 0:
            cursor = start + len(chunk['text'])
    return result
