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

import local_model
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

# Said before a model request, which is the only path here that can make anyone
# wait. The extractive path is instant and gets no such warning.
WORKING = "Summarizing."

# Spoken around a brief. The opener tells the listener this is not the whole
# thing; the trailer says how much was dropped, which is the number that decides
# whether they want to hear the source.
BRIEF_CUE = "Brief:"
BRIEF_END = "End of brief. {kept} sentences from {total}."

# Said when the same text is briefed twice inside the duplicate window. Speaking
# the same summary again is the one thing a listener cannot tell from a bug.
DUPLICATE = "Same as the last brief."

# Said when there is nothing to work with at all.
NOTHING = "Nothing to brief."


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
    engine: str = "extractive"
    fell_back: bool = False
    # Why the text was left alone, when it was. The brief path speaks a
    # different line for a duplicate than for a passage too short to trim, and
    # without this it would have to ask the summarizer a second time to find out.
    bypass: str | None = None
    n_input: int = 0
    n_output: int = 0

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


def _model_sentences(text: str, model: str, before) -> list[str] | None:
    """The Ollama tier, or None when it could not oblige."""
    try:
        if before is not None:
            before()
        return local_model.summarize(text, model)
    except local_model.Unavailable:
        return None


def plan(
    text: str,
    offset: int = 0,
    summary: bool | None = None,
    rules: dict | None = None,
    engine: str | None = None,
    model: str | None = None,
    before_model=None,
    source: str = "app",
    summarizer: summarize.Summarizer | None = None,
) -> ReadingPlan:
    """Split `text` into the sentences to be spoken.

    `offset` shifts the reported spans, for callers reading a selection out of a
    larger document and highlighting against the whole of it. It does not apply
    to a summary, whose spans point into the summary itself — there is nothing
    in the source for them to line up with.

    `summary` overrides the stored setting; leaving it None reads the setting,
    which is the safety net rather than the usual path, since both apps hold the
    preference in memory already. `engine` and `model` work the same way.

    `before_model` is called immediately before an Ollama request and not at all
    otherwise, so a caller can say "Summarizing" out loud without having to work
    out for itself whether the model is going to be asked.

    `summarizer` is the scorer to use. A caller that holds one across presses --
    the tray reader does -- passes it in, because the duplicate window lives on
    the instance and a fresh one every time would never see a repeat. Left out,
    one is built for this call against the real log, which is what the desktop
    app wants and what makes this function usable on its own.

    The Ollama request is synchronous. With the model resident -- which is what
    the warm-up is for -- it returns in well under a second; the 20-second read
    timeout is the ceiling on a cold or wedged one, and the window is
    unresponsive for that time. That is the price of the opt-in tier, and the
    reason the default is a summarizer that cannot stall at all.
    """
    spans = chunks(text)
    if summary is None:
        summary = bool(settings.load()["summary_mode"])

    if summary and spans:
        stored = None
        if engine is None or model is None:
            stored = settings.load()
        if engine is None:
            engine = stored["summary_engine"]
        if model is None:
            model = stored["summary_model"]

        if summarizer is None:
            summarizer = summarize.ExtractiveSummarizer(
                log_path=summarize.default_log_path(),
                source=source,
                rules=rules,
                budget=summarize.Budget.from_settings(),
            )

        kept = None
        fell_back = False
        result = None
        # Bypass first: code, a URL and short text are no better through a
        # model, and there is no reason to make anyone wait to learn that.
        if summarize.bypass_reason(text, budget=summarizer.budget) is None and engine == "ollama":
            kept = _model_sentences(text, model, before_model)
            fell_back = kept is None
        if kept is None:
            result = summarizer.summarize(text)
            kept = result.sentences
        else:
            # The model answered, so summarize() never ran and never logged.
            # Record the run anyway, or an ollama session leaves no trail.
            summarize.log_run({
                "timestamp": summarize.now_iso(),
                "event": "summary",
                "source": summarizer.source,
                "sentence_count": len(spans),
                "word_count": len(summarize._WORD.findall(text.lower())),
                "bypass_reason": None,
                "k": len(kept),
                "engine": "ollama",
                "picked": [],
            }, summarizer.log_path)

        if kept != [piece for _s, _e, piece in spans]:
            summary_text, pieces = _stitch(kept)
            return ReadingPlan(
                text=summary_text,
                source=text,
                pieces=pieces,
                summarized=True,
                engine="extractive" if fell_back or engine != "ollama" else "ollama",
                fell_back=fell_back,
                n_input=result.n_input if result else len(spans),
                n_output=result.n_output if result else len(kept),
            )
        # Summarizer declined — too short to be worth it, a duplicate, or a
        # table. Fall through so an ordinary bypass stays indistinguishable from
        # the mode being off, but carry the reason out for the brief path, which
        # has something different to say about a repeat.
        bypass = result.bypass if result else None
        pieces = [(s + offset, e + offset, piece) for s, e, piece in spans]
        return ReadingPlan(text=text, source=text, pieces=pieces,
                           summarized=False, bypass=bypass,
                           n_input=len(spans), n_output=len(spans))

    pieces = [(s + offset, e + offset, piece) for s, e, piece in spans]
    return ReadingPlan(text=text, source=text, pieces=pieces, summarized=False,
                       n_input=len(spans), n_output=len(spans))
