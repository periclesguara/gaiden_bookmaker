# Book-Specific Patches Policy

Gaiden must not promote book-specific emergency fixes into generic pipeline logic.

Book-specific patches, including book_0026 PT-BR fixes, must remain isolated
from the internal agent runtime and from generic editorial stages.

Generic stages must be driven by:

- `data/contracts/`
- `gaiden/application/agents/`
- validators
- stage resolver
- translate/refine/polish routers
- neutral fixtures

Book-specific scripts may exist only as historical, legacy, or one-off tooling,
and must not be imported by the generic runtime.

A correction for one book is not a product rule.

Any reusable rule must be promoted intentionally into a contract, validator,
or generic engine only after review.
