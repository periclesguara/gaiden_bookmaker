from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings

from gaiden.infrastructure import storage


def _clear_storage_resolver_caches() -> None:
    for resolver in (storage.repo_root, storage.storage_root):
        cache_clear = getattr(resolver, "cache_clear", None)
        if callable(cache_clear):
            cache_clear()


def write_json_fixture(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


class IsolatedStorageMixin:
    """Run each test with canonical storage and process state isolated in /tmp."""

    patch_storage_repo_root = False

    def run(self, result=None):
        try:
            self._prepare_isolated_storage()
        except BaseException:
            self.doCleanups()
            raise
        return super().run(result)

    def _prepare_isolated_storage(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="gaiden-pipeline-test-")
        self.addCleanup(temporary.cleanup)
        # Registered before the environment/settings cleanups so LIFO cleanup
        # clears resolver state after those process-wide values are restored.
        self.addCleanup(_clear_storage_resolver_caches)

        self.test_project_root = Path(temporary.name).resolve()
        self.test_storage_root = self.test_project_root / "data"
        self.test_fixture_root = self.test_storage_root / "fixtures"
        self.test_storage_root.mkdir(parents=True, exist_ok=True)
        self.test_fixture_root.mkdir(parents=True, exist_ok=True)

        original_cwd = Path.cwd()
        self.addCleanup(os.chdir, original_cwd)
        os.chdir(self.test_project_root)

        environment = patch.dict(
            os.environ,
            {"GAIDEN_STORAGE_ROOT": str(self.test_storage_root)},
            clear=False,
        )
        environment.start()
        self.addCleanup(environment.stop)

        settings_override = override_settings(
            BASE_DIR=self.test_project_root / "web",
            MEDIA_ROOT=self.test_storage_root,
        )
        settings_override.enable()
        self.addCleanup(settings_override.disable)

        if self.patch_storage_repo_root:
            repo_root_patcher = patch.object(
                storage,
                "repo_root",
                return_value=self.test_project_root,
            )
            repo_root_patcher.start()
            self.addCleanup(repo_root_patcher.stop)

        _clear_storage_resolver_caches()


class IsolatedStorageTestCase(IsolatedStorageMixin, TestCase):
    pass


class IsolatedStorageSimpleTestCase(IsolatedStorageMixin, SimpleTestCase):
    pass
