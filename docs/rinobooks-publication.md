# RinoBooks publication bridge

## Scope

The bridge sends one completed Gaiden edition to the RinoBooks site as a private
storefront draft. It does not publish the draft, change the public catalog, or
process payment.

The publication boundary is intentionally push-based:

1. Gaiden resolves the final EPUB and cover from external runtime storage.
2. Gaiden runs EPUBCheck and fails closed on any non-zero result.
3. Gaiden builds a storefront manifest from canonical editorial metadata.
4. Gaiden sends the manifest, cover, and EPUB over HTTPS.
5. RinoBooks verifies the request, hashes both files, stores the blobs, and
   records the edition as `DRAFT`.
6. The operator reviews ISBN, price, rights, description, and assets before a
   separate publication action is allowed.

## Runtime configuration

Set these values only in the local environment or approved secret manager:

```text
RINOBOOKS_PUBLISH_URL=https://your-rinobooks-site.example
RINOBOOKS_PUBLISH_TOKEN=<project-scoped secret>
```

The URL must use HTTPS. The token must match the secret configured in the
RinoBooks Site. Never place the real token in Git, command history, logs, issue
comments, or pull-request descriptions.

## Operator command

```bash
python web/manage.py publish_to_rinobooks --edition-id <edition_id>
```

The command reports the remote draft identifier and whether the upload was an
exact duplicate or replaced the existing draft for that storefront slug.
Failures do not downgrade or overwrite the canonical Gaiden edition.

## Idempotency and recovery

RinoBooks uses the Gaiden book code, language, and EPUB SHA-256 to identify an
exact duplicate. A changed EPUB for the same storefront slug replaces the
remote draft while preserving the new object keys by content hash.

If delivery fails, correct the configuration, cover, EPUB, or network issue and
run the same command again. The command does not delete local editorial
artifacts and does not make a public storefront mutation.

## Validation

Run the focused regression tests and the repository gates before merge:

```bash
python web/manage.py test pipeline.test_rinobooks_publish
python web/manage.py check
python web/manage.py makemigrations --check --dry-run
bash scripts/ci/check_repo_hygiene.sh
python3 scripts/ci/scan_tracked_secrets.py
```

Ordinary CI must mock the RinoBooks HTTP request. It must never upload a real
cover or EPUB, call the production Site, or require the production token.
