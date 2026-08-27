"""Pick the sentences that carry the pain out of a piece of text.

Extractive, not generative: nothing is written, sentences are chosen. That keeps
the app's promise — no model, no keys, no network — and it keeps the result
defensible, because every spoken word appeared in the source.

Two scores are blended per sentence:

  *cue* — vocabulary and shape. Words that mark trouble ("failed", "blocked",
  "deadline"), questions, figures with units or money, and position, since the
  first and last paragraph usually carry the ask and the consequence.

  *TextRank* — how much a sentence looks like the rest of the text. A sentence
  sharing vocabulary with many others is usually the one restating the point.

Cue-only picks vivid outliers; TextRank-only picks the blandly central. The
blend is what stops either failure mode.

The vocabulary and the weights live in %APPDATA%\\ReadAloud\\summary_rules.json
so they can be tuned without a rebuild.

Determinism is a requirement, not an accident: no clock, no randomness, and
every ordering that could depend on set or dict iteration is sorted explicitly.
"""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path

from chunker import chunks

RULES_PATH = (
    Path(os.environ.get("APPDATA") or Path.home()) / "ReadAloud" / "summary_rules.json"
)

# Below either of these a summary is worse than the text it replaces.
MIN_SENTENCES = 4
MIN_WORDS = 60

K_DIVISOR = 5
K_MIN, K_MAX = 2, 8

DEFAULT_RULES: dict = {
    "pain_words": [
        "again", "blocked", "broken", "cannot", "can't", "complaint", "cost",
        "couldn't", "deadline", "denied", "didn't", "doesn't", "don't", "error",
        "expensive", "fail", "failed", "failing", "fails", "isn't", "late",
        "missing", "must", "never", "no", "not", "outage", "overdue", "refund",
        "risk", "slow", "still", "stuck", "unable", "urgent", "wasn't", "won't",
        "wrong",
    ],
    "weights": {
        "pain_word": 1.0,
        "question": 0.8,
        "figure": 0.7,
        "first_paragraph": 0.6,
        "last_paragraph": 0.5,
        "cue_blend": 0.6,
        "textrank_blend": 0.4,
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
# Words too common to say anything about what a sentence is about.
_STOP = frozenset("""
a an and are as at be been being but by for from had has have he her his i if in
into is it its me my of on or our she that the their them there these they this
to was we were what when which who will with you your
""".split())


def load_rules(path: Path | None = None) -> dict:
    """Read the tuning file, writing the defaults out on first run.

    A broken or hand-mangled file falls back to the defaults rather than
    stopping the app; anything the file does not mention keeps its default.
    """
    path = RULES_PATH if path is None else path
    rules = {"pain_words": list(DEFAULT_RULES["pain_words"]),
             "weights": dict(DEFAULT_RULES["weights"])}

    try:
        stored = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        save_rules(rules, path)
        return rules

    if isinstance(stored, dict):
        words = stored.get("pain_words")
        if isinstance(words, list):
            rules["pain_words"] = [str(w).lower() for w in words if str(w).strip()]
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


def target_count(sentence_count: int) -> int:
    """How many sentences a summary of this length should keep."""
    return max(K_MIN, min(K_MAX, math.ceil(sentence_count / K_DIVISOR)))


def _words(sentence: str) -> list[str]:
    return _WORD.findall(sentence.lower())


def should_summarize(text: str, sentences: list[str] | None = None) -> bool:
    """Short text is its own best summary."""
    if sentences is None:
        sentences = [piece for _s, _e, piece in chunks(text)]
    if len(sentences) < MIN_SENTENCES:
        return False
    return len(_WORD.findall(text.lower())) >= MIN_WORDS


def _cue_scores(sentences: list[str], rules: dict) -> list[float]:
    weights = rules["weights"]
    pain = frozenset(rules["pain_words"])
    total = len(sentences)
    # Treat the opening and closing fifth as the first and last paragraph: it
    # needs no paragraph structure and behaves the same on a wall of text.
    edge = max(1, total // 5)

    scores = []
    for index, sentence in enumerate(sentences):
        words = _words(sentence)
        hits = sum(1 for word in words if word in pain)
        # Saturating, so one furious sentence cannot own the whole summary.
        score = weights["pain_word"] * math.sqrt(hits)

        if "?" in sentence:
            score += weights["question"]
        if _FIGURE.search(sentence):
            score += weights["figure"]
        if index < edge:
            score += weights["first_paragraph"]
        elif index >= total - edge:
            score += weights["last_paragraph"]

        scores.append(score)
    return scores


def _textrank_scores(sentences: list[str], iterations: int = 30) -> list[float]:
    """Undirected PageRank over sentence similarity.

    Similarity is shared non-stopword vocabulary, normalised by length so a long
    sentence is not central merely for being long.
    """
    total = len(sentences)
    bags = [frozenset(w for w in _words(s) if w not in _STOP) for s in sentences]

    weights = [[0.0] * total for _ in range(total)]
    for i in range(total):
        for j in range(i + 1, total):
            shared = len(bags[i] & bags[j])
            if not shared:
                continue
            norm = math.log(len(bags[i]) + 1) + math.log(len(bags[j]) + 1)
            similarity = shared / norm if norm else 0.0
            weights[i][j] = weights[j][i] = similarity

    outgoing = [sum(row) for row in weights]
    rank = [1.0 / total] * total
    damping = 0.85

    for _step in range(iterations):
        updated = []
        for i in range(total):
            inflow = sum(
                rank[j] * weights[j][i] / outgoing[j]
                for j in range(total)
                if outgoing[j] > 0 and weights[j][i]
            )
            updated.append((1 - damping) / total + damping * inflow)
        rank = updated
    return rank


def _normalise(scores: list[float]) -> list[float]:
    low, high = min(scores), max(scores)
    if high - low < 1e-12:
        return [0.0] * len(scores)
    return [(s - low) / (high - low) for s in scores]


def rank_sentences(sentences: list[str], rules: dict | None = None) -> list[float]:
    """The blended score per sentence, in sentence order."""
    if not sentences:
        return []
    rules = load_rules() if rules is None else rules
    weights = rules["weights"]

    cue = _normalise(_cue_scores(sentences, rules))
    textrank = _normalise(_textrank_scores(sentences))
    return [
        weights["cue_blend"] * c + weights["textrank_blend"] * t
        for c, t in zip(cue, textrank)
    ]


def summarize(text: str, rules: dict | None = None) -> list[str]:
    """Return the sentences worth hearing, in the order they were written.

    Short text comes back whole. Everything else comes back as the top-scoring
    sentences, re-sorted into source order so the summary still reads forwards.
    """
    sentences = [piece for _s, _e, piece in chunks(text)]
    if not sentences:
        return []
    if not should_summarize(text, sentences):
        return sentences

    scores = rank_sentences(sentences, rules)
    keep = target_count(len(sentences))

    # Index breaks ties, so an even score never depends on sort implementation.
    order = sorted(range(len(sentences)), key=lambda i: (-scores[i], i))
    return [sentences[i] for i in sorted(order[:keep])]
