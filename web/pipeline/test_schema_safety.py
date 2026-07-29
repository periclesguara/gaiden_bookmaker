from unittest.mock import MagicMock, patch

from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext

from pipeline.models import BookEditionTemplate, ensure_bookeditiontemplate_runtime_columns


class RuntimeSchemaSafetyTests(SimpleTestCase):
    @patch("pipeline.models.connection")
    def test_missing_columns_raise_without_executing_ddl(self, mocked_connection):
        cursor = MagicMock()
        mocked_connection.cursor.return_value.__enter__.return_value = cursor
        mocked_connection.introspection.table_names.return_value = [
            BookEditionTemplate._meta.db_table
        ]
        mocked_connection.introspection.get_table_description.return_value = [
            ("id",)
        ]

        with self.assertRaises(ImproperlyConfigured):
            ensure_bookeditiontemplate_runtime_columns()

        cursor.execute.assert_not_called()

    @patch("pipeline.models.connection")
    def test_complete_schema_is_a_read_only_check(self, mocked_connection):
        cursor = MagicMock()
        mocked_connection.cursor.return_value.__enter__.return_value = cursor
        mocked_connection.introspection.table_names.return_value = [
            BookEditionTemplate._meta.db_table
        ]
        mocked_connection.introspection.get_table_description.return_value = [
            (field.column,)
            for field in BookEditionTemplate._meta.local_concrete_fields
        ]

        ensure_bookeditiontemplate_runtime_columns()

        cursor.execute.assert_not_called()


class RuntimeSchemaPostgreSQLTests(TestCase):
    def test_common_queryset_executes_no_schema_ddl(self):
        with CaptureQueriesContext(connection) as queries:
            BookEditionTemplate.objects.count()

        sql = "\n".join(query["sql"].upper() for query in queries.captured_queries)
        for statement in ("ALTER TABLE", "CREATE TABLE", "DROP TABLE"):
            self.assertNotIn(statement, sql)
