"""Tests for the ATS sources (Greenhouse, Ashby, Lever).

No network: every board payload below is a fixture shaped like the live
response. The three providers are tested for the same properties - normalised
fields, local query filtering, location filtering, limits - because the point of
this package is that a posting looks identical whichever board it came from.
"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jobsearch.errors import DetailUnavailableError, NetworkError, SourceError, UsageError
from jobsearch.sources.ats import (
    Ashby,
    Greenhouse,
    Lever,
    _contract_fields,
    _epoch_ms_to_date,
    load_boards,
)

GREENHOUSE = {
    "jobs": [
        {
            "id": 111,
            "title": "AI Safety Researcher",
            "location": {"name": "London, UK"},
            "absolute_url": "https://boards.greenhouse.io/anthropic/jobs/111",
            "content": "<p>Work on <b>safety</b> evaluations</p>",
            "updated_at": "2026-10-20T10:00:00-04:00",
        },
        {
            "id": 222,
            "title": "Account Executive",
            "location": {"name": "New York, NY"},
            "absolute_url": "https://boards.greenhouse.io/anthropic/jobs/222",
            "content": "Sell things",
            "updated_at": "2026-10-21T10:00:00Z",
        },
    ]
}

ASHBY = {
    "jobs": [
        {
            "id": "a1",
            "title": "AI Trainer",
            "location": "Remote - EMEA",
            "isRemote": True,
            "employmentType": "PartTime",
            "descriptionHtml": "<p>Train models on rubrics</p>",
            "jobUrl": "https://jobs.ashbyhq.com/openai/a1",
            "publishedAt": "2026-10-19T12:00:00.000Z",
            "department": "Research",
            "team": "Safety",
        },
        {
            "id": "a2",
            "title": "Software Engineer",
            "location": "San Francisco, CA",
            "isRemote": False,
            "employmentType": "FullTime",
            "descriptionHtml": "<p>Build infrastructure</p>",
            "jobUrl": "https://jobs.ashbyhq.com/openai/a2",
            "publishedAt": "2026-10-18T12:00:00.000Z",
        },
    ]
}

LEVER = [
    {
        "id": "l1",
        "text": "AI Evaluation Specialist",
        "categories": {"location": "London", "commitment": "Contract", "team": "Research"},
        "hostedUrl": "https://jobs.lever.co/palantir/l1",
        "createdAt": 1760000000000,
        "descriptionPlain": "Evaluate model output against a rubric",
        "workplaceType": "hybrid",
    }
]


def _fake_get_json(payloads):
    """Return a get_json stand-in that answers by substring of the URL."""

    def fake(url, params=None, headers=None, timeout=20.0):
        for marker, payload in payloads.items():
            if marker in url:
                return payload
        raise AssertionError("unexpected URL {!r}".format(url))

    return fake


class GreenhouseTests(unittest.TestCase):
    def _search(self, **kwargs):
        source = Greenhouse(boards={"anthropic": "Anthropic"})
        with patch(
            "jobsearch.sources.ats.get_json",
            side_effect=_fake_get_json({"anthropic": GREENHOUSE}),
        ):
            return source.search("AI safety", limit=10, **kwargs)

    def test_fields_are_normalised(self):
        found = self._search()
        self.assertEqual(len(found), 1)
        posting = found[0]
        self.assertEqual(posting.source, "greenhouse")
        self.assertEqual(posting.company, "Anthropic")
        self.assertEqual(posting.title, "AI Safety Researcher")
        self.assertEqual(posting.location, "London, UK")
        self.assertEqual(posting.posted_date, "2026-10-20")
        self.assertIn("safety evaluations", posting.description)
        self.assertNotIn("<p>", posting.description)
        self.assertTrue(posting.url.startswith("https://boards.greenhouse.io/"))
        self.assertEqual(posting.extra["org"], "anthropic")

    def test_provider_id_includes_the_board(self):
        # Two boards can serve the same numeric id; the slug keeps them apart.
        posting = self._search()[0]
        self.assertEqual(posting.provider_id, "anthropic:111")

    def test_location_filter_excludes_other_countries(self):
        """The whole-UK default drops a New York role even if it matches."""
        source = Greenhouse(boards={"anthropic": "Anthropic"})
        with patch(
            "jobsearch.sources.ats.get_json",
            side_effect=_fake_get_json({"anthropic": GREENHOUSE}),
        ):
            found = source.search("", limit=10)
        self.assertEqual([p.title for p in found], ["AI Safety Researcher"])

    def test_detail_is_not_supported(self):
        with self.assertRaises(DetailUnavailableError):
            Greenhouse(boards={"anthropic": "Anthropic"}).detail("anthropic:111")


class AshbyTests(unittest.TestCase):
    def _search(self, query="AI trainer", **kwargs):
        source = Ashby(boards={"openai": "OpenAI"})
        with patch(
            "jobsearch.sources.ats.get_json",
            side_effect=_fake_get_json({"ashbyhq": ASHBY}),
        ):
            return source.search(query, limit=10, **kwargs)

    def test_part_time_contract_fields_are_mapped(self):
        posting = self._search()[0]
        self.assertEqual(posting.contract_time, "part_time")
        self.assertTrue(posting.is_remote)
        self.assertEqual(posting.company, "OpenAI")

    def test_query_requires_every_keyword(self):
        # "Software Engineer" carries neither "ai" nor "trainer".
        titles = [p.title for p in self._search("AI trainer")]
        self.assertEqual(titles, ["AI Trainer"])

    def test_emea_role_is_kept_us_role_is_not(self):
        titles = sorted(p.title for p in self._search(""))
        self.assertEqual(titles, ["AI Trainer"])


class LeverTests(unittest.TestCase):
    def _search(self, **kwargs):
        source = Lever(boards={"palantir": "Palantir"})
        with patch(
            "jobsearch.sources.ats.get_json",
            side_effect=_fake_get_json({"lever.co": LEVER}),
        ):
            return source.search("evaluation", limit=10, **kwargs)

    def test_epoch_millis_become_iso_date(self):
        posting = self._search()[0]
        self.assertRegex(posting.posted_date or "", r"^\d{4}-\d{2}-\d{2}$")

    def test_commitment_maps_to_contract_type(self):
        posting = self._search()[0]
        self.assertEqual(posting.contract_type, "contract")
        self.assertEqual(posting.company, "Palantir")
        self.assertEqual(posting.location, "London")


class FilteringTests(unittest.TestCase):
    def test_limit_and_order(self):
        source = Ashby(boards={"openai": "OpenAI"})
        with patch(
            "jobsearch.sources.ats.get_json",
            side_effect=_fake_get_json({"ashbyhq": ASHBY}),
        ):
            found = source.search("", limit=1)
        self.assertEqual(len(found), 1)
        # Newest first: 2026-10-19 beats 2026-10-18.
        self.assertEqual(found[0].title, "AI Trainer")

    def test_specific_location_narrows_results(self):
        source = Greenhouse(boards={"anthropic": "Anthropic"})
        with patch(
            "jobsearch.sources.ats.get_json",
            side_effect=_fake_get_json({"anthropic": GREENHOUSE}),
        ):
            found = source.search("", location="New York")
        self.assertEqual([p.title for p in found], ["Account Executive"])

    def test_failed_board_is_named_not_silently_skipped(self):
        source = Ashby(boards={"openai": "OpenAI"})

        def boom(*args, **kwargs):
            raise NetworkError("boards-api returned HTTP 404", hint="check the slug")

        with patch("jobsearch.sources.ats.get_json", side_effect=boom):
            with self.assertRaises(SourceError) as caught:
                source.search("ai")
        self.assertIn("openai", caught.exception.message)

    def test_no_boards_configured_is_a_usage_error(self):
        source = Ashby(boards={})
        with self.assertRaises(UsageError):
            source.search("ai")

    def test_orgs_argument_adds_to_configured_boards(self):
        source = Ashby(boards={"openai": "OpenAI"})
        self.assertEqual(set(source.boards), {"openai"})
        # --org adds to the configured list; it must not drop the defaults.
        source2 = Ashby(boards={"openai": "OpenAI"}, orgs=["cohere"])
        self.assertEqual(set(source2.boards), {"openai", "cohere"})


class BoardConfigTests(unittest.TestCase):
    def test_list_form_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "boards.json"
            path.write_text(json.dumps({"ashby": ["openai", "cohere"]}), encoding="utf-8")
            self.assertEqual(load_boards("ashby", path), {"openai": "openai", "cohere": "cohere"})

    def test_object_form_carries_display_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "boards.json"
            path.write_text(json.dumps({"ashby": {"openai": "OpenAI"}}), encoding="utf-8")
            self.assertEqual(load_boards("ashby", path), {"openai": "OpenAI"})

    def test_unknown_provider_is_empty_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "boards.json"
            path.write_text(json.dumps({"ashby": ["openai"]}), encoding="utf-8")
            self.assertEqual(load_boards("lever", path), {})

    def test_missing_file_is_a_usage_error(self):
        with self.assertRaises(UsageError):
            load_boards("ashby", Path("/nonexistent/boards.json"))

    def test_malformed_json_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "boards.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(UsageError):
                load_boards("ashby", path)

    def test_repository_defaults_are_present(self):
        boards = load_boards("ashby")
        self.assertIn("openai", boards)
        self.assertEqual(boards["openai"], "OpenAI")


class HelperTests(unittest.TestCase):
    def test_contract_mapping(self):
        self.assertEqual(_contract_fields("PartTime"), ("unknown", "part_time"))
        self.assertEqual(_contract_fields("full-time"), ("permanent", "full_time"))
        self.assertEqual(_contract_fields("Contract"), ("contract", "unknown"))
        self.assertEqual(_contract_fields("Temporary"), ("contract", "unknown"))
        self.assertEqual(_contract_fields(""), ("unknown", "unknown"))

    def test_epoch_conversion_handles_junk(self):
        self.assertIsNone(_epoch_ms_to_date("not a number"))
        self.assertIsNone(_epoch_ms_to_date(None))
        self.assertIsNone(_epoch_ms_to_date(0))
        self.assertRegex(_epoch_ms_to_date(1760000000000), r"^\d{4}-\d{2}-\d{2}$")


if __name__ == "__main__":
    unittest.main()
