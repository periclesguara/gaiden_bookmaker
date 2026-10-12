from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ChapterTranslationMigrationTests(TransactionTestCase):
    migrate_from = [("editorial", "0020_work_source_provenance_runtime_compat"), ("pipeline", "0020_productionbookmark_immutable")]
    migrate_to = [
        ("editorial", "0021_pipelineartifact_sha256_alter_pipelineartifact_stage"),
        ("pipeline", "0021_translationjobevent_translationunit_and_more"),
    ]

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_from)
        existing_columns = {
            column.name
            for column in connection.introspection.get_table_description(
                connection.cursor(), "editorial_pipelineartifact"
            )
        }
        if "sha256" not in existing_columns:
            with connection.cursor() as cursor:
                cursor.execute(
                    "ALTER TABLE editorial_pipelineartifact "
                    "ADD COLUMN sha256 varchar(64) NOT NULL DEFAULT ''"
                )
        old_apps = self.executor.loader.project_state(self.migrate_from).apps

        Contributor = old_apps.get_model("editorial", "Contributor")
        Language = old_apps.get_model("editorial", "Language")
        Seal = old_apps.get_model("editorial", "Seal")
        Work = old_apps.get_model("editorial", "Work")
        Edition = old_apps.get_model("editorial", "Edition")
        ManualTranslationJob = old_apps.get_model("pipeline", "ManualTranslationJob")

        author = Contributor.objects.create(name="Migration Author", role="AUTHOR")
        language = Language.objects.create(code="en", name="English", native_name="English")
        seal = Seal.objects.create(slug="migration-seal", name="Migration Seal")
        work = Work.objects.create(
            code="book_migration_v1",
            title="Migration Work",
            author=author,
            original_language=language,
        )
        edition = Edition.objects.create(
            work=work,
            language=language,
            seal=seal,
            title="Preserved title",
            author="Preserved author",
            translator="Preserved translator",
            adapter="Preserved adapter",
        )
        job = ManualTranslationJob.objects.create(
            edition=edition,
            source_language="en",
            target_language="ptbr",
            drive_path="04_TRANSLATION_JOBS/book_migration_v1/ptbr",
            source_path="/preserved/heading_clean.txt",
            source_sha256="a" * 64,
            expected_return_name="book_migration_v1_ptbr.txt",
            status="FAILED",
            return_source="return/book_migration_v1_ptbr.txt",
            return_sha256="b" * 64,
            last_error="preserved retryable error",
        )
        second_job = ManualTranslationJob.objects.create(
            edition=edition,
            source_language="en",
            target_language="fr",
            drive_path="04_TRANSLATION_JOBS/book_migration_v1/fr",
            source_path="/preserved/heading_clean.txt",
            source_sha256="a" * 64,
            expected_return_name="book_migration_v1_fr_translated.txt",
        )
        self.job_id = job.id
        self.second_job_id = second_job.id
        self.edition_id = edition.id

    def tearDown(self):
        MigrationExecutor(connection).migrate(self.migrate_to)
        super().tearDown()

    def test_v1_job_and_editorial_fields_are_preserved_by_v2_migrations(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_to)
        new_apps = self.executor.loader.project_state(self.migrate_to).apps
        Edition = new_apps.get_model("editorial", "Edition")
        ManualTranslationJob = new_apps.get_model("pipeline", "ManualTranslationJob")

        edition = Edition.objects.get(pk=self.edition_id)
        self.assertEqual(
            (edition.title, edition.author, edition.translator, edition.adapter),
            ("Preserved title", "Preserved author", "Preserved translator", "Preserved adapter"),
        )

        job = ManualTranslationJob.objects.get(pk=self.job_id)
        self.assertEqual(job.source_language, "en")
        self.assertEqual(job.target_language, "ptbr")
        self.assertEqual(job.drive_path, "04_TRANSLATION_JOBS/book_migration_v1/ptbr")
        self.assertEqual(job.source_path, "/preserved/heading_clean.txt")
        self.assertEqual(job.source_sha256, "a" * 64)
        self.assertEqual(job.expected_return_name, "book_migration_v1_ptbr.txt")
        self.assertEqual(job.status, "FAILED")
        self.assertEqual(job.return_source, "return/book_migration_v1_ptbr.txt")
        self.assertEqual(job.return_sha256, "b" * 64)
        self.assertEqual(job.last_error, "preserved retryable error")
        self.assertEqual(job.schema_version, "gaiden_manual_translation_job_v1")
        self.assertEqual(job.job_id, "")
        self.assertEqual(job.chapter_count, 0)
        self.assertEqual(job.split_manifest, {})
        self.assertIsNone(job.source_artifact_id)
        self.assertIsNone(job.final_artifact_id)
        second_job = ManualTranslationJob.objects.get(pk=self.second_job_id)
        self.assertTrue(job.correlation_id)
        self.assertTrue(second_job.correlation_id)
        self.assertNotEqual(job.correlation_id, second_job.correlation_id)
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM pg_indexes "
                    "WHERE schemaname = 'public' "
                    "AND tablename = 'editorial_pipelineartifact' "
                    "AND indexdef ILIKE '%%sha256%%'"
                )
                self.assertGreaterEqual(cursor.fetchone()[0], 1)
