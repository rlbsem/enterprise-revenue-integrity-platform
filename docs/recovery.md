# Recovery and bounded guarantees

An inbox event and its initial outbox intent commit together. Nothing dispatches while registration remains uncommitted. An effect ID derives from tenant, legal entity, contract revision and destination, with exact-content collision checks at both controller and receiver.

The worker holds a PostgreSQL session advisory lock for the selected effect, including across its attempt-record commit and HTTP calls. A second worker returns `busy`; there is no time-based lease takeover while the first process remains alive. The lock releases when its database session dies. This trades availability and connection efficiency for straightforward local safety. An operating session can block progress until its process is explicitly stopped; production fencing/HA is not established.

Before any POST, the controller reads the receiver receipt. If found, it checks stable identity, original payload, hashes, scope, receipt ID and every individual business record through separate GET requests. This establishes history even if current permission/mapping has since changed. A newly planned ERP action separately revalidates current source and identity. A reconciled billing receipt is not new-action authorization.

If no receipt exists, current source and mappings must still agree. A dispatch-start attempt commits before HTTP. At most three ordinary dispatches are permitted, with persisted due times `min(2^attempts,30)` seconds. The ordinary worker honors due times. The acceptance tests explicitly call a single effect step to exercise the finite budget without waiting; they do not claim elapsed-time backoff benchmarking. Exhaustion is explicit. Unknown readback/network state remains uncertain, not proof of absence. HTTP 409/422 is a blocked contract conflict, not blindly retried.

On success, the controller ignores an acknowledgment as sole proof and performs independent receipt and record GETs. Billing may then authorize a separate durable finance intent. Finance verification completes the source event. A response can be lost after the receiver commits. Restart readback recovers the original effect without another POST. The actual process-death test terminates a child **after its real HTTP response and before controller confirmation**, verifies a competing worker is busy, then verifies one receiver effect after restart. This differs from merely raising an exception.

## Limits that matter

- A receipt and business effect are atomic only within each demonstrated receiver transaction. Native vendors may require different idempotency/reconciliation protocols.
- The pre-dispatch source/mapping check and remote commit are not a distributed transaction. Concurrent external revocation between them is not prevented by this foundation. There is no claim of atomic cross-system policy revocation.
- The initial operation permits new activation only. Separate typed contract changes and billing/finance adjustments retain historical authority. Bounded operator approval and reviewed restored-state rehydration are now implemented; neither permits arbitrary financial editing.
- All processes share one local PostgreSQL server with distinct databases and roles. There is no claim of isolated infrastructure failure domains, cross-region recovery or production RPO/RTO.
- Credentials are randomly generated in ignored private workspaces, sent only to local subprocesses and loopback HTTP. The test harness has installer privileges. Local browser authentication and native scoped row-security are tested. TLS, public authentication, SSO, production isolation and secret lifecycle are not proven.
- Fault controls require a separate injector token and run only on local contract services. No live payment, customer communication, native vendor mutation or cloud action occurs.

## Fencing, review and restored-state recovery

A monotonic fence and lease-owner token guard status writes. Lease expiry cannot steal a live PostgreSQL effect lock. The worker records due times and stops at exhaustion; an exact scoped proposal and different approver can grant three additional dispatches. Current authorization is checked before each new POST; receipt readback remains the first step even after revocation.

Before new dispatch or financial extraction, the controller compares its intents against complete independent receiver inventories for scopes pinned outside the restored database. A missing intent, mismatched content, unavailable receiver or missing verified effect blocks continuation. The executed rollback test copies an earlier local PostgreSQL database while receiver databases retain later effects. It is not a managed-cloud PITR claim.

Index agreement alone is insufficient. Current recovery acceptance rereads the exact receipt and every referenced business record, then compares retained verified control proof. The [additional corruption and process-boundary acceptance](evidence/adversarial/README.md) preserves the original index-only failure and its correction. Reviewed history import also checks existing scope, event, destination, payload, hash and verified proof before any import; a conflicting control hash cannot leave a partial import behind on rejection.

A trusted local maintenance utility provides a narrow history-repair path:

```text
python -m integrity.recovery --config .local/healthy/worker.json --archive .local/verified-journal.json
# Preserve and separately review the printed digest before any rollback.
python -m integrity.recovery --config .local/restored/worker.json --archive .local/verified-journal.json --reviewed-digest <reviewed SHA-256>
```

Stop ordinary workers first and quiesce the independent receiver writers. The utility takes an exclusive PostgreSQL maintenance lock; ingests and dispatches hold its shared counterpart. It checks the reviewed archive digest, independently pinned scopes, original source events, stable effect keys, exact receipt content and every business record. Both destinations must cover each source event, and the reviewed archive must exactly cover the receiver inventory. Conflicting control history is refused. Only missing already-verified intents/events are imported, with a new audit observation; no remote action POST is sent. Repeating the repair imports zero additional intents.

This archive is not a full backup or cryptographic authorization service. Preserve the original private archive and digest outside the restored database. It does not reconstruct lost approvals/attempt journals or allow an incomplete archive to invent history. If coverage or exact readback fails, keep workers stopped, obtain a verified later backup or investigate source coverage; do not reset the gate or replay blindly. After repair, rerun the independent gate and capture a complete fresh financial snapshot before publication.

## Publication recovery

A filesystem ownership lock excludes parallel publishers. A stale lock is never stolen automatically. The local `integrity.publication.recover(root, expected_owner)` helper requires exact lock bytes and an independently verified dead OS process. Review the archived inputs and use the normal publisher afterward; prior output remains intact after the demonstrated kill. Process identity reuse conservatively blocks repair. This is a single-host publication design, not distributed consensus or power-loss durability proof.
