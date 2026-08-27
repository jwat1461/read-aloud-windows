"""Re-record what the extractive summarizer picks on the fixed 12-file corpus.

The snapshot is asserted on every test run, so it fails loudly when scoring
changes. That is the point: run this only when the change was deliberate, and
read the diff before committing it.

    python tools\\resnapshot.py
    python tools\\resnapshot.py --show     # print the picks, write nothing
"""

import json
import sys
import tempfile
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
sys.path.insert(0, str(APP))

import summarize  # noqa: E402

GOLDEN = APP / "test_golden_pieces.json"
SNAPSHOT = APP / "test_summary_snapshot.json"


def main() -> int:
    show_only = "--show" in sys.argv
    golden = json.loads(GOLDEN.read_text("utf-8"))
    # A temporary rules file, so a tuned copy in %APPDATA% cannot leak into the
    # snapshot and make it unreproducible on anybody else's machine.
    rules = summarize.load_rules(Path(tempfile.mkdtemp()) / "rules.json")

    recorded = {}
    for name in sorted(golden):
        text = golden[name]["text"]
        reason = summarize.bypass_reason(text)
        picked = summarize.summarize(text, rules)
        recorded[name] = {"bypass": reason, "picked": picked}

        print(f"--- {name}  ({reason or f'summarized, {len(picked)} sentences'})")
        if reason is None:
            for sentence in picked:
                print("      " + " ".join(sentence.split()))

    if show_only:
        return 0

    SNAPSHOT.write_bytes(
        (json.dumps(recorded, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
        .encode("utf-8")
    )
    print()
    print("wrote", SNAPSHOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
