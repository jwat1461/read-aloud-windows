"""Say what a piece of code is and what it does, instead of reading it out.

Reading source aloud is close to useless: you cannot hear indentation, and a
PowerShell one-liner spelled out character by character takes longer to listen
to than it took to write. What a listener wants is which language, how big, and
what it will do to their machine.

Everything here is structural and deterministic — regular expressions over
lines, no model. The optional Ollama tier sits on top of this, never instead of
it: its answer is always prefixed with the anchor line computed here, so a
confident wrong explanation is still pinned to facts that can be checked.

Detection refuses to guess. An ambiguous clip comes back as "unknown" and is
described by shape alone, because naming the wrong language is worse than
declining to name one.
"""

from __future__ import annotations

import re

LANGUAGES = ("powershell", "python", "sql", "rust", "javascript", "batch", "unknown")

MAX_DESCRIPTION_SENTENCES = 6

# PowerShell verbs worth interrupting someone about.
# Format is deliberately absent: Format-Volume is dangerous but Format-Table and
# Format-List are how half of all PowerShell output is printed, and crying wolf
# on those would train the listener to ignore the warning that matters.
DESTRUCTIVE_VERBS = ("Remove", "Stop", "Set", "Clear", "Disable", "Uninstall",
                     "Kill", "Reset", "Revoke")

_SHEBANG = re.compile(r"^#!.*\b(python[0-9.]*|node|bash|sh|pwsh)\b", re.MULTILINE)
_PS_CMDLET = re.compile(r"\b([A-Z][a-z]+)-([A-Z][A-Za-z]+)\b")
_PS_VAR = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*")
_PS_PARAM = re.compile(r"(?<!\S)-[A-Za-z][A-Za-z0-9]*\b")
_PY_DEF = re.compile(r"^\s*(?:def|class)\s+[A-Za-z_]", re.MULTILINE)
_PY_IMPORT = re.compile(r"^\s*(?:import|from)\s+[A-Za-z_.]", re.MULTILINE)
_PY_MAIN = re.compile(r'^\s*if\s+__name__\s*==\s*[\'"]__main__[\'"]', re.MULTILINE)
_SQL = re.compile(
    r"\b(SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM|CREATE\s+TABLE|ALTER\s+TABLE"
    r"|DROP\s+TABLE|MERGE)\b", re.IGNORECASE)
_SQL_FROM = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+([A-Za-z_][\w.\[\]]*)",
                       re.IGNORECASE)
_SQL_WHERE = re.compile(r"\bWHERE\b", re.IGNORECASE)
_RUST = re.compile(r"\bfn\s+[a-z_]|\blet\s+mut\b|::<|\bimpl\b|\bpub\s+fn\b|&str\b")
_JS = re.compile(r"\b(?:const|let|var)\s+[A-Za-z_$]|=>|\bfunction\s*[A-Za-z_$(]"
                 r"|\bconsole\.log\b|\brequire\(|\bexport\s+(?:default|const)\b")
_BATCH = re.compile(r"^\s*@echo\s+off|^\s*setlocal\b|%~dp0|^\s*goto\s+:|%[A-Za-z_]+%",
                    re.IGNORECASE | re.MULTILINE)


def _lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


def _score_language(text: str) -> dict[str, int]:
    """Weighted evidence per language. Deliberately blunt and countable."""
    scores = dict.fromkeys(LANGUAGES[:-1], 0)

    shebang = _SHEBANG.search(text)
    if shebang:
        word = shebang.group(1).lower()
        if word.startswith("python"):
            scores["python"] += 4
        elif word == "node":
            scores["javascript"] += 4
        elif word == "pwsh":
            scores["powershell"] += 4

    cmdlets = _PS_CMDLET.findall(text)
    scores["powershell"] += 3 * min(len(cmdlets), 3)
    if _PS_VAR.search(text) and ("|" in text or _PS_PARAM.search(text)):
        scores["powershell"] += 2

    scores["python"] += 3 * min(len(_PY_DEF.findall(text)), 2)
    scores["python"] += 2 * min(len(_PY_IMPORT.findall(text)), 2)
    if _PY_MAIN.search(text):
        scores["python"] += 3

    scores["sql"] += 4 * min(len(_SQL.findall(text)), 2)
    scores["rust"] += 3 * min(len(_RUST.findall(text)), 2)
    scores["javascript"] += 2 * min(len(_JS.findall(text)), 3)
    scores["batch"] += 3 * min(len(_BATCH.findall(text)), 2)
    return scores


def detect_language(text: str) -> str:
    """One of LANGUAGES. "unknown" whenever the evidence is thin or split.

    A clear winner needs both a real score and daylight over the runner-up:
    naming the wrong language out loud is worse than declining to name one.
    """
    if not text.strip():
        return "unknown"
    scores = _score_language(text)
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    (best, best_score), (_second, second_score) = ranked[0], ranked[1]
    if best_score < 3 or best_score - second_score < 2:
        return "unknown"
    return best


# ------------------------------------------------------------- descriptions


def _join(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def anchor(text: str, lang: str | None = None) -> str:
    """The one line that is always true, whatever else is said afterwards."""
    lang = detect_language(text) if lang is None else lang
    count = len(_lines(text))
    plural = "" if count == 1 else "s"
    name = "Code, language not recognised," if lang == "unknown" else lang.capitalize()
    if lang == "powershell":
        name = "PowerShell"
    elif lang == "sql":
        name = "SQL"
    elif lang == "javascript":
        name = "JavaScript"
    return f"{name} {count} line{plural}."


def _describe_powershell(text: str) -> list[str]:
    facts = []
    verbs = [f"{verb}-{noun}" for verb, noun in _PS_CMDLET.findall(text)]
    if verbs:
        shape = _join([v.split("-")[0] for v in verbs][:6])
        piped = " piped" if "|" in text else ""
        facts.append(f"It runs {shape}{piped}.")

    dangerous = sorted({v for v in verbs if v.split("-")[0] in DESTRUCTIVE_VERBS})
    if dangerous:
        facts.append(f"It changes things: {_join(dangerous)}.")
    if re.search(r"(?<!\S)-[Ff]orce\b", text):
        facts.append("It uses minus Force, so it will not stop to ask.")
    if re.search(r"(?<!\S)-[Rr]ecurse\b", text):
        facts.append("It works recursively.")
    return facts


def _describe_python(text: str) -> list[str]:
    facts = []
    imports = sorted({
        line.split()[1].split(".")[0]
        for line in text.splitlines()
        if re.match(r"^\s*(?:import|from)\s+[A-Za-z_.]", line)
    })
    if imports:
        facts.append(f"It imports {_join(imports[:6])}.")

    functions = re.findall(r"^\s*def\s+([A-Za-z_]\w*)", text, re.MULTILINE)
    classes = re.findall(r"^\s*class\s+([A-Za-z_]\w*)", text, re.MULTILINE)
    if classes:
        facts.append(f"It defines {_join(sorted(set(classes))[:5])}.")
    if functions:
        named = _join(sorted(set(functions))[:5])
        facts.append(f"It has {len(set(functions))} function"
                     f"{'' if len(set(functions)) == 1 else 's'}: {named}.")
    if _PY_MAIN.search(text):
        facts.append("It runs something when called directly.")
    return facts


def _describe_sql(text: str) -> list[str]:
    facts = []
    statements = [m.upper().split()[0] for m in _SQL.findall(text)]
    if statements:
        facts.append(f"It is {'an' if statements[0][0] in 'AIU' else 'a'} "
                     f"{statements[0]} statement.")

    tables = sorted({t.strip("[]") for t in _SQL_FROM.findall(text)})
    if tables:
        facts.append(f"It touches {_join(tables[:5])}.")

    upper = text.upper()
    for verb in ("UPDATE", "DELETE"):
        if re.search(rf"\b{verb}\b", upper) and not _SQL_WHERE.search(text):
            facts.append(f"Careful: this {verb}S every row. There is no WHERE clause.")
            break
    return facts


def _describe_rust(text: str) -> list[str]:
    functions = re.findall(r"\bfn\s+([a-z_]\w*)", text)
    facts = []
    if functions:
        facts.append(f"It defines {_join(sorted(set(functions))[:5])}.")
    if "let mut" in text:
        facts.append("It uses mutable state.")
    return facts


def _describe_javascript(text: str) -> list[str]:
    functions = re.findall(r"\bfunction\s+([A-Za-z_$]\w*)", text)
    functions += re.findall(r"\b(?:const|let)\s+([A-Za-z_$]\w*)\s*=\s*(?:async\s*)?\(",
                            text)
    facts = []
    if functions:
        facts.append(f"It defines {_join(sorted(set(functions))[:5])}.")
    if "await " in text or "async " in text:
        facts.append("It does asynchronous work.")
    return facts


def _describe_batch(text: str) -> list[str]:
    facts = []
    if re.search(r"^\s*@echo\s+off", text, re.IGNORECASE | re.MULTILINE):
        facts.append("It runs quietly.")
    calls = re.findall(r"^\s*(?:start|call)\s+(\S+)", text, re.IGNORECASE | re.MULTILINE)
    if calls:
        facts.append(f"It launches {_join(sorted(set(calls))[:4])}.")
    return facts


_DESCRIBERS = {
    "powershell": _describe_powershell,
    "python": _describe_python,
    "sql": _describe_sql,
    "rust": _describe_rust,
    "javascript": _describe_javascript,
    "batch": _describe_batch,
}


def describe_code(text: str, lang: str | None = None) -> list[str]:
    """Spoken sentences describing the code, anchor line first.

    Capped at MAX_DESCRIPTION_SENTENCES, because past about six the listener has
    stopped following and would have been better off reading it.
    """
    lang = detect_language(text) if lang is None else lang
    sentences = [anchor(text, lang)]
    describer = _DESCRIBERS.get(lang)
    if describer:
        sentences.extend(describer(text))
    return sentences[:MAX_DESCRIPTION_SENTENCES]
