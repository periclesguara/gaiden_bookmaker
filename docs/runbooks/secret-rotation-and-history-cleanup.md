# Secret rotation and history cleanup

An OpenAI key was committed in `.gaiden_secrets`. Treat it as compromised.
Deleting the current file does not revoke the key and does not remove historical
blobs. The owner must revoke and replace it in the OpenAI dashboard.

The file changed in commits `03b4309d`, `d096a422`, and `046ed66b`; those commits
are reachable from `main`, the PR #2 branch, many derived branches, and published
checkpoint/freeze tags. Never print their patches or blob contents during audit.

Current-tree prevention:

```bash
bash scripts/ci/check_repo_hygiene.sh
python scripts/ci/scan_tracked_secrets.py
```

Store real values in a local `.env` or secret manager and use `.env.example` only
as a name/template reference.

History removal requires a coordinated freeze, collaborator notification,
restricted mirror backup, exact affected-ref inventory, and explicit approval.
The proposed rewrite is:

```bash
git filter-repo --invert-paths --path .gaiden_secrets --force
```

Afterward, run a redacted full-history Gitleaks scan, review the ref map, obtain
approval for the exact force-push set, and require fresh clones. This runbook
does not authorize rewriting history, force-pushing, deleting branches/tags,
closing PRs, or merging.
