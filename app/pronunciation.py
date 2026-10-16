"""Literal whole-word pronunciation replacements with source-offset mapping."""
import re


def apply_rules(text, rules):
    if not rules:
        return text, []
    lookup = {r['word']: r['replacement'] for r in rules}
    pattern = re.compile(r'(?<!\w)(?:' + '|'.join(re.escape(w) for w in sorted(lookup, key=len, reverse=True)) + r')(?!\w)')
    pieces, edits, cursor, length = [], [], 0, 0
    for match in pattern.finditer(text):
        prefix = text[cursor:match.start()]
        pieces.append(prefix); length += len(prefix)
        replacement = lookup[match.group()]
        edits.append({'start': length, 'end': length + len(replacement), 'source_start': match.start(), 'source_end': match.end()})
        pieces.append(replacement); length += len(replacement); cursor = match.end()
    pieces.append(text[cursor:])
    return ''.join(pieces), edits


def source_words(words, edits):
    if words is None or not edits:
        return words
    def position(pos, end=False):
        shift = 0
        for edit in edits:
            if pos < edit['start'] or (end and pos == edit['start']):
                break
            if pos < edit['end'] or (end and pos == edit['end']):
                return edit['source_end'] if end else edit['source_start']
            shift = edit['source_end'] - edit['end']
        return pos + shift
    merged = {}
    for word in words:
        start, end = position(word['start']), position(word['end'], True)
        key = (start, end)
        if key in merged:
            merged[key]['end_time'] = max(merged[key]['end_time'], word['end_time'])
        else:
            merged[key] = dict(word, start=start, end=end)
    return list(merged.values())
