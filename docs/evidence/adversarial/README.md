# Adversarial security and recovery acceptance

The separate acceptance driver executed **25 PostgreSQL/HTTP adversarial cases**, alongside the existing **65-test regression** and fresh **47-check browser acceptance**. This is independently observed local execution with synthetic source/receiver contracts, not native Salesforce, Stripe or NetSuite, an external human audit or production acceptance.

## Executed source and results

- [Adversarial result and exact source bindings](run01/acceptance.json), with [individual observations](run01/cases/environment.json) and [artifact inventory](run01/artifact-integrity.json). All 25 cases passed with zero failures, errors or skips. The executed checkpoint is `05ec1a91ceb6f34e72406e979a942ab060f0bfed`.
- [Original regression](regression-run01/verification.json): 65 passed, zero failures/errors/skips, followed by the original integration and financial demos. [Lifecycle output](regression-run01/lifecycle.json) retains incremental/full equality, six models and actual local PostgreSQL analytical-query parity.
- [Real browser acceptance](browser-run01/browser.json): 47 checks, ten automated accessibility views and zero script errors. [Overview](browser-run01/console-overview.png), [approval](browser-run01/console-approval.png), [independent lineage](browser-run01/console-lineage.png), [recovered result](browser-run01/console-recovered.png), [mobile](browser-run01/console-mobile.png).
- Regression and browser execution bind `d678bad3f57421d9c92b277160477885fdaba0eb`. Later checkpoints correct an acceptance assertion, container configuration and canonical source-binding helper; the executed application bytes are unchanged. Each record pins its actual revision rather than claiming it ran on a later documentation commit.
- [Independent native teardown](run01/native-teardown.json) checked 26 owned endpoints, zero listeners, zero exact-workspace processes and six stopped PostgreSQL clusters using Windows inventory and `pg_ctl status`. No general machine cleanup or cloud query occurred.
- [Executed Linux container result](container-run01/run/acceptance.json): the same 25 cases passed with zero failures, errors or skips, using PostgreSQL 17.11 and Python 3.12.14 at `47f235b830124cb3a7d5737a736a1e33a9f84629`. [Actual containment](container-run01/containment.json), [teardown](container-run01/teardown.json) and the [GitHub job](https://github.com/rlbsem/enterprise-revenue-integrity-platform/actions/runs/40818335424) establish non-root execution, stopped PID zero, exit zero, no published ports, no Docker volumes and exact container removal. These are 25 repeated cases on two platforms, not 50 distinct security tests.
- [Preserved files](preserved-files.json): all **114** historical evidence/image files from the merged baseline remain byte-for-byte unchanged. Existing failed portable runs are retained.

## What is new

| Investigation | Independently observed result |
|---|---|
| Populated isolation | Three restricted identities each saw their own nonempty tenant/entity scope. PostgreSQL rejected role/session impersonation, mutation and RLS bypass; changing client scope settings did not change access. |
| Real HTTP security | Authenticated Uvicorn requests rejected foreign tenant/entity records, wrong host/origin, missing/wrong CSRF, extra fields, oversized payloads and replayed logged-out sessions. Exported data stayed within scope without credentials. |
| Competing approvals | Two approved retry proposals produced one billing and one finance effect. Rejected requests were distinguished from the receiver journal's created-application marker; attempt caps stayed bounded. |
| Current authority | Proposer revocation, approver-scope removal and expired approval invalidated execution before a new effect. |
| Four process boundaries | Actual worker processes were terminated after durable intent, during in-flight dispatch, after receipt readback before control confirmation, and before pending finance work. Recovery produced one committed application per destination. |
| Restored control | An earlier PostgreSQL template copy was behind unchanged receiver databases. The gate blocked; exact reviewed history repair made no new receiver POSTs, repeated idempotently and did not authorize work after current ownership changed. |
| Corrupted recovery state | Receiver records, receipt IDs, local verified proof, archive identity/scope/content and conflicting existing hashes were independently challenged. Invalid state blocked admission. |
| Restart | Actual billing-service and owned PostgreSQL restarts retained effects, receipts and row-security behavior. |
| Financial correctness | Separate source-database arithmetic agreed with published values. A one-cent corrupted candidate could not replace the prior publication or clear the open variance. |

The source-correction case independently computes **$41,500 billing, $40,000 ERP and $16,500 allocated cash**, with **-$1,500 posting variance**. The persisted case remains `open` and the release remains `review_required`. These are synthetic invoice/posting/cash measures with distinct grains, not recognized revenue or commercial outcomes. The separate browser lifecycle retains its existing $39,000/$37,500 comparison and $45,600/$44,100 recovered comparison; neither clears the $1,500 issue.

## Findings preserved before correction

[Original failed observations](failed-before-fix/acceptance-baseline.json) recorded **four failures out of ten probes** against application baseline `91822e2c014a4b9f5f7977ed8584ef33b3795677`. The initial newly authored harness had not yet been committed; that provenance limitation is explicit. The final committed harness retains the same invariant assertions and adds further acceptance coverage.

1. The recovery gate accepted an unchanged receiver index even when the actual record, receipt identity or retained verified proof was corrupted. The correction rereads receipts and every referenced business record and compares retained control proof.
2. With a conflicting existing control hash, reviewed history repair imported additional state before its later postcheck failed. The correction checks existing scope, source event, destination, payload, hash and verified proof within the import transaction, before any write.

These are demonstrated recovery-validation defects under installer-induced corruption. They are not demonstrated ordinary-user SQL exploits, production incidents or native-vendor failures. Restricted application-role mutation is separately denied.

[A later failed suite](failed-request-accounting/acceptance.json) recorded 24 passes and one **test-harness accounting failure**. The receiver's request journal marks created applications with 201; its wire-response convention is separate. The initial assertion used 200, while one billing/finance effect already existed. The corrected test separates rejected attempts, created applications and duplicate requests. This is not presented as a third application vulnerability.

Application connection attempts now specify a three-second libpq timeout in addition to the existing twelve-second SQL statement timeout. The first owned-database restart test completed; no indefinite failure or controlled performance gain is claimed.

[An unqualified passed run](unqualified-line-ending-run/acceptance.json) and its [classification](unqualified-line-ending-classification.json) are also preserved. That runner declared canonical UTF-8/LF fingerprints but initially hashed raw Windows line endings for some working files. The helper was corrected to the existing repository convention and the suite reran at the qualified checkpoint above. Original recorded hashes were not rewritten to obtain acceptance.

## Container acceptance and boundaries

The new `Adversarial security and recovery` workflow runs the same suite inside the normal application image with a read-only mounted test driver, no published ports, `network=none`, a non-root user, read-only root and bounded resources. It inspects actual containment, stopped PID/exit state and exact container removal. The committed container record preserves genuine downloaded observations and the original artifact digest; its inspection projection omits runner paths and unrelated configuration. Two earlier CI reporting/setup failures remain identified in that record rather than being described as successful container acceptance.

The portable workflow separately verifies **normal application startup**, authenticated browser recovery, restart and independent teardown twice. GitHub results and downloaded records are separate from the native Windows evidence above; inspect the PR's exact-head checks before release. The checked application, driver and test fingerprints match both committed acceptance runs; later documentation/workflow-only commits do not retroactively change either executed revision.

No live vendor, cloud resource, customer record, profile or licensing change is involved. Logical databases share a single PostgreSQL server. Managed PITR, physical fault-domain isolation, public hosting, production HA/SSO, an external human security audit and performance guarantees remain unverified. Source checks and external commits are not a distributed authorization transaction, and receiver writers must remain quiescent during reviewed history repair.

[Architecture, trust boundaries and reproduction](../../adversarial-acceptance.md) · [Financial definitions](../../financial-contract.md) · [Recovery contract](../../recovery.md).
