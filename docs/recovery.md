# Recovery and bounded guarantees

An inbox event and its initial outbox intent commit together. Nothing dispatches while registration remains uncommitted. An effect ID derives from tenant, legal entity, contract revision and destination, with exact-content collision checks at both controller and receiver.

The worker holds a PostgreSQL session advisory lock for the selected effect, including across its attempt-record commit and HTTP calls. A second worker returns `busy`; there is no time-based lease takeover while the first process remains alive. The lock releases when its database session dies. This trades availability and connection efficiency for straightforward local safety. An operating session can block progress until its process is explicitly stopped; production fencing/HA is not established.

Before any POST, the controller reads the receiver receipt. If found, it checks stable identity, original payload, hashes, scope, receipt ID and every individual business record through separate GET requests. This establishes history even if current permission/mapping has since changed. A newly planned ERP action separately revalidates current source and identity. A reconciled billing receipt is not new-action authorization.

If no receipt exists, current source and mappings must still agree. A dispatch-start attempt commits before HTTP. At most three dispatches are permitted, with persisted due times `min(2^attempts,30)` seconds. The ordinary worker honors due times. The acceptance tests explicitly call a single effect step to exercise the finite budget without waiting; they do not claim elapsed-time backoff benchmarking. Exhaustion is explicit. Unknown readback/network state remains uncertain, not proof of absence. HTTP 409/422 is a blocked contract conflict, not blindly retried.

On success, the controller ignores an acknowledgment as sole proof and performs independent receipt and record GETs. Billing may then authorize a separate durable finance intent. Finance verification completes the source event. A response can be lost after the receiver commits. Restart readback recovers the original effect without another POST. The actual process-death test terminates a child **after its real HTTP response and before controller confirmation**, verifies a competing worker is busy, then verifies one receiver effect after restart. This differs from merely raising an exception.

## Limits that matter

- A receipt and business effect are atomic only within each demonstrated receiver transaction. Native vendors may require different idempotency/reconciliation protocols.
- The pre-dispatch source/mapping check and remote commit are not a distributed transaction. Concurrent external revocation between them is not prevented by this foundation. There is no claim of atomic cross-system policy revocation.
- The initial operation permits new activation only, not silent full re-invoicing on a revised contract. Amendments, credits/reversals, source coverage repair, restore-ahead recovery and operator approval are future acceptance gates.
- All processes share one local PostgreSQL server with distinct databases and roles. There is no claim of isolated infrastructure failure domains, cross-region recovery or production RPO/RTO.
- Credentials are randomly generated in ignored private workspaces, sent only to local subprocesses and loopback HTTP. The test harness has installer privileges. TLS, public authentication, SSO, production RLS and secret lifecycle are not proven.
- Fault controls require a separate injector token and run only on local contract services. No live payment, customer communication, native vendor mutation or cloud action occurs.
