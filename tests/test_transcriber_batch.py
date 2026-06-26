import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import transcriber  # noqa: E402


_LARGE_WORD_BODY = "x" * (256 * 1024)


def _large_payload_batch_worker(audio_paths, model_size, language, result_channel):
    results = []
    for idx, audio_path in enumerate(audio_paths):
        name = Path(audio_path).name
        results.append(
            {
                "words": [
                    {"text": f"{idx}:{name}", "start": float(idx), "end": float(idx) + 0.5},
                    {"text": f"{idx:03d}-{_LARGE_WORD_BODY}", "start": float(idx) + 0.5, "end": float(idx) + 1.0},
                ],
                "language": language or "en",
            }
        )

    sender = getattr(result_channel, "send", None)
    if sender is None:
        sender = result_channel.put
    sender({"results": results})

    close = getattr(result_channel, "close", None)
    if close is not None:
        close()


class TranscriberBatchTests(unittest.TestCase):
    def test_transcribe_clips_maps_worker_results_to_paths(self):
        paths = [Path("one.wav"), Path("two.wav")]
        payload = {
            "results": [
                {"words": [{"text": "hello", "start": 0.0, "end": 0.5}], "language": "en"},
                {"error": "decode failed"},
            ]
        }

        with patch("transcriber._batch_transcription_timeout_seconds", return_value=120), \
             patch("transcriber._run_transcription_batch_process", return_value=payload) as worker:
            result = transcriber.transcribe_clips(paths, model_size="tiny", language=None)

        worker.assert_called_once()
        self.assertEqual(result[0][0]["text"], "hello")
        self.assertEqual(result[1], [])

    def test_transcribe_clips_timeout_or_cancel_returns_empty_rows(self):
        paths = [Path("one.wav"), Path("two.wav")]

        with patch("transcriber._batch_transcription_timeout_seconds", return_value=120), \
             patch("transcriber._run_transcription_batch_process", return_value={"timeout": True}):
            self.assertEqual(transcriber.transcribe_clips(paths), [[], []])

        with patch("transcriber._batch_transcription_timeout_seconds", return_value=120), \
             patch("transcriber._run_transcription_batch_process", return_value={"cancelled": True}):
            self.assertEqual(transcriber.transcribe_clips(paths), [[], []])

    def test_transcribe_clip_cancel_returns_empty_words(self):
        with patch("transcriber._transcription_timeout_seconds", return_value=120), \
             patch("transcriber._run_transcription_process", return_value={"cancelled": True}):
            self.assertEqual(transcriber.transcribe_clip(Path("one.wav")), [])

    def test_transcribe_clips_delivers_large_batch_payload_in_order(self):
        paths = [Path(f"clip_{idx}.wav") for idx in range(16)]

        with patch("transcriber._batch_transcription_timeout_seconds", return_value=5), \
             patch("transcriber._transcribe_batch_process_worker", new=_large_payload_batch_worker):
            result = transcriber.transcribe_clips(paths, model_size="tiny", language="en")

        self.assertEqual(len(result), len(paths))
        for idx, words in enumerate(result):
            self.assertEqual(words[0]["text"], f"{idx}:clip_{idx}.wav")
            self.assertTrue(words[1]["text"].startswith(f"{idx:03d}-"))
            self.assertGreater(len(words[1]["text"]), len(_LARGE_WORD_BODY))

    def test_receive_transcription_result_terminates_worker_on_cancel(self):
        class FakeProc:
            sentinel = object()

            def __init__(self):
                self.terminated = False
                self.killed = False

            def is_alive(self):
                return not self.terminated and not self.killed

            def terminate(self):
                self.terminated = True

            def kill(self):
                self.killed = True

            def join(self, timeout=0):
                return None

        proc = FakeProc()
        with patch("transcriber._transcription_cancel_requested", return_value=True):
            result = transcriber._receive_transcription_result(proc, object(), timeout=120)

        self.assertTrue(result["cancelled"])
        self.assertTrue(proc.terminated)


if __name__ == "__main__":
    unittest.main()
