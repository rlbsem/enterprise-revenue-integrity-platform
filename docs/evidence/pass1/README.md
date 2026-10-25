# Local contract acceptance

This lane executes real PostgreSQL 17 databases and three local HTTP subprocesses on synthetic records. It makes no native Salesforce, Stripe, NetSuite or Snowflake claim.

The [qualified run](run03/verification.json) passed **20 tests, zero failures/errors/skips** against source `cd4954c25da8313d228747ba6f8645eaf4bfc6f1` on Windows with Python 3.12.14 and PostgreSQL 17.11. Source fingerprints use UTF-8/LF normalization and were independently compared with that Git revision. [Manifest](run03/manifest.json).

The [executed transaction trace](run03/demo.json) contains source event/contract identities, exact USD cents, controller inbox/outbox/attempts/audit records, independent billing and finance receipts, and receiver request journals. It confirms $42,000 invoiced/posted and $16,500 allocated cash across two entities. One billing response times out after commit and recovers by readback; the third ambiguous account blocks without a receiver effect.

[Actual process-death observation](run03/process-death.json) records the killed subprocess, blocked competing worker, durable unconfirmed intent, and exactly one independent effect before and after restart. [Pinned dependency audit](run03/dependency-audit.json) found no known advisories in the audited set at execution time; this is not a guarantee of future vulnerability absence.

[Development diagnostics](development-notes.json) preserve preliminary failures and the corrections made before qualification. Raw private logs and databases remain excluded. Original qualified records are immutable; subsequent passes write new paths.
