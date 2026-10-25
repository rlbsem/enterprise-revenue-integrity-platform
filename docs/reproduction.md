# Reproduction

Prerequisites: Git (including repository history for source-provenance checks), Python 3.12 and PostgreSQL 17 binaries (`initdb`, `pg_ctl`, `postgres`). Install them using your normal OS package manager/official distribution. Clone the repository normally, without a shallow-history limit. Do not initialize a server as root on Unix. Run from this repository root in an unprivileged user account.

```text
python -m venv .venv
# Activate first: .venv\Scripts\Activate.ps1 (Windows) or source .venv/bin/activate (Unix).
python -m pip install pip==26.2.2
python -m pip install -r requirements.lock
python -m pip install --no-deps -e .
```

Windows PowerShell, substitute your actual PostgreSQL 17 installation directory:

```powershell
$env:PG_BIN='C:\Program Files\PostgreSQL\17\bin'
python -m integrity.demo --workspace .local/demo-01 --output .local/demo-01.json
python scripts/verify.py --workspace .local/verify-01 --output .local/evidence-01
```

Linux:

```sh
export PG_BIN=/usr/lib/postgresql/17/bin
python -m integrity.demo --workspace .local/demo-01 --output .local/demo-01.json
python scripts/verify.py --workspace .local/verify-01 --output .local/evidence-01
```

The harness starts a **new owned** SCRAM-authenticated PostgreSQL cluster on a free loopback port. It creates four random databases and four restricted roles, applies migrations as installer, seeds synthetic data and starts three real HTTP subprocesses. It stops those processes and its PostgreSQL cluster at exit. Its private data directories remain for inspection; it never deletes an existing workspace. Use new `-02` paths for another run.

Alternatively, set private `INTEGRITY_ADMIN_DSN` to an **isolated disposable** PostgreSQL 17 test server. The installer requires CREATE DATABASE/ROLE privileges. This path creates random test databases and removes those exact databases and roles at exit. GitHub CI uses an ephemeral PostgreSQL 17.11 service this way. This is destructive only to databases the harness itself created; do not supply a production server.

The JSON demo contains actual persisted source/controller/receiver records and request journals. Verification adds sanitized test outcomes, actual process-termination observations, source-content hashes and an evidence manifest. Raw logs, admin DSNs, service tokens, databases and private runtime files stay under `.local` and are excluded from Git. Do not upload them. Generated records contain synthetic identifiers; review new evidence before publication.

The full verifier also executes the [financial slice](financial-contract.md): source credits/corrections and proration, baseline/corrected/independent DuckDB/dbt builds, full-row comparison and safe JSON publication. To run only that separate lane:

```text
python -m integrity.finance_demo --workspace .local/finance-01 --output .local/finance-01.json
```

The code uses loopback HTTP without TLS and random service bearer tokens for finite local testing. It is not configured for public deployment. There is no native Salesforce/Stripe/NetSuite/Snowflake authentication path in this release. Docker is not required; a container-based local run is not yet an executed acceptance claim.

To check preserved evidence without starting services:

```text
python scripts/check_evidence.py
```
