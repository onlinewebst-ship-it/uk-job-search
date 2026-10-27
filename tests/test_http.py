"""Tests for the stdlib HTTP layer: URL building, credential redaction, errors."""

import io
import json
import unittest
import urllib.error
from unittest import mock

from jobsearch.errors import NetworkError
from jobsearch.http import build_url, get_json, redact_url


class FakeResponse:
    def __init__(self, body, status=200):
        self._body = body.encode("utf-8") if isinstance(body, str) else body
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class BuildUrlTests(unittest.TestCase):
    def test_none_and_empty_values_are_dropped(self):
        url = build_url("https://example.test/search", {"a": "1", "b": None, "c": ""})
        self.assertEqual(url, "https://example.test/search?a=1")

    def test_zero_is_kept(self):
        self.assertIn("salary_min=0", build_url("https://example.test", {"salary_min": 0}))

    def test_existing_query_string_is_respected(self):
        url = build_url("https://example.test/search?x=1", {"y": "2"})
        self.assertEqual(url, "https://example.test/search?x=1&y=2")

    def test_redaction_hides_credentials_but_keeps_the_path(self):
        url = "https://api.adzuna.com/v1/api/jobs/gb/search/1?app_id=abc&app_key=secret&what=trainer"
        redacted = redact_url(url)
        self.assertNotIn("secret", redacted)
        self.assertNotIn("abc", redacted)
        self.assertIn("what=trainer", redacted)
        self.assertIn("/v1/api/jobs/gb/search/1", redacted)


class GetJsonTests(unittest.TestCase):
    def test_decodes_json(self):
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse('{"ok": true}')):
            self.assertEqual(get_json("https://example.test"), {"ok": True})

    def test_http_401_names_the_credentials_problem(self):
        error = urllib.error.HTTPError(
            "https://api.adzuna.com/x?app_key=secret",
            401,
            "Unauthorized",
            {},
            io.BytesIO(b'{"error":"invalid key"}'),
        )
        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(NetworkError) as caught:
                get_json("https://api.adzuna.com/x?app_key=secret")
        self.assertEqual(caught.exception.context["status"], 401)
        self.assertIn("credentials", caught.exception.hint)
        self.assertIn("HTTP 401", caught.exception.message)
        self.assertNotIn("secret", caught.exception.message)

    def test_http_429_mentions_rate_limiting(self):
        error = urllib.error.HTTPError(
            "https://www.reed.co.uk/api/1.0/search", 429, "Too Many Requests", {}, io.BytesIO(b"")
        )
        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(NetworkError) as caught:
                get_json("https://www.reed.co.uk/api/1.0/search")
        self.assertIn("Rate limited", caught.exception.hint)

    def test_connection_error_is_actionable(self):
        error = urllib.error.URLError(OSError("Name or service not known"))
        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(NetworkError) as caught:
                get_json("https://api.adzuna.com")
        self.assertIn("Could not reach api.adzuna.com", caught.exception.message)
        self.assertIn("internet connection", caught.exception.hint)

    def test_invalid_json_is_reported_as_such(self):
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse("<html>login</html>")):
            with self.assertRaises(NetworkError) as caught:
                get_json("https://example.test")
        self.assertIn("not JSON", caught.exception.message)

    def test_empty_body_is_reported(self):
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse("")):
            with self.assertRaises(NetworkError) as caught:
                get_json("https://example.test")
        self.assertIn("empty body", caught.exception.message)


if __name__ == "__main__":
    unittest.main()
