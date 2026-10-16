"""Reading text stays verbatim; long paragraphs split at sentence boundaries."""
import re


def paragraphs_from_text(text):
    return [re.sub(r'\s+', ' ', p).strip() for p in re.split(r'\n\s*\n', text.strip()) if p.strip()]


def chunk_paragraphs(paragraphs, limit=650):
    chunks = []
    for index, paragraph in enumerate(paragraphs):
        # Protect common abbreviations and decimal points from sentence splitting.
        protected = re.sub(r'\b(Mr|Mrs|Ms|Dr|Prof|St|vs|etc)\.', lambda m: m[0][:-1] + '\u2024', paragraph)
        protected = re.sub(r'(?<=\d)\.(?=\d)', '\u2024', protected)
        sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z0-9“"\'])', protected)
        current = ''
        for sentence in sentences:
            sentence = sentence.replace('\u2024', '.')
            # A very long sentence is split at a clause or word boundary only as a fallback.
            pieces = []
            while len(sentence) > limit:
                cut = max(sentence.rfind('; ', 0, limit), sentence.rfind(', ', 0, limit))
                if cut < limit // 3:
                    cut = sentence.rfind(' ', 0, limit)
                if cut < 1:
                    cut = limit
                else:
                    cut += 1
                pieces.append(sentence[:cut].strip())
                sentence = sentence[cut:].strip()
            if sentence:
                pieces.append(sentence)
            for piece in pieces:
                if current and len(current) + len(piece) + 1 > limit:
                    chunks.append({'text': current, 'paragraph': index, 'paragraph_end': False})
                    current = ''
                current = f'{current} {piece}'.strip()
        if current:
            chunks.append({'text': current, 'paragraph': index, 'paragraph_end': True})
    return chunks
