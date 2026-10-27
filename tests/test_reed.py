"""Reed client tests: parsing, auth header, paging parameters, detail endpoint."""

import base64
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
from jobsearch.sources.reed import Reed

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
ENV = {"REED_API_KEY": "test-reed-key"}


def load(name):
    with open(os.path.join(FIXTURE_DIR, name), encoding="utf-8") as fh:
        return json.load(fh)


class ReedParseTests(unittest.TestCase):
    def setUp(self):
        self.client = Reed()
        self.postings = self.client.parse_search(load("reed_search.json"))

    def test_every_result_is_mapped(self):
        self.assertEqual(len(self.postings), 3)

    def test_core_fields(self):
        first = self.postings[0]
        self.assertEqual(first.source, "reed")
        self.assertEqual(first.provider_id, "51530164")
        self.assertEqual(first.title, "AI Trainer")
        self.assertEqual(first.company, "Halcyon Learning")
        self.assertEqual(first.location, "Leeds, West Yorkshire")
        self.assertEqual(first.salary_min, 32000)
        self.assertEqual(first.salary_max, 38000)
        self.assertEqual(first.currency, "GBP")
        self.assertEqual(first.url, "https://www.reed.co.uk/jobs/ai-trainer/51530164")

    def test_uk_dates_are_normalised_to_iso(self):
        self.assertEqual(self.postings[0].posted_date, "2026-10-24")
        self.assertEqual(self.postings[1].posted_date, "2026-10-12")

    def test_missing_salary_stays_missing(self):
        second = self.postings[1]
        self.assertIsNone(second.salary_min)
        self.assertIsNone(second.salary_max)

    def test_remote_is_inferred_from_the_location(self):
        self.assertTrue(self.postings[1].is_remote)  # locationName "Remote"
        self.assertFalse(self.postings[2].is_remote)

    def test_search_results_have_unknown_contract_fields(self):
        # Reed's search endpoint does not return contractType; only detail does.
        self.assertEqual(self.postings[0].contract_type, "unknown")
        self.assertEqual(self.postings[0].contract_time, "unknown")

    def test_ids_are_stable_and_source_scoped(self):
        self.assertEqual(self.postings[0].id, stable_id("reed", "51530164"))

    def test_url_is_synthesised_when_absent(self):
        payload = {"results": [{"jobId": 42, "jobTitle": "Trainer"}]}
        posting = self.client.parse_search(payload)[0]
        self.assertEqual(posting.url, "https://www.reed.co.uk/jobs/42")

    def test_incomplete_items_are_skipped(self):
        payload = {"results": [{"jobTitle": "No id"}, {"jobId": 7, "jobTitle": ""}]}
        self.assertEqual(self.client.parse_search(payload), [])

    def test_results_wrapper_is_required(self):
        with self.assertRaises(SourceError):
            self.client.parse_search({"totalResults": 0})

    def test_a_bare_list_is_accepted(self):
        payload = [{"jobId": 9, "jobTitle": "Trainer"}]
        self.assertEqual(len(self.client.parse_search(payload)), 1)


class ReedDetailTests(unittest.TestCase):
    def setUp(self):
        self.client = Reed()

    def test_detail_maps_full_advert_fields(self):
        payload = load("reed_detail.json")
        posting = self.client.parse_detail(payload, "51530164")
        self.assertIsNotNone(posting)
        self.assertEqual(posting.contract_type, "contract")
        self.assertEqual(posting.contract_time, "part_time")
        self.assertEqual(posting.title, "AI Trainer")
        self.assertIn("<strong>AI Trainer</strong>", posting.description)
        self.assertEqual(posting.extra["salary_type"], "per annum")

    def test_detail_is_fetched_from_the_documented_endpoint(self):
        payload = load("reed_detail.json")
        with mock.patch.dict(os.environ, ENV, clear=False), mock.patch(
            "jobsearch.sources.reed.get_json", return_value=payload
        ) as fake:
            posting = self.client.detail("51530164")
        self.assertEqual(
            fake.call_args[0][0], "https://www.reed.co.uk/api/1.0/jobs/51530164"
        )
        self.assertEqual(posting.provider_id, "51530164")

    def test_empty_detail_response_means_expired(self):
        with mock.patch.dict(os.environ, ENV, clear=False), mock.patch(
            "jobsearch.sources.reed.get_json", return_value={}
        ):
            with self.assertRaises(DetailUnavailableError) as caught:
                self.client.detail("999")
        self.assertIn("expired", caught.exception.hint)

    def test_empty_job_id_is_rejected(self):
        with self.assertRaises(UsageError):
            self.client.detail("   ")


class ReedRequestTests(unittest.TestCase):
    def setUp(self):
        self.client = Reed()
        self.payload = load("reed_search.json")

    def test_basic_auth_uses_the_key_as_username_and_blank_password(self):
        with mock.patch.dict(os.environ, ENV, clear=False) as _env, mock.patch(
            "jobsearch.sources.reed.get_json", return_value=self.payload
        ) as fake:
            self.client.search("AI trainer", "London", limit=5)
        headers = fake.call_args[1]["headers"]
        expected = base64.b64encode(b"test-reed-key:").decode("ascii")
        self.assertEqual(headers["Authorization"], "Basic {}".format(expected))

    def test_search_parameters(self):
        with mock.patch.dict(os.environ, ENV, clear=False), mock.patch(
            "jobsearch.sources.reed.get_json", return_value=self.payload
        ) as fake:
            self.client.search(
                "AI trainer",
                "Leeds",
                limit=10,
                distance=25,
                salary_min=28000,
                salary_max=60000,
                permanent=True,
                part_time=False,
            )
        url = fake.call_args[0][0]
        params = fake.call_args[1]["params"]
        self.assertEqual(url, "https://www.reed.co.uk/api/1.0/search")
        self.assertEqual(params["keywords"], "AI trainer")
        self.assertEqual(params["locationName"], "Leeds")
        self.assertEqual(params["distanceFromLocation"], 25)
        self.assertEqual(params["minimumSalary"], 28000)
        self.assertEqual(params["maximumSalary"], 60000)
        self.assertEqual(params["permanent"], "true")
        self.assertEqual(params["partTime"], "false")
        self.assertEqual(params["resultsToTake"], 10)
        # Unset booleans are omitted, not sent as false.
        self.assertIsNone(params["contract"])
        self.assertIsNone(params["temp"])

    def test_whole_uk_location_is_omitted(self):
        with mock.patch.dict(os.environ, ENV, clear=False), mock.patch(
            "jobsearch.sources.reed.get_json", return_value=self.payload
        ) as fake:
            self.client.search("trainer", "United Kingdom", limit=5)
        self.assertIsNone(fake.call_args[1]["params"]["locationName"])

    def test_distance_without_a_place_is_rejected(self):
        with mock.patch.dict(os.environ, ENV, clear=False):
            with self.assertRaises(UsageError) as caught:
                self.client.search("trainer", "United Kingdom", distance=20)
        self.assertIn("--location", str(caught.exception))

    def test_limit_is_capped_at_one_hundred(self):
        with mock.patch.dict(os.environ, ENV, clear=False), mock.patch(
            "jobsearch.sources.reed.get_json", return_value={"results": []}
        ) as fake:
            self.client.search("trainer", limit=500)
        self.assertEqual(fake.call_args[1]["params"]["resultsToTake"], 100)

    def test_empty_query_is_rejected(self):
        with mock.patch.dict(os.environ, ENV, clear=False):
            with self.assertRaises(UsageError):
                self.client.search("")

    def test_missing_key_names_the_environment_variable(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(MissingCredentialError) as caught:
                self.client.search("trainer")
        self.assertIn("REED_API_KEY", str(caught.exception))
        self.assertIn("reed.co.uk/developers", caught.exception.hint)


if __name__ == "__main__":
    unittest.main()
