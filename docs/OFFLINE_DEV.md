# Offline Dev Setup (Normalize + Chunks Only)

This project is intended to work without network access during local development.
Build isolation can trigger pip to download build requirements, so use the
`--no-build-isolation` flag for editable installs when offline.

## Activate venv
```bash
source .venv/bin/activate
```

## Ensure build tools are present (online only)
If you have network access, you may update the build tools:
```bash
python -m pip install -U pip setuptools wheel
```
If you are offline, skip this step and rely on the versions already installed
in the virtualenv.

## Editable install (offline-safe)
```bash
python -m pip install -e . --no-build-isolation
```

## Verify Django
```bash
python -c "import django; print(django.get_version())"
```

## Diagnostics (canonical scope)
```bash
python web/manage.py diagnostics --check normalized --only-book book_0003
python web/manage.py diagnostics --check chunks --only-book book_0003
```
