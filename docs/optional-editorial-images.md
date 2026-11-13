# Optional editorial images

Internal illustrations are optional in both the ordinary post-Intake flow and
the Writer-to-Intake asset lane. An edition with no gallery images can proceed
to assembly, preview approval, EPUB generation, EPUBCheck, and finalization.
The cover remains governed by the existing final-publication gate.

Asset selection does not submit forms or run checks automatically. Operators
must click the corresponding button to save a cover, upload optional internal
images, run pre-flight, approve the manuscript, assemble the book, generate an
EPUB, or finalize the edition. The generated-image confirmation applies only
when optional gallery files are actually uploaded.

No database migration or persistent-data rewrite is required. To roll back the
interface behavior, revert the focused commit; existing covers, illustrations,
builds, and operator data remain unchanged.
