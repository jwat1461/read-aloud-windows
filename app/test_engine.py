"""Live round-trip tests against the PowerShell SAPI server.

These are integration tests: they start a real PowerShell process and the WAV test
renders real audio. Speaking happens at low volume so running them is not startling.

    python -m unittest test_engine -v
"""

import queue
import tempfile
import time
import unittest
import wave
from pathlib import Path

from speech_engine import SpeechEngine

TIMEOUT = 25.0


class EngineRoundTrip(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = SpeechEngine()
        cls.assertTrue(cls.engine.alive, "speech server did not start")
        cls.ready = cls._await(cls, "READY")

    @classmethod
    def tearDownClass(cls):
        cls.engine.shutdown()

    def _await(self, tag, timeout=TIMEOUT):
        """Drain replies until one matching `tag` arrives."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                reply = self.engine.replies.get(timeout=0.2)
            except queue.Empty:
                continue
            if reply[0] == tag:
                return reply
            if reply[0] == "ERR":
                raise AssertionError(f"server error: {reply}")
            if reply[0] == "EXIT":
                raise AssertionError("server exited unexpectedly")
        raise AssertionError(f"timed out waiting for {tag}")

    def test_01_server_announced_ready(self):
        self.assertEqual(self.ready[0], "READY")

    def test_02_lists_installed_voices(self):
        self.engine.list_voices()
        reply = self._await("VOICES")
        voices = [v for v in reply[1:] if v]
        self.assertGreater(len(voices), 0, "no SAPI voices installed")
        print(f"\n    voices: {', '.join(voices)}")
        type(self).voices = voices

    def test_03_selects_each_voice(self):
        for name in self.voices:
            self.engine.set_voice(name)
            self.assertEqual(self._await("OK")[1], "VOICE")

    def test_04_rejects_unknown_voice_without_dying(self):
        self.engine.set_voice("No Such Voice 9000")
        deadline = time.monotonic() + TIMEOUT
        saw_error = False
        while time.monotonic() < deadline:
            try:
                reply = self.engine.replies.get(timeout=0.2)
            except queue.Empty:
                continue
            if reply[0] == "ERR":
                saw_error = True
                break
            if reply[0] == "OK" and reply[1] == "VOICE":
                break
        self.assertTrue(saw_error, "expected an ERR reply for an unknown voice")
        self.assertTrue(self.engine.alive, "server died on a bad voice name")
        self.engine.set_voice(self.voices[0])
        self._await("OK")

    def test_05_rate_and_volume_accepted(self):
        for rate in (-10, 0, 5, 10):
            self.engine.set_rate(rate)
            self.assertEqual(self._await("OK")[1], "RATE")
        self.engine.set_rate(0)
        self._await("OK")
        for vol in (0, 50, 100):
            self.engine.set_volume(vol)
            self.assertEqual(self._await("OK")[1], "VOLUME")

    def test_06_speaks_and_reports_completion(self):
        self.engine.set_volume(15)
        self._await("OK")
        self.engine.set_rate(4)
        self._await("OK")

        self.engine.speak("Testing one two three.")
        self.assertEqual(self._await("OK")[1], "SPEAK")

        saw_speaking = False
        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline:
            self.engine.poll_state()
            state = self._await("STATE")
            if state[1] == "Speaking":
                saw_speaking = True
            if state[2] == "1":
                break
            time.sleep(0.1)
        else:
            self.fail("utterance never reported completion")

        self.assertTrue(saw_speaking, "never observed the Speaking state")

    def test_07_pause_resume_stop(self):
        self.engine.set_rate(-2)
        self._await("OK")
        self.engine.speak("One two three four five six seven eight nine ten.")
        self._await("OK")

        time.sleep(0.6)
        self.engine.pause()
        self._await("OK")
        self.engine.poll_state()
        self.assertEqual(self._await("STATE")[1], "Paused")

        self.engine.resume()
        self._await("OK")
        self.engine.poll_state()
        self.assertIn(self._await("STATE")[1], ("Speaking", "Ready"))

        # Stop while paused used to wedge the engine; make sure it recovers.
        self.engine.pause()
        self._await("OK")
        self.engine.stop()
        self._await("OK")
        self.engine.poll_state()
        state = self._await("STATE")
        self.assertEqual(state[1], "Ready")
        self.assertEqual(state[2], "1")

    def test_08_speaks_again_after_a_stop(self):
        self.engine.speak("Still alive.")
        self.assertEqual(self._await("OK")[1], "SPEAK")
        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline:
            self.engine.poll_state()
            if self._await("STATE")[2] == "1":
                return
            time.sleep(0.1)
        self.fail("engine did not finish speaking after a stop")

    def test_09_unicode_and_pipes_survive_the_protocol(self):
        self.engine.set_volume(10)
        self._await("OK")
        self.engine.speak("Café | résumé | naïve — em dash, and a\nnewline.")
        self.assertEqual(self._await("OK")[1], "SPEAK")
        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline:
            self.engine.poll_state()
            if self._await("STATE")[2] == "1":
                return
            time.sleep(0.1)
        self.fail("unicode utterance never completed")

    def test_10_saves_a_playable_wav(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.wav"
            self.engine.save_wav(str(path), "Saving this sentence to disk.")
            self.assertEqual(self._await("OK", timeout=40)[1], "SAVE")

            self.assertTrue(path.exists(), "no WAV file produced")
            self.assertGreater(path.stat().st_size, 1000, "WAV file is suspiciously small")
            with wave.open(str(path)) as wav:
                frames = wav.getnframes()
                seconds = frames / wav.getframerate()
            self.assertGreater(seconds, 0.5, "WAV holds almost no audio")
            print(f"\n    wrote {path.stat().st_size:,} bytes / {seconds:.1f}s of audio")

    def test_11_unknown_command_is_reported_not_fatal(self):
        self.engine._send("FLURB")
        reply = self._await("ERR")
        self.assertIn("unknown command", reply[1].lower())
        self.assertTrue(self.engine.alive)


if __name__ == "__main__":
    unittest.main()
