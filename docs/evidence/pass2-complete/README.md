# Contract lifecycle and revenue-integrity acceptance

[Qualified verification](run01/verification.json): 43 Python tests, zero failures/errors/skips, at the recorded source revision. [Hashes](run01/manifest.json).

[Actual executed scenario](run01/lifecycle.json) includes five CRM changes, controller and independent receiver effects, backdated/historical reconstruction, an incremental rebuild, a separate full refresh, every-row equality across six models, actual local PostgreSQL analytical execution and the 2027 renewal cohort. All financial builds passed six models and 23 dbt data tests. Snowflake is syntax-only, not native execution.

Q1 lifecycle values are USD $39,000 billing, $37,500 ERP invoice postings, $16,500 allocated cash and $120,000 ARR. The original $1,500 unposted adjustment remains open and separately explained. A February 10 source-history cutoff and the 2027 renewal cohort are separately inspectable; neither rewrites earlier evidence.

The nine defined variance categories have executed positive/negative cases. A posting outside the quarter explains only its own amount; any unposted remainder remains unresolved. Source snapshots with missing parents or forged billing history fail. Concurrency uses source compare-and-swap; a repeated contract change or lost billing acknowledgment does not create another amendment. Financial cases and append-only observations persist by tenant, entity and reporting period.

Development qualification first exposed a PostgreSQL JSONB versus JSON function mismatch: 36 tests passed and three fixture-dependent cases errored. Declaring the translated warehouse's JSON landing contract corrected it. Raw diagnostic logs remain private; no failed observation was relabeled as a pass.

Preserved [earlier financial slice](../pass2/README.md) and [foundation](../pass1/README.md) remain unchanged. This is local synthetic acceptance, not a production/native-vendor claim or the separate independent architecture/security audit.
