"""Tests for merging and de-duplication."""

import unittest

from jobsearch.models import Posting, stable_id
from jobsearch.pipeline import dedupe, identity_key


def make(source, provider_id, title="AI Trainer", company="Acme", location="London"):
    return Posting(
        id=stable_id(source, provider_id),
        source=source,
        provider_id=provider_id,
        title=title,
        company=company,
        location=location,
        currency="GBP",
        posted_date="2026-10-20",
        description="Body",
    )


class DedupeTests(unittest.TestCase):
    def test_identical_posting_appearing_twice_is_removed(self):
        first = make("adzuna", "1")
        again = make("adzuna", "1")
        self.assertEqual([p.id for p in dedupe([first, again])], [first.id])

    def test_same_job_on_two_boards_is_merged(self):
        adzuna = make("adzuna", "1")
        reed = make("reed", "1")
        merged = dedupe([adzuna, reed])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].source, "adzuna")

    def test_first_source_in_order_wins(self):
        adzuna = make("adzuna", "1")
        reed = make("reed", "1")
        merged = dedupe([reed, adzuna])
        self.assertEqual(merged[0].source, "reed")

    def test_same_title_at_different_companies_is_kept(self):
        one = make("adzuna", "1", company="Acme")
        two = make("reed", "2", company="Other")
        self.assertEqual(len(dedupe([one, two])), 2)

    def test_same_title_in_different_locations_is_kept(self):
        one = make("adzuna", "1", location="London")
        two = make("reed", "2", location="Leeds")
        self.assertEqual(len(dedupe([one, two])), 2)

    def test_hidden_employer_is_not_merged_across_boards(self):
        one = make("adzuna", "1", company="")
        two = make("reed", "2", company="")
        self.assertEqual(len(dedupe([one, two])), 2)
        self.assertIsNone(identity_key(one))

    def test_anonymous_employer_labels_are_not_merged(self):
        one = make("adzuna", "1", company="Confidential")
        two = make("reed", "2", company="Confidential")
        self.assertEqual(len(dedupe([one, two])), 2)

    def test_case_and_whitespace_are_normalised_for_identity(self):
        one = make("adzuna", "1", title="AI  Trainer", company="ACME ")
        two = make("reed", "2", title="ai trainer", company="Acme")
        self.assertEqual(len(dedupe([one, two])), 1)


if __name__ == "__main__":
    unittest.main()
