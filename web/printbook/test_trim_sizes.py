from django.test import SimpleTestCase

from .trim_sizes import KDP_PAPERBACK_TRIMS, TRIM_CHOICES, compatibility_map, trim_is_supported


class TrimSizeCatalogTests(SimpleTestCase):
    def test_contains_complete_kdp_us_paperback_catalog(self):
        self.assertEqual(len(KDP_PAPERBACK_TRIMS), 17)
        self.assertTrue(KDP_PAPERBACK_TRIMS.issubset(dict(TRIM_CHOICES)))

    def test_kdp_hardcover_is_limited_to_official_five_sizes(self):
        allowed = compatibility_map()["amazon_kdp|hardcover|cream"]
        self.assertEqual(allowed, ["5.5x8.5", "6x9", "6.14x9.21", "7x10", "8.25x11"])

    def test_ingram_cream_rejects_large_trim(self):
        self.assertFalse(trim_is_supported("ingramspark", "paperback", "cream", "8.5x11"))
        self.assertTrue(trim_is_supported("ingramspark", "paperback", "white", "8.5x11"))
        self.assertFalse(trim_is_supported("ingramspark", "paperback", "cream", "5.433071x8.267717"))

    def test_ingram_dust_jacket_respects_documented_maximum(self):
        self.assertTrue(trim_is_supported("ingramspark", "hardcover_dust_jacket", "white", "6.14x9.252"))
        self.assertFalse(trim_is_supported("ingramspark", "hardcover_dust_jacket", "white", "7x10"))

    def test_uiclap_exposes_its_five_official_standard_sizes(self):
        allowed = compatibility_map()["uiclap|paperback|cream"]
        self.assertEqual(allowed, [
            "4.527559x7.086614", "5.433071x8.267717", "6.299213x9.055118",
            "7.874016x7.874016", "8.267717x11.692913",
        ])
        self.assertFalse(trim_is_supported("uiclap", "hardcover", "cream", "6.299213x9.055118"))
