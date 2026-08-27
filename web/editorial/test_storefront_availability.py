from types import SimpleNamespace

from django.test import SimpleTestCase

from editorial.storefront_availability import (
    COMING_SOON,
    LAUNCHED,
    NOT_ATTACHED,
    derive_storefront_availability,
    normalize_sales_channels,
)


class StorefrontAvailabilityTests(SimpleTestCase):
    @staticmethod
    def metadata(**overrides):
        values = {
            "lulu_url": "",
            "hotmart_url": "",
            "sales_channels": [],
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_without_attached_ebook_is_not_attached(self):
        result = derive_storefront_availability(
            self.metadata(lulu_url="https://www.lulu.com/shop/example"),
            ebook_attached=False,
        )
        self.assertEqual(result.status, NOT_ATTACHED)
        self.assertEqual(result.label, "E-book não anexado")

    def test_attached_ebook_without_active_store_is_coming_soon(self):
        result = derive_storefront_availability(
            self.metadata(),
            ebook_attached=True,
        )
        self.assertEqual(result.status, COMING_SOON)
        self.assertEqual(result.label, "Lançamento em breve")
        self.assertEqual(result.active_sales_channels, ())

    def test_lulu_https_link_launches_attached_ebook(self):
        result = derive_storefront_availability(
            self.metadata(lulu_url="https://www.lulu.com/shop/example"),
            ebook_attached=True,
        )
        self.assertEqual(result.status, LAUNCHED)
        self.assertEqual(result.label, "Lançado")
        self.assertEqual(result.active_sales_channels[0]["name"], "Lulu")

    def test_any_active_https_store_can_launch(self):
        metadata = self.metadata(
            sales_channels=[
                {
                    "name": "IngramSpark",
                    "url": "https://shop.example.com/book/123",
                    "active": True,
                }
            ]
        )
        result = derive_storefront_availability(metadata, ebook_attached=True)
        self.assertEqual(result.status, LAUNCHED)
        self.assertEqual(result.active_sales_channels[0]["name"], "IngramSpark")

    def test_inactive_or_non_https_store_does_not_launch(self):
        metadata = self.metadata(
            sales_channels=[
                {
                    "name": "Store A",
                    "url": "https://shop.example.com/book/123",
                    "active": False,
                },
                {
                    "name": "Store B",
                    "url": "http://unsafe.example.com/book/123",
                    "active": True,
                },
            ]
        )
        result = derive_storefront_availability(metadata, ebook_attached=True)
        self.assertEqual(result.status, COMING_SOON)
        self.assertEqual(result.active_sales_channels, ())

    def test_legacy_and_generic_channels_are_deduplicated(self):
        url = "https://www.lulu.com/shop/example"
        channels = normalize_sales_channels(
            self.metadata(
                lulu_url=url,
                sales_channels=[{"name": "Lulu Books", "url": url, "active": True}],
            )
        )
        self.assertEqual(len(channels), 1)
        self.assertEqual(channels[0]["name"], "Lulu")
