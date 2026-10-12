# Security policy

## Reporting

This is a private operational repository. Report suspected vulnerabilities to
the repository owner through a private channel. Do not open a public issue and
never paste credentials, database contents, personal data, unpublished
manuscripts, or exploit payloads into an issue or pull request.

A useful report contains:

- affected component and branch;
- impact and reproducible steps with sensitive values removed;
- the earliest known affected commit, when available;
- a safe remediation proposal.

## Supported code

The default branch and active integration branch receive security fixes. Old
feature branches are unsupported until rebased onto a supported baseline.

## Credential handling

- Store secrets only in local environment variables or an approved secret
  manager.
- Use project-scoped credentials and the minimum required permissions.
- Treat any committed value as compromised, including values in deleted files.
- Rotate first; clean Git history only after branch impact is understood.
- Never emit matched secret values from CI scanners.

## Dependency and workflow security

- Keep the dependency graph and Dependabot alerts enabled.
- Pin third-party GitHub Actions to full commit SHAs.
- Give `GITHUB_TOKEN` read-only permissions by default.
- Require security and test checks before merging into the default branch.

See `docs/runbooks/secret-rotation-and-history-cleanup.md` for the current
incident-response sequence.
