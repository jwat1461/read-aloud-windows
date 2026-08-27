"""The one place text becomes something the speech engine will be handed.

Both the desktop app and the OS-wide reader used to split text into sentences
themselves, a line apiece, which is fine until something has to happen to every
piece of text on its way to being read — summary mode, in this case. Two copies
of that decision is one too many, so both now come through `plan()`.

`plan()` returns spans into the text it also hands back, so a caller that
highlights (the desktop app) and one that does not (the tray reader) share the
same result. When the text is left alone, `plan(text).text is text` and the
spans are exactly what `chunker.chunks()` returned — the reason this module can
be dropped in without changing a single spoken word.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import settings
import summarize
from chunker import chunks

# Sentences are stitched back together with a single space. A summary is read
# aloud, so paragraph shape buys nothing and a predictable join keeps the spans
# arithmetic rather than guesswork.
JOIN = " "

# Spoken before the first sentence of a summarized item, so a listener knows
# text was cut rather than wondering why the message sounded so short. It is
# prefixed to what the engine is handed, never added to `pieces`, so sentence
# spans and highlighting stay pointing at real text.
CUE = "Summary."


@dataclass(frozen=True)
class ReadingPlan:
    """What will actually be read, and where each sentence sits inside it.

    `text` is what a caller should display: the source itself, or the summary
    when one was made. `source` is always the untouched original, so the desktop
    app can keep offering it.
    """

    text: str
    source: str
    pieces: list[tuple[int, int, str]] = field(default_factory=list)
    summarized: bool = False

    @property
    def sentences(self) -> list[str]:
        return [piece for _start, _end, piece in self.pieces]

    def __bool__(self) -> bool:
        return bool(self.pieces)


def _stitch(sentences: list[str]) -> tuple[str, list[tuple[int, int, str]]]:
    """Join the kept sentences and report where each one landed."""
    pieces = []
    cursor = 0
    for sentence in sentences:
        pieces.append((cursor, cursor + len(sentence), sentence))
        cursor += len(sentence) + len(JOIN)
    return JOIN.join(sentences), pieces


def plan(
    text: str,
    offset: int = 0,
    summary: bool | None = None,
    rules: dict | None = None,
) -> ReadingPlan:
    """Split `text` into the sentences to be spoken.

    `offset` shifts the reported spans, for callers reading a selection out of a
    larger document and highlighting against the whole of it. It does not apply
    to a summary, whose spans point into the summary itself — there is nothing
    in the source for them to line up with.

    `summary` overrides the stored setting; leaving it None reads the setting,
    which is the safety net rather than the usual path, since both apps hold the
    preference in memory already.
    """
    spans = chunks(text)
    if summary is None:
        summary = bool(settings.load()["summary_mode"])

    if summary and spans:
        kept = summarize.summarize(text, rules)
        if kept != [piece for _s, _e, piece in spans]:
            summary_text, pieces = _stitch(kept)
            return ReadingPlan(
                text=summary_text, source=text, pieces=pieces, summarized=True
            )
        # Summarizer declined — too short to be worth it. Fall through, so a
        # bypass is indistinguishable from the mode being off.

    pieces = [(s + offset, e + offset, piece) for s, e, piece in spans]
    return ReadingPlan(text=text, source=text, pieces=pieces, summarized=False)
