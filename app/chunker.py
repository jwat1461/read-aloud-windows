"""Split text into speakable chunks and report where each one sits in the source.

The GUI highlights the chunk currently being spoken, so every chunk is returned as
a (start, end) character span into the original string rather than as a copy.
"""

from __future__ import annotations

SENTENCE_ENDERS = ".!?…"
# Closing punctuation that belongs to the sentence it trails.
TRAILING = "\"'”’)]}…!?."
MAX_CHUNK = 280


def sentence_spans(text: str, max_len: int = MAX_CHUNK) -> list[tuple[int, int]]:
    """Return (start, end) spans covering the non-whitespace content of `text`.

    Breaks on sentence-ending punctuation, on blank lines, and — for runaway text
    with no punctuation — at the last word boundary before `max_len`.
    """
    spans: list[tuple[int, int]] = []
    i, n = 0, len(text)

    while i < n:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break

        start = i
        end = None
        last_space = -1
        j = i

        while j < n:
            ch = text[j]

            if ch in SENTENCE_ENDERS:
                k = j + 1
                while k < n and text[k] in TRAILING:
                    k += 1
                if k >= n or text[k].isspace():
                    end = k
                    break

            if ch == "\n":
                # Blank line = paragraph break. A single newline is just a wrap.
                k = j + 1
                while k < n and text[k] in " \t\r":
                    k += 1
                if k < n and text[k] == "\n":
                    end = j
                    break

            if ch.isspace():
                last_space = j

            if j - start >= max_len:
                end = last_space if last_space > start else j
                break

            j += 1

        if end is None:
            end = n
        spans.append((start, end))
        i = end

    return spans


def chunks(text: str, max_len: int = MAX_CHUNK) -> list[tuple[int, int, str]]:
    """Same as `sentence_spans` but carries the chunk text along."""
    out = []
    for start, end in sentence_spans(text, max_len):
        piece = text[start:end].strip()
        if piece:
            out.append((start, end, piece))
    return out
