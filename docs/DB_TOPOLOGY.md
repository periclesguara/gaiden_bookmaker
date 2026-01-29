# Database Topology (Gaiden)

## Active Database (Canonical)
- **Name:** gaiden
- **Port:** 5433
- **Usage:** All active development and production-like runs
- **How to run server:**
  ```bash
  ./scripts/gaiden_web_gaiden.sh
  ```

## Legacy Database (Do Not Use)
- **Name:** gaiden_django
- **Port:** 5432
- **Status:** Legacy / deprecated

**Notes:**
- Contains historical/duplicated data (EN/DE templates).
- Must not be used for current development.
- Kept only for reference/debugging.

## Important
The Django server must never fall back to sqlite or gaiden_django.
The wrapper script enforces this and should always be used.
