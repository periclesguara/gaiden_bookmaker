# Secret rotation and history cleanup

## Current operating rule

Gaiden reads `DJANGO_SECRET_KEY` and `PGPASSWORD` only from the environment or
an approved secret manager. Local scripts must not provide fallback values for
either credential. API-key examples must use an unmistakably nonfunctional
placeholder.

## Rotation procedure

1. Generate a new Django secret key and a new database password outside this
   repository.
2. Set them in the approved secret manager or the process environment.
3. Restart the affected Gaiden processes and verify that startup succeeds only
   with the new values.
4. Revoke the replaced values at their issuing systems.
5. Review access logs and any CI or deployment logs that may have exposed a
   credential.

## Historical exposure

The repository intentionally does not rewrite history in an ordinary cleanup.
Any credential that appeared in an earlier commit is treated as compromised and
must be rotated. Removing a tracked backup prevents future checkout exposure;
it is not a substitute for rotation. History rewriting requires a separately
approved recovery plan, verified backup, exact reference list, and a forced
push window.
