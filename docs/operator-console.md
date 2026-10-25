# Operator console

The browser talks to a real loopback FastAPI application, three HTTP service subprocesses and independently owned PostgreSQL databases. It requires local generated credentials; no public demonstration password, vendor account or remote model is needed.

## Reproduce the review

1. Start `python -m integrity.console --workspace .local/console-01 --port 8582` from the installed repository. Startup executes the contract lifecycle and source-ledger/dbt comparisons.
2. Open `http://127.0.0.1:8582`. Read the ignored workspace's `operator-credentials.json` locally. Keep the file private.
3. Sign in as `auditor`. Inspect the $39,000/$39,000/$37,500 invoice comparison, separate cash/ARR, scope selector, exceptions, source lineage and audit.
4. Sign in as `operator`, open **Exceptions & delivery**, and propose a bounded retry for `sf-recovery-v1`. A reason is required. No action executes merely by proposing.
5. Sign in separately as `approver`. Inspect the immutable hash and exact payload. A rejection leaves the delivery exhausted. A new proposal can then be approved with a review note.
6. The worker revalidates both identities, expiry, payload, source and mappings before new dispatch. It reads independent receipts, confirms the billing and dependent finance effects, then schedules a durable financial recheck.
7. Refresh the comparison. Billing is $45,600 and ERP $44,100, cash stays $16,500, and ARR is $146,400. Inspect the original $1,500 variance still open. The reviewer cannot edit a financial amount or declare it resolved.
8. Follow **Source lineage** for fresh authenticated receipt and individual business-record GETs. Canonical JSON retains original integer money types; dashboard money travels as exact integer-cent strings.

`reader_eu` can inspect only EU records. `outsider` has a different tenant and sees no Northstar data. Database row-security tests also exercise direct SQL without relying on browser filters.

## Working views

- Reconciliation: comparable invoice values, separate cash/ARR, invoice grain, source health and last per-scope comparison times.
- Exceptions & delivery: assigned financial cases, bounded attempts, exact-content reviews and durable financial recheck states. A failed recheck preserves the prior release.
- Source lineage: original source event, exact intent, attempt history and fresh independent receiver readback.
- Decision ledger: append-only scoped observations. JSON download contains only the authenticated scope and no runtime credentials.

The console reads stored verified financial comparisons; it does not claim a globally simultaneous distributed snapshot. The three sources must remain quiescent during capture/build/publication. Polling refreshes recorded state; it does not silently rewrite financial history.

[Actual browser evidence and screenshots](evidence/operator-console/README.md) · [Security controls](security.md)
