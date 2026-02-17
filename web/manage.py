#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gaiden.env_guard import assert_venv

assert_venv(ROOT)


def main():
    """Run administrative tasks."""
    try:
        from gaiden.secrets_loader import load_secrets
        load_secrets()
    except Exception:
        pass
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gaiden_portal.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
