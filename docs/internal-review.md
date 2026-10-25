# Internal acceptance and review boundary

[Executed local regression and scenarios](evidence/internal-acceptance/README.md) and [actual browser acceptance](evidence/operator-console/README.md) qualify the implemented local release. These are constructor-run acceptance checks, not the separate independent Astra architecture/security audit.

## What the checks establish

- Independent local PostgreSQL databases and real authenticated HTTP subprocesses execute synthetic commercial, billing and finance operations.
- Source identity/content, ownership, mappings, duplicate/replay safety, receipt corruption, concurrency, process interruption and bounded recovery have negative tests.
- Native PostgreSQL row security and restricted roles reject cross-tenant/entity reads and unauthorized mutations; separate review is enforced in both API and database paths.
- Six dbt models pass 23 data tests per qualifying build and match independent Python calculations and a separately loaded full warehouse. Compiled queries also execute against local PostgreSQL with typed row equality. Snowflake is parsing only.
- Control rollback against unchanged receivers blocks new dispatch. Reviewed historical rehydration verifies exact source events, independent receipts and receiver records without another action POST.
- Actual browser rejection, approval, dependent effect confirmation and a durable financial recheck produce the displayed synthetic values. Desktop/mobile, keyboard focus, accessibility, JSON download and logout are exercised.
- Publication interruption preserves the prior pointer; an actual killed publisher leaves an explicit dead-owner review requirement. Failed financial jobs do not replace prior output.

## Material limitations for independent review

- One physical local PostgreSQL server hosts separate logical databases. No independent infrastructure failure domains, native cloud restore, RPO/RTO or production deployment are established.
- Loopback bearer authentication, generated local passwords and row security are finite local controls. Production SSO/TLS/secret management and privileged-administrator threats are not proven.
- Capture/build/publication requires quiescent sources; there is no global distributed snapshot or atomic external policy revocation.
- Historical receipt rehydration requires a separately reviewed archive digest and quiescent independent receivers. It reconstructs verified intents/source heads, not lost decisions, attempts or a complete database backup. Conflicting or incompletely covered histories require operator investigation; they are not auto-overwritten.
- Current financial policy uses month-boundary recurring changes and quarterly upfront billing. Arbitrary daily proration, FX, tax, refunds and revenue recognition remain excluded.
- Incremental delete/insert is correctness-tested; it recomputes the finite input and makes no performance/scalability claim.
- Browser/accessibility automation is a repeatable finite check, not universal assistive-technology certification.

Historical failed observations and immutable evidence remain preserved. Development found and corrected JSON landing portability, SQL proposal null-semantics, shared-fixture freshness, mobile scroll focus and Unicode rendering issues. New qualification supersedes current code claims without relabeling earlier records.
