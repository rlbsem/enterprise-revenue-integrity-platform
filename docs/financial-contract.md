# Financial reconciliation contract — implemented slice

This is a finite local source-ledger/dbt acceptance lane, not a native warehouse run or a complete accounting engine. It extends the verified integration foundation without rewriting its evidence.

## Comparable numbers

The **3-Number Truth Test** compares Q1 **issued-invoice** economics at invoice/tenant/legal-entity/USD grain:

1. Expected quarterly charge: the executed commercial commitment's monthly unit price × quantity × three service months, plus explicit billing proration, less the latest known credit.
2. Billing net invoice: independently summed invoice lines, plus the same authoritative billing adjustments. Header/line disagreement is a quality failure.
3. ERP invoice posting: invoice posting debits, less credit postings, plus proration postings in the same accounting period.

The first measure is a scheduled invoice basis adjusted for approved source billing events. It is **not total Salesforce bookings**. The third is an A/R/deferred-revenue invoice posting basis, not recognized revenue. This contract supports first-day annual commitments with quarterly upfront billing only. It does not generalize to arbitrary daily proration, taxes, FX, refunds or revenue recognition schedules.

Coverage of unactivated commitments is explicit and separate. The ambiguous third contract has no invoice; its scheduled $5,400 quarter is in the unbilled commitment queue, not silently dropped or included in an issued-invoice comparison. Ambiguous identity is actionable, not presumed fraud. An approved but unactivated contract is classified as activation lag requiring review.

## Actual correction scenario

The original two-entity transaction yields $42,000 invoiced and posted, with $16,500 allocated cash. The billing source then emits a $1,000 credit, later explicitly corrects it to $2,000, effective January 15 and observed February 15. Finance emits its own matching credit revisions. A separate $1,500 billing proration is not posted to finance.

Latest known revision selection retains both original and corrected source events; it never sums the old and new credit. Exact duplicate event delivery produces a duplicate receipt and no new source event. A newer linked **decrease** is also allowed in a test, while stale/missing correction history and wrong-domain writes are rejected. This is a source correction protocol, not an unrestricted operator editing capability.

The corrected output is:

| Q1 issued-invoice measure | Synthetic USD |
|---|---:|
| Expected invoice basis | $41,500 |
| Billing net invoice | $41,500 |
| ERP invoice posting | $40,000 |
| Allocated cash / matching ERP applications | $16,500 |
| Invoice-cohort balance after allocations | $25,000 |
| Period-end active ARR, shown separately | $168,000 |

The $1,500 ERP shortfall remains `unposted_adjustment_requires_review`. Missing posting may be timing or an error; this implementation does not label it benign without supporting review. The release remains **review_required**, also carrying the unactivated ambiguous commitment. Passing software/data gates means the discrepancy is faithfully represented, not that the business books reconcile to zero.

[Executed records and exact tests](evidence/pass2/README.md). `observed_source_at` and the March 31 cutoff are synthetic source-history attributes; the actual verification timestamp is recorded separately. They do not assert that this application was running in January–March. Actual server receipt times remain in the PostgreSQL source journal.

## Evidence, models and independent comparison

The CLI reads complete scoped HTTP exports from each source in a repeatable-read transaction per service, validates exact export content and source/correction coverage, and loads raw records into DuckDB. The services are quiescent during capture/build/publication. There is no global distributed snapshot; a fresh source capture must match before the current release pointer changes.

Four dbt table models expose invoice grain, latest known adjustments, invoice reconciliation and separate period/ARR measures. Eighteen data tests cover grain, required fields, header/line parity and cash bounds. All declared tests must appear in actual dbt results. Raw line, allocation and posting groups aggregate independently before joining; no monetary many-to-many multiplication is used.

The Python oracle independently traverses complete source commitments, subscriptions, items, invoice lines, settlement allocations and ERP records. It does not query dbt or reuse SQL formulas. It checks source identity, scope, exact integer cents, reference coverage and full correction chains. It compares every invoice value and independently derives period/ARR measures from unit price × quantity, rather than trusting the precomputed monthly subtotal. Equal-count one-cent corruption is rejected at publication.

The demo performs a baseline build, rebuilds after real local HTTP source corrections, and creates a separate fresh-loaded warehouse from the corrected full snapshot. Every row in all four models must match. **These are full-table builds, not incremental-performance evidence.** Incremental optimization is not claimed.

Publication writes a content-addressed JSON release after dbt and independent financial gates, then atomically replaces its pointer. Existing identical release files are not rewritten. Controlled interruption, changed source snapshots and failed data gates leave the prior pointer intact. The current CLI requires one trusted publisher; concurrent publication/recovery hardening remains a later gate. Consumers receive only read-only JSON, not source credentials or a database connection.

```text
python -m integrity.finance_demo --workspace .local/finance-01 --output .local/finance-01.json
```

## Scope still open in Pass 2

This slice implements credits, bounded explicit proration, partial settlement, unposted adjustments, backdated credit correction, duplicate rejection and mapping/activation coverage. It does **not yet complete** subscription expansion/contraction/cancellation workflows, a full nine-category governed Variance Bridge, business-time/SCD corrections, a native Snowflake adapter, or operator-authorized corrective dispatch. Snowflake execution is not claimed; the warehouse contract is currently implemented only in DuckDB. See [the progress ledger](progress.md).
