import copy
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb

from integrity.control import Controller, Refused
from integrity.contracts import digest
from integrity.db import connect
from integrity.recovery import archive, restore_history


def query(stack, database, text, args=()):
    with connect(stack.admins[database]) as c:
        return c.execute(text, args).fetchall()


def completed(stack):
    controller = Controller(stack.config)
    assert controller.ingest(stack.events[0])["status"] == "planned"
    controller.drain()
    return controller


def remote_writes(stack):
    return {
        d: query(
            stack,
            d,
            "SELECT id,operation,status FROM requests WHERE operation IN ('apply','duplicate') ORDER BY id",
        )
        for d in ["stripe", "netsuite"]
    }


@pytest.mark.parametrize("damage", ["record", "receipt", "control_proof"])
def test_recovery_rechecks_contents_not_only_index_hashes(stack, damage, observe):
    controller = completed(stack)
    before = query(stack, "stripe", "SELECT effect_id,hash FROM effects")
    target_db = "control" if damage == "control_proof" else "stripe"
    with connect(stack.admins[target_db]) as c:
        if damage == "record":
            c.execute("ALTER TABLE records DISABLE TRIGGER immutable_record")
            c.execute(
                "UPDATE records SET body=jsonb_set(body,'{amount_cents}',to_jsonb((body->>'amount_cents')::bigint+1)) WHERE kind='invoice'"
            )
            c.execute("ALTER TABLE records ENABLE TRIGGER immutable_record")
        elif damage == "receipt":
            c.execute("ALTER TABLE effects DISABLE TRIGGER immutable_effect")
            c.execute(
                "UPDATE effects SET receipt=jsonb_set(receipt,'{receipt_id}','\"wrong-receipt\"')"
            )
            c.execute("ALTER TABLE effects ENABLE TRIGGER immutable_effect")
        else:
            c.execute(
                "UPDATE outbox SET proof=jsonb_set(proof,'{receipt_id}','\"wrong-local-proof\"') WHERE destination='stripe'"
            )
    result = controller.verify_recovery()
    after = query(stack, "stripe", "SELECT effect_id,hash FROM effects")
    observe(
        controlled_fixture_corruption=damage,
        receiver_index_unchanged=before == after,
        gate=result,
        receiver_effects=len(after),
        note="Installer-only corruption; application-role mutation is separately denied",
    )
    assert before == after
    assert result["safe_to_resume"] is False
    assert any("proof" in i["reason"] or "record" in i["reason"] for i in result["issues"])


def test_bad_existing_hash_refuses_restore_before_any_history_import(stack, observe):
    # A real earlier control database, while independent receivers retain later effects.
    name = stack.tag + "_audit_restore"
    original = conninfo_to_dict(stack.admins["control"])["dbname"]
    with psycopg.connect(stack.admin, autocommit=True) as admin:
        admin.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                sql.Identifier(name), sql.Identifier(original)
            )
        )
    try:
        controller = completed(stack)
        journal = archive(controller)
        restored = Controller(
            {**stack.config, "control_dsn": make_conninfo(stack.config["control_dsn"], dbname=name)}
        )
        # Import only a conflicting pre-existing intent through the trusted fault installer.
        with connect(make_conninfo(stack.admins["control"], dbname=name)) as c:
            event = journal["events"][0]
            c.execute(
                "INSERT INTO inbox(tenant,event_id,entity,opportunity_id,revision,hash,body,status) VALUES(%s,%s,%s,%s,%s,%s,%s,'completed')",
                (
                    event["tenant"],
                    event["event_id"],
                    event["entity"],
                    event["opportunity_id"],
                    event["revision"],
                    event["hash"],
                    Jsonb(event["body"]),
                ),
            )
            item = journal["effects"][0]
            c.execute(
                "INSERT INTO outbox(effect_id,tenant,entity,event_id,destination,payload,hash,status) VALUES(%s,%s,%s,%s,%s,%s,%s,'pending')",
                (
                    item["effect_id"],
                    item["tenant"],
                    item["entity"],
                    item["event_id"],
                    item["destination"],
                    Jsonb(item["payload"]),
                    "0" * 64,
                ),
            )

        def counts():
            with connect(restored.dsn) as c:
                return {
                    t: c.execute("SELECT count(*) n FROM " + t).fetchone()["n"]
                    for t in ["inbox", "outbox", "source_heads", "audit"]
                }

        before = counts()
        writes_before = remote_writes(stack)
        with pytest.raises(Refused):
            restore_history(restored, journal, digest(journal))
        after = counts()
        writes_after = remote_writes(stack)
        observe(
            before=before,
            after=after,
            receiver_write_journal_before=writes_before,
            receiver_write_journal_after=writes_after,
            gate=restored.verify_recovery(),
        )
        assert writes_before == writes_after
        assert before == after, "rejected_recovery_must_not_partially_import_history"
    finally:
        with psycopg.connect(stack.admin, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH(FORCE)").format(sql.Identifier(name)))


@pytest.mark.parametrize(
    "damage", ["digest", "scope", "missing_effect", "duplicate_effect", "proof", "source"]
)
def test_reviewed_journal_corruption_is_rejected_without_financial_writes(stack, damage, observe):
    controller = completed(stack)
    healthy = archive(controller)
    journal = copy.deepcopy(healthy)
    if damage == "scope":
        journal["approved_scopes"] = [["unapproved", "US"]]
    elif damage == "missing_effect":
        journal["effects"].pop()
    elif damage == "duplicate_effect":
        journal["effects"].append(copy.deepcopy(journal["effects"][0]))
    elif damage == "proof":
        journal["effects"][0]["proof"]["receipt_id"] = str(uuid4())
    elif damage == "source":
        journal["events"][0]["body"]["quantity"] += 1
    before = query(stack, "control", "SELECT count(*) n FROM audit")[0]["n"]
    writes_before = remote_writes(stack)
    with pytest.raises((Refused, ValueError)):
        restore_history(controller, journal, "0" * 64 if damage == "digest" else digest(journal))
    after = query(stack, "control", "SELECT count(*) n FROM audit")[0]["n"]
    writes_after = remote_writes(stack)
    observe(
        rejection=damage,
        audit_rows_before=before,
        audit_rows_after=after,
        receiver_effects={
            d: len(query(stack, d, "SELECT effect_id FROM effects")) for d in ["stripe", "netsuite"]
        },
        receiver_write_journal_before=writes_before,
        receiver_write_journal_after=writes_after,
    )
    assert before == after and writes_before == writes_after


def test_native_control_restore_rehydrates_history_without_new_authority(stack, observe):
    name = stack.tag + "_clean_restore"
    original = conninfo_to_dict(stack.admins["control"])["dbname"]
    with psycopg.connect(stack.admin, autocommit=True) as admin:
        admin.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                sql.Identifier(name), sql.Identifier(original)
            )
        )
    try:
        controller = completed(stack)
        journal = archive(controller)
        restored = Controller(
            {**stack.config, "control_dsn": make_conninfo(stack.config["control_dsn"], dbname=name)}
        )
        writes_before = remote_writes(stack)
        blocked = restored.verify_recovery()
        assert not blocked["safe_to_resume"]
        assert {r["reason"] for r in blocked["issues"]} == {"remote_ahead_missing_intent"}
        with psycopg.connect(restored.dsn, autocommit=True) as fence:
            fence.execute("SELECT pg_advisory_lock(714290311)")
            try:
                with pytest.raises(Refused, match="recovery_maintenance"):
                    restored.ingest(stack.events[1])
            finally:
                fence.execute("SELECT pg_advisory_unlock(714290311)")
        # Historical event readback remains valid while current ownership changes.
        with connect(stack.admins["salesforce"]) as c:
            c.execute(
                "UPDATE opportunities SET body=jsonb_set(body,'{owner_id}','\"changed-owner\"')"
            )
        result = restore_history(restored, journal, digest(journal))
        repeat = restore_history(restored, journal, digest(journal))
        assert result["restored_intents"] == 2 and repeat["restored_intents"] == 0
        assert restored.verify_recovery()["safe_to_resume"]
        future = restored.ingest(stack.events[1])
        assert future["status"] == "blocked" and "source_not_current" in future["detail"]
        writes_after = remote_writes(stack)
        assert writes_before == writes_after
        with connect(restored.dsn) as c:
            assert (
                c.execute("SELECT count(*) n FROM outbox WHERE status<>'verified'").fetchone()["n"]
                == 0
            )
            actions = [r["action"] for r in c.execute("SELECT action FROM audit ORDER BY id")]
        observe(
            actual_local_PostgreSQL_template_restore=True,
            blocked_before_recovery=blocked,
            history_recovery=result,
            idempotent_history_recovery=repeat,
            current_authority_result=future,
            receiver_write_journal_before=writes_before,
            receiver_write_journal_after=writes_after,
            audit_actions=actions,
            source_and_receiver_databases_not_restored=True,
        )
    finally:
        with psycopg.connect(stack.admin, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH(FORCE)").format(sql.Identifier(name)))
