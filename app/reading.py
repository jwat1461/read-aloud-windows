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

from chunker import chunks


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


def plan(text: str, offset: int = 0) -> ReadingPlan:
    """Split `text` into the sentences to be spoken.

    `offset` shifts the reported spans, for callers reading a selection out of a
    larger document and highlighting against the whole of it.
    """
    pieces = [(s + offset, e + offset, piece) for s, e, piece in chunks(text)]
    return ReadingPlan(text=text, source=text, pieces=pieces, summarized=False)
