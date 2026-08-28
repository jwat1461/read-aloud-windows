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

Sentences inherit from the header they sit under. "Waiting on others:" tells you
what the lines below it are, and a status document says most of what matters in
its headings, so a header that scores lends part of its score to everything
under it until the next one. Headers are never themselves selected -- reading a
heading aloud in place of its content is not a summary.

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

# One line, set when a user's rules file was brought forward to a newer version.
upgrade_notes: list[str] = []

# How many losing sentences to record beside the winners. Three is enough to
# see the margin without turning every line into a transcript of the document.
NEAR_MISS_COUNT = 3

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
RULES_VERSION = 4

DEFAULT_RULES: dict = {
    "version": RULES_VERSION,
    # A phrase with a space in it is matched as a phrase; everything else is a
    # single token, plural-folded.
    "pain_words": [
        "again", "blocked", "broken", "cannot", "cant", "complaint", "cost",
        "deadline", "error", "expensive", "fail", "failed", "late", "missing",
        "must", "never", "overdue", "owed", "pending", "refund", "risk", "slow",
        "still", "stuck", "urgent", "waiting", "waiting on", "wont", "wrong",
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
    # Verbs that open an instruction. An agent telling you what to do writes
    # imperatives, and an imperative scores nothing against pain vocabulary --
    # which is why steps were losing to complaints in every read.
    "action_words": [
        "add", "check", "click", "close", "confirm", "copy", "create", "delete",
        "disable", "download", "drag", "enable", "enter", "ensure", "go",
        "install", "make", "navigate", "open", "paste", "press", "put", "reload",
        "remove", "rename", "replace", "restart", "run", "save", "scroll",
        "select", "set", "start", "stop", "switch", "turn", "type", "update",
        "use", "verify",
    ],
    # Phrases that mark an instruction without opening with a verb.
    "action_phrases": [
        "you need to", "you should", "you must", "make sure", "be sure to",
        "do not forget", "dont forget", "remember to", "next step", "first you",
        "then you", "note that",
    ],
    "negation_window": 3,
    "weights": {
        "pain_word": 1.0,
        "question": 0.8,
        "figure": 0.7,
        # Modest, per the note above: this is email and chat, not news copy.
        "first_paragraph": 0.25,
        "last_paragraph": 0.2,
        # An instruction is worth at least as much as a complaint: reading
        # somebody the problem while dropping the fix is the wrong half.
        "action_word": 1.2,
        # What a scoring header lends to each sentence beneath it.
        "header_weight": 0.5,
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

# Headers, by the three shapes that actually turn up in pasted text.
_MD_HEADER = re.compile(r"^\s{0,3}#{1,6}\s+\S")
_BOLD_ONLY = re.compile(r"^\s*(\*\*|__)\s*[^*_\s].*?\s*(\*\*|__)\s*:?\s*$")
_COLON_HEADER = re.compile(r"^[^.!?\n]{1,70}:\s*$")
HEADER_MAX_WORDS = 8

# "1." / "2)" / "Step 3" at the start of a line: a numbered instruction.
_STEP = re.compile(r"^\s*(?:step\s+)?\(?\d{1,2}[.)]?\s+\S", re.IGNORECASE)

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
    # Built from DEFAULT_RULES rather than a hand-written list of keys. The
    # hand-written version silently dropped every key added after it, so a new
    # signal read an empty vocabulary and scored zero for everything.
    rules = {
        key: (dict(value) if isinstance(value, dict)
              else list(value) if isinstance(value, list)
              else value)
        for key, value in DEFAULT_RULES.items()
    }
    rules["version"] = RULES_VERSION

    try:
        stored = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        save_rules(rules, path)
        return rules

    # An older file is merged, not replaced: whatever was tuned by hand stays,
    # and only the keys a newer build added are filled in. Replacing it outright
    # threw away the tuning, which is the whole reason the file exists.
    #
    # One thing is repaired rather than kept. Version 1 listed "no" and "not" as
    # pain words and version 2 made them negators; carried forward, every
    # negated sentence scores as pain, which is precisely what the negation
    # window exists to prevent. Any word that is also a negator is dropped.
    if isinstance(stored, dict) and stored.get("version") != RULES_VERSION:
        merged = dict(rules)
        added = []
        for key in ("pain_words", "negations", "action_words", "action_phrases"):
            words = stored.get(key)
            if isinstance(words, list) and words:
                kept = [_fold(str(w)) for w in words if str(w).strip()]
                if key == "pain_words":
                    negators = frozenset(merged["negations"])
                    kept = [w for w in kept if w not in negators]
                merged[key] = sorted(set(kept) | set(rules[key])) if key in rules \
                    else kept
                added += [w for w in rules.get(key, ()) if w not in kept]
        weights = stored.get("weights")
        if isinstance(weights, dict):
            for name in DEFAULT_RULES["weights"]:
                if name in weights:
                    try:
                        merged["weights"][name] = float(weights[name])
                    except (TypeError, ValueError):
                        pass
        merged["version"] = RULES_VERSION
        try:
            merged["negation_window"] = max(
                0, int(stored.get("negation_window", merged["negation_window"]))
            )
        except (TypeError, ValueError):
            pass

        upgrade_notes.clear()
        if added:
            upgrade_notes.append(
                f"summary_rules.json updated to version {RULES_VERSION}; added "
                + ", ".join(sorted(set(added))[:12])
            )
        save_rules(merged, path)
        return merged

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
    missed: list[int] | None = None,
) -> dict:
    if with_text is None:
        with_text = _log_sentence_text()

    def entry(index: int) -> dict:
        signals = breakdown[index]
        row = {
            "index": index,
            "score": round(scores[index], 6),
            "pain": round(signals["pain"], 6),
            "action": round(signals.get("action", 0.0), 6),
            "negation_hits": signals["negation_hits"],
            "question": round(signals["question"], 6),
            "number": round(signals["number"], 6),
            "position": round(signals["position"], 6),
            "header_bonus": round(signals.get("header_bonus", 0.0), 6),
            "frequency": round(signals.get("frequency", 0.0), 6),
        }
        if with_text:
            row["text"] = sentences[index]
        return row

    picked = [entry(index) for index in chosen]
    near = [entry(index) for index in (missed or ())]

    return {
        "timestamp": now_iso(),
        "source": source,
        "sentence_count": len(sentences),
        "word_count": len(_WORD.findall(text.lower())),
        "bypass_reason": reason,
        "k": keep,
        "picked": picked,
        # The sentences that came closest and lost. Without these you can see
        # what won but never by how much, which is the one number that says
        # which weight would have changed the answer.
        "near_misses": near,
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


def is_header(sentence: str) -> bool:
    """A markdown heading, a bold-only line, or a short line ending in a colon.

    Deliberately narrow. A false positive costs a sentence its place in the
    summary and hands its score to whatever follows, which is worse than
    missing a heading altogether.
    """
    stripped = sentence.strip()
    if not stripped:
        return False
    if "\n" in stripped:
        # Without a blank line after it the chunker keeps a heading and the line
        # below it in one piece. That piece is content, and calling it a header
        # would both bar it from selection and hand its score to the next
        # section. A header is a line.
        return False
    if _MD_HEADER.match(stripped):
        return True
    if _BOLD_ONLY.match(stripped):
        return True
    return bool(
        _COLON_HEADER.match(stripped)
        and len(stripped.split()) <= HEADER_MAX_WORDS
    )


def is_excluded_line(sentence: str) -> bool:
    """A sentence that must never be *chosen*, even inside ordinary prose."""
    stripped = sentence.strip()
    if not stripped:
        return True
    if is_header(stripped):
        return True  # a heading read in place of its content is not a summary
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


def _pain_positions(tokens: list[str], vocabulary: frozenset) -> list[int]:
    """Start positions of every pain hit, single words and phrases alike.

    "waiting on" has to be matchable as a phrase or it can only ever be entered
    in the rules file as the bare word, which then also fires on "waiting for
    the kettle". Anything with a space in it is read as a sequence.
    """
    phrases = sorted(
        (tuple(entry.split()) for entry in vocabulary if " " in entry),
        key=len,
        reverse=True,
    )
    hits = []
    index = 0
    while index < len(tokens):
        for phrase in phrases:
            if tuple(tokens[index:index + len(phrase)]) == phrase:
                hits.append(index)
                index += len(phrase)
                break
        else:
            if _matches(tokens[index], vocabulary):
                hits.append(index)
            index += 1
    return hits


def _negated_positions(tokens: list[str], negations: frozenset, window: int) -> set:
    """Token positions falling inside the window after a negator."""
    blocked = set()
    for index, token in enumerate(tokens):
        if _matches(token, negations):
            for offset in range(1, window + 1):
                blocked.add(index + offset)
    return blocked


def _pain_score(sentence: str, rules: dict) -> tuple[float, int]:
    """(score, suppressed) for one line, saturating and negation-aware."""
    pain = frozenset(rules["pain_words"])
    negations = frozenset(rules["negations"])
    blocked = _negated_positions(
        _words(sentence), negations, int(rules["negation_window"])
    )
    positions = _pain_positions(_words(sentence), pain)
    hits = sum(1 for p in positions if p not in blocked)
    suppressed = len(positions) - hits
    # Saturating, so one furious line cannot own the whole summary.
    return rules["weights"]["pain_word"] * math.sqrt(hits), suppressed


def action_score(sentence: str, rules: dict) -> float:
    """How much this sentence reads like something to go and do.

    Three ways in: it opens with an imperative verb, it opens as a numbered
    step, or it carries a phrase like "make sure". Saturating like pain, so a
    paragraph of instructions cannot swamp everything else.
    """
    stripped = sentence.strip()
    if not stripped:
        return 0.0

    verbs = frozenset(rules.get("action_words", ()))
    phrases = [p for p in rules.get("action_phrases", ())]
    tokens = _words(stripped)
    if not tokens:
        return 0.0

    hits = 0
    # Only the opening verb counts. "run" in the middle of a sentence is
    # usually a noun or a past tense, and counting those made ordinary prose
    # look like a checklist.
    lead = tokens[0]
    if _STEP.match(stripped):
        hits += 1
        after = _words(re.sub(r"^\s*(?:step\s+)?\(?\d{1,2}[.)]?\s+", "",
                              stripped, flags=re.IGNORECASE))
        lead = after[0] if after else lead
    if _matches(lead, verbs):
        hits += 1

    folded = " ".join(tokens)
    hits += sum(1 for phrase in phrases if phrase in folded)

    return rules["weights"]["action_word"] * math.sqrt(hits)


def header_bonuses(sentences: list[str], rules: dict) -> list[float]:
    """What each sentence inherits from the header above it.

    A header that scores nothing lends nothing, and it still closes off the one
    before it -- otherwise a neutral section would keep collecting a bonus from
    whatever heading last happened to mention a deadline.
    """
    weight = rules["weights"]["header_weight"]
    bonuses = []
    current = 0.0
    for sentence in sentences:
        if is_header(sentence):
            score, _suppressed = _pain_score(sentence, rules)
            current = score * weight
            bonuses.append(0.0)  # the header itself inherits nothing
        else:
            bonuses.append(current)
    return bonuses


def _signals(sentences: list[str], rules: dict) -> list[dict]:
    """Each sentence's cue signals, kept apart rather than summed.

    Split out so the log can say *why* a sentence won, not just that it did.
    Tuning weights against a single blended number is guesswork.
    """
    weights = rules["weights"]
    total = len(sentences)
    # Treat the opening and closing fifth as first and last paragraph: it needs
    # no paragraph structure and behaves the same on a wall of text.
    edge = max(1, total // 5)
    inherited = header_bonuses(sentences, rules)

    breakdown = []
    for index, sentence in enumerate(sentences):
        pain_score, suppressed = _pain_score(sentence, rules)

        if index < edge:
            position_score = weights["first_paragraph"]
        elif index >= total - edge:
            position_score = weights["last_paragraph"]
        else:
            position_score = 0.0

        breakdown.append({
            "pain": pain_score,
            "action": action_score(sentence, rules),
            "negation_hits": suppressed,
            "question": weights["question"] if "?" in sentence else 0.0,
            "number": weights["figure"] if _FIGURE.search(sentence) else 0.0,
            "position": position_score,
            "header_bonus": inherited[index],
        })
    return breakdown


CUE_PARTS = ("pain", "action", "question", "number", "position", "header_bonus")


def _cue_scores(sentences: list[str], rules: dict) -> list[float]:
    return [
        sum(s[part] for part in CUE_PARTS) for s in _signals(sentences, rules)
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
    cue = _normalise([sum(s[part] for part in CUE_PARTS) for s in breakdown])
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
    missed = order[keep:keep + NEAR_MISS_COUNT]

    log_run(
        _record(text, sentences, None, chosen, scores, breakdown, keep, source,
                missed=missed),
        log_path,
    )
    return [sentences[i] for i in chosen]
