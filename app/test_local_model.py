"""Tests for the optional Ollama tier.

No mocking of our own internals: a real HTTP server is started on 127.0.0.1 and
`local_model.PORT` is pointed at it, so the connection, the timeouts and the
JSON handling are all genuinely exercised. The server records what it was sent,
which is how the warm-up assertions are made.

    python -m unittest test_local_model -v
"""

import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

import local_model
import reading
import settings
import summarize
import test_summary

LONG_TEXT = test_summary.CORPUS["escalation"]

MODEL_REPLY = (
    "The migration failed twice overnight. The reporting rebuild is blocked. "
    "The client wants a refund. It costs four thousand a month."
)


class FakeOllama(BaseHTTPRequestHandler):
    """Whatever `mode` the server was built with, done badly on purpose."""

    def log_message(self, *_args):
        pass  # the test output is noisy enough

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {"unparseable": raw.decode("utf-8", "replace")}
        self.server.received.append(payload)

        mode = self.server.mode
        if mode == "slow":
            time.sleep(self.server.delay)

        body = {
            "ok": json.dumps({"response": MODEL_REPLY, "done": True}).encode(),
            "slow": json.dumps({"response": MODEL_REPLY, "done": True}).encode(),
            "malformed": b'{"response": "unterminated',
            "empty": b"",
            "no_response": json.dumps({"done": True}).encode(),
            "blank_response": json.dumps({"response": "   "}).encode(),
            "not_an_object": b'["a list, somehow"]',
            "bulleted": json.dumps(
                {"response": "1. The migration failed twice.\n"
                             "2. The rebuild is blocked.\n"
                             "- The client wants a refund."}
            ).encode(),
        }[mode]

        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass  # the timeout tests hang up mid-reply; that is the point


def start_server(mode="ok", delay=0.0):
    server = HTTPServer(("127.0.0.1", 0), FakeOllama)
    server.mode = mode
    server.delay = delay
    server.received = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def closed_port():
    """A port with nothing listening: the connection-refused case."""
    server = HTTPServer(("127.0.0.1", 0), FakeOllama)
    port = server.server_port
    server.server_close()
    return port


class OllamaClient(unittest.TestCase):
    def setUp(self):
        local_model.reset_session()
        self.original_port = local_model.PORT
        self.server = None

    def tearDown(self):
        local_model.PORT = self.original_port
        local_model.reset_session()
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()

    def _serve(self, mode="ok", delay=0.0):
        self.server = start_server(mode, delay)
        local_model.PORT = self.server.server_port
        return self.server

    # ------------------------------------------------------------- happy path

    def test_a_working_model_comes_back_as_sentences(self):
        self._serve("ok")
        sentences = local_model.summarize(LONG_TEXT)
        self.assertTrue(sentences)
        self.assertLessEqual(len(sentences), local_model.MAX_SENTENCES)
        self.assertIn("failed twice", " ".join(sentences))

    def test_the_request_is_shaped_the_way_ollama_wants(self):
        server = self._serve("ok")
        local_model.summarize(LONG_TEXT, "llama3.2")

        real = [r for r in server.received if r.get("prompt")]
        self.assertEqual(len(real), 1)
        request = real[0]
        self.assertEqual(request["model"], "llama3.2")
        self.assertFalse(request["stream"])
        self.assertEqual(request["keep_alive"], "30m")
        self.assertEqual(request["options"]["temperature"], 0)
        self.assertEqual(request["options"]["seed"], 0)
        self.assertEqual(request["options"]["num_predict"], 200)
        self.assertIn(LONG_TEXT, request["prompt"])
        for banned in ("markdown", "bullet", "numbering", "preamble"):
            self.assertIn(banned, request["prompt"].lower())

    def test_numbering_and_bullets_are_stripped_out_of_the_reply(self):
        self._serve("bulleted")
        sentences = local_model.summarize(LONG_TEXT)
        joined = " ".join(sentences)
        self.assertNotIn("1.", joined)
        self.assertNotIn("- ", joined)
        self.assertIn("migration failed", joined)

    def test_the_reply_is_capped_at_eight_sentences(self):
        self._serve("ok")
        self.assertLessEqual(
            len(local_model.summarize(LONG_TEXT)), local_model.MAX_SENTENCES
        )

    # ---------------------------------------------------------------- warm-up

    def test_the_warm_up_fires_once_and_carries_keep_alive(self):
        server = self._serve("ok")
        self.assertEqual(local_model.warmed_models(), set())

        local_model.summarize(LONG_TEXT, "llama3.2")
        warmups = [r for r in server.received if r.get("prompt") == ""]
        self.assertEqual(len(warmups), 1, server.received)
        self.assertEqual(warmups[0]["keep_alive"], "30m")
        self.assertEqual(warmups[0]["model"], "llama3.2")

        local_model.summarize(LONG_TEXT, "llama3.2")
        local_model.summarize(LONG_TEXT, "llama3.2")
        self.assertEqual(
            len([r for r in server.received if r.get("prompt") == ""]),
            1,
            "warmed up more than once",
        )

    def test_each_model_is_warmed_separately(self):
        server = self._serve("ok")
        local_model.summarize(LONG_TEXT, "llama3.2")
        local_model.summarize(LONG_TEXT, local_model.ALTERNATIVE_MODEL)
        warmed = {r["model"] for r in server.received if r.get("prompt") == ""}
        self.assertEqual(warmed, {"llama3.2", local_model.ALTERNATIVE_MODEL})

    def test_a_failed_warm_up_is_not_retried_before_every_summary(self):
        local_model.PORT = closed_port()
        self.assertFalse(local_model.warm_up("llama3.2"))
        self.assertIn("llama3.2", local_model.warmed_models())
        self.assertFalse(local_model.warm_up("llama3.2"))

    # -------------------------------------------------------------- failures

    def test_connection_refused_is_unavailable(self):
        local_model.PORT = closed_port()
        with self.assertRaises(local_model.Unavailable):
            local_model.summarize(LONG_TEXT)

    def test_a_read_timeout_is_unavailable(self):
        self._serve("slow", delay=3.0)
        # Warm up first and off the clock: it goes to the same slow server on
        # the full read timeout, and would otherwise be most of what is timed.
        local_model.warm_up("llama3.2")

        started = time.monotonic()
        with self.assertRaises(local_model.Unavailable):
            local_model.summarize(LONG_TEXT, "llama3.2", read_timeout=0.3)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 2.0, f"waited {elapsed:.1f}s for a 0.3s timeout")

    def test_the_connect_timeout_is_separate_from_the_read_timeout(self):
        """Two budgets, not one: reaching a process on this machine either
        happens at once or will not, while generating tokens takes a while."""
        self.assertLess(local_model.CONNECT_TIMEOUT, local_model.READ_TIMEOUT)
        self.assertEqual(local_model.CONNECT_TIMEOUT, 2.0)
        self.assertEqual(local_model.READ_TIMEOUT, 20.0)

    def test_malformed_json_is_unavailable(self):
        self._serve("malformed")
        with self.assertRaises(local_model.Unavailable):
            local_model.summarize(LONG_TEXT)

    def test_an_empty_reply_is_unavailable(self):
        self._serve("empty")
        with self.assertRaises(local_model.Unavailable):
            local_model.summarize(LONG_TEXT)

    def test_a_reply_with_no_response_field_is_unavailable(self):
        self._serve("no_response")
        with self.assertRaises(local_model.Unavailable):
            local_model.summarize(LONG_TEXT)

    def test_a_blank_response_is_unavailable(self):
        self._serve("blank_response")
        with self.assertRaises(local_model.Unavailable):
            local_model.summarize(LONG_TEXT)

    def test_a_reply_that_is_not_an_object_is_unavailable(self):
        self._serve("not_an_object")
        with self.assertRaises(local_model.Unavailable):
            local_model.summarize(LONG_TEXT)


class EngineSelection(unittest.TestCase):
    """How plan() chooses, and what it does when the choice does not answer."""

    def setUp(self):
        local_model.reset_session()
        self.original_port = local_model.PORT
        self.server = None

    def tearDown(self):
        local_model.PORT = self.original_port
        local_model.reset_session()
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()

    def _serve(self, mode="ok", delay=0.0):
        self.server = start_server(mode, delay)
        local_model.PORT = self.server.server_port
        return self.server

    def test_the_ollama_engine_is_used_when_it_is_selected_and_working(self):
        self._serve("ok")
        plan = reading.plan(LONG_TEXT, summary=True, engine="ollama")
        self.assertTrue(plan.summarized)
        self.assertEqual(plan.engine, "ollama")
        self.assertFalse(plan.fell_back)
        self.assertIn("failed twice", plan.text)

    def test_an_absent_ollama_falls_back_without_a_word(self):
        local_model.PORT = closed_port()
        plan = reading.plan(LONG_TEXT, summary=True, engine="ollama")
        self.assertTrue(plan.summarized)
        self.assertEqual(plan.engine, "extractive")
        self.assertTrue(plan.fell_back)
        self.assertEqual(plan.sentences, summarize.summarize(LONG_TEXT))

    def test_a_timeout_falls_back(self):
        self._serve("slow", delay=1.0)
        original = local_model.READ_TIMEOUT
        local_model.READ_TIMEOUT = 0.2
        try:
            plan = reading.plan(LONG_TEXT, summary=True, engine="ollama")
        finally:
            local_model.READ_TIMEOUT = original
        self.assertTrue(plan.fell_back)
        self.assertEqual(plan.engine, "extractive")

    def test_malformed_json_falls_back(self):
        self._serve("malformed")
        plan = reading.plan(LONG_TEXT, summary=True, engine="ollama")
        self.assertTrue(plan.fell_back)
        self.assertEqual(plan.sentences, summarize.summarize(LONG_TEXT))

    def test_an_empty_reply_falls_back(self):
        self._serve("empty")
        self.assertTrue(reading.plan(LONG_TEXT, summary=True, engine="ollama").fell_back)

    def test_the_before_model_hook_fires_only_for_a_model_request(self):
        self._serve("ok")
        called = []
        reading.plan(LONG_TEXT, summary=True, engine="ollama",
                     before_model=lambda: called.append("said it"))
        self.assertEqual(called, ["said it"])

        called.clear()
        reading.plan(LONG_TEXT, summary=True, engine="extractive",
                     before_model=lambda: called.append("said it"))
        self.assertEqual(called, [], "warned about a wait that never happens")

    def test_the_extractive_engine_never_warms_a_model_up(self):
        server = self._serve("ok")
        reading.plan(LONG_TEXT, summary=True, engine="extractive")
        self.assertEqual(server.received, [], "extractive mode talked to Ollama")
        self.assertEqual(local_model.warmed_models(), set())

    def test_a_bypass_is_never_sent_to_the_model(self):
        """Code, a URL and short text are no better through a model, and there
        is no reason to make anyone wait to find that out."""
        server = self._serve("ok")
        for text in (
            test_summary.SHORT_THREE_SENTENCES,
            test_summary.CODE_FIXTURE,
            test_summary.URL_FIXTURE,
            test_summary.LIST_FIXTURE,
        ):
            plan = reading.plan(text, summary=True, engine="ollama")
            self.assertFalse(plan.summarized)
        self.assertEqual(server.received, [], "a bypass was sent to the model")

    def test_summary_mode_off_never_reaches_the_model(self):
        server = self._serve("ok")
        reading.plan(LONG_TEXT, summary=False, engine="ollama")
        self.assertEqual(server.received, [])


class HostRule(unittest.TestCase):
    """127.0.0.1 is not a default here, it is the only permitted value."""

    def test_the_module_only_ever_addresses_localhost(self):
        self.assertEqual(local_model.HOST, "127.0.0.1")
        self.assertEqual(settings.LOCAL_HOST, "127.0.0.1")

    def test_another_host_in_the_settings_file_is_rejected_and_logged(self):
        stored = settings.load()
        try:
            settings.save(dict(stored, summary_host="10.0.0.9"))
            loaded = settings.load()
            self.assertEqual(loaded["summary_host"], "127.0.0.1")
            self.assertTrue(
                any("summary_host" in w for w in settings.warnings), settings.warnings
            )
        finally:
            settings.save(stored)

    def test_a_hostname_that_merely_looks_local_is_still_rejected(self):
        stored = settings.load()
        try:
            for host in ("localhost", "127.0.0.2", "0.0.0.0", "example.com"):
                settings.save(dict(stored, summary_host=host))
                self.assertEqual(settings.load()["summary_host"], "127.0.0.1", host)
        finally:
            settings.save(stored)

    def test_an_unknown_engine_is_rejected_and_logged(self):
        stored = settings.load()
        try:
            settings.save(dict(stored, summary_engine="openai"))
            loaded = settings.load()
            self.assertEqual(loaded["summary_engine"], "extractive")
            self.assertTrue(
                any("summary_engine" in w for w in settings.warnings), settings.warnings
            )
        finally:
            settings.save(stored)

    def test_the_defaults_are_the_safe_ones(self):
        self.assertEqual(settings.DEFAULTS["summary_engine"], "extractive")
        self.assertEqual(settings.DEFAULTS["summary_host"], "127.0.0.1")
        self.assertEqual(settings.DEFAULTS["summary_model"], "llama3.2")
        self.assertFalse(settings.DEFAULTS["summary_mode"])


if __name__ == "__main__":
    unittest.main()
