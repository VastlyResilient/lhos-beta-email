import unittest
from datetime import date

from content_guard import validate_daily_content
from email_template import build_varied_email
from iris_fallback import deterministic_bundle, generate_bundle, select_topic, validate_generated_bundle


class IrisFallbackTests(unittest.TestCase):
    def test_topic_rotation_varies_by_day_and_week(self):
        ids = {select_topic(date(2030, 1, d))["id"] for d in range(1, 15)}
        self.assertGreaterEqual(len(ids), 10)

    def test_deterministic_bundle_is_actionable_and_safe(self):
        topic_ids = set()
        for offset in range(28):
            day = date.fromordinal(date(2030, 1, 1).toordinal() + offset)
            bundle = deterministic_bundle(day, reference="")
            ok, reasons = validate_generated_bundle(bundle, has_reference=False)
            self.assertTrue(ok, (day, reasons))
            content_ok, content_reasons = validate_daily_content(bundle["raw"])
            self.assertTrue(content_ok, (day, content_reasons))
            self.assertGreaterEqual(len(bundle["sections"]), 4)
            self.assertIn("Daily Briefing", bundle["subject"])
            topic_ids.add(bundle["topic_id"])
        self.assertEqual(len(topic_ids), 28)

    def test_incomplete_reference_routes_safe_theme_without_provider(self):
        bundle = generate_bundle(date(2030, 1, 2), "Please use travel preparation and a smoother return home as the theme.", "", "https://unused.example")
        self.assertIn(bundle["topic_id"], {"travel-48-hour", "pack-by-activity", "house-shutdown", "travel-reentry"})
        ok, reasons = validate_generated_bundle(bundle, has_reference=True)
        self.assertTrue(ok, reasons)
        self.assertEqual(bundle["generator"], "curated-v1")

    def test_unverified_product_claim_is_rejected_without_reference(self):
        bundle = deterministic_bundle(date(2030, 1, 2), reference="")
        bundle["sections"][0]["body"] += " Sprint 9 launches tomorrow with a new feature."
        ok, reasons = validate_generated_bundle(bundle, has_reference=False)
        self.assertFalse(ok)
        self.assertTrue(any("product claim" in reason for reason in reasons), reasons)

    def test_varied_template_uses_large_responsive_logo(self):
        bundle = deterministic_bundle(date(2030, 1, 2), reference="")
        rendered = build_varied_email(bundle["sections"], "January 02, 2030", bundle["intro"])
        self.assertIn('width="320"', rendered)
        self.assertIn('max-width: 100%', rendered)
        self.assertIn(bundle["sections"][0]["title"], rendered)
        self.assertNotIn('width="280"', rendered)


if __name__ == "__main__":
    unittest.main()
