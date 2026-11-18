from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from fenixsoft.stages import tts


class TtsRequestTest(unittest.TestCase):
    def test_elevenlabs_payload(self):
        env = {"ELEVENLABS_API_KEY": "test-key"}
        with patch.dict(os.environ, env, clear=True):
            url, headers, body = tts.build_request(
                {"text": "hola", "voice_id": "voice1", "speed": 1.1}, "elevenlabs")
            self.assertIn("voice1", url)
            self.assertEqual(headers["xi-api-key"], "test-key")
            payload = json.loads(body)
            self.assertEqual(payload["voice_settings"]["speed"], 1.1)
            self.assertEqual(payload["text"], "hola")

    def test_openai_payload(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "oai"}, clear=True):
            url, headers, body = tts.build_request(
                {"text": "hello", "openai_voice": "nova"}, "openai")
            self.assertIn("/audio/speech", url)
            self.assertEqual(headers["Authorization"], "Bearer oai")
            self.assertEqual(json.loads(body)["voice"], "nova")

    def test_missing_key_is_rejected(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit):
                tts.build_request({"text": "x"}, "elevenlabs")

    def test_auto_provider_prefers_elevenlabs(self):
        with patch.dict(os.environ, {"ELEVENLABS_API_KEY": "a",
                                     "OPENAI_API_KEY": "b"}, clear=True):
            self.assertEqual(tts.choose_provider({"provider": "auto"}),
                             "elevenlabs")
        with patch.dict(os.environ, {"OPENAI_API_KEY": "b"}, clear=True):
            self.assertEqual(tts.choose_provider({"provider": "auto"}),
                             "openai")

    def test_missing_text_is_rejected(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "o"}, clear=True):
            with self.assertRaises(SystemExit):
                tts.build_request({}, "openai")


if __name__ == "__main__":
    unittest.main()
