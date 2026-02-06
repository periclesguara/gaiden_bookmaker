# Projects as Source of Truth (2026)

## Definition
Projects is the official entry point for book registration:
- `/pipeline/projects/` (list)
- `/pipeline/projects/new/` (wizard)
- `/pipeline/projects/<book_code>/` (hub)

Only Projects can create new books.

## Canonical RAW Path
All RAW uploads are stored as:
- `data/raw/<book_code>/<language>/source.txt`
- `data/raw/<book_code>/<language>/source.md`

The canonical filename is always `source.<ext>` (original upload name is ignored).

## Separation of Concerns
- Projects = registration (metadata + RAW upload only)
- Runner Matrix = execution (translate/split/return/build)
- Steps (Assets) = images/cover + read-only status
- Frontmatter = editorial content
- Dashboard = observability

## Non-goals
- No pipeline execution from Projects
- No RAW upload outside Projects
- No legacy scripts
