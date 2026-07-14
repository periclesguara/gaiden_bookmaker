import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from gaiden.application.author_studio.create_author import create_author

from ..models import Author, CanonicalText, Work

MEDIA_ROOT = tempfile.mkdtemp(prefix="gaiden-author-studio-view-tests-")


def tearDownModule():
    shutil.rmtree(MEDIA_ROOT, ignore_errors=True)


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class AuthorStudioViewTests(TestCase):
    def test_author_list_and_creation(self):
        response = self.client.get(reverse("author_studio:author_list"))
        self.assertEqual(response.status_code, 200)
        response = self.client.post(reverse("author_studio:author_create"), {"name": "Arthur Conan Doyle"})
        author = Author.objects.get()
        self.assertRedirects(response, reverse("author_studio:author_detail", args=[author.slug]))
        detail = self.client.get(response.url)
        self.assertContains(detail, "Arthur Conan Doyle")
        self.assertContains(detail, "ACD")

    def test_author_list_has_add_work_action_for_each_author(self):
        author = create_author("Arthur Conan Doyle")
        response = self.client.get(reverse("author_studio:author_list"))
        self.assertContains(response, "Adicionar obra")
        self.assertContains(response, reverse("author_studio:work_create", args=[author.slug]))

    def test_duplicate_author_returns_form_error(self):
        create_author("Arthur Conan Doyle")
        response = self.client.post(reverse("author_studio:author_create"), {"name": "arthur  conan doyle"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "já está cadastrado")

    def test_add_work_detail_and_canonical_view(self):
        author = create_author("Arthur Conan Doyle")
        body = " ".join(["The narrative remains intact and contains enough meaningful words for validation."] * 25)
        upload = SimpleUploadedFile("adventures.txt", f"THE ADVENTURES\nCHAPTER 1\n{body}".encode(), content_type="text/plain")
        response = self.client.post(
            reverse("author_studio:work_create", args=[author.slug]),
            {"title": "The Adventures of Sherlock Holmes", "original_language": "en", "source_file": upload},
        )
        work = Work.objects.get()
        self.assertRedirects(response, reverse("author_studio:work_detail", args=[work.code]))
        detail = self.client.get(response.url)
        self.assertContains(detail, "ACD-ADVEN")
        self.assertContains(detail, "SRC001")
        self.assertContains(detail, "SHA-256")
        canonical = CanonicalText.objects.get(work=work)
        text_response = self.client.get(reverse("author_studio:canonical_text", args=[work.code]))
        self.assertContains(text_response, canonical.code)
        self.assertContains(text_response, "CHAPTER 1")

    def test_author_detail_has_add_work_action(self):
        author = create_author("Epictetus")
        response = self.client.get(reverse("author_studio:author_detail", args=[author.slug]))
        self.assertContains(response, "Adicionar obra")

    def test_work_edit_preserves_code(self):
        from gaiden.application.author_studio.create_work import create_work

        author = create_author("Arthur Conan Doyle")
        work = create_work(author=author, title="The Hound")
        response = self.client.post(
            reverse("author_studio:work_edit", args=[work.code]),
            {"title": "The Hound of the Baskervilles", "original_language": "en"},
        )
        self.assertRedirects(response, reverse("author_studio:work_detail", args=[work.code]))
        work.refresh_from_db()
        self.assertEqual(work.code, "ACD-HOUND")
        self.assertEqual(work.title, "The Hound of the Baskervilles")

    def test_work_delete_requires_post_and_removes_only_selected_work(self):
        from gaiden.application.author_studio.create_work import create_work

        author = create_author("Arthur Conan Doyle")
        selected = create_work(author=author, title="The Hound")
        preserved = create_work(author=author, title="The Sign")
        get_response = self.client.get(reverse("author_studio:work_delete", args=[selected.code]))
        self.assertRedirects(get_response, reverse("author_studio:work_detail", args=[selected.code]))
        response = self.client.post(reverse("author_studio:work_delete", args=[selected.code]))
        self.assertRedirects(response, reverse("author_studio:author_detail", args=[author.slug]))
        self.assertFalse(Work.objects.filter(pk=selected.pk).exists())
        self.assertTrue(Work.objects.filter(pk=preserved.pk).exists())
