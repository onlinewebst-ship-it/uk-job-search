"""Tests for the seen-postings store."""

import json
import os
import shutil
import tempfile
import unittest

from jobsearch.errors import AmbiguousIdError, NotFoundError, StateError, UsageError
from jobsearch.models import Posting, stable_id
from jobsearch.state import STATUSES, SeenStore


def make_posting(provider_id="1001", source="adzuna", **overrides):
    values = dict(
        id=stable_id(source, provider_id),
        source=source,
        provider_id=str(provider_id),
        title="AI Trainer",
        company="Acme Analytics",
        location="London",
        salary_min=40000,
        salary_max=50000,
        currency="GBP",
        contract_type="permanent",
        contract_time="full_time",
        posted_date="2026-10-20",
        url="https://example.invalid/job/{}".format(provider_id),
        description="Short search snippet.",
        is_remote=True,
    )
    values.update(overrides)
    return Posting(**values)


class StateTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="jobsearch-state-")
        self.path = os.path.join(self.tmpdir, "seen.json")
        self.store = SeenStore(self.path)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class AddTests(StateTestCase):
    def test_add_creates_the_file_and_returns_ids(self):
        posting = make_posting()
        added = self.store.add([posting], query="AI trainer")
        self.assertEqual(added, [posting.id])
        self.assertTrue(os.path.exists(self.path))

        reloaded = SeenStore(self.path)
        entry = reloaded.get(posting.id)
        self.assertEqual(entry["title"], "AI Trainer")
        self.assertEqual(entry["status"], "new")
        self.assertEqual(entry["query"], "AI trainer")
        self.assertIsNone(entry["fit"])
        self.assertTrue(entry["first_seen"])

    def test_add_is_idempotent(self):
        posting = make_posting()
        self.store.add([posting])
        first_seen = self.store.get(posting.id)["first_seen"]
        second = self.store.add([posting])
        self.assertEqual(second, [])
        self.assertEqual(len(self.store.entries()), 1)
        self.assertEqual(self.store.get(posting.id)["first_seen"], first_seen)

    def test_add_never_loses_existing_entries(self):
        first = make_posting("1001")
        second = make_posting("1002", title="Data Annotator")
        self.store.add([first])
        self.store.add([second])
        self.assertEqual(set(self.store.entries()), {first.id, second.id})

    def test_add_does_not_reset_a_user_status(self):
        posting = make_posting()
        self.store.add([posting])
        self.store.mark(posting.id, status="applied", notes="Sent CV", fit=81)
        self.store.add([posting])
        entry = self.store.get(posting.id)
        self.assertEqual(entry["status"], "applied")
        self.assertEqual(entry["notes"], "Sent CV")
        self.assertEqual(entry["fit"], 81)

    def test_longer_description_replaces_shorter(self):
        posting = make_posting(description="Short snippet.")
        self.store.add([posting])
        self.store.add([make_posting(description="A much longer full advert body.")])
        self.assertEqual(
            self.store.get(posting.id)["description"], "A much longer full advert body."
        )

    def test_shorter_description_never_overwrites(self):
        posting = make_posting(description="A much longer full advert body.")
        self.store.add([posting])
        self.store.add([make_posting(description="Snippet.")])
        self.assertEqual(
            self.store.get(posting.id)["description"], "A much longer full advert body."
        )

    def test_empty_incoming_fields_do_not_erase_values(self):
        posting = make_posting()
        self.store.add([posting])
        self.store.add([make_posting(company="", salary_min=None, salary_max=None)])
        entry = self.store.get(posting.id)
        self.assertEqual(entry["company"], "Acme Analytics")
        self.assertEqual(entry["salary_min"], 40000)

    def test_real_load_from_disk_keeps_every_entry(self):
        postings = [make_posting(str(i)) for i in range(5)]
        self.store.add(postings)
        self.assertEqual(len(SeenStore(self.path).entries()), 5)


class MarkTests(StateTestCase):
    def test_mark_updates_status_notes_and_fit(self):
        posting = make_posting()
        self.store.add([posting])
        entry = self.store.mark(posting.id, status="shortlisted", notes="Strong", fit=77)
        self.assertEqual(entry["status"], "shortlisted")
        self.assertEqual(entry["notes"], "Strong")
        self.assertEqual(entry["fit"], 77)
        self.assertEqual(SeenStore(self.path).get(posting.id)["status"], "shortlisted")

    def test_mark_rejects_unknown_status(self):
        posting = make_posting()
        self.store.add([posting])
        with self.assertRaises(UsageError):
            self.store.mark(posting.id, status="maybe")

    def test_mark_requires_something_to_change(self):
        posting = make_posting()
        self.store.add([posting])
        with self.assertRaises(UsageError):
            self.store.mark(posting.id)

    def test_mark_rejects_out_of_range_fit(self):
        posting = make_posting()
        self.store.add([posting])
        with self.assertRaises(UsageError):
            self.store.mark(posting.id, fit=101)

    def test_mark_unknown_id_raises(self):
        with self.assertRaises(NotFoundError):
            self.store.mark("nope", status="applied")

    def test_every_status_is_accepted(self):
        posting = make_posting()
        self.store.add([posting])
        for status in STATUSES:
            self.assertEqual(self.store.mark(posting.id, status=status)["status"], status)


class ResolveTests(StateTestCase):
    def setUp(self):
        super().setUp()
        payload = {
            "version": 1,
            "postings": {
                "abc111": {"id": "abc111", "title": "One", "status": "new"},
                "abc222": {"id": "abc222", "title": "Two", "status": "new"},
                "def333": {"id": "def333", "title": "Three", "status": "new"},
            },
        }
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        self.store = SeenStore(self.path)

    def test_exact_id_resolves(self):
        self.assertEqual(self.store.resolve("def333"), "def333")

    def test_unique_prefix_resolves(self):
        self.assertEqual(self.store.resolve("def"), "def333")

    def test_ambiguous_prefix_raises(self):
        with self.assertRaises(AmbiguousIdError) as caught:
            self.store.resolve("abc")
        self.assertEqual(caught.exception.code, "ambiguous_id")

    def test_missing_id_raises_not_found(self):
        with self.assertRaises(NotFoundError):
            self.store.resolve("zzz")

    def test_empty_reference_is_a_usage_error(self):
        with self.assertRaises(UsageError):
            self.store.resolve("  ")

    def test_counts_by_status(self):
        counts = self.store.counts()
        self.assertEqual(counts["total"], 3)
        self.assertEqual(counts["new"], 3)


class DurabilityTests(StateTestCase):
    def test_corrupt_file_is_refused_not_overwritten(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{ this is not json")
        store = SeenStore(self.path)
        with self.assertRaises(StateError):
            store.add([make_posting()])
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "{ this is not json")

    def test_backup_of_previous_version_is_kept(self):
        posting = make_posting()
        self.store.add([posting])
        self.store.mark(posting.id, status="applied")
        backup = self.path + ".bak"
        self.assertTrue(os.path.exists(backup))
        with open(backup, encoding="utf-8") as fh:
            previous = json.load(fh)
        self.assertEqual(previous["postings"][posting.id]["status"], "new")

    def test_no_temporary_files_are_left_behind(self):
        self.store.add([make_posting()])
        leftovers = [name for name in os.listdir(self.tmpdir) if name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_empty_file_is_treated_as_an_empty_store(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        open(self.path, "w", encoding="utf-8").close()
        self.assertEqual(SeenStore(self.path).entries(), {})

    def test_bare_mapping_is_read(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"xyz789": {"id": "xyz789", "title": "Bare"}}, fh)
        self.assertEqual(SeenStore(self.path).get("xyz789")["title"], "Bare")

    def test_missing_file_is_an_empty_store(self):
        self.assertEqual(self.store.entries(), {})

    def test_state_path_can_come_from_the_environment(self):
        previous = os.environ.get("JOBSEARCH_STATE")
        os.environ["JOBSEARCH_STATE"] = self.path
        try:
            self.assertEqual(SeenStore().path, SeenStore(self.path).path)
        finally:
            if previous is None:
                os.environ.pop("JOBSEARCH_STATE", None)
            else:
                os.environ["JOBSEARCH_STATE"] = previous

    def test_posting_round_trips_through_the_store(self):
        posting = make_posting(extra={"category": "IT Jobs"})
        self.store.add([posting])
        rebuilt = Posting.from_dict(self.store.get(posting.id))
        self.assertEqual(rebuilt.title, posting.title)
        self.assertEqual(rebuilt.salary_max, 50000)
        self.assertTrue(rebuilt.is_remote)
        self.assertEqual(rebuilt.extra, {"category": "IT Jobs"})


if __name__ == "__main__":
    unittest.main()
