from __future__ import annotations

import json

from django.test import TestCase
from django.urls import reverse

from gaiden.domain.editorial.collections import CollectionKind
from gaiden.infrastructure import collections_storage

from .models import Collection, CollectionItem


class CollectionModuleTests(TestCase):
    def _new_collection_code(self, suffix: str = "") -> str:
        base = f"collection_test_{Collection.objects.count() + 1:04d}"
        return f"{base}_{suffix}" if suffix else base

    def test_root_shows_choice_between_book_and_collection(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Book")
        self.assertContains(response, "Collection")

    def test_create_collection(self):
        response = self.client.post(
            reverse("collection_new"),
            {
                "title": "Sherlock Collection",
                "subtitle": "",
                "collection_kind": "collected_tales",
                "author_display_name": "Arthur Conan Doyle",
                "language": "en",
                "item_count": 2,
            },
        )
        self.assertEqual(response.status_code, 302)
        collection = Collection.objects.get()
        self.assertEqual(collection.title, "Sherlock Collection")

    def test_new_collection_kinds_are_valid_model_choices(self):
        expected = {
            "thematic_collection",
            "collected_dialogues",
            "selected_works",
            "complete_works",
            "collected_works",
            "anthology",
            "omnibus",
            "mixed_collection",
            "cycle_collection",
            "companion_volume",
        }
        self.assertTrue(expected.issubset({value for value, _label in CollectionKind.choices}))

        for collection_kind in expected:
            with self.subTest(collection_kind=collection_kind):
                collection = Collection(
                    code=self._new_collection_code(collection_kind),
                    title="Kind Test",
                    subtitle="",
                    collection_kind=collection_kind,
                    author_display_name="Author",
                    language="en",
                    status="COLLECTION_CREATED",
                    item_count=2,
                )
                collection.full_clean()

    def test_create_socrates_as_collected_dialogues(self):
        response = self.client.post(
            reverse("collection_new"),
            {
                "title": "Socrates",
                "subtitle": "The Trial, Death, and Immortality of the Soul",
                "collection_kind": "collected_dialogues",
                "author_display_name": "Plato",
                "language": "en",
                "item_count": 4,
            },
        )
        self.assertEqual(response.status_code, 302)
        collection = Collection.objects.get(title="Socrates")
        self.assertEqual(collection.collection_kind, "collected_dialogues")
        self.assertEqual(collection.get_collection_kind_display(), "Collected Dialogues")

    def test_collection_create_page_shows_new_collection_kind_labels(self):
        response = self.client.get(reverse("collection_new"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Thematic Collection")
        self.assertContains(response, "Collected Dialogues")
        self.assertContains(response, "Companion Volume")

    def test_collection_context_exposes_french_translate_agent(self):
        from .services.workflow import build_collection_context

        collection = Collection.objects.create(
            code=self._new_collection_code("fr-agent"),
            title="French Collection",
            subtitle="",
            collection_kind="collected_tales",
            author_display_name="Author",
            language="fr",
            status="COLLECTION_CREATED",
            item_count=2,
        )

        context = build_collection_context(collection)
        french_option = next((item for item in context["translate_options"] if item["target"] == "FR"), None)

        self.assertIsNotNone(french_option)
        self.assertEqual(french_option["route"], "Agent")
        self.assertEqual(french_option["agent"], "LE_GRAND_COULHON")

    def test_collection_create_page_uses_responsive_selection_ui(self):
        response = self.client.get(reverse("collection_new"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Numero de itens")
        self.assertContains(response, "Continuar para os itens")

    def test_create_collection_requires_metadata(self):
        response = self.client.post(
            reverse("collection_new"),
            {
                "title": "",
                "subtitle": "",
                "collection_kind": "",
                "author_display_name": "",
                "language": "en",
                "item_count": 2,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This field is required", count=3)

    def test_item_count_limits(self):
        response = self.client.post(
            reverse("collection_new"),
            {
                "title": "Too Small",
                "subtitle": "",
                "collection_kind": "collected_tales",
                "author_display_name": "Author",
                "language": "en",
                "item_count": 1,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ensure this value is greater than or equal to 2")

        response = self.client.post(
            reverse("collection_new"),
            {
                "title": "Too Large",
                "subtitle": "",
                "collection_kind": "collected_tales",
                "author_display_name": "Author",
                "language": "en",
                "item_count": 11,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ensure this value is less than or equal to 10")

    def test_order_is_required_and_contiguous_for_prepare(self):
        collection = Collection.objects.create(
            code=self._new_collection_code("gap"),
            title="C",
            subtitle="",
            collection_kind="omnibus",
            author_display_name="Author",
            language="en",
            status="COLLECTION_CREATED",
            item_count=2,
        )
        CollectionItem.objects.create(collection=collection, order_index=1, author_name="A", work_title="W1")
        CollectionItem.objects.create(collection=collection, order_index=3, author_name="A", work_title="W2")
        response = self.client.post(reverse("collection_process", kwargs={"collection_id": collection.id}), {"action": "prepare"})
        self.assertEqual(response.status_code, 302)
        collection.refresh_from_db()
        self.assertEqual(collection.status, "COLLECTION_FAILED")

    def test_items_ui_rejects_gap_before_persisting_item(self):
        collection = Collection.objects.create(
            code=self._new_collection_code("ui-gap"),
            title="C",
            subtitle="",
            collection_kind="omnibus",
            author_display_name="Author",
            language="en",
            status="COLLECTION_CREATED",
            item_count=2,
        )
        response = self.client.post(
            reverse("collection_items", kwargs={"collection_id": collection.id}),
            {"order_index": 2, "author_name": "A", "work_title": "W1"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Next item must use contiguous order 1.")
        self.assertEqual(collection.items.count(), 0)

    def test_uploads_use_collection_storage_namespace(self):
        collection = Collection.objects.create(
            code=self._new_collection_code("upload"),
            title="C",
            subtitle="",
            collection_kind="omnibus",
            author_display_name="Author",
            language="en",
            status="COLLECTION_CREATED",
            item_count=2,
        )
        item = CollectionItem.objects.create(collection=collection, order_index=1, author_name="A", work_title="W1")
        response = self.client.post(
            reverse("collection_upload", kwargs={"collection_id": collection.id}),
            {
                "item_id": item.id,
                "source_format": "html",
                "source_file": self._html_upload("one.html", "<html><body><h1>One</h1><p>Body</p></body></html>"),
            },
        )
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertIn(f"/data/collections/{collection.code}/en/uploads/", item.source_original_path)
        self.assertNotIn("/data/raw/", item.source_original_path)
        self.assertEqual(item.upload_status, "completed")

    def test_upload_page_has_txt_html_epub_format_options(self):
        collection = self._collection_with_two_items()
        response = self.client.get(reverse("collection_upload", kwargs={"collection_id": collection.id}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="txt"')
        self.assertContains(response, 'value="html"')
        self.assertContains(response, 'value="epub"')
        self.assertContains(response, 'accept=".txt,.html,.htm,.epub"')

    def test_upload_rejects_format_file_mismatch(self):
        collection = self._collection_with_two_items()
        item = collection.items.order_by("order_index").first()
        response = self.client.post(
            reverse("collection_upload", kwargs={"collection_id": collection.id}),
            {
                "item_id": item.id,
                "source_format": "epub",
                "source_file": self._html_upload("one.html", "<html><body>One</body></html>"),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Arquivo incompativel com EPUB")

    def test_prepare_and_merge_create_isolated_artifacts(self):
        collection = self._collection_with_two_items()
        self._upload_all(collection)
        response = self.client.post(reverse("collection_process", kwargs={"collection_id": collection.id}), {"action": "prepare"})
        self.assertEqual(response.status_code, 302)
        prepared = collections_storage.item_prepared_path(collection.code, collection.language, 1)
        self.assertTrue(prepared.exists())
        collection.refresh_from_db()
        self.assertEqual(collection.status, "COLLECTION_PREPARED")
        self.assertEqual(collection.items.get(order_index=2).normalize_status, "pending")

        response = self.client.post(reverse("collection_process", kwargs={"collection_id": collection.id}), {"action": "normalize"})
        self.assertEqual(response.status_code, 302)
        normalized = collections_storage.item_normalized_path(collection.code, collection.language, 2)
        self.assertTrue(normalized.exists())
        collection.refresh_from_db()
        self.assertEqual(collection.status, "COLLECTION_NORMALIZED")

        response = self.client.post(reverse("collection_process", kwargs={"collection_id": collection.id}), {"action": "merge"})
        self.assertEqual(response.status_code, 302)
        merged = collections_storage.merged_source_path(collection.code, collection.language)
        self.assertTrue(merged.exists())
        merged_text = merged.read_text(encoding="utf-8")
        self.assertIn("BOOK ONE", merged_text)
        self.assertIn("First Work", merged_text)
        self.assertIn("BOOK TWO", merged_text)
        self.assertIn("Second Work", merged_text)
        manifest = collections_storage.manifest_path(collection.code, collection.language)
        self.assertTrue(manifest.exists())
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(payload["collection_code"], collection.code)
        self.assertEqual(payload["items"][0]["upload_status"], "completed")
        self.assertEqual(payload["items"][0]["normalize_status"], "completed")
        self.assertEqual(payload["merged_final"]["path"], str(merged))

    def test_prepare_mechanical_cleanup_removes_gutenberg_boilerplate_and_contents(self):
        collection = self._collection_with_two_items()
        html = """
        <html><body>
        <p>*** START OF THE PROJECT GUTENBERG EBOOK ***</p>
        <p>Title: Old Title</p>
        <p>Contents</p>
        <p>I. Something</p>
        <h1>Book I</h1>
        <h2>Chapter IV</h2>
        <p>12</p>
        <p>Real body starts here.</p>
        <p>*** END OF THE PROJECT GUTENBERG EBOOK ***</p>
        </body></html>
        """
        for item in collection.items.order_by("order_index"):
            self.client.post(
                reverse("collection_upload", kwargs={"collection_id": collection.id}),
                {
                    "item_id": item.id,
                    "source_format": "html",
                    "source_file": self._html_upload(f"item_{item.order_index}.html", html),
                },
            )
        self.client.post(reverse("collection_process", kwargs={"collection_id": collection.id}), {"action": "prepare"})
        prepared = collections_storage.item_prepared_path(collection.code, collection.language, 1).read_text(encoding="utf-8")
        self.assertNotIn("PROJECT GUTENBERG", prepared)
        self.assertNotIn("Contents", prepared)
        self.assertNotIn("\n12\n", prepared)
        self.assertIn("BOOK 1", prepared)
        self.assertIn("CHAPTER 4", prepared)
        self.assertIn("Real body starts here.", prepared)

    def test_prepare_keeps_only_book_body_and_drops_standardebooks_front_back_matter(self):
        collection = self._collection_with_two_items()
        html = """
        <html><body>
        <h1>Title Page</h1>
        <p>Standard Ebooks</p>
        <p>Title: Old Metadata</p>
        <h1>Frontispiece</h1>
        <p>[Illustration: Portrait.jpg]</p>
        <h1>Preface</h1>
        <p>This explanatory page should go away.</p>
        <h1>Introduction</h1>
        <p>This introduction should go away.</p>
        <h1>Table of Contents</h1>
        <p>Chapter I. The Start</p>
        <h1>Chapter I</h1>
        <p>Real chapter body starts here.</p>
        <h1>Chapter II</h1>
        <p>Second chapter body.</p>
        <h1>Epilogue</h1>
        <p>Back matter should go away.</p>
        <h1>Index</h1>
        <p>Alpha 1</p>
        </body></html>
        """
        for item in collection.items.order_by("order_index"):
            self.client.post(
                reverse("collection_upload", kwargs={"collection_id": collection.id}),
                {
                    "item_id": item.id,
                    "source_format": "html",
                    "source_file": self._html_upload(f"item_{item.order_index}.html", html),
                },
            )
        self.client.post(reverse("collection_process", kwargs={"collection_id": collection.id}), {"action": "prepare"})
        prepared = collections_storage.item_prepared_path(collection.code, collection.language, 1).read_text(encoding="utf-8")
        self.assertNotIn("Standard Ebooks", prepared)
        self.assertNotIn("Old Metadata", prepared)
        self.assertNotIn("Illustration", prepared)
        self.assertNotIn("explanatory page", prepared)
        self.assertNotIn("introduction should go away", prepared)
        self.assertNotIn("Table of Contents", prepared)
        self.assertNotIn("Back matter", prepared)
        self.assertNotIn("Alpha 1", prepared)
        self.assertIn("CHAPTER 1", prepared)
        self.assertIn("Real chapter body starts here.", prepared)
        self.assertIn("CHAPTER 2", prepared)
        self.assertIn("Second chapter body.", prepared)

    def test_prepare_keeps_short_fiction_without_chapter_headings(self):
        collection = self._collection_with_two_items()
        html = """
        <html><body>
        <h1>Short Fiction</h1>
        <h2>The Alchemist</h2>
        <p>High up, crowning the grassy summit, stands the old chateau.</p>
        <h2>Dagon</h2>
        <p>I am writing this under an appreciable mental strain.</p>
        </body></html>
        """
        for item in collection.items.order_by("order_index"):
            self.client.post(
                reverse("collection_upload", kwargs={"collection_id": collection.id}),
                {
                    "item_id": item.id,
                    "source_format": "html",
                    "source_file": self._html_upload(f"item_{item.order_index}.html", html),
                },
            )
        self.client.post(reverse("collection_process", kwargs={"collection_id": collection.id}), {"action": "prepare"})
        prepared = collections_storage.item_prepared_path(collection.code, collection.language, 1).read_text(encoding="utf-8")
        self.assertIn("Short Fiction", prepared)
        self.assertIn("The Alchemist", prepared)
        self.assertIn("High up, crowning the grassy summit", prepared)
        self.assertIn("Dagon", prepared)

    def test_merge_does_not_duplicate_item_title_when_body_starts_with_same_title(self):
        collection = self._collection_with_two_items()
        normalized_one = collections_storage.item_normalized_path(collection.code, collection.language, 1)
        normalized_two = collections_storage.item_normalized_path(collection.code, collection.language, 2)
        normalized_one.parent.mkdir(parents=True, exist_ok=True)
        normalized_one.write_text("First Work\nCHAPTER 1\nBody one.\n", encoding="utf-8")
        normalized_two.write_text("Second Work\nCHAPTER 1\nBody two.\n", encoding="utf-8")
        self.client.post(reverse("collection_process", kwargs={"collection_id": collection.id}), {"action": "merge"})
        merged = collections_storage.merged_source_path(collection.code, collection.language).read_text(encoding="utf-8")
        self.assertIn("BOOK ONE\n\nFirst Work\n\nCHAPTER 1", merged)
        self.assertIn("BOOK TWO\n\nSecond Work\n\nCHAPTER 1", merged)
        self.assertEqual(merged.count("\nFirst Work\n"), 1)
        self.assertEqual(merged.count("\nSecond Work\n"), 1)

    def test_prepare_generates_prepared_output_per_item(self):
        collection = self._collection_with_two_items()
        self._upload_all(collection)
        self.client.post(reverse("collection_process", kwargs={"collection_id": collection.id}), {"action": "prepare"})
        item_one = collections_storage.item_prepared_path(collection.code, collection.language, 1).read_text(encoding="utf-8")
        item_two = collections_storage.item_prepared_path(collection.code, collection.language, 2).read_text(encoding="utf-8")
        self.assertIn("CHAPTER 1", item_one.upper())
        self.assertIn("CHAPTER 1", item_two.upper())

    def test_normalize_generates_normalized_output_per_item(self):
        collection = self._collection_with_two_items()
        self._upload_all(collection)
        self.client.post(reverse("collection_process", kwargs={"collection_id": collection.id}), {"action": "prepare"})
        self.client.post(reverse("collection_process", kwargs={"collection_id": collection.id}), {"action": "normalize"})
        item_one = collections_storage.item_normalized_path(collection.code, collection.language, 1).read_text(encoding="utf-8")
        item_two = collections_storage.item_normalized_path(collection.code, collection.language, 2).read_text(encoding="utf-8")
        self.assertIn("CHAPTER 1", item_one.upper())
        self.assertIn("CHAPTER 1", item_two.upper())

    def test_merge_is_blocked_before_normalize(self):
        collection = self._collection_with_two_items()
        self._upload_all(collection)
        self.client.post(reverse("collection_process", kwargs={"collection_id": collection.id}), {"action": "prepare"})
        response = self.client.post(reverse("collection_process", kwargs={"collection_id": collection.id}), {"action": "merge"})
        self.assertEqual(response.status_code, 302)
        collection.refresh_from_db()
        self.assertEqual(collection.status, "COLLECTION_FAILED")

    def test_handoff_requires_merged_final(self):
        collection = self._collection_with_two_items()
        response = self.client.post(reverse("collection_handoff", kwargs={"collection_id": collection.id}))
        self.assertEqual(response.status_code, 302)
        collection.refresh_from_db()
        self.assertEqual(collection.pipeline_book_code, "")

    def test_handoff_to_pipeline_after_merge(self):
        collection = self._collection_with_two_items()
        self._upload_all(collection)
        self.client.post(reverse("collection_process", kwargs={"collection_id": collection.id}), {"action": "prepare"})
        self.client.post(reverse("collection_process", kwargs={"collection_id": collection.id}), {"action": "normalize"})
        self.client.post(reverse("collection_process", kwargs={"collection_id": collection.id}), {"action": "merge"})
        response = self.client.post(reverse("collection_handoff", kwargs={"collection_id": collection.id}))
        self.assertEqual(response.status_code, 302)
        collection.refresh_from_db()
        self.assertTrue(collection.pipeline_book_code.startswith("book_"))
        self.assertEqual(collection.status, "COLLECTION_PIPELINE_RUNNING")

    def test_items_ui_rejects_duplicate_item_before_persisting(self):
        collection = Collection.objects.create(
            code=self._new_collection_code("dup"),
            title="C",
            subtitle="",
            collection_kind="omnibus",
            author_display_name="Author",
            language="en",
            status="COLLECTION_CREATED",
            item_count=2,
        )
        CollectionItem.objects.create(collection=collection, order_index=1, author_name="A", work_title="Same")
        response = self.client.post(
            reverse("collection_items", kwargs={"collection_id": collection.id}),
            {"order_index": 2, "author_name": "A", "work_title": "Same"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Duplicate item in the same collection is not allowed.")
        self.assertEqual(collection.items.count(), 1)

    def test_items_ui_supports_batch_registration(self):
        collection = Collection.objects.create(
            code=self._new_collection_code("batch"),
            title="Batch Collection",
            subtitle="",
            collection_kind="omnibus",
            author_display_name="Author",
            language="en",
            status="COLLECTION_CREATED",
            item_count=2,
        )
        response = self.client.post(
            reverse("collection_items", kwargs={"collection_id": collection.id}),
            {
                "form-TOTAL_FORMS": "2",
                "form-INITIAL_FORMS": "0",
                "form-MIN_NUM_FORMS": "0",
                "form-MAX_NUM_FORMS": "1000",
                "form-0-order_index": "1",
                "form-0-author_name": "Author",
                "form-0-work_title": "First Work",
                "form-1-order_index": "2",
                "form-1-author_name": "Author",
                "form-1-work_title": "Second Work",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(collection.items.count(), 2)
        self.assertEqual(collection.items.get(order_index=2).work_title, "Second Work")

    def test_collection_review_preview_uses_merged_source_only(self):
        collection = self._collection_with_two_items()
        self._upload_all(collection)
        self.client.post(reverse("collection_process", kwargs={"collection_id": collection.id}), {"action": "prepare"})
        self.client.post(reverse("collection_process", kwargs={"collection_id": collection.id}), {"action": "normalize"})
        self.client.post(reverse("collection_process", kwargs={"collection_id": collection.id}), {"action": "merge"})
        response = self.client.get(reverse("collection_review", kwargs={"collection_id": collection.id}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "BOOK ONE")

    def _collection_with_two_items(self) -> Collection:
        collection = Collection.objects.create(
            code=self._new_collection_code("helper"),
            title="Collection",
            subtitle="",
            collection_kind="omnibus",
            author_display_name="Author",
            language="en",
            status="COLLECTION_CREATED",
            item_count=2,
        )
        CollectionItem.objects.create(collection=collection, order_index=1, author_name="Author", work_title="First Work")
        CollectionItem.objects.create(collection=collection, order_index=2, author_name="Author", work_title="Second Work")
        return collection

    def _upload_all(self, collection: Collection) -> None:
        items = collection.items.order_by("order_index")
        payloads = {
            1: "<html><body><h1>First Work</h1><p>Chapter I. A beginning.</p></body></html>",
            2: "<html><body><h1>Second Work</h1><p>Chapter I. Another beginning.</p></body></html>",
        }
        for item in items:
            self.client.post(
                reverse("collection_upload", kwargs={"collection_id": collection.id}),
                {
                    "item_id": item.id,
                    "source_format": "html",
                    "source_file": self._html_upload(f"item_{item.order_index}.html", payloads[item.order_index]),
                },
            )

    def _html_upload(self, name: str, content: str):
        from django.core.files.uploadedfile import SimpleUploadedFile

        return SimpleUploadedFile(name, content.encode("utf-8"), content_type="text/html")
