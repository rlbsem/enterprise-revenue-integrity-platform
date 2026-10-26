# Portable operator demonstration

## Launch

Use Docker Engine 28+ with Compose V2 and Linux containers. From the repository:

```text
docker compose up --build
```

Wait for the `ready` event, then open **http://127.0.0.1:8582**. Startup creates a private PostgreSQL 17 cluster, four separate databases, restricted SQL roles and three independently running HTTP contract services. It executes the existing financial lifecycle, dbt/DuckDB builds, independent oracle/full-build comparisons and local PostgreSQL analytical parity before exposing the console.

No native vendor credentials, manual PostgreSQL installation, model endpoint or paid cloud resource is required. Image construction needs internet access for the pinned public dependencies. The normal image contains the application, not browser-test tooling or Node dependencies.

In another terminal, retrieve only the selected local user's generated credential:

```text
docker compose exec demo python -m integrity.portable credentials --user auditor
docker compose exec demo python -m integrity.portable credentials --user operator
docker compose exec demo python -m integrity.portable credentials --user approver
```

These commands explicitly display private, per-startup passwords. Do not paste them into issues, screenshots or public logs. Credentials and runtime configuration stay in the private volume, with mode-600 files under a mode-700 state directory. `reader_eu` and `outsider` are available for scope inspection.

If port 8582 is occupied, choose another port **before starting**. On PowerShell, set `$env:INTEGRITY_DEMO_PORT='8584'`; on Linux/macOS, use `INTEGRITY_DEMO_PORT=8584 docker compose up --build`. Open the corresponding `http://127.0.0.1:8584`. The same value configures the published port and exact Origin/Host boundary. Use the literal loopback address; arbitrary Host aliases are rejected. A conflicting port fails rather than attaching to another application.

## Five-minute workflow

1. **Auditor:** Inspect the initial synthetic USD $39,000 billing / $37,500 ERP comparison, separate $16,500 cash and $120,000 ARR. The open $1,500 posting difference is a source finding, not evidence that a quality gate should be bypassed.
2. **Source lineage:** Open the independent receipt and business-record readback. A timeout or exhausted delivery does not establish absence of a committed effect.
3. **Operator:** Under **Exceptions & delivery**, propose an exact retry of `sf-recovery-v1` with a reason. The delivery has three actual rejected attempts. A proposal cannot execute itself, and the operator has no approval privilege.
4. **Approver:** Sign in separately and reject the proposal. No dispatch occurs. Ask the operator to create a new proposal, then review and approve that exact payload. The worker revalidates identity, current source authority, approval and attempt budget before dispatch.
5. **Auditor:** Refresh after the durable financial recheck. Independently verified billing and finance effects produce $45,600 billing / $44,100 ERP, cash still $16,500 and ARR $146,400. The $1,500 issue stays open. Inspect the decision ledger and source records; none of these measures establishes recognized revenue or a commercial outcome.

[Detailed operator walkthrough](operator-console.md) · [Financial definitions](financial-contract.md) · [Security](security.md).

## Stop and start fresh

Use Ctrl+C, or:

```text
docker compose down --volumes --remove-orphans
```

Only this Compose project's containers, network and private volume are removed. Source files, historical repository evidence and other projects are untouched. Images/build cache can remain and contain no generated credentials. Allow the configured shutdown grace period.

A later start always initializes a fresh synthetic workspace and new credentials. It does not resume previous operator edits. Old private run directories remain in the named volume until it is removed; never publish the volume or its logs. Forced interruption is not a durability or disaster-recovery guarantee.

For detached startup, use `docker compose up --build -d --wait`. The container is bounded to two CPUs, 2 GiB and 512 processes; allow additional Docker/build storage and host overhead. These are demonstration limits, not capacity benchmarks.

## Security boundary

Only the existing authenticated console listens on the container interface. Compose publishes **only** its port on host loopback. PostgreSQL and all CRM/billing/finance services retain loopback listeners inside the container. The worker runs within the original console process; there is no new unauthenticated dispatch route. Native PostgreSQL RLS, separate operator/approver SQL privileges, sessions, CSRF, source authentication and append-only decision/receipt contracts remain in force.

The container runs as UID/GID 10001 with a read-only root filesystem, no Linux capabilities and no privilege escalation. Private state uses a named volume; temporary files and PostgreSQL sockets use bounded temporary mounts. The image context is allowlisted and excludes local state, environment files, credentials and keys. Supplied external database/service configuration is refused by the portable entrypoint.

This is one trusted local host/container with four separate databases and SQL identities, not four production security domains. The host and Docker daemon remain trusted. The dedicated bridge disables inter-container communication and defaults publication to loopback, but is **not an outbound firewall**. No production TLS/SSO, native vendor acceptance or public hosting is established. Engine 28+ avoids the older localhost-publication behavior described in [Docker's port guidance](https://docs.docker.com/engine/network/port-publishing/).

## Reproduce packaged acceptance

The host test runner needs the locked Python and Node test dependencies plus Playwright Chromium. Ordinary reviewers launching the demo need only Docker.

```text
python scripts/verify_portable.py --workspace .local/portable-review --output .local/portable-review-evidence
python scripts/check_portable_evidence.py .local/portable-review-evidence
```

It tests the **normal image**, runs the existing browser workflow through the host-published console, independently queries restricted SQL roles and receiver records, stops the application, restarts the same disposable state volume, rejects stale passwords/sessions and verifies the fresh fixture. Container settings and exact image source bytes are checked; post-teardown inventory independently requires no owned containers, networks or volumes.

`--native` uses installed PostgreSQL/Python/Node and the same wrapper on loopback. It does not establish Docker execution. All test credentials and raw runtime diagnostics remain private; only sanitized synthetic evidence and genuine screenshots are published.

[Recorded results, independent verification and genuine screenshots](evidence/portable/README.md).
