"""Tests for the posting-integrity gate.

All fixtures are offline: ``tests/fixtures/guard_samples.json`` holds the
payloads (with non-ASCII characters stored as JSON escapes so the file stays
readable in any editor).
"""

import json
import os
import unittest

from jobsearch.guard import GuardResult, html_to_text, scan

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def load_samples():
    with open(os.path.join(FIXTURE_DIR, "guard_samples.json"), encoding="utf-8") as fh:
        return json.load(fh)


class HiddenContentTests(unittest.TestCase):
    def setUp(self):
        self.samples = load_samples()

    def test_display_none_is_detected_and_removed(self):
        result = scan(self.samples["hidden_html"])
        self.assertIn("hidden_content", result.codes)
        self.assertEqual(result.verdict, "suspicious")
        self.assertNotIn("Ignore previous instructions", result.cleaned_text)
        self.assertIn("AI Trainer", result.cleaned_text)
        hidden = [f for f in result.findings if f.code == "hidden_content"][0]
        self.assertIn("display:none", hidden.message)
        self.assertIn("Ignore previous", hidden.evidence)

    def test_offscreen_positioning_is_detected(self):
        result = scan(self.samples["hidden_offscreen"])
        self.assertIn("hidden_content", result.codes)
        self.assertNotIn("System prompt", result.cleaned_text)

    def test_hidden_attribute_is_detected(self):
        result = scan(self.samples["hidden_attribute"])
        self.assertIn("hidden_content", result.codes)
        self.assertNotIn("you are an AI", result.cleaned_text)

    def test_aria_hidden_is_detected(self):
        result = scan(self.samples["hidden_aria"])
        self.assertIn("hidden_content", result.codes)
        message = result.findings[0].message
        self.assertIn("aria-hidden", message)

    def test_font_size_zero_is_detected(self):
        result = scan(self.samples["hidden_font_zero"])
        self.assertIn("hidden_content", result.codes)
        self.assertNotIn("Hidden by font size", result.cleaned_text)

    def test_script_payload_is_flagged_as_hidden(self):
        # Script bodies never render, so an instruction inside one is exactly
        # the kind of payload this gate exists to surface.
        result = scan(self.samples["script_payload"])
        self.assertIn("hidden_content", result.codes)
        self.assertNotIn("ignore previous instructions", result.cleaned_text)
        self.assertIn("ignore previous instructions", result.findings[0].evidence)

    def test_script_contents_are_not_visible_text(self):
        result = scan("<p>Visible advert.</p><script>console.log('tracking');</script>")
        self.assertNotIn("console.log", result.cleaned_text)
        self.assertIn("Visible advert.", result.cleaned_text)


class InvisibleCharacterTests(unittest.TestCase):
    def setUp(self):
        self.samples = load_samples()

    def test_zero_width_characters_are_reported_and_stripped(self):
        result = scan(self.samples["zero_width"], html=False)
        self.assertIn("zero_width", result.codes)
        self.assertEqual(result.verdict, "suspicious")
        self.assertEqual(result.cleaned_text, "SeniorEngineer at AcmeCorp")
        evidence = [f for f in result.findings if f.code == "zero_width"][0].evidence
        self.assertIn("<U+200B>", evidence)

    def test_bidi_controls_are_reported_and_stripped(self):
        result = scan(self.samples["bidi"], html=False)
        self.assertIn("bidi_control", result.codes)
        self.assertNotIn("\u202e", result.cleaned_text)
        self.assertIn("<U+202E>", result.findings[0].evidence)

    def test_tag_characters_are_decoded_for_the_reviewer(self):
        result = scan(self.samples["tag_chars"], html=False)
        self.assertIn("unicode_tags", result.codes)
        self.assertEqual(result.verdict, "high_risk")
        finding = [f for f in result.findings if f.code == "unicode_tags"][0]
        self.assertIn("ignore previous instructions", finding.evidence)
        self.assertEqual(result.cleaned_text, "Graduate scheme")
        self.assertGreater(finding.count, 20)


class InstructionAndSpoofTests(unittest.TestCase):
    def setUp(self):
        self.samples = load_samples()

    def test_instruction_shaped_line_is_flagged_but_kept(self):
        result = scan(self.samples["instruction_line"], html=False)
        self.assertIn("instruction_like", result.codes)
        self.assertEqual(result.verdict, "high_risk")
        finding = result.findings[0]
        self.assertIn("line 2", finding.location)
        # The visible line stays in the cleaned text: the reviewer needs to see it.
        self.assertIn("Ignore previous instructions", result.cleaned_text)

    def test_you_are_an_ai_is_flagged(self):
        result = scan(self.samples["you_are_an_ai"], html=False)
        self.assertIn("instruction_like", result.codes)
        self.assertEqual(result.verdict, "high_risk")

    def test_system_prompt_mention_is_medium_severity(self):
        result = scan(self.samples["system_prompt"], html=False)
        self.assertIn("instruction_like", result.codes)
        self.assertEqual(result.verdict, "suspicious")
        self.assertEqual(result.of_severity("medium")[0].code, "instruction_like")

    def test_mixed_latin_cyrillic_word_is_flagged(self):
        result = scan(self.samples["mixed_script"], html=False)
        self.assertIn("mixed_script", result.codes)
        finding = result.findings[0]
        self.assertIn("p\u0430ypal", finding.message)
        self.assertIn("<CYR:\u0430>", finding.evidence)
        self.assertEqual(result.verdict, "high_risk")


class CleanInputTests(unittest.TestCase):
    def setUp(self):
        self.samples = load_samples()

    def test_clean_html_has_no_findings(self):
        result = scan(self.samples["clean_html"])
        self.assertEqual(result.findings, [])
        self.assertEqual(result.verdict, "clean")
        self.assertIn("AI Trainer", result.cleaned_text)
        self.assertIn("- Write and maintain grading rubrics", result.cleaned_text)

    def test_clean_plain_text_has_no_findings(self):
        result = scan(self.samples["clean_text"], html=False)
        self.assertEqual(result.verdict, "clean")
        self.assertIn("Fully remote", result.cleaned_text)

    def test_angle_brackets_in_prose_are_not_treated_as_html(self):
        text = self.samples["angle_brackets_text"]
        result = scan(text)
        self.assertEqual(result.verdict, "clean")
        self.assertEqual(result.cleaned_text, text)

    def test_html_to_text_collapses_markup(self):
        text = html_to_text("<div><p>Line one</p><p>Line&nbsp;two</p></div>")
        self.assertIn("Line one", text)
        self.assertIn("Line two", text)
        self.assertNotIn("<p>", text)

    def test_result_serialises_for_json(self):
        result = scan(self.samples["zero_width"], html=False)
        self.assertIsInstance(result, GuardResult)
        payload = result.as_dict()
        self.assertEqual(payload["verdict"], "suspicious")
        self.assertEqual(payload["findings"][0]["code"], "zero_width")
        json.dumps(payload)  # must not raise


if __name__ == "__main__":
    unittest.main()
