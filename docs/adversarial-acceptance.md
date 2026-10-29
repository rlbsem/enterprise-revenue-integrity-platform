# Independent security and recovery acceptance

This additive lane tests the existing application through PostgreSQL logins, real loopback HTTP, independent source/receiver queries, controlled corruption and actual process termination. It imports no existing test helper or application oracle for its independent financial arithmetic. Fixture installation and normal application calls are reused; observations and expected results are collected separately.

## Baseline and review dependency

The branch starts at main `f653af9b0bfe782402a75d7cfe88e7e6395a9445`, then normally merges the unchanged portable PR #3 head `94d134b466b6deea76e2f9bd744a545d21e57ed2`. PR #3 remains draft and untouched. Review its portable-runtime changes separately; the new adversarial work depends on that implementation. Main's current licensing notice is retained byte for byte.

The earlier 65-test suite already covers individual scope checks, source/receipt recovery, bounded approvals and financial modeling. The new lane composes different failures and collects actual database/receiver state rather than only asserting an application return value.

| Boundary | New executed probe |
|---|---|
| Tenant and legal entity | Populate a second tenant with actual effects; each restricted principal must see its own nonempty scope. Attempt role/session impersonation, disabled RLS, mutation and forged scope settings directly in PostgreSQL. |
| Browser API | Use a real Uvicorn process, authenticated HTTP cookies and CSRF tokens. Probe foreign records, wrong host/origin, missing/wrong CSRF, extra fields, oversized input and replayed logged-out sessions. |
| Approval | Race two separately approved proposals against the same exhausted effect; independently count failed requests, successful applications and resulting effects. Revoke proposer/approver scope or expire approval before execution. |
| Worker | Kill actual subprocesses after committed intent, while dispatch waits on a receiver transaction lock, after receipt readback before control confirmation, and before pending finance work resumes. |
| Corruption | Alter a receiver record, receipt identity or retained control proof using the trusted fixture installer; unchanged index hashes must not permit recovery. |
| Restored control | Clone an earlier local PostgreSQL database while source/receiver databases retain later history. Require a blocked gate, reviewed exact history repair, idempotent rehydration and no new receiver POSTs. Current ownership still rejects new work. |
| Financial publication | Recompute totals from separate source-database invoice lines, latest adjustment revisions, postings and allocations. Attempt a one-cent corrupted publication; retain the prior pointer and open $1,500 discrepancy. |
| Restart | Restart the billing HTTP process and owned PostgreSQL server; independently compare retained receipts, role scope and gate state. |

## Narrow corrections

The initial probes establish two recovery defects, detailed with failed observations in the [engineering report](evidence/adversarial/README.md):

1. Index hash agreement did not revalidate business-record contents or compare retained verified control proof. Recovery now performs independent receipt and record GETs and compares existing verified proof before admitting continuation.
2. History repair checked existing payload but not all existing identity/hash/proof fields. A corrupt existing hash could allow partial import before postcheck rejection. Conflicting current control history is now rejected inside the transaction before any import or audit insertion.

Application database connections also receive a three-second libpq connection timeout, separate from the existing twelve-second statement timeout. This is defensive bounded-failure configuration, not a claimed production vulnerability or controlled performance improvement. No financial formula, fixture, dbt model, migration, license, public UI or source-authority rule is changed.

## Reproduce locally

Use the existing pinned Python 3.12 environment and PostgreSQL binaries. Set `PG_BIN` to their directory. This lane refuses `INTEGRITY_ADMIN_DSN`: it requires a new owned disposable cluster and never restarts an externally supplied database.

```sh
python scripts/verify_adversarial.py --workspace .local/adversarial-private --output .local/adversarial-evidence
python scripts/check_adversarial.py
python scripts/check_evidence.py
```

Use fresh paths. Raw pytest logs and temporary PostgreSQL files remain private because assertion diagnostics can include generated credentials. Public observations and sanitized test outcomes are written separately; failed checks are retained. The checker verifies source revision bindings, artifact hashes and every historical evidence file.

## Container acceptance

The dedicated workflow builds the **normal application image**, mounts only the independent driver/tests and a source binding read-only, and runs with no network interface beyond loopback, no published ports, no capabilities, a read-only root filesystem, a non-root user and finite memory/CPU/process limits. PostgreSQL and HTTP processes run genuinely inside that container. Temporary databases live on disposable tmpfs; only sanitized observations are copied out. The workflow checks actual container configuration, stopped PID/exit state and removal.

The unchanged portable workflow separately exercises normal startup, authenticated browser recovery and teardown twice at the current PR head. These are distinct execution lanes. No static interface export or infrastructure-availability result substitutes for recovery acceptance.

## Limits

The tests use synthetic records and finite local vendor contracts, not native Salesforce, Stripe or NetSuite. All logical databases share one PostgreSQL server; physical failure-domain independence, managed PITR, production deployment, HA, penetration testing and real commercial outcomes remain unproven. Corruption uses installer-only privileges to model broken recovery state; it is not evidence that ordinary application users can modify immutable records.

The before/after verification uses independently observed SQL/HTTP effects. It is an automated principal-engineering review, not an external human security audit. Source checks and remote commits remain separate transactions; revocation in the interval between them is not claimed atomic. Independent receiver writers must be quiescent during history repair. No new workload, cloud service, public console or profile change is required.
