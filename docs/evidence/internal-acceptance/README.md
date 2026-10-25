# Internal engineering acceptance

[Executed verification](run01/verification.json): **60 Python tests, zero failures/errors/skips**, at source `9f7ce8105f6d1aa623e1a7e22e6829b9067e41c8`. The exact tested source hashes and case outcomes are recorded. One Starlette/httpx dependency deprecation warning was emitted; it did not skip a test or weaken a gate. [Immutable hashes](run01/manifest.json).

This Windows execution used real PostgreSQL 17.11 and three independent local HTTP subprocesses. It executed the complete regression suite plus the standalone [transaction](run01/demo.json) and [financial demo](run01/finance.json). The [lifecycle](run01/lifecycle.json) covers six models/23 dbt tests, independent oracle, historical reconstruction, incremental/full row equivalence, actual translated PostgreSQL query parity and the 2027 renewal cohort.

Direct PostgreSQL negative tests cover scope bindings, revoked identities, unauthorized updates, malformed exact-content proposals and forged completed jobs. Application tests cover login budget, session revocation, CSRF, scope overrides, separate approval, expiry, changed owners, finite retry grants and export safety. A verified recovery schedules one durable financial recheck; an injected data-gate failure preserves prior output.

Actual process evidence is separate: [worker interruption](run01/process-death.json), [restored database gate](run01/restored-state.json), [reviewed history rehydration](run01/history-rehydration.json), and [killed publisher recovery](run01/publication-process-death.json). Historical rehydration imported two already-verified intents, verified original source and receiver records, and issued zero remote action POSTs. It does not reconstruct a complete database backup or claim managed-cloud PITR.

The original $1,500 unresolved invoice-posting variance remains open. All money is synthetic USD. No native business-vendor APIs, cloud resources, commercial results or production deployment were executed. Browser acceptance has its own [recorded provenance](../operator-console/README.md). This is internal acceptance, not the separately required independent architecture/security audit.

Development failures were not relabeled: a history-repair test initially counted its legitimate readback GETs as writes; the assertion was corrected to count actual apply/duplicate action requests. Mobile scroll-focus and asynchronous logout verification issues are documented with the browser records. Earlier immutable foundation and financial records remain unchanged.
