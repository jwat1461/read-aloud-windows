"""Read summary_log.jsonl and say what the scorer has actually been doing.

Read-only. Nothing here writes, and nothing here needs the app to be running.

    python tools\\summary_log_report.py
    python tools\\summary_log_report.py <path-to-log>
"""

import collections
import json
import os
import sys
from pathlib import Path

DEFAULT_LOG = (
    Path(os.environ.get("APPDATA") or Path.home()) / "ReadAloud" / "summary_log.jsonl"
)

SIGNALS = ("pain", "action", "question", "number", "position", "header_bonus",
           "frequency", "negation_hits")


def load(path: Path) -> list[dict]:
    rows = []
    for number, line in enumerate(path.read_text("utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            print(f"  (line {number} is not JSON, skipped)")
    return rows


def bar(count: int, total: int, width: int = 28) -> str:
    if not total:
        return ""
    filled = round(width * count / total)
    return "#" * filled + "." * (width - filled)


def section(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_LOG
    if not path.exists():
        print(f"No log at {path}")
        print("Turn summary mode on and read something; it is written as it goes.")
        return 1

    rows = load(path)
    if not rows:
        print(f"{path} is empty.")
        return 1

    # Startup rows say which weights the rows after them were scored with. They
    # are not calls, so they must not land in any of the per-call counts.
    weight_rows = [r for r in rows if r.get("event") == "weights"]
    rows = [r for r in rows if r.get("event") != "weights"]
    if not rows:
        print(f"{path} holds no scored calls yet.")
        return 1

    print(f"{path}")
    print(f"{len(rows)} calls, {rows[0]['timestamp']} to {rows[-1]['timestamp']}")

    if weight_rows:
        latest = weight_rows[-1]
        section("Weights in force (most recent startup)")
        budget = latest.get("budget", {})
        print(f"  rules version {latest.get('rules_version')}, "
              f"negation window {latest.get('negation_window')}")
        for name, value in sorted(latest.get("weights", {}).items()):
            print(f"  {name:<18} {value}")
        if budget:
            print(f"  budget: ratio {budget.get('ratio')}, "
                  f"k {budget.get('min_sentences')}-{budget.get('max_sentences')}, "
                  f"min chars {budget.get('min_chars')}")
        if len(weight_rows) > 1:
            print(f"  ({len(weight_rows)} startups recorded; "
                  f"scores above may span more than one weight set)")

    # ------------------------------------------------------------ by source
    section("Calls by source")
    sources = collections.Counter(r.get("source", "unlabelled") for r in rows)
    for name, count in sources.most_common():
        print(f"  {name:<10} {count:4}  {bar(count, len(rows))}")

    # ------------------------------------------------------------- bypasses
    section("Bypasses")
    bypassed = [r for r in rows if r.get("bypass_reason")]
    summarized = [r for r in rows if not r.get("bypass_reason")]
    print(f"  summarized {len(summarized):4}  {bar(len(summarized), len(rows))}")
    print(f"  bypassed   {len(bypassed):4}  {bar(len(bypassed), len(rows))}")
    print()
    for reason, count in collections.Counter(
        r["bypass_reason"] for r in bypassed
    ).most_common():
        share = 100 * count / len(rows)
        print(f"  {reason:<22} {count:4}  ({share:.0f}% of all calls)")

    if bypassed:
        words = sorted(r.get("word_count", 0) for r in bypassed)
        middle = words[len(words) // 2]
        print()
        print(f"  bypassed clips: median {middle} words, "
              f"largest {words[-1]}, smallest {words[0]}")

    # ---------------------------------------------------------------- sizes
    section("What it was given")
    if summarized:
        counts = sorted(r["sentence_count"] for r in summarized)
        print(f"  sentences: min {counts[0]}, median {counts[len(counts)//2]}, "
              f"max {counts[-1]}")
        words = sorted(r["word_count"] for r in summarized)
        print(f"  words:     min {words[0]}, median {words[len(words)//2]}, "
              f"max {words[-1]}")

    section("k distribution")
    ks = collections.Counter(r["k"] for r in summarized if r.get("k"))
    for value in sorted(ks):
        print(f"  k={value}  {ks[value]:4}  {bar(ks[value], len(summarized))}")
    picked_total = sum(len(r.get("picked", [])) for r in summarized)
    print(f"  {picked_total} sentences picked across {len(summarized)} summaries")

    # --------------------------------------------------------- signal rates
    section("Signal fire rates on picked sentences")
    print(f"  (out of {picked_total} picked sentences)")
    print()
    picks = [p for r in summarized for p in r.get("picked", [])]
    never = []
    for signal in SIGNALS:
        present = [p for p in picks if signal in p]
        fired = [p for p in present if p.get(signal)]
        if not present:
            never.append(f"{signal} (not recorded in this log at all)")
            print(f"  {signal:<14}    -   not logged in these entries")
            continue
        share = 100 * len(fired) / len(present)
        print(f"  {signal:<14} {len(fired):4}/{len(present):<4} {share:5.1f}%  "
              f"{bar(len(fired), len(present))}")
        if not fired:
            never.append(signal)

    if never:
        section("Signals that never fired")
        for name in never:
            print(f"  {name}")
        print()
        print("  A signal that never fires is a weight you cannot tune: moving it")
        print("  changes nothing, so the sentence you wanted is being decided by")
        print("  whatever is left.")

    # ------------------------------------------------------- what carried it
    section("What actually carried the picks")
    carried = collections.Counter()
    for pick in picks:
        parts = {s: pick.get(s, 0.0) for s in ("pain", "action", "question",
                                               "number", "position",
                                               "header_bonus")}
        cue = sum(parts.values())
        if cue <= 0:
            carried["frequency alone"] += 1
        else:
            carried[max(parts, key=lambda k: parts[k]) + " (+ frequency)"] += 1
    for name, count in carried.most_common():
        share = 100 * count / max(picked_total, 1)
        print(f"  {name:<28} {count:4}  {share:5.1f}%  {bar(count, picked_total)}")

    # --------------------------------------------------------------- margin
    section("Margin between the last pick and the best sentence left behind")
    have_near = [r for r in summarized if r.get("near_misses")]
    if not have_near:
        print("  Not computable from this log.")
        print()
        print("  Only picked sentences are recorded, so there is nothing to compare")
        print("  the weakest pick against. This is exactly the gap near-miss")
        print("  logging closes: without it you can see what won but never by how")
        print("  much, and so never which weight would have changed the answer.")
    else:
        margins = []
        for row in have_near:
            worst = min(p["score"] for p in row["picked"])
            best_missed = max(p["score"] for p in row["near_misses"])
            margins.append(worst - best_missed)
        margins.sort()
        print(f"  {len(margins)} calls with near-miss data")
        print(f"  median margin {margins[len(margins)//2]:.4f}, "
              f"tightest {margins[0]:.4f}, widest {margins[-1]:.4f}")
        tight = [m for m in margins if m < 0.05]
        print(f"  {len(tight)} within 0.05 -- these are the ones a small weight "
              f"change would flip")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
