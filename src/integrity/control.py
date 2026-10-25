"""Durable intents first; read back historical effects before authorizing NEW dispatch."""

import json
import time
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .contracts import Commercial, Effect, digest, key
from .db import connect, lock, coordinated


class Refused(ValueError):
    pass


class Controller:
    def __init__(self, config):
        self.config = config
        self.dsn = config["control_dsn"]

    def client(self, destination, timeout=2):
        lane = self.config["services"][destination]
        return httpx.Client(
            base_url=lane["url"],
            headers={"Authorization": "Bearer " + lane["token"]},
            timeout=timeout,
            trust_env=False,
            follow_redirects=False,
        )

    def source(self, event):
        if event.get("schema_version") == "contract-change.v1":
            from .lifecycle import verify_source

            return verify_source(self, event)
        scope = {"tenant": event["tenant"], "entity": event["entity"]}
        with self.client("salesforce") as client:
            event_response = client.get("/events/" + event["event_id"], params=scope)
            current = client.get("/opportunities/" + event["opportunity_id"], params=scope)
            contract = client.get("/contracts/" + event["contract_id"], params=scope)
            account = client.get("/accounts/" + event["account_id"], params=scope)
        if any(r.status_code != 200 for r in (event_response, current, contract, account)):
            raise Refused("source_record_or_scope_missing")
        if account.json().get("Id") != event["account_id"]:
            raise Refused("current_account_identity_mismatch")
        if event_response.json() != event:
            raise Refused("source_event_exact_content_mismatch")
        if current.json() != event:
            raise Refused("source_not_current_replan_required")
        commitment = contract.json()
        expected = {
            k: event[k]
            for k in (
                "account_id",
                "contract_id",
                "tenant",
                "entity",
                "currency",
                "unit_monthly_cents",
                "quantity",
                "effective_start",
                "effective_end",
            )
        }
        if commitment != expected or event["stage"] != "Closed Won":
            raise Refused("executed_commercial_commitment_required")

    def mapping(self, c, event):
        rows = c.execute(
            "SELECT * FROM mappings WHERE tenant=%s AND entity=%s AND source='salesforce' "
            "AND source_id=%s AND status='approved'",
            (event["tenant"], event["entity"], event["account_id"]),
        ).fetchall()
        if len(rows) != 1:
            raise Refused("ambiguous_or_missing_canonical_mapping")
        canonical_account = rows[0]["canonical_account"]
        mapped = {"salesforce": event["account_id"]}
        for source in ("stripe", "netsuite"):
            found = c.execute(
                "SELECT * FROM mappings WHERE tenant=%s AND entity=%s AND canonical_account=%s "
                "AND source=%s AND status='approved'",
                (event["tenant"], event["entity"], canonical_account, source),
            ).fetchall()
            if len(found) != 1:
                raise Refused("ambiguous_or_missing_" + source + "_mapping")
            mapped[source] = found[0]["source_id"]
        return canonical_account, mapped

    @staticmethod
    def audit(c, event, action, reference, detail):
        c.execute(
            "INSERT INTO audit(tenant,entity,action,reference,detail) VALUES(%s,%s,%s,%s,%s)",
            (event["tenant"], event["entity"], action, reference, Jsonb(detail)),
        )

    @staticmethod
    def exception(c, event, reason, effect_id=None):
        c.execute(
            "INSERT INTO exceptions(tenant,entity,event_id,effect_id,reason) VALUES(%s,%s,%s,%s,%s)",
            (event["tenant"], event["entity"], event["event_id"], effect_id, reason),
        )

    def intent(self, c, event, canonical_account, customer, destination, facts):
        changed = event.get("schema_version") == "contract-change.v1"
        effect = Effect(
            effect_id=key(
                event["tenant"],
                event["entity"],
                event["contract_id"],
                event["revision"],
                destination,
            ),
            tenant=event["tenant"],
            entity=event["entity"],
            canonical_account=canonical_account,
            destination_customer=customer,
            source_event=event["event_id"],
            source_revision=event["revision"],
            facts=facts,
            operation=(
                "apply_contract_change" if destination == "stripe" else "post_contract_change"
            )
            if changed
            else ("activate_and_invoice" if destination == "stripe" else "post_invoice_and_cash"),
        )
        body = effect.model_dump(mode="json")
        old = c.execute(
            "SELECT payload FROM outbox WHERE effect_id=%s", (effect.effect_id,)
        ).fetchone()
        if old and old["payload"] != body:
            raise Refused("durable_intent_conflict")
        c.execute(
            "INSERT INTO outbox(effect_id,tenant,entity,event_id,destination,payload,hash,status) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,'pending') ON CONFLICT(effect_id) DO NOTHING",
            (
                effect.effect_id,
                event["tenant"],
                event["entity"],
                event["event_id"],
                destination,
                Jsonb(body),
                digest(body),
            ),
        )
        self.audit(
            c,
            event,
            "intent_committed",
            effect.effect_id,
            {"destination": destination, "hash": digest(body)},
        )
        return effect.effect_id

    @coordinated
    def ingest(self, document):
        if document.get("schema_version") == "contract-change.v1":
            from .lifecycle import ingest

            return ingest(self, document)
        event = Commercial.model_validate(document).model_dump(mode="json")
        with connect(self.dsn) as c:
            lock(c, event["tenant"], event["opportunity_id"])
            previous = c.execute(
                "SELECT * FROM inbox WHERE tenant=%s AND event_id=%s",
                (event["tenant"], event["event_id"]),
            ).fetchone()
            if previous:
                if previous["body"] != event or previous["hash"] != digest(event):
                    raise Refused("event_identity_content_conflict")
                return {"status": previous["status"], "duplicate": True}
            collision = c.execute(
                "SELECT 1 FROM inbox WHERE tenant=%s AND opportunity_id=%s AND revision=%s",
                (event["tenant"], event["opportunity_id"], event["revision"]),
            ).fetchone()
            if collision:
                raise Refused("revision_identity_conflict")
            c.execute(
                "INSERT INTO inbox(tenant,event_id,entity,opportunity_id,revision,hash,body,status) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,'registered')",
                (
                    event["tenant"],
                    event["event_id"],
                    event["entity"],
                    event["opportunity_id"],
                    event["revision"],
                    digest(event),
                    Jsonb(event),
                ),
            )
            try:
                self.source(event)
                head = c.execute(
                    "SELECT * FROM source_heads WHERE tenant=%s AND opportunity_id=%s",
                    (event["tenant"], event["opportunity_id"]),
                ).fetchone()
                if head and event["revision"] <= head["revision"]:
                    raise Refused("stale_source_revision")
                if head:
                    # No re-invoicing an amended commitment via the initial activation operation.
                    raise Refused("amendment_requires_explicit_correction_workflow")
                canonical_account, mapped = self.mapping(c, event)
                self.intent(c, event, canonical_account, mapped["stripe"], "stripe", event)
                c.execute(
                    "INSERT INTO source_heads VALUES(%s,%s,%s,%s)",
                    (
                        event["tenant"],
                        event["opportunity_id"],
                        event["revision"],
                        event["event_id"],
                    ),
                )
                status, detail = "planned", ""
            except (Refused, httpx.TransportError) as exc:
                status, detail = (
                    "blocked",
                    str(exc) if isinstance(exc, Refused) else "source_unavailable",
                )
                self.exception(c, event, detail)
            c.execute(
                "UPDATE inbox SET status=%s,detail=%s WHERE tenant=%s AND event_id=%s",
                (status, detail, event["tenant"], event["event_id"]),
            )
            self.audit(c, event, status, event["event_id"], {"reason": detail})
        return {"status": status, "detail": detail, "duplicate": False}

    def read_proof(self, client, row):
        scope = {"tenant": row["tenant"], "entity": row["entity"]}
        response = client.get("/receipts/" + str(row["effect_id"]), params=scope)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        proof = response.json()
        if (
            proof.get("effect_id") != str(row["effect_id"])
            or proof.get("payload_hash") != row["hash"]
            or proof.get("payload") != row["payload"]
            or proof.get("service") != row["destination"]
            or proof.get("receipt_id") != key(row["destination"], str(row["effect_id"]), "receipt")
            or proof.get("records_hash") != digest(proof.get("records"))
        ):
            raise Refused("receipt_exact_content_mismatch")
        records = proof["records"]
        if not records or len({(x["kind"], x["id"]) for x in records}) != len(records):
            raise Refused("receipt_record_identity_conflict")
        for item in records:
            if (
                item["tenant"],
                item["entity"],
                item["canonical_account"],
                item["source_event"],
                item["currency"],
            ) != (
                row["tenant"],
                row["entity"],
                row["payload"]["canonical_account"],
                row["event_id"],
                "USD",
            ):
                raise Refused("receipt_business_scope_mismatch")
            actual = client.get(f"/records/{item['kind']}/{item['id']}", params=scope)
            actual.raise_for_status()
            if actual.json() != item:
                raise Refused("independent_record_readback_mismatch")
        return proof

    @coordinated
    def step(self, effect_id, timeout=2, crash_marker=None):
        # Session lock survives attempt commits; a real process death releases it.
        # No lease takeover while an old worker is alive: simpler safety, less concurrency.
        with psycopg.connect(self.dsn, row_factory=dict_row, autocommit=True) as session:
            got = session.execute(
                "SELECT pg_try_advisory_lock(hashtextextended(%s,1)) AS held", (str(effect_id),)
            ).fetchone()["held"]
            if not got:
                return "busy"
            owner = str(uuid4())
            claim = session.execute(
                "UPDATE outbox SET fence=fence+1,lease_owner=%s,lease_until=clock_timestamp()+interval '30 seconds' WHERE effect_id=%s RETURNING fence",
                (owner, effect_id),
            ).fetchone()
            try:
                return self._step(session, effect_id, timeout, crash_marker)
            finally:
                if claim:
                    session.execute(
                        "UPDATE outbox SET lease_owner=NULL,lease_until=NULL WHERE effect_id=%s AND fence=%s AND lease_owner=%s",
                        (effect_id, claim["fence"], owner),
                    )
                session.execute(
                    "SELECT pg_advisory_unlock(hashtextextended(%s,1))", (str(effect_id),)
                )

    def _step(self, session, effect_id, timeout, crash_marker):
        row = session.execute("SELECT * FROM outbox WHERE effect_id=%s", (effect_id,)).fetchone()
        if not row:
            raise Refused("unknown_effect")
        if row["status"] == "verified":
            return "verified"
        with self.client(row["destination"], timeout) as client:
            try:
                # Receipt reconciliation establishes history; does not authorize new effects.
                proof = self.read_proof(client, row)
                if proof is None:
                    if row["status"] in ("blocked", "exhausted"):
                        return row["status"]
                    if row["attempts"] >= row["max_attempts"]:
                        return self.mark(row, "exhausted", "retry_budget_exhausted")
                    with connect(self.dsn) as c:
                        from .operations import check_grant

                        check_grant(c, row)
                        event = c.execute(
                            "SELECT body FROM inbox WHERE tenant=%s AND event_id=%s",
                            (row["tenant"], row["event_id"]),
                        ).fetchone()["body"]
                        self.source(event)
                        canonical_account, mapped = self.mapping(c, event)
                        if (
                            canonical_account != row["payload"]["canonical_account"]
                            or mapped[row["destination"]] != row["payload"]["destination_customer"]
                        ):
                            raise Refused("mapping_changed_replan_required")
                    recovery = self.verify_recovery()
                    if not recovery["safe_to_resume"]:
                        raise Refused("recovery_gate:" + recovery["issues"][0]["reason"])
                    with session.transaction():
                        session.execute(
                            "UPDATE outbox SET attempts=attempts+1 WHERE effect_id=%s", (effect_id,)
                        )
                        session.execute(
                            "INSERT INTO attempts(effect_id,ordinal,stage,result) VALUES(%s,%s,'dispatch','started')",
                            (effect_id, row["attempts"] + 1),
                        )
                    response = client.post(
                        "/effects", json=row["payload"], headers={"Idempotency-Key": str(effect_id)}
                    )
                    if response.status_code in (409, 422):
                        return self.mark(row, "blocked", "receiver_contract_rejected")
                    response.raise_for_status()
                    if crash_marker:
                        Path(crash_marker).write_text(
                            "receiver_response_observed_before_control_confirmation"
                        )
                        while True:
                            time.sleep(
                                0.1
                            )  # Actual subprocess is terminated by the acceptance test.
                    proof = self.read_proof(client, row)
                    if proof is None:
                        raise Refused("acknowledgment_without_independent_receipt")
                with connect(self.dsn) as c:
                    event = c.execute(
                        "SELECT body FROM inbox WHERE tenant=%s AND event_id=%s",
                        (row["tenant"], row["event_id"]),
                    ).fetchone()["body"]
                    changed = c.execute(
                        "UPDATE outbox SET status='verified',proof=%s,detail='independent_receipt_and_records' WHERE effect_id=%s AND fence=%s AND lease_owner=%s",
                        (Jsonb(proof), effect_id, row["fence"], row["lease_owner"]),
                    ).rowcount
                    if changed != 1:
                        raise Refused("stale_worker_fence")
                    self.audit(
                        c,
                        event,
                        "effect_verified",
                        str(effect_id),
                        {"receipt_id": proof["receipt_id"]},
                    )
                    if row["destination"] == "stripe":
                        # Current mappings authorize new ERP intent separately from old billing history.
                        try:
                            self.source(event)
                            canonical_account, mapped = self.mapping(c, event)
                            if canonical_account != row["payload"]["canonical_account"]:
                                raise Refused("canonical_identity_changed")
                            self.intent(
                                c,
                                event,
                                canonical_account,
                                mapped["netsuite"],
                                "netsuite",
                                {"billing_receipt": proof},
                            )
                        except (Refused, httpx.TransportError) as exc:
                            self.exception(
                                c,
                                event,
                                "new_finance_intent_blocked:"
                                + (str(exc) if isinstance(exc, Refused) else "source_unavailable"),
                                effect_id,
                            )
                    else:
                        c.execute(
                            "UPDATE inbox SET status='completed' WHERE tenant=%s AND event_id=%s",
                            (row["tenant"], row["event_id"]),
                        )
                return "verified"
            except Refused as exc:
                return self.mark(row, "blocked", str(exc))
            except (httpx.TransportError, httpx.HTTPStatusError):
                return self.mark(
                    row, "uncertain", "remote_outcome_unconfirmed_readback_before_retry"
                )

    def mark(self, row, status, reason):
        with connect(self.dsn) as c:
            changed = c.execute(
                "UPDATE outbox SET status=%s,detail=%s,next_at=clock_timestamp()+interval '1 second' * "
                "least(power(2,attempts),30) WHERE effect_id=%s AND fence=%s AND lease_owner IS NOT DISTINCT FROM %s::uuid",
                (status, reason, row["effect_id"], row["fence"], row["lease_owner"]),
            ).rowcount
            if changed != 1:
                raise Refused("stale_worker_fence")
            event = {"tenant": row["tenant"], "entity": row["entity"], "event_id": row["event_id"]}
            self.audit(c, event, status, str(row["effect_id"]), {"reason": reason})
            if status in ("blocked", "exhausted"):
                self.exception(c, event, reason, row["effect_id"])
        return status

    def verify_recovery(self):
        with connect(self.dsn) as c:
            approved = self.config.get("approved_scopes")
            if not approved:
                raise Refused("independent_scope_manifest_required")
            scopes = [{"tenant": s[0], "entity": s[1]} for s in approved]
            intents = c.execute("SELECT * FROM outbox").fetchall()
        issues = []
        for destination in ("stripe", "netsuite"):
            observed = {}
            try:
                with self.client(destination) as client:
                    for scope in scopes:
                        response = client.get("/effect-index", params=scope)
                        response.raise_for_status()
                        for receipt in response.json():
                            if receipt["effect_id"] in observed:
                                raise Refused("duplicate_receiver_identity")
                            observed[receipt["effect_id"]] = receipt["payload_hash"]
            except (httpx.HTTPError, Refused):
                issues.append(
                    {"destination": destination, "reason": "receiver_inventory_unavailable"}
                )
                continue
            expected = {str(r["effect_id"]): r for r in intents if r["destination"] == destination}
            for effect_id, content in observed.items():
                if effect_id not in expected:
                    reason = "remote_ahead_missing_intent"
                elif expected[effect_id]["hash"] != content:
                    reason = "receiver_intent_content_conflict"
                else:
                    continue
                issues.append(
                    {"destination": destination, "effect_id": effect_id, "reason": reason}
                )
            for effect_id, intent in expected.items():
                if intent["status"] == "verified" and effect_id not in observed:
                    issues.append(
                        {
                            "destination": destination,
                            "effect_id": effect_id,
                            "reason": "verified_remote_effect_missing",
                        }
                    )
        return {"safe_to_resume": not issues, "issues": issues}

    def drain(self, due_only=True):
        for _ in range(100):
            with connect(self.dsn) as c:
                rows = c.execute(
                    "SELECT effect_id FROM outbox WHERE status IN ('pending','uncertain') "
                    + ("AND next_at<=clock_timestamp() " if due_only else "")
                    + "ORDER BY created_at"
                ).fetchall()
            if not rows:
                return
            for row in rows:
                self.step(row["effect_id"])
        raise Refused("bounded_worker_iteration_limit")


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("config")
    parser.add_argument("effect")
    parser.add_argument("--crash-marker")
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    Controller(config).step(args.effect, crash_marker=args.crash_marker)


if __name__ == "__main__":
    main()
