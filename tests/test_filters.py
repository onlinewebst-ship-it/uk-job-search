"""Tests for the deterministic filter and scoring layer.

Dates are pinned (``TODAY``) so freshness arithmetic is exact and the suite does
not rot: these tests must produce identical results in 2030.
"""

import datetime as dt
import unittest

from jobsearch.errors import UsageError
from jobsearch.filters import (
    FilterConfig,
    Outcome,
    Weights,
    evaluate,
    keyword_match,
    keywords_from_query,
    rank,
    score,
)
from jobsearch.models import Posting

TODAY = dt.date(2026, 10, 27)


def make_posting(**overrides):
    """A complete, good posting. Override only what a test cares about."""
    values = dict(
        id="abc123",
        source="adzuna",
        provider_id="1",
        title="AI Trainer",
        company="Acme Analytics",
        location="London",
        salary_min=40000,
        salary_max=50000,
        currency="GBP",
        contract_type="permanent",
        contract_time="full_time",
        posted_date="2026-10-20",
        url="https://example.invalid/job/1",
        description="AI trainer role building rubrics. Fully remote within the UK.",
        is_remote=True,
    )
    values.update(overrides)
    return Posting(**values)


class FreshnessFilterTests(unittest.TestCase):
    def test_recent_posting_passes(self):
        outcome = evaluate(make_posting(), FilterConfig(max_age_days=30), today=TODAY)
        self.assertTrue(outcome.passed, outcome.failures)

    def test_old_posting_fails_with_the_age_in_the_message(self):
        outcome = evaluate(
            make_posting(posted_date="2026-09-17"),  # 40 days before TODAY
            FilterConfig(max_age_days=30),
            today=TODAY,
        )
        self.assertFalse(outcome.passed)
        self.assertIn("40 days ago", outcome.failures[0])

    def test_exactly_the_window_passes(self):
        outcome = evaluate(
            make_posting(posted_date="2026-09-27"),  # 30 days
            FilterConfig(max_age_days=30),
            today=TODAY,
        )
        self.assertTrue(outcome.passed, outcome.failures)

    def test_unknown_date_fails(self):
        outcome = evaluate(
            make_posting(posted_date=None),
            FilterConfig(max_age_days=30),
            today=TODAY,
        )
        self.assertFalse(outcome.passed)
        self.assertIn("posted date unknown", outcome.failures[0])

    def test_future_date_clamps_to_zero_days(self):
        outcome = evaluate(
            make_posting(posted_date="2026-12-01"),
            FilterConfig(max_age_days=1),
            today=TODAY,
        )
        self.assertTrue(outcome.passed, outcome.failures)


class ContractFilterTests(unittest.TestCase):
    def test_contract_only_rejects_permanent_full_time(self):
        outcome = evaluate(
            make_posting(), FilterConfig(contract_only=True), today=TODAY
        )
        self.assertFalse(outcome.passed)
        self.assertIn("not contract/part-time", outcome.failures[0])

    def test_contract_only_accepts_contract_type(self):
        outcome = evaluate(
            make_posting(contract_type="contract", contract_time="full_time"),
            FilterConfig(contract_only=True),
            today=TODAY,
        )
        self.assertTrue(outcome.passed, outcome.failures)

    def test_contract_only_accepts_part_time(self):
        outcome = evaluate(
            make_posting(contract_time="part_time"),
            FilterConfig(contract_only=True),
            today=TODAY,
        )
        self.assertTrue(outcome.passed, outcome.failures)

    def test_contract_only_accepts_title_hint_when_type_is_unknown(self):
        outcome = evaluate(
            make_posting(title="Contract AI Trainer", contract_type="unknown"),
            FilterConfig(contract_only=True),
            today=TODAY,
        )
        self.assertTrue(outcome.passed, outcome.failures)

    def test_part_time_only_rejects_full_time(self):
        outcome = evaluate(
            make_posting(), FilterConfig(part_time_only=True), today=TODAY
        )
        self.assertFalse(outcome.passed)
        self.assertIn("not part-time", outcome.failures[0])

    def test_part_time_only_accepts_part_time(self):
        outcome = evaluate(
            make_posting(contract_time="part_time"),
            FilterConfig(part_time_only=True),
            today=TODAY,
        )
        self.assertTrue(outcome.passed, outcome.failures)


class RemoteFilterTests(unittest.TestCase):
    def test_remote_only_rejects_on_site(self):
        outcome = evaluate(
            make_posting(is_remote=False),
            FilterConfig(remote_only=True),
            today=TODAY,
        )
        self.assertFalse(outcome.passed)
        self.assertIn("not remote", outcome.failures[0])

    def test_remote_only_accepts_remote(self):
        outcome = evaluate(
            make_posting(is_remote=True), FilterConfig(remote_only=True), today=TODAY
        )
        self.assertTrue(outcome.passed, outcome.failures)


class SalaryFloorTests(unittest.TestCase):
    def test_top_of_range_above_floor_passes(self):
        outcome = evaluate(
            make_posting(salary_min=30000, salary_max=45000),
            FilterConfig(salary_floor=45000),
            today=TODAY,
        )
        self.assertTrue(outcome.passed, outcome.failures)

    def test_top_of_range_below_floor_fails(self):
        outcome = evaluate(
            make_posting(salary_min=25000, salary_max=30000),
            FilterConfig(salary_floor=35000),
            today=TODAY,
        )
        self.assertFalse(outcome.passed)
        self.assertIn("below the 35,000 floor", outcome.failures[0])

    def test_one_sided_salary_uses_the_known_bound(self):
        outcome = evaluate(
            make_posting(salary_min=50000, salary_max=None),
            FilterConfig(salary_floor=45000),
            today=TODAY,
        )
        self.assertTrue(outcome.passed, outcome.failures)

    def test_missing_salary_passes_with_a_warning(self):
        outcome = evaluate(
            make_posting(salary_min=None, salary_max=None),
            FilterConfig(salary_floor=40000),
            today=TODAY,
        )
        self.assertTrue(outcome.passed)
        self.assertTrue(any("no salary stated" in w for w in outcome.warnings))

    def test_predicted_salary_warns(self):
        outcome = evaluate(
            make_posting(salary_is_predicted=True),
            FilterConfig(salary_floor=30000),
            today=TODAY,
        )
        self.assertTrue(any("board estimate" in w for w in outcome.warnings))


class KeywordFilterTests(unittest.TestCase):
    def test_include_keyword_must_appear_in_the_title(self):
        outcome = evaluate(
            make_posting(), FilterConfig(include_keywords=("engineer",)), today=TODAY
        )
        self.assertFalse(outcome.passed)
        self.assertIn("include keyword", outcome.failures[0])

    def test_include_keyword_matches(self):
        outcome = evaluate(
            make_posting(), FilterConfig(include_keywords=("trainer",)), today=TODAY
        )
        self.assertTrue(outcome.passed, outcome.failures)

    def test_include_keyword_does_not_match_a_body_only_mention(self):
        outcome = evaluate(
            make_posting(title="Operations Lead", description="You will work with an engineer daily."),
            FilterConfig(include_keywords=("engineer",)),
            today=TODAY,
        )
        self.assertFalse(outcome.passed)

    def test_exclude_keyword_rejects(self):
        outcome = evaluate(
            make_posting(title="Senior AI Trainer"),
            FilterConfig(exclude_keywords=("senior",)),
            today=TODAY,
        )
        self.assertFalse(outcome.passed)
        self.assertIn("excluded keyword", outcome.failures[0])

    def test_short_keywords_do_not_match_inside_words(self):
        outcome = evaluate(
            make_posting(title="Trainer", description="Said the team."),
            FilterConfig(include_keywords=("ai",)),
            today=TODAY,
        )
        self.assertFalse(outcome.passed)

    def test_all_failures_are_collected_not_short_circuited(self):
        outcome = evaluate(
            make_posting(posted_date="2026-01-01", is_remote=False),
            FilterConfig(max_age_days=7, remote_only=True, contract_only=True),
            today=TODAY,
        )
        self.assertEqual(len(outcome.failures), 3)


class ScoreTests(unittest.TestCase):
    def test_full_marks(self):
        result = score(
            make_posting(posted_date="2026-10-27"),
            keywords=["ai", "trainer"],
            today=TODAY,
        )
        self.assertEqual(result.total, 100)
        for name, expected in (
            ("keyword", 45),
            ("freshness", 20),
            ("salary", 20),
            ("remote", 15),
        ):
            self.assertEqual(result.components[name]["awarded"], expected, name)

    def test_half_keyword_match_rounds_half_up(self):
        result = score(
            make_posting(posted_date="2026-10-27"),
            keywords=["ai", "python"],
            today=TODAY,
        )
        self.assertEqual(result.components["keyword"]["awarded"], 23)
        self.assertEqual(result.components["keyword"]["missing"], ["python"])
        self.assertEqual(result.total, 78)

    def test_no_keywords_scores_zero_for_the_component(self):
        result = score(make_posting(posted_date="2026-10-27"), today=TODAY)
        self.assertEqual(result.components["keyword"]["awarded"], 0)
        self.assertEqual(result.total, 55)

    def test_freshness_decays_linearly(self):
        result = score(
            make_posting(posted_date="2026-10-12"),  # 15 days
            keywords=["ai", "trainer"],
            today=TODAY,
            window_days=30,
        )
        self.assertEqual(result.components["freshness"]["awarded"], 10)
        self.assertEqual(result.total, 90)

    def test_freshness_is_zero_at_the_window_edge(self):
        result = score(
            make_posting(posted_date="2026-09-27"),  # 30 days
            keywords=["ai", "trainer"],
            today=TODAY,
            window_days=30,
        )
        self.assertEqual(result.components["freshness"]["awarded"], 0)
        self.assertEqual(result.total, 80)

    def test_unknown_date_scores_zero_freshness(self):
        result = score(
            make_posting(posted_date=None),
            keywords=["ai", "trainer"],
            today=TODAY,
        )
        self.assertEqual(result.components["freshness"]["awarded"], 0)
        self.assertEqual(result.total, 80)

    def test_no_salary_scores_zero(self):
        result = score(
            make_posting(posted_date="2026-10-27", salary_min=None, salary_max=None),
            keywords=["ai", "trainer"],
            today=TODAY,
        )
        self.assertEqual(result.components["salary"]["awarded"], 0)
        self.assertEqual(result.total, 80)

    def test_single_figure_salary_scores_less_than_a_range(self):
        result = score(
            make_posting(posted_date="2026-10-27", salary_min=40000, salary_max=40000),
            keywords=["ai", "trainer"],
            today=TODAY,
        )
        self.assertEqual(result.components["salary"]["awarded"], 15)
        self.assertEqual(result.total, 95)

    def test_one_sided_salary_scores_less_again(self):
        result = score(
            make_posting(posted_date="2026-10-27", salary_min=40000, salary_max=None),
            keywords=["ai", "trainer"],
            today=TODAY,
        )
        self.assertEqual(result.components["salary"]["awarded"], 12)
        self.assertEqual(result.total, 92)

    def test_predicted_salary_is_half_credit(self):
        result = score(
            make_posting(posted_date="2026-10-27", salary_is_predicted=True),
            keywords=["ai", "trainer"],
            today=TODAY,
        )
        self.assertEqual(result.components["salary"]["awarded"], 10)
        self.assertEqual(result.total, 90)

    def test_on_site_loses_remote_points(self):
        result = score(
            make_posting(posted_date="2026-10-27", is_remote=False),
            keywords=["ai", "trainer"],
            today=TODAY,
        )
        self.assertEqual(result.components["remote"]["awarded"], 0)
        self.assertEqual(result.total, 85)

    def test_awarded_components_sum_to_the_total(self):
        result = score(
            make_posting(posted_date="2026-10-17", salary_min=35000, salary_max=None),
            keywords=["ai", "trainer", "rubrics"],
            today=TODAY,
        )
        components = sum(int(c["awarded"]) for c in result.components.values())
        self.assertEqual(components, result.total)

    def test_score_never_exceeds_the_weights_total(self):
        result = score(
            make_posting(posted_date="2026-10-27"),
            keywords=["ai", "trainer"],
            today=TODAY,
        )
        self.assertLessEqual(result.total, Weights().total())
        self.assertEqual(Weights().total(), 100)

    def test_reasons_explain_every_component(self):
        result = score(
            make_posting(posted_date="2026-10-27"),
            keywords=["ai", "trainer"],
            today=TODAY,
        )
        joined = " | ".join(result.reasons)
        for word in ("keywords", "freshness", "salary", "remote"):
            self.assertIn(word, joined)

    def test_as_dict_is_json_ready(self):
        import json

        result = score(make_posting(), keywords=["ai"], today=TODAY)
        json.dumps(result.as_dict())


class RankTests(unittest.TestCase):
    def _make(self, posting, passed=True):
        return Outcome(posting=posting, passed=passed, failures=[] if passed else ["x"])

    def test_survivors_sorted_by_score_then_recency(self):
        older = make_posting(id="old", provider_id="old", posted_date="2026-10-01")
        newer = make_posting(id="new", provider_id="new", posted_date="2026-10-26")
        ranked = rank(
            [self._make(older), self._make(newer)],
            keywords_for=lambda p: ["ai", "trainer"],
            today=TODAY,
        )
        self.assertEqual([o.posting.id for o, _ in ranked], ["new", "old"])
        self.assertGreater(ranked[0][1].total, ranked[1][1].total)

    def test_equal_scores_break_ties_by_title(self):
        b = make_posting(id="b", provider_id="b", title="B Trainer", posted_date="2026-10-26")
        a = make_posting(id="a", provider_id="a", title="A Trainer", posted_date="2026-10-26")
        ranked = rank(
            [self._make(b), self._make(a)],
            keywords_for=lambda p: ["trainer"],
            today=TODAY,
        )
        self.assertEqual([o.posting.id for o, _ in ranked], ["a", "b"])

    def test_failed_outcomes_are_dropped(self):
        good = make_posting(id="good", provider_id="good")
        bad = make_posting(id="bad", provider_id="bad")
        ranked = rank(
            [self._make(good), self._make(bad, passed=False)],
            today=TODAY,
        )
        self.assertEqual([o.posting.id for o, _ in ranked], ["good"])

    def test_min_score_filters(self):
        low = make_posting(id="low", provider_id="low", posted_date="2026-10-27", salary_min=None, salary_max=None, is_remote=False)
        ranked = rank(
            [self._make(low)],
            keywords_for=lambda p: [],
            today=TODAY,
            min_score=56,
        )
        self.assertEqual(ranked, [])

    def test_keywords_from_query_drops_stopwords(self):
        self.assertEqual(
            keywords_from_query("AI trainer in London"), ["ai", "trainer", "london"]
        )

    def test_keyword_match_reports_both_sides(self):
        matched, missing = keyword_match(["ai", "python"], "AI trainer")
        self.assertEqual(matched, ["ai"])
        self.assertEqual(missing, ["python"])


class ConfigValidationTests(unittest.TestCase):
    def test_negative_days_is_rejected(self):
        with self.assertRaises(UsageError):
            FilterConfig(max_age_days=-1)

    def test_negative_salary_floor_is_rejected(self):
        with self.assertRaises(UsageError):
            FilterConfig(salary_floor=-5)

    def test_keywords_are_deduped_and_trimmed(self):
        config = FilterConfig(include_keywords=("ai", " AI ", "trainer"))
        self.assertEqual(config.include_keywords, ("ai", "trainer"))


if __name__ == "__main__":
    unittest.main()
