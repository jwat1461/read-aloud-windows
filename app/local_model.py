"""Optional local-model summaries through Ollama, on localhost only.

Off by default. The app's promise is that it makes no network calls, and this
does not break it: 127.0.0.1 is not the network, the port is a process on this
machine, and nothing is configurable to point anywhere else — `settings.py`
rejects any other host rather than trusting it.

Everything here fails soft. A refused connection, either timeout, an empty
reply or malformed JSON all raise `Unavailable`, and the caller falls back to
the extractive summarizer without saying a word about it at the time. The user
finds out on the next toggle, in the balloon, which is the first moment they are
actually asking.

Determinism is asked for as loudly as Ollama allows: temperature 0 and seed 0.
That is still a model, so it is not the guarantee the extractive path gives —
which is exactly why the extractive path is the default.
"""

from __future__ import annotations

import http.client
import json
import socket
import threading

HOST = "127.0.0.1"
PORT = 11434
PATH = "/api/generate"

# Set separately on purpose: reaching a process on this machine either happens
# at once or is not going to, while generating 200 tokens legitimately takes a
# while on a cold model.
CONNECT_TIMEOUT = 2.0
READ_TIMEOUT = 20.0

KEEP_ALIVE = "30m"
NUM_PREDICT = 200
MAX_SENTENCES = 8

DEFAULT_MODEL = "llama3.2"
ALTERNATIVE_MODEL = "qwen2.5:3b"

PROMPT = (
    "Read the text below and say what the problems are: what is failing, "
    "blocked, late, expensive, or being asked for. Reply with at most eight "
    "short sentences meant to be read out loud. No markdown, no bullet points, "
    "no numbering, no preamble, no closing remark. If there is no problem in "
    "the text, say what it is about in one sentence.\n\n"
    "TEXT:\n"
)

_warmed: set[str] = set()
_lock = threading.Lock()


class Unavailable(RuntimeError):
    """Ollama is not there, not answering, or not making sense."""


def reset_session() -> None:
    """Forget which models have been warmed. Called by tests; the real app just
    restarts."""
    with _lock:
        _warmed.clear()


def warmed_models() -> set[str]:
    with _lock:
        return set(_warmed)


def _post(payload: dict, read_timeout: float | None = None) -> dict:
    body = json.dumps(payload).encode("utf-8")
    connection = http.client.HTTPConnection(HOST, PORT, timeout=CONNECT_TIMEOUT)
    try:
        connection.connect()
        # Past the handshake the budget is generation, not reachability.
        connection.sock.settimeout(
            READ_TIMEOUT if read_timeout is None else read_timeout
        )
        connection.request(
            "POST", PATH, body, {"Content-Type": "application/json"}
        )
        raw = connection.getresponse().read()
    except (OSError, socket.timeout, http.client.HTTPException) as exc:
        raise Unavailable(f"{type(exc).__name__}: {exc}") from exc
    finally:
        connection.close()

    if not raw.strip():
        raise Unavailable("empty reply")
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise Unavailable(f"malformed JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise Unavailable("reply was not an object")
    return parsed


def warm_up(model: str = DEFAULT_MODEL) -> bool:
    """Ask Ollama to load the model and hold it, once per session.

    An empty prompt makes Ollama resident without generating anything, so the
    first real summary is not paying the load cost with the user waiting.
    """
    with _lock:
        if model in _warmed:
            return False
        _warmed.add(model)

    try:
        _post({"model": model, "prompt": "", "stream": False,
               "keep_alive": KEEP_ALIVE})
        return True
    except Unavailable:
        # Leave it marked: a machine without Ollama should not be re-probed
        # before every single summary.
        return False


def summarize(text: str, model: str = DEFAULT_MODEL,
              read_timeout: float | None = None) -> list[str]:
    """Return the model's pain-point sentences, or raise Unavailable."""
    from chunker import chunks  # local: keeps this module importable on its own

    warm_up(model)
    payload = {
        "model": model,
        "prompt": PROMPT + text,
        "stream": False,
        "keep_alive": KEEP_ALIVE,
        "options": {"temperature": 0, "seed": 0, "num_predict": NUM_PREDICT},
    }
    reply = _post(payload, read_timeout)

    answer = reply.get("response")
    if not isinstance(answer, str) or not answer.strip():
        raise Unavailable("no response field")

    # Models add bullets and numbering however firmly they are asked not to.
    cleaned = []
    for line in answer.splitlines():
        line = line.strip().lstrip("-*•").strip()
        while line[:2].rstrip(".)").isdigit() and line[:1].isdigit():
            line = line.split(".", 1)[-1].split(")", 1)[-1].strip()
        if line:
            cleaned.append(line)
    joined = " ".join(cleaned)
    if not joined:
        raise Unavailable("nothing left after cleaning")

    sentences = [piece for _s, _e, piece in chunks(joined)]
    if not sentences:
        raise Unavailable("nothing left after chunking")
    return sentences[:MAX_SENTENCES]
