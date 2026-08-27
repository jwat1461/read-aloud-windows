"""Pick the sentences that carry the pain out of a piece of text.

Extractive, not generative: nothing is written, sentences are chosen. That keeps
the app's promise — no model, no keys, no network — and it keeps the result
defensible, because every spoken word appeared in the source.

Two scores are blended per sentence:

  *cue* — vocabulary and shape. Words that mark trouble ("failed", "blocked",
  "deadline"), questions, figures with units or money, and a small nudge for the
  opening and closing. The nudge is small on purpose: clipboard text is mostly
  email, chat and docs, where the lead-bias that works on news copy does not.

  *Luhn* — term frequency after stopword removal. A sentence built from the
  words the text keeps returning to is usually the one carrying its subject.

Deliberately no TextRank or PageRank. Graph ranking has no convergence
guarantee, orders by node insertion, and sums floats non-associatively, so
near-tied sentences can swap places between runs. Determinism is a requirement
here, not a nicety, so the whole family is out.

Negation is handled with a window: a pain word within a few tokens after a
negator does not count, so "no errors" and "not broken" read as neutral rather
than as the loudest sentences in the document.

Some text should not be summarized at all — code, a bare URL, a list of short
lines — and inside otherwise-ordinary prose those same shapes should never be
*chosen*, because a summary that reads out a bullet or a URL is worse than one
sentence too long.

Vocabulary, negators, window and weights all live in
%APPDATA%\\ReadAloud\\summary_rules.json so they can be tuned without a rebuild,
and every call writes one line to summary_log.jsonl beside it saying what was
picked and what each signal contributed — because tuning weights against text
you cannot see afterwards is guesswork. The sentences themselves are not logged
unless you ask for them: scores and indices are enough to see why something won,
and are not a copy of everything you have ever put on the clipboard.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from chunker import chunks

_HOME = Path(os.environ.get("APPDATA") or Path.home()) / "ReadAloud"

RULES_PATH = _HOME / "summary_rules.json"

# One JSON line per summarize() call. Scores without the sentences they scored
# are the point: they are enough to see why something won and tune the weights,
# and they are not a copy of everything you put on the clipboard.
LOG_PATH = _HOME / "summary_log.jsonl"
LOG_ROTATED = _HOME / "summary_log.1.jsonl"
LOG_MAX_BYTES = 5 * 1024 * 1024

# Below either of these a summary is worse than the text it replaces.
MIN_SENTENCES = 4
MIN_WORDS = 60

K_DIVISOR = 5
K_MIN, K_MAX = 2, 8

# A "short" line, for deciding whether something is a list rather than prose.
LIST_SHORT_WORDS = 6
LIST_MIN_LINES = 3
LIST_SHARE = 0.7
CODE_SHARE = 0.4

# Bumped whenever the shape of the file changes in a way that makes an older
# one wrong rather than merely incomplete. See load_rules().
RULES_VERSION = 2

DEFAULT_RULES: dict = {
    "version": RULES_VERSION,
    "pain_words": [
        "again", "blocked", "broken", "cannot", "cant", "complaint", "cost",
        "deadline", "error", "expensive", "fail", "failed", "late", "missing",
        "must", "never", "refund", "risk", "slow", "still", "stuck", "urgent",
        "wont", "wrong",
    ],
    # Seeded from VADER's NEGATE set, with the contractions written both ways so
    # the tokenizer's apostrophe handling cannot matter.
    "negations": [
        "aint", "arent", "cannot", "cant", "couldnt", "darent", "didnt",
        "doesnt", "dont", "hadnt", "hasnt", "havent", "isnt", "mightnt",
        "mustnt", "neither", "never", "no", "nobody", "none", "nope", "nor",
        "not", "nothing", "nowhere", "oughtnt", "shant", "shouldnt", "uhuh",
        "wasnt", "werent", "without", "wont", "wouldnt",
    ],
    "negation_window": 3,
    "weights": {
        "pain_word": 1.0,
        "question": 0.8,
        "figure": 0.7,
        # Modest, per the note above: this is email and chat, not news copy.
        "first_paragraph": 0.25,
        "last_paragraph": 0.2,
        "cue_blend": 0.6,
        "luhn_blend": 0.4,
    },
}

_WORD = re.compile(r"[a-z0-9']+")
# A number that means something: money, a percentage, or a number with a unit.
_FIGURE = re.compile(
    r"[$£€]\s?\d|\d+\s?%|\b\d+(?:[.,]\d+)?\s*"
    r"(?:k|m|bn|hours?|hrs?|days?|weeks?|months?|years?|mins?|minutes?|seconds?"
    r"|users?|customers?|times|x|gb|mb|kb|ms)\b",
    re.IGNORECASE,
)
_URL = re.compile(r"(?:https?://|www\.)\S+|\b\S+\.(?:com|org|net|io|dev|gov|edu)\b/?\S*",
                  re.IGNORECASE)
_BULLET = re.compile(r"^\s*(?:[-*•·–—+]|\(?\d{1,2}[.)]|[a-z][.)])\s+", re.IGNORECASE)
_CODEY = re.compile(
    r"[;{}]\s*$|=>|::|==|!=|\+\+|&&|\|\||</|/>|"
    r"^\s*(?:def|class|import|from|function|const|let|var|public|private|return|"
    r"if|for|while|switch|case|elif|else|try|catch|except|package|using|#include|"
    r"#define|@|\$|\}|\{)\b|"
    r"^\s*[\w.]+\s*=\s*\S|\w+\([^)]*\)\s*[;{]?\s*$",
    re.IGNORECASE,
)
_FENCE = re.compile(r"^\s*```", re.MULTILINE)

# Words too common to say anything about what a sentence is about.
_STOP = frozenset("""
a an and are as at be been being but by for from had has have he her his i if in
into is it its me my of on or our she that the their them there these they this
to was we were what when which who will with you your
""".split())


# ------------------------------------------------------------------- rules


def load_rules(path: Path | None = None) -> dict:
    """Read the tuning file, writing the defaults out on first run.

    A broken or hand-mangled file falls back to the defaults rather than
    stopping the app; anything the file does not mention keeps its default.
    """
    path = RULES_PATH if path is None else path
    rules = {
        "version": RULES_VERSION,
        "pain_words": list(DEFAULT_RULES["pain_words"]),
        "negations": list(DEFAULT_RULES["negations"]),
        "negation_window": DEFAULT_RULES["negation_window"],
        "weights": dict(DEFAULT_RULES["weights"]),
    }

    try:
        stored = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        save_rules(rules, path)
        return rules

    # A file from an older build is worse than no file at all. Version 1 listed
    # "no" and "not" as pain words; version 2 treats them as negators, so
    # keeping the old list would have every negated sentence scoring as pain --
    # the exact thing the negation window exists to stop. Keep the old file
    # beside the new one so a tuned copy is never simply destroyed.
    if isinstance(stored, dict) and stored.get("version") != RULES_VERSION:
        try:
            path.with_suffix(".v1.json").write_text(
                json.dumps(stored, indent=2, sort_keys=True), "utf-8"
            )
        except OSError:
            pass
        save_rules(rules, path)
        return rules

    if isinstance(stored, dict):
        for key in ("pain_words", "negations"):
            words = stored.get(key)
            if isinstance(words, list):
                rules[key] = [_fold(str(w)) for w in words if str(w).strip()]
        try:
            rules["negation_window"] = max(0, int(stored.get("negation_window",
                                                            rules["negation_window"])))
        except (TypeError, ValueError):
            pass
        weights = stored.get("weights")
        if isinstance(weights, dict):
            for name, default in DEFAULT_RULES["weights"].items():
                try:
                    rules["weights"][name] = float(weights.get(name, default))
                except (TypeError, ValueError):
                    rules["weights"][name] = default
    return rules


def save_rules(rules: dict, path: Path | None = None) -> None:
    path = RULES_PATH if path is None else path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rules, indent=2, sort_keys=True), "utf-8")
    except OSError:
        pass  # a read-only profile is not worth crashing over


# ------------------------------------------------------------------- logging


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _rotate(path: Path, rotated: Path) -> None:
    if path.exists() and path.stat().st_size >= LOG_MAX_BYTES:
        path.replace(rotated)  # os.replace: overwrites the previous rotation


def log_run(
    record: dict,
    path: Path | None = None,
    rotated: Path | None = None,
) -> None:
    """Append one line. Never raises: a summary must not fail over its diary."""
    path = LOG_PATH if path is None else path
    rotated = (
        (path.with_name("summary_log.1.jsonl") if path is not LOG_PATH else LOG_ROTATED)
        if rotated is None
        else rotated
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _rotate(path, rotated)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except (OSError, ValueError, TypeError):
        pass


def _log_sentence_text() -> bool:
    """Off unless settings.json says otherwise. Imported here rather than at the
    top so this module stays importable on its own, as the tests use it."""
    try:
        import settings

        return bool(settings.load()["log_sentence_text"])
    except Exception:
        return False


def _record(
    text: str,
    sentences: list[str],
    reason: str | None,
    chosen: list[int],
    scores: list[float],
    breakdown: list[dict],
    keep: int | None,
    source: str,
    with_text: bool | None = None,
) -> dict:
    if with_text is None:
        with_text = _log_sentence_text()

    picked = []
    for index in chosen:
        signals = breakdown[index]
        entry = {
            "index": index,
            "score": round(scores[index], 6),
            "pain": round(signals["pain"], 6),
            "negation_hits": signals["negation_hits"],
            "question": round(signals["question"], 6),
            "number": round(signals["number"], 6),
            "position": round(signals["position"], 6),
            "frequency": round(signals.get("frequency", 0.0), 6),
        }
        if with_text:
            entry["text"] = sentences[index]
        picked.append(entry)

    return {
        "timestamp": now_iso(),
        "source": source,
        "sentence_count": len(sentences),
        "word_count": len(_WORD.findall(text.lower())),
        "bypass_reason": reason,
        "k": keep,
        "picked": picked,
    }


# --------------------------------------------------------------- tokenising


def _fold(word: str) -> str:
    """can't, cant and CAN'T are one word as far as the rules are concerned."""
    return word.lower().replace("'", "").replace("’", "")


def _words(sentence: str) -> list[str]:
    return [_fold(w) for w in _WORD.findall(sentence.lower())]


def _matches(token: str, vocabulary: frozenset) -> bool:
    """Match a token against a rules list, forgiving a plural.

    Without this "error" in the file would not catch "errors", which is both
    surprising to anyone editing the file and quietly fatal to the negation
    window: a pain word that never matches is a pain word that can never be
    suppressed. Only the plural is folded — no real stemming, because "missing"
    and "miss" are not the same complaint.
    """
    if token in vocabulary:
        return True
    if token.endswith("es") and token[:-2] in vocabulary:
        return True
    return token.endswith("s") and token[:-1] in vocabulary


# ------------------------------------------------------------------ bypasses


def _lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


def looks_like_code(text: str) -> bool:
    if _FENCE.search(text):
        return True
    lines = _lines(text)
    if len(lines) < 2:
        return bool(_CODEY.search(text.strip())) and len(_words(text)) < MIN_WORDS
    codey = sum(1 for line in lines if _CODEY.search(line))
    return codey / len(lines) >= CODE_SHARE


def is_single_url(text: str) -> bool:
    stripped = text.strip()
    if not stripped or len(stripped.split()) > 1:
        return False
    return bool(_URL.fullmatch(stripped.rstrip(".,;:!?")))


def looks_like_list(text: str) -> bool:
    lines = _lines(text)
    if len(lines) < LIST_MIN_LINES:
        return False
    listy = sum(
        1
        for line in lines
        if _BULLET.match(line) or len(line.split()) <= LIST_SHORT_WORDS
    )
    return listy / len(lines) >= LIST_SHARE


def is_excluded_line(sentence: str) -> bool:
    """A sentence that must never be *chosen*, even inside ordinary prose."""
    stripped = sentence.strip()
    if not stripped:
        return True
    if _BULLET.match(stripped):
        return True
    if _URL.search(stripped):
        return True
    return bool(_CODEY.search(stripped))


def bypass_reason(text: str, sentences: list[str] | None = None) -> str | None:
    """Why this text should be read as-is, or None if it should be summarized.

    Returned rather than a bare bool so the caller can say what happened and the
    tests can assert on which rule fired.
    """
    if sentences is None:
        sentences = [piece for _s, _e, piece in chunks(text)]

    if is_single_url(text):
        return "url"
    if looks_like_code(text):
        return "code"
    if looks_like_list(text):
        return "list"
    if len(sentences) < MIN_SENTENCES:
        return "too few sentences"
    if len(_WORD.findall(text.lower())) < MIN_WORDS:
        return "too few words"
    if len(eligible_indexes(sentences)) < K_MIN:
        return "nothing quotable"
    return None


def should_summarize(text: str, sentences: list[str] | None = None) -> bool:
    return bypass_reason(text, sentences) is None


def eligible_indexes(sentences: list[str]) -> list[int]:
    """The sentences a summary is allowed to draw from."""
    return [i for i, s in enumerate(sentences) if not is_excluded_line(s)]


def target_count(sentence_count: int) -> int:
    """How many sentences a summary of this length should keep."""
    return max(K_MIN, min(K_MAX, math.ceil(sentence_count / K_DIVISOR)))


# -------------------------------------------------------------------- scoring


def _negated_positions(tokens: list[str], negations: frozenset, window: int) -> set:
    """Token positions falling inside the window after a negator."""
    blocked = set()
    for index, token in enumerate(tokens):
        if _matches(token, negations):
            for offset in range(1, window + 1):
                blocked.add(index + offset)
    return blocked


def _signals(sentences: list[str], rules: dict) -> list[dict]:
    """Each sentence's cue signals, kept apart rather than summed.

    Split out so the log can say *why* a sentence won, not just that it did.
    Tuning weights against a single blended number is guesswork.
    """
    weights = rules["weights"]
    pain = frozenset(rules["pain_words"])
    negations = frozenset(rules["negations"])
    window = int(rules["negation_window"])
    total = len(sentences)
    # Treat the opening and closing fifth as first and last paragraph: it needs
    # no paragraph structure and behaves the same on a wall of text.
    edge = max(1, total // 5)

    breakdown = []
    for index, sentence in enumerate(sentences):
        tokens = _words(sentence)
        blocked = _negated_positions(tokens, negations, window)
        hits = 0
        suppressed = 0
        for position, token in enumerate(tokens):
            if not _matches(token, pain):
                continue
            if position in blocked:
                suppressed += 1
            else:
                hits += 1

        if index < edge:
            position_score = weights["first_paragraph"]
        elif index >= total - edge:
            position_score = weights["last_paragraph"]
        else:
            position_score = 0.0

        breakdown.append({
            # Saturating, so one furious sentence cannot own the summary.
            "pain": weights["pain_word"] * math.sqrt(hits),
            "negation_hits": suppressed,
            "question": weights["question"] if "?" in sentence else 0.0,
            "number": weights["figure"] if _FIGURE.search(sentence) else 0.0,
            "position": position_score,
        })
    return breakdown


def _cue_scores(sentences: list[str], rules: dict) -> list[float]:
    return [
        s["pain"] + s["question"] + s["number"] + s["position"]
        for s in _signals(sentences, rules)
    ]


def _luhn_scores(sentences: list[str]) -> list[float]:
    """Term frequency after stopword removal, per sentence.

    Sums are over integer counts and the term list is sorted, so the result does
    not depend on set iteration order or float associativity.
    """
    per_sentence = [[w for w in _words(s) if w not in _STOP] for s in sentences]
    frequency: Counter = Counter()
    for tokens in per_sentence:
        frequency.update(tokens)

    # Luhn's "significant words" are the ones that recur. When nothing recurs —
    # short text, or text that never repeats itself — every term counts, or the
    # signal would be uniformly zero and the blend would collapse to cue alone.
    threshold = 2 if any(count >= 2 for count in frequency.values()) else 1

    scores = []
    for tokens in per_sentence:
        if not tokens:
            scores.append(0.0)
            continue
        weight = sum(
            frequency[term] for term in sorted(set(tokens)) if frequency[term] >= threshold
        )
        scores.append(weight / math.sqrt(len(tokens)))
    return scores


def _normalise(scores: list[float]) -> list[float]:
    if not scores:
        return []
    low, high = min(scores), max(scores)
    if high - low < 1e-12:
        return [0.0] * len(scores)
    return [(s - low) / (high - low) for s in scores]


def score_detail(
    sentences: list[str], rules: dict | None = None
) -> tuple[list[float], list[dict]]:
    """Blended scores, and the signals each one was built from."""
    if not sentences:
        return [], []
    rules = load_rules() if rules is None else rules
    weights = rules["weights"]

    breakdown = _signals(sentences, rules)
    cue = _normalise(
        [s["pain"] + s["question"] + s["number"] + s["position"] for s in breakdown]
    )
    frequency = _normalise(_luhn_scores(sentences))

    totals = []
    for signals, c, f in zip(breakdown, cue, frequency):
        signals["frequency"] = f
        totals.append(weights["cue_blend"] * c + weights["luhn_blend"] * f)
    return totals, breakdown


def rank_sentences(sentences: list[str], rules: dict | None = None) -> list[float]:
    """The blended score per sentence, in sentence order."""
    return score_detail(sentences, rules)[0]


def summarize(
    text: str,
    rules: dict | None = None,
    source: str = "unknown",
    log_path: Path | None = None,
) -> list[str]:
    """Return the sentences worth hearing, in the order they were written.

    Anything that should be read as-is comes back whole. Everything else comes
    back as the top-scoring eligible sentences, re-sorted into source order so
    the summary still reads forwards.

    Every call writes one line to the log, bypasses included: "it decided not to
    bother, and here is which rule said so" is exactly as useful when tuning as
    a list of picks.
    """
    sentences = [piece for _s, _e, piece in chunks(text)]
    reason = bypass_reason(text, sentences) if sentences else "empty"

    if reason is not None:
        log_run(
            _record(text, sentences, reason, [], [], [], None, source), log_path
        )
        return sentences

    eligible = eligible_indexes(sentences)
    scores, breakdown = score_detail(sentences, rules)
    keep = target_count(len(eligible))

    # Index breaks ties, so an even score never depends on sort implementation.
    order = sorted(eligible, key=lambda i: (-scores[i], i))
    chosen = sorted(order[:keep])

    log_run(
        _record(text, sentences, None, chosen, scores, breakdown, keep, source),
        log_path,
    )
    return [sentences[i] for i in chosen]
