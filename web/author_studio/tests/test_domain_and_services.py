from django.db import IntegrityError
from django.test import TestCase

from gaiden.application.author_studio.create_author import create_author
from gaiden.application.author_studio.create_work import create_work
from gaiden.domain.author_studio.codes import author_code_base, canonicalize
from gaiden.domain.author_studio.exceptions import DuplicateAuthorError, DuplicateWorkError

from ..models import Author, Work


class AuthorServiceTests(TestCase):
    def test_creation_normalization_and_expected_code(self):
        author = create_author("  Arthur  Conan Doyle ")
        self.assertEqual(author.name, "Arthur Conan Doyle")
        self.assertEqual(author.canonical_name, "arthur conan doyle")
        self.assertEqual(author.code, "ACD")
        self.assertEqual(author.slug, "arthur-conan-doyle")

    def test_accents_and_initial_algorithm(self):
        self.assertEqual(canonicalize("  Épíctétus "), "epictetus")
        self.assertEqual(author_code_base("H. P. Lovecraft"), "HPL")
        self.assertEqual(author_code_base("Marcus Aurelius"), "MAU")

    def test_code_collision_adds_sequence(self):
        self.assertEqual(create_author("Arthur Conan Doyle").code, "ACD")
        self.assertEqual(create_author("Andrew Charles Dickens").code, "ACD2")

    def test_equivalent_duplicate_is_blocked(self):
        create_author("Arthur Conan Doyle")
        with self.assertRaises(DuplicateAuthorError):
            create_author("  árthur   conan DOYLE ")

    def test_database_constraint_blocks_duplicate_canonical_name(self):
        create_author("Arthur Conan Doyle")
        with self.assertRaises(IntegrityError):
            with self.atomic():
                Author.objects.create(name="Other", canonical_name="arthur conan doyle", slug="other", code="OTH")

    def atomic(self):
        from django.db import transaction
        return transaction.atomic()

    def test_code_is_stable_after_display_name_edit(self):
        author = create_author("Arthur Conan Doyle")
        author.name = "Sir Arthur Conan Doyle"
        author.save(update_fields=["name", "updated_at"])
        author.refresh_from_db()
        self.assertEqual(author.code, "ACD")


class WorkServiceTests(TestCase):
    def setUp(self):
        self.author = create_author("Arthur Conan Doyle")

    def test_creation_and_code(self):
        work = create_work(author=self.author, title="The Adventures of Sherlock Holmes", original_language="en")
        self.assertEqual(work.author, self.author)
        self.assertEqual(work.code, "ACD-ADVEN")
        self.assertEqual(work.original_language, "en")

    def test_duplicate_title_is_blocked_for_same_author(self):
        create_work(author=self.author, title="The Adventures")
        with self.assertRaises(DuplicateWorkError):
            create_work(author=self.author, title="  the   ADVENTURES ")

    def test_same_title_is_allowed_for_different_authors(self):
        other = create_author("Henry Peter Lang")
        one = create_work(author=self.author, title="The Adventure")
        two = create_work(author=other, title="The Adventure")
        self.assertNotEqual(one.code, two.code)

    def test_work_code_collision_adds_sequence(self):
        first = create_work(author=self.author, title="Adventure")
        second = create_work(author=self.author, title="Adventurous Journey")
        self.assertEqual(first.code, "ACD-ADVEN")
        self.assertEqual(second.code, "ACD-ADVE2")

    def test_database_constraint_blocks_duplicate_canonical_title(self):
        create_work(author=self.author, title="The Hound")
        with self.assertRaises(IntegrityError):
            with self.atomic():
                Work.objects.create(author=self.author, title="Other", canonical_title="the hound", slug="other", code="ACD-OTHER")

    def atomic(self):
        from django.db import transaction
        return transaction.atomic()
