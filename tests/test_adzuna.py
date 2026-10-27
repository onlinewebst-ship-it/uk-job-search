"""Adzuna client tests: parsing, query construction, error behaviour.

No network: ``get_json`` is patched, and the fixtures are recorded-shaped
payloads checked into ``tests/fixtures``.
"""

import json
import os
import unittest
from unittest import mock

from jobsearch.errors import (
    DetailUnavailableError,
    MissingCredentialError,
    SourceError,
    UsageError,
)
from jobsearch.models import stable_id
from jobsearch.sources.adzuna import Adzuna

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
ENV = {"ADZUNA_APP_ID": "test-app-id", "ADZUNA_APP_KEY": "test-app-key"}


def load(name):
    with open(os.path.join(FIXTURE_DIR, name), encoding="utf-8") as fh:
        return json.load(fh)


class AdzunaParseTests(unittest.TestCase):
    def setUp(self):
        self.client = Adzuna(country="gb")
        self.payload = load("adzuna_search.json")
        self.postings = self.client.parse_search(self.payload)

    def test_every_usable_result_is_mapped(self):
        self.assertEqual(len(self.postings), 3)

    def test_core_fields_are_normalised(self):
        first = self.postings[0]
        self.assertEqual(first.source, "adzuna")
        self.assertEqual(first.provider_id, "4512345678")
        self.assertEqual(first.title, "AI Trainer (Remote)")
        self.assertEqual(first.company, "Northwind Analytics Ltd")
        self.assertEqual(first.location, "London, Central London")
        self.assertEqual(first.currency, "GBP")
        self.assertEqual(first.posted_date, "2026-10-24")
        self.assertEqual(first.url, "https://www.adzuna.co.uk/jobs/land/ad/4512345678?utm_medium=api")

    def test_salary_values_are_integers(self):
        first = self.postings[0]
        self.assertEqual(first.salary_min, 42000)
        self.assertEqual(first.salary_max, 48000)
        self.assertIsInstance(first.salary_min, int)
        self.assertFalse(first.salary_is_predicted)

    def test_contract_fields(self):
        contract = self.postings[1]
        self.assertEqual(contract.contract_type, "contract")
        self.assertEqual(contract.contract_time, "part_time")
        self.assertTrue(contract.salary_is_predicted)

    def test_remote_is_inferred_from_text(self):
        self.assertTrue(self.postings[0].is_remote)  # "Fully remote"
        self.assertFalse(self.postings[2].is_remote)  # "Hybrid"

    def test_missing_company_and_salary_become_empty(self):
        third = self.postings[2]
        self.assertEqual(third.company, "")
        self.assertIsNone(third.salary_min)
        self.assertEqual(third.salary_text(), "Not stated")

    def test_ids_are_stable_and_source_scoped(self):
        first = self.postings[0]
        self.assertEqual(first.id, stable_id("adzuna", "4512345678"))
        self.assertNotEqual(first.id, stable_id("reed", "4512345678"))
        self.assertEqual(len(first.id), 16)

    def test_extra_category_is_kept(self):
        self.assertEqual(self.postings[0].extra["category"], "IT Jobs")

    def test_incomplete_items_are_skipped(self):
        payload = {
            "results": [
                {"id": "", "title": "No id"},
                {"id": "777", "title": "   "},
                {"id": "778", "title": " Valid role  "},
                "not a dict",
            ]
        }
        postings = self.client.parse_search(payload)
        self.assertEqual([p.title for p in postings], ["Valid role"])

    def test_missing_results_field_is_a_source_error(self):
        with self.assertRaises(SourceError):
            self.client.parse_search({"count": 0})

    def test_non_dict_payload_is_a_source_error(self):
        with self.assertRaises(SourceError):
            self.client.parse_search(["unexpected"])


class AdzunaRequestTests(unittest.TestCase):
    def setUp(self):
        self.client = Adzuna(country="gb")
        self.payload = load("adzuna_search.json")

    def test_query_uses_the_documented_parameters(self):
        with mock.patch.dict(os.environ, ENV, clear=False), mock.patch(
            "jobsearch.sources.adzuna.get_json", return_value=self.payload
        ) as fake:
            self.client.search(
                "AI trainer",
                "United Kingdom",
                limit=3,
                max_days_old=7,
                salary_min=30000,
                category="it-jobs",
            )
        url = fake.call_args[0][0]
        params = fake.call_args[1]["params"]
        self.assertEqual(url, "https://api.adzuna.com/v1/api/jobs/gb/search/1")
        self.assertEqual(params["app_id"], "test-app-id")
        self.assertEqual(params["app_key"], "test-app-key")
        self.assertEqual(params["what"], "AI trainer")
        self.assertEqual(params["results_per_page"], 3)
        self.assertEqual(params["max_days_old"], 7)
        self.assertEqual(params["salary_min"], 30000)
        self.assertEqual(params["category"], "it-jobs")
        # "United Kingdom" means the whole country, so no `where` is sent.
        self.assertIsNone(params["where"])

    def test_city_location_is_sent_as_where(self):
        with mock.patch.dict(os.environ, ENV, clear=False), mock.patch(
            "jobsearch.sources.adzuna.get_json", return_value=self.payload
        ) as fake:
            self.client.search("trainer", "Manchester", limit=2)
        self.assertEqual(fake.call_args[1]["params"]["where"], "Manchester")

    def test_results_are_trimmed_to_the_limit(self):
        with mock.patch.dict(os.environ, ENV, clear=False), mock.patch(
            "jobsearch.sources.adzuna.get_json", return_value=self.payload
        ):
            postings = self.client.search("trainer", limit=2)
        self.assertEqual(len(postings), 2)
        self.assertEqual(postings[0].title, "AI Trainer (Remote)")

    def test_second_page_is_requested_when_needed(self):
        def page(start, count):
            results = []
            for number in range(start, start + count):
                item = dict(self.payload["results"][0])
                item["id"] = str(9000000000 + number)
                results.append(item)
            return {"results": results}

        with mock.patch.dict(os.environ, ENV, clear=False), mock.patch(
            "jobsearch.sources.adzuna.get_json", side_effect=[page(0, 50), page(50, 10)]
        ) as fake:
            postings = self.client.search("trainer", limit=60)
        self.assertEqual(len(postings), 60)
        self.assertEqual(len(set(p.id for p in postings)), 60)
        urls = [call[0][0] for call in fake.call_args_list]
        self.assertEqual(
            urls,
            [
                "https://api.adzuna.com/v1/api/jobs/gb/search/1",
                "https://api.adzuna.com/v1/api/jobs/gb/search/2",
            ],
        )
        self.assertEqual(fake.call_args_list[0][1]["params"]["results_per_page"], 50)
        self.assertEqual(fake.call_args_list[1][1]["params"]["results_per_page"], 10)

    def test_paging_stops_at_a_short_page(self):
        short = {"results": self.payload["results"][:2]}
        with mock.patch.dict(os.environ, ENV, clear=False), mock.patch(
            "jobsearch.sources.adzuna.get_json", return_value=short
        ) as fake:
            postings = self.client.search("trainer", limit=10)
        self.assertEqual(len(postings), 2)
        self.assertEqual(fake.call_count, 1)

    def test_empty_query_is_rejected(self):
        with mock.patch.dict(os.environ, ENV, clear=False):
            with self.assertRaises(UsageError):
                self.client.search("   ")

    def test_unsupported_country_is_rejected(self):
        with self.assertRaises(UsageError):
            Adzuna(country="zz")

    def test_missing_credentials_name_the_environment_variable(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(MissingCredentialError) as caught:
                self.client.search("trainer")
        message = str(caught.exception)
        self.assertIn("ADZUNA_APP_ID", message)
        self.assertIn("developer.adzuna.com", caught.exception.hint)
        self.assertEqual(caught.exception.code, "missing_credential")

    def test_only_the_missing_key_is_named(self):
        with mock.patch.dict(os.environ, {"ADZUNA_APP_ID": "x"}, clear=True):
            with self.assertRaises(MissingCredentialError) as caught:
                self.client.search("trainer")
        self.assertIn("ADZUNA_APP_KEY", str(caught.exception))
        self.assertNotIn("ADZUNA_APP_ID is not set", str(caught.exception))


class AdzunaDetailTests(unittest.TestCase):
    def setUp(self):
        self.client = Adzuna()
        self.posting = self.client.parse_search(load("adzuna_search.json"))[0]

    def test_detail_returns_a_cached_snapshot(self):
        self.assertIs(self.client.detail(self.posting.provider_id, cached=self.posting), self.posting)

    def test_detail_without_a_cache_explains_the_api_limitation(self):
        with self.assertRaises(DetailUnavailableError) as caught:
            self.client.detail("4512345678")
        self.assertEqual(caught.exception.code, "detail_unavailable")
        self.assertIn("truncated description", str(caught.exception))
        self.assertIn("advert URL", caught.exception.hint)


if __name__ == "__main__":
    unittest.main()
