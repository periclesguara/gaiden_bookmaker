# Secret rotation and Git history cleanup

## Incident status

An OpenAI credential was committed to `.gaiden_secrets`. Treat it as compromised.
Removing the file from the current tree does not revoke the credential and does
not remove it from Git history. The owner must revoke it in the OpenAI dashboard,
create a replacement, and store the replacement only in a local environment or
secret manager.

The current-tree remediation removes these tracked files without deleting an
operator's local copies:

- `.gaiden_secrets`
- `data/db/gaiden.sqlite3`
- `web/data/db/gaiden.sqlite3`
- `web/db.sqlite3`

Use `.env.example` as the variable-name template. Never place real values in it.

## Confirmed history exposure

The secret file changed in these commits:

- `03b4309de1a11adc040edfcfeb4ba4173c9da7a8`
- `d096a42226e8240d2c9dc20eaaa0897ca243d880`
- `046ed66bd1b1e06dc3e20b3e8c76a3243c3acebf`

Those commits are reachable from `main`, `feature/intake-module-v1`, the active
development branches derived from them, and the published checkpoint/freeze/
contract/release tags. Before rewriting history, generate the authoritative list:

```bash
for ref in $(git for-each-ref --format='%(refname)' refs/heads refs/remotes/origin refs/tags); do
  for commit in 03b4309de1a11adc040edfcfeb4ba4173c9da7a8 \
                d096a42226e8240d2c9dc20eaaa0897ca243d880 \
                046ed66bd1b1e06dc3e20b3e8c76a3243c3acebf; do
    if git merge-base --is-ancestor "$commit" "$ref" 2>/dev/null; then
      printf '%s\n' "$ref"
      break
    fi
  done
done
```

Do not print patches or blob contents while investigating this incident.

## History rewrite plan (requires explicit approval)

1. Revoke and replace the credential first.
2. Freeze pushes and notify every collaborator that clones must be replaced.
3. Create a mirror backup with restricted access.
4. Install `git-filter-repo` and rewrite all approved branches and tags:

   ```bash
   git filter-repo --invert-paths --path .gaiden_secrets --force
   ```

5. Run a full-history Gitleaks scan with redaction enabled.
6. Review the rewritten ref map and obtain approval for the exact force-push set.
7. Force-push only those approved refs, delete affected remote PR branches only
   when explicitly approved, and invalidate caches/forks where possible.
8. Re-clone all working copies; never merge an old branch back into clean history.

No history rewrite or force push is authorized by this runbook.

## Preventive checks

Run locally:

```bash
bash scripts/ci/check_repo_hygiene.sh
python3 scripts/ci/scan_tracked_secrets.py
```

The security workflow runs the same current-tree checks on pull requests without
printing matched values. A separate full-history Gitleaks scan is mandatory in
an authorized environment after the approved rewrite.
