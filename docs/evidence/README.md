# Execution evidence

Each record names its actual source revision and execution lane. Business dates in the synthetic fixtures are distinct from verification timestamps. Original evidence bytes and hashes are preserved.

| Record | What actually executed |
|---|---|
| [Foundation](pass1/README.md) | Separate local PostgreSQL databases and HTTP subprocesses, exact receipts, duplicate/lost acknowledgment and process-death recovery |
| [Initial financial slice](pass2/README.md) | Credits/corrections, source oracle, four models and 18 dbt tests at that historical revision |
| [Completed lifecycle](pass2-complete/README.md) | Contract changes and temporal reconstruction, six models/23 tests, incremental/full equality, actual translated PostgreSQL query execution |
| [Internal acceptance](internal-acceptance/README.md) | Current regression, security/row-security, reviewed historical recovery, failed publication, all scenarios and source fingerprints |
| [Operator console](operator-console/README.md) | Actual authenticated browser, separate decisions, verified effects, financial recheck, desktop/mobile accessibility and real screenshots |

These are local synthetic executions. No Salesforce, Stripe, NetSuite or Snowflake vendor API acceptance, production deployment, independent infrastructure recovery or commercial outcome is implied. The separate independent architecture/security audit remains open.
