"""Trusted local maintenance: rehydrate verified history, never replay remote actions.

The caller supplies a separately reviewed journal digest. This is not a replacement
for database backups, signatures, or a production recovery authorization service.
"""

import argparse
import json
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .contracts import Commercial, ContractChange, Effect, digest, key
from .control import Controller, Refused
from .db import connect


def archive(controller):
    if not controller.verify_recovery()["safe_to_resume"]:
        raise Refused("unsafe_state_cannot_be_recovery_baseline")
    with connect(controller.dsn) as c:
        effects = c.execute("SELECT * FROM outbox ORDER BY effect_id").fetchall()
        if not effects or any(x["status"] != "verified" for x in effects):
            raise Refused("complete_verified_journal_required")
        events = c.execute(
            "SELECT * FROM inbox WHERE status='completed' ORDER BY tenant,event_id"
        ).fetchall()
    result = {
        "schema_version": "verified-intent-journal.v1",
        "approved_scopes": controller.config["approved_scopes"],
        "events": events,
        "effects": effects,
    }
    # Freeze database UUID/timestamp values into explicit JSON types.
    return json.loads(json.dumps(result, default=str))


def restore_history(controller, journal, expected_digest):
    if digest(journal) != expected_digest:
        raise Refused("reviewed_recovery_digest_mismatch")
    if (
        journal["schema_version"] != "verified-intent-journal.v1"
        or journal["approved_scopes"] != controller.config["approved_scopes"]
    ):
        raise Refused("recovery_scope_manifest_mismatch")
    with psycopg.connect(controller.dsn, autocommit=True, row_factory=dict_row) as session:
        if not session.execute("SELECT pg_try_advisory_lock(714290311) AS held").fetchone()["held"]:
            raise Refused("recovery_requires_quiescent_control_workers")
        try:
            return _restore(controller, session, journal)
        finally:
            session.execute("SELECT pg_advisory_unlock(714290311)")


def _restore(controller, session, journal):
    scopes = {tuple(s) for s in controller.config["approved_scopes"]}
    events = {(e["tenant"], e["event_id"]): e for e in journal["events"]}
    if len(events) != len(journal["events"]):
        raise Refused("duplicate_recovery_source_identity")
    effects = {}
    for item in journal["effects"]:
        body = Effect.model_validate(item["payload"]).model_dump(mode="json")
        event = events.get((item["tenant"], item["event_id"]))
        if not event or (item["tenant"], item["entity"]) not in scopes:
            raise Refused("recovery_source_or_scope_missing")
        model = (
            ContractChange
            if event["body"]["schema_version"] == "contract-change.v1"
            else Commercial
        )
        source = model.model_validate(event["body"]).model_dump(mode="json")
        if (
            any(
                event[field] != source[field]
                for field in ("tenant", "entity", "event_id", "opportunity_id", "revision")
            )
            or event["status"] != "completed"
        ):
            raise Refused("recovery_source_envelope_mismatch")
        expected_id = key(
            source["tenant"],
            source["entity"],
            source["contract_id"],
            source["revision"],
            item["destination"],
        )
        if (
            item["effect_id"] != expected_id
            or body["effect_id"] != expected_id
            or item["hash"] != digest(body)
            or event["hash"] != digest(source)
            or body["source_event"] != event["event_id"]
            or body["source_revision"] != source["revision"]
            or body["tenant"] != event["tenant"]
            or body["entity"] != event["entity"]
            or item["entity"] != source["entity"]
            or item["status"] != "verified"
            or item["effect_id"] in effects
        ):
            raise Refused("recovery_exact_identity_or_content_conflict")
        with controller.client("salesforce") as client:
            observed = client.get(
                "/events/" + event["event_id"],
                params={"tenant": event["tenant"], "entity": event["entity"]},
            )
            observed.raise_for_status()
            if observed.json() != source:
                raise Refused("recovery_historical_source_mismatch")
        with controller.client(item["destination"]) as client:
            proof = controller.read_proof(client, item)
        if proof is None or proof != item["proof"]:
            raise Refused("recovery_independent_receipt_required")
        effects[item["effect_id"]] = item
    if not effects:
        raise Refused("empty_recovery_journal")
    # A baseline may only establish complete historical source events, not new work.
    for identity, event in events.items():
        destinations = {
            e["destination"] for e in effects.values() if (e["tenant"], e["event_id"]) == identity
        }
        if destinations != {"stripe", "netsuite"}:
            raise Refused("incomplete_recovery_event_receipts")
    for destination in ("stripe", "netsuite"):
        with controller.client(destination) as client:
            remote = []
            for tenant, entity in scopes:
                r = client.get("/effect-index", params={"tenant": tenant, "entity": entity})
                r.raise_for_status()
                remote.extend(r.json())
        expected = {k: e["hash"] for k, e in effects.items() if e["destination"] == destination}
        if (
            len(remote) != len(expected)
            or {x["effect_id"]: x["payload_hash"] for x in remote} != expected
        ):
            raise Refused("receiver_history_not_exactly_covered_by_reviewed_archive")
    with session.transaction():
        # Independent receivers must remain quiescent during this local maintenance.
        session.execute("LOCK TABLE inbox,outbox,source_heads IN ACCESS EXCLUSIVE MODE")
        current = session.execute("SELECT * FROM outbox").fetchall()
        if any(
            str(e["effect_id"]) not in effects
            or e["payload"] != effects[str(e["effect_id"])]["payload"]
            for e in current
        ):
            raise Refused("existing_control_history_conflicts_with_archive")
        for event in sorted(
            events.values(), key=lambda e: (e["tenant"], e["opportunity_id"], e["revision"])
        ):
            old = session.execute(
                "SELECT * FROM inbox WHERE tenant=%s AND event_id=%s",
                (event["tenant"], event["event_id"]),
            ).fetchone()
            if old and (old["body"] != event["body"] or old["hash"] != event["hash"]):
                raise Refused("existing_source_history_conflicts_with_archive")
            session.execute(
                "INSERT INTO inbox(tenant,event_id,entity,opportunity_id,revision,hash,body,status,observed_at) VALUES(%s,%s,%s,%s,%s,%s,%s,'completed',%s) ON CONFLICT(tenant,event_id) DO NOTHING",
                (
                    event["tenant"],
                    event["event_id"],
                    event["entity"],
                    event["opportunity_id"],
                    event["revision"],
                    event["hash"],
                    Jsonb(event["body"]),
                    event["observed_at"],
                ),
            )
            session.execute(
                "INSERT INTO source_heads VALUES(%s,%s,%s,%s) ON CONFLICT(tenant,opportunity_id) DO UPDATE SET revision=EXCLUDED.revision,event_id=EXCLUDED.event_id WHERE source_heads.revision<EXCLUDED.revision",
                (event["tenant"], event["opportunity_id"], event["revision"], event["event_id"]),
            )
        restored = 0
        for item in effects.values():
            restored += session.execute(
                "INSERT INTO outbox(effect_id,tenant,entity,event_id,destination,payload,hash,status,proof,attempts,detail,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,'verified',%s,%s,'recovered_verified_history',%s) ON CONFLICT(effect_id) DO NOTHING",
                (
                    item["effect_id"],
                    item["tenant"],
                    item["entity"],
                    item["event_id"],
                    item["destination"],
                    Jsonb(item["payload"]),
                    item["hash"],
                    Jsonb(item["proof"]),
                    item["attempts"],
                    item["created_at"],
                ),
            ).rowcount
            Controller.audit(
                session,
                item,
                "historical_receipt_rehydrated",
                item["effect_id"],
                {"archive_hash": digest(journal), "remote_posts": 0},
            )
    gate = controller.verify_recovery()
    if not gate["safe_to_resume"]:
        raise Refused("recovery_postcheck_requires_further_review")
    return {
        "restored_intents": restored,
        "remote_posts": 0,
        "safe_to_resume": True,
        "archive_hash": digest(journal),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--reviewed-digest")
    args = parser.parse_args()
    controller = Controller(json.loads(Path(args.config).read_text(encoding="utf-8")))
    path = Path(args.archive)
    if args.reviewed_digest:
        print(
            json.dumps(
                restore_history(
                    controller, json.loads(path.read_text(encoding="utf-8")), args.reviewed_digest
                )
            )
        )
    else:
        result = archive(controller)
        with path.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(result, indent=2) + "\n")
        print(json.dumps({"archive_hash": digest(result), "remote_posts": 0}))


if __name__ == "__main__":
    main()
