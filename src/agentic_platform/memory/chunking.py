"""Split documents into overlapping chunks for semantic memory.

Paragraph-aware: paragraphs are packed into chunks of at most `max_chars`; a
paragraph longer than that is split on sentence or word boundaries. Consecutive
chunks share up to `overlap_chars` of trailing text so facts that straddle a
boundary stay retrievable.
"""

from __future__ import annotations

import re

_PARAGRAPHS = re.compile(r"\n\s*\n")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _split_long(text: str, max_chars: int) -> list[str]:
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(text):
        while len(sentence) > max_chars:  # no sentence boundary: cut at a space
            cut = sentence.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            pieces.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if current and len(current) + 1 + len(sentence) > max_chars:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return [p for p in pieces if p]


def chunk_text(text: str, *, max_chars: int, overlap_chars: int = 0) -> list[str]:
    if max_chars < 50:
        raise ValueError("max_chars must be at least 50")
    overlap_chars = max(0, min(overlap_chars, max_chars // 2))
    units: list[str] = []
    for paragraph in _PARAGRAPHS.split(text.strip()):
        paragraph = " ".join(paragraph.split())
        if paragraph:
            # Leave room for the overlap tail plus a separator: chunks never exceed max_chars.
            units.extend(_split_long(paragraph, max_chars - overlap_chars - 1))

    chunks: list[str] = []
    current = ""
    for unit in units:
        if current and len(current) + 2 + len(unit) > max_chars - overlap_chars:
            chunks.append(current)
            tail = current[-overlap_chars:] if overlap_chars else ""
            if tail and " " in tail:
                tail = tail[tail.index(" ") + 1 :]  # start the overlap at a word
            current = f"{tail} {unit}".strip() if tail else unit
        else:
            current = f"{current}\n\n{unit}" if current else unit
    if current:
        chunks.append(current)
    return chunks
