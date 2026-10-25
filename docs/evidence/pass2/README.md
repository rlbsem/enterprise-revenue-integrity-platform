# Executed financial reconciliation slice

The [latest qualified verification](run02/verification.json) passed **34 Python tests, zero failures/errors/skips**, against source `85bf1f6af4512a4d5d30dc0287ff1bf74851d0ea`. [Exact evidence hashes](run02/manifest.json). The [preceding 33-test checkpoint](run01/verification.json) remains unchanged.

The [financial execution record](run02/finance.json) contains complete scoped HTTP exports, actual source-event receipts, both original and corrected source revisions, three executed dbt result inventories, independent full-build equivalence, source lineage and content-addressed release payloads. All three builds executed **four models and 18 data tests**. The corrected warehouse and a separate fresh warehouse matched every row in all four models. The independent Python oracle matched invoice values and separately derived period/ARR values.

The [current foundational transaction](run02/demo.json) additionally records the PostgreSQL server version and verifies the current CRM Account in scope before new dispatch. The earlier [Pass 1 evidence](../pass1/README.md) is preserved, not relabeled.

## Observations

- Initial: $42,000 invoices = $42,000 ERP invoice postings; $16,500 allocated cash.
- Corrected issued-invoice cohort: $41,500 expected basis = $41,500 billing net; $40,000 ERP invoice posting; $16,500 allocations/applications; $168,000 ARR separately.
- A credit changes from $1,000 to $2,000 through a retained, explicit backdated revision chain. Two repeated source-event requests are deduplicated.
- A $1,500 source proration remains unposted in ERP. The release reports `review_required`; it does not certify that all business variances are resolved.
- The ambiguous unactivated commitment remains in the coverage queue with a separate $5,400 scheduled quarter. It is not silently included in an issued-invoice comparison.
- Tests execute an actual failing dbt gate and reject equal-count one-cent corruption, duplicate identities, missing source/correction history, orphan financial records, cross-entity/currency mismatches, invalid settlement lineage and unsafe source-domain operations.
- Current-pointer bytes survive failed gates, changed source capture and controlled interruption before pointer replacement. [Genuine killed-process recovery](run02/process-death.json) remains a separate actual HTTP/PostgreSQL test.

The [pinned dependency audit](dependency-audit.json) found no known vulnerabilities at execution time. [Financial definitions and open scope](../../financial-contract.md) describe the finite policy and unsupported lifecycle/warehouse operations. This is local PostgreSQL/HTTP and DuckDB/dbt execution, **not native vendor acceptance, production reliability or an independent architecture/security audit**.
