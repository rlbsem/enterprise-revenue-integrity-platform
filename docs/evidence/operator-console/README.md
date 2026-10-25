# Actual operator-console browser execution

The qualifying run is [run02/browser.json](run02/browser.json), with pinned source revision, browser version, actual UTC verification time, desktop/mobile assertions, accessibility results, source fingerprints and screenshot hashes. [Artifact manifest](run02/manifest.json).

The verifier starts a fresh local PostgreSQL/HTTP application, signs in with generated private roles, rejects a proposal, submits a new exact proposal for separate approval, verifies billing and finance receipts, waits for the durable dbt/source-oracle financial recheck, downloads scoped JSON and signs out. No vendor API, mock dashboard, paid service or production deployment is involved.

| Stage | Billing / ERP invoice basis | Separate cash / ARR |
|---|---|---|
| Before recovery | $39,000 / $37,500 | $16,500 / $120,000 |
| Verified recovery and financial recheck | $45,600 / $44,100 | $16,500 / $146,400 |

All amounts are synthetic USD from persisted source events. The original $1,500 posting discrepancy remains open. EU-only mobile scope has different, correctly scoped values; it is not the all-entity total.

[Reconciliation](run02/console-overview.png) · [Separate approval](run02/console-approval.png) · [Verified recovery](run02/console-recovered.png) · [Fresh independent readback](run02/console-lineage.png) · [Mobile](run02/console-mobile.png)

Screenshots were taken directly by Playwright from the working Chrome interface, without compositing or manual edits. The test checks WCAG A/AA rules using axe, scroll containment, keyboard focus, API denial, exact integer money, credentials absent from downloads and session logout. Automated accessibility checks cover finite states, not universal certification.

Preserved [run01 failure](run01/failure.json) and [original screenshot hashes](run01/manifest.json) record the earlier attempt. It reached verified recovery and mobile views but checked logout before its asynchronous request completed; it is not a qualifying browser run. The test now waits for the authenticated workspace to close before checking session revocation. Earlier development also found and corrected mobile scroll focus and malformed Unicode display.
