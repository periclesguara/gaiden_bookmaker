# Credential incident response and Git history cleanup

## Status

The repository history contains an OpenAI credential previously committed in
`.gaiden_secrets`. The default branch also contained a Django signing key and a
PostgreSQL password in tracked source files. All three values must be treated as
compromised, even though the repository is private.

This remediation removes the values from current branch tips and prevents the
same file classes from being tracked again. It does not revoke credentials or
erase historical blobs.

## Immediate owner actions

1. Revoke the exposed OpenAI key and create a project-scoped replacement.
2. Rotate the PostgreSQL password.
3. Generate a new Django secret key. Existing signed sessions and tokens should
   be considered invalid after rotation.
4. Store replacements only in the local environment or an approved secret
   manager. Never place them in chat, issues, pull requests, Actions logs, or
   repository files.
5. Run application smoke tests with the new environment before deployment.

Use `.env.example` only as a list of variable names. It contains no usable
credentials.

## Current-tree remediation

The security branch removes or neutralizes:

- `.gaiden_secrets`;
- local SQLite databases;
- `web/gaiden_portal/settings.py.bak.*`;
- literal secrets in Django settings and `scripts/ops/env_gaiden.sh`;
- permissive development defaults that could leak into production.

Preventive controls:

- `scripts/ci/check_repo_hygiene.sh` rejects forbidden tracked files;
- `scripts/ci/scan_tracked_secrets.py` detects vendor tokens, private keys,
  credentialed URLs, and generic literal secret assignments;
- `.github/workflows/security.yml` runs both checks with read-only permissions.

## Confirmed history exposure

The OpenAI secret file changed in at least these commits:

- `03b4309de1a11adc040edfcfeb4ba4173c9da7a8`
- `d096a42226e8240d2c9dc20eaaa0897ca243d880`
- `046ed66bd1b1e06dc3e20b3e8c76a3243c3acebf`

Do not print patches or blob contents while investigating the incident.

## History rewrite plan

History rewriting is intentionally deferred until credential rotation and branch
consolidation are complete. With many active branches and stacked pull requests,
an early force-push could reintroduce contaminated commits or destroy review
continuity.

When separately authorized:

1. Freeze pushes and create a restricted mirror backup.
2. Inventory every affected branch and tag.
3. Rewrite approved refs with `git filter-repo`.
4. Run a full-history secret scan with redacted output.
5. Review the ref map and exact force-push set.
6. Force-push only approved refs.
7. Replace every working clone; never merge an old branch into clean history.

No history rewrite or force-push is authorized merely by this runbook.

## Verification

```bash
bash scripts/ci/check_repo_hygiene.sh
python3 scripts/ci/scan_tracked_secrets.py
python3 -m py_compile scripts/ci/scan_tracked_secrets.py
```
