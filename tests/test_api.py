import os
import subprocess
import threading
import unittest
from unittest.mock import Mock, patch

from texttospeechapi import create_app, speak_on_mac


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"TTS_API_KEY": "test-only-key-0123456789abcdef0123456789"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.speaker = Mock()
        self.now = 0
        self.app = create_app(speaker=self.speaker, clock=lambda: self.now)
        self.client = self.app.test_client()

    def post(self, **changes):
        data = {"api_key": "test-only-key-0123456789abcdef0123456789", "message": "Hello"}
        data.update(changes)
        return self.client.post("/speak", json=data)

    def test_success(self):
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, dict(ok=True, received=True, spoken=True))
        self.speaker.assert_called_once_with("Hello")

    def test_authentication_precedes_message_validation(self):
        for key in (None, "wrong", "kkyian1", 123, [], "你好"):
            self.assertEqual(self.post(api_key=key, message=None).status_code, 401)
        self.assertEqual(self.client.post("/speak", json={"message": "Hello"}).status_code, 401)
        self.speaker.assert_not_called()

    def test_message_validation(self):
        for message in (None, "", "  ", 123, [], {}, "x" * 501):
            self.assertEqual(self.post(message=message).status_code, 400)
        self.assertEqual(self.client.post("/speak", json={"api_key": "test-only-key-0123456789abcdef0123456789"}).status_code, 400)
        self.speaker.assert_not_called()

    def test_body_validation(self):
        for data, content_type, expected in (
            ('{', 'application/json', 400),
            ('[]', 'application/json', 400),
            ('null', 'application/json', 400),
            ('hello', 'text/plain', 415),
            ('x' * 8193, 'application/json', 413),
        ):
            response = self.client.post('/speak', data=data, content_type=content_type)
            self.assertEqual(response.status_code, expected)
            self.assertFalse(response.json['spoken'])
        self.speaker.assert_not_called()

    def test_rate_limit_and_expiration(self):
        for _ in range(5):
            self.assertEqual(self.post().status_code, 200)
        response = self.post()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers['Retry-After'], '60')
        self.assertEqual(self.speaker.call_count, 5)
        self.now = 60
        self.assertEqual(self.post().status_code, 200)

    def test_speech_failures_release_lock(self):
        for exc, status in (
            (OSError(), 503),
            (subprocess.CalledProcessError(1, 'say'), 503),
            (subprocess.TimeoutExpired('say', 90), 504),
        ):
            self.speaker.side_effect = exc
            response = self.post()
            self.assertEqual(response.status_code, status)
            self.assertTrue(response.json['received'])
            self.assertFalse(response.json['spoken'])
        self.speaker.side_effect = None
        self.assertEqual(self.post().status_code, 200)

    def test_overlapping_request_is_rejected(self):
        started, release = threading.Event(), threading.Event()
        def blocking_speaker(message):
            started.set()
            release.wait(5)
        self.speaker.side_effect = blocking_speaker
        responses = []
        def first_request():
            with self.app.test_client() as client:
                responses.append(client.post('/speak', json={'api_key': 'test-only-key-0123456789abcdef0123456789', 'message': 'Hello'}))
        worker = threading.Thread(target=first_request)
        worker.start()
        try:
            self.assertTrue(started.wait(2))
            self.assertEqual(self.post().status_code, 429)
            self.assertEqual(self.speaker.call_count, 1)
        finally:
            release.set()
            worker.join(5)
        self.assertEqual(responses[0].status_code, 200)

    def test_environment_override(self):
        with patch.dict(os.environ, {'TTS_API_KEY': 'private-test-key-0123456789abcdef0123456789'}):
            client = create_app(speaker=self.speaker).test_client()
        self.assertEqual(client.post('/speak', json={'api_key': 'test-only-key-0123456789abcdef0123456789', 'message': 'Hello'}).status_code, 401)
        self.assertEqual(client.post('/speak', json={'api_key': 'private-test-key-0123456789abcdef0123456789', 'message': 'Hello'}).status_code, 200)

    def test_empty_environment_key_is_rejected(self):
        with patch.dict(os.environ, {'TTS_API_KEY': ''}):
            with self.assertRaises(ValueError):
                create_app()

    def test_missing_environment_key_is_rejected(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                create_app()

    def test_old_prototype_key_cannot_configure_server(self):
        with patch.dict(os.environ, {'TTS_API_KEY': 'kkyian1'}):
            with self.assertRaises(ValueError):
                create_app()

    def test_speech_text_is_passed_as_stdin(self):
        with patch('texttospeechapi.shutil.which', return_value='/usr/bin/say'), patch('texttospeechapi.subprocess.run') as run:
            speak_on_mac('$(touch unsafe); --help')
        self.assertEqual(run.call_args.args, (['/usr/bin/say'],))
        self.assertEqual(run.call_args.kwargs['input'], '$(touch unsafe); --help')
        self.assertNotIn('shell', run.call_args.kwargs)


if __name__ == '__main__':
    unittest.main()
