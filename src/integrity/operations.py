"""Exact-content, separate-person recovery review. No arbitrary financial mutation operation."""

from uuid import uuid4

from psycopg.types.json import Jsonb

from .contracts import digest
from .control import Refused
from .db import connect, lock


def propose(actor, effect_id, reason):
    if actor["role"] != "operator":
        raise Refused("operator_role_required")
    with connect(actor["dsn"]) as c:
        effect = c.execute("SELECT * FROM outbox WHERE effect_id=%s", (effect_id,)).fetchone()
        if not effect:
            raise Refused("effect_not_found_in_scope")
        payload = {
            "effect_id": str(effect["effect_id"]),
            "payload_hash": effect["hash"],
            "expected_attempts": effect["attempts"],
            "source_revision": effect["payload"]["source_revision"],
            "grant_max_attempts": 6,
        }
        identifier = str(uuid4())
        c.execute(
            "INSERT INTO proposals(id,tenant,entity,kind,effect_id,payload,payload_hash,reason) VALUES(%s,%s,%s,'retry_effect',%s,%s,%s,%s)",
            (
                identifier,
                effect["tenant"],
                effect["entity"],
                effect_id,
                Jsonb(payload),
                digest(payload),
                reason,
            ),
        )
    return identifier


def decide(actor, proposal_id, result, reason):
    if actor["role"] != "approver" or result not in ("approved", "rejected"):
        raise Refused("approver_role_required")
    with connect(actor["dsn"]) as c:
        proposal = c.execute("SELECT * FROM proposals WHERE id=%s", (proposal_id,)).fetchone()
        if not proposal:
            raise Refused("proposal_not_found_in_scope")
        c.execute(
            "INSERT INTO decisions(proposal_id,tenant,entity,result,reason) VALUES(%s,%s,%s,%s,%s)",
            (proposal_id, proposal["tenant"], proposal["entity"], result, reason),
        )


def check_grant(c, effect):
    if effect["max_attempts"] <= 3:
        return
    proposal = c.execute(
        "SELECT p.*,p.expires_at>clock_timestamp() AS fresh,d.actor AS approver,d.result FROM proposals p JOIN decisions d ON d.proposal_id=p.id WHERE p.id=%s",
        (effect["authorization_proposal"],),
    ).fetchone()
    if (
        not proposal
        or proposal["state"] not in ("approved", "executed")
        or proposal["result"] != "approved"
        or not proposal["fresh"]
    ):
        raise Refused("recovery_approval_missing_stale_or_revoked")
    if (
        proposal["payload_hash"] != digest(proposal["payload"])
        or proposal["payload"]["payload_hash"] != effect["hash"]
        or proposal["payload"]["source_revision"] != effect["payload"]["source_revision"]
    ):
        raise Refused("recovery_approval_content_changed")
    for role_name, identity in (
        ("operator", proposal["proposed_by"]),
        ("approver", proposal["approver"]),
    ):
        allowed = c.execute(
            "SELECT 1 FROM operator_users u JOIN operator_scope s USING(db_role) WHERE u.db_role=%s AND u.active AND s.role=%s AND s.tenant=%s AND s.entity=%s",
            (identity, role_name, effect["tenant"], effect["entity"]),
        ).fetchone()
        if not allowed:
            raise Refused("recovery_operator_permission_revoked")


def execute(controller, proposal_id):
    with connect(controller.dsn) as c:
        lock(c, "operator-execution", str(proposal_id))
        proposal = c.execute(
            "SELECT * FROM proposals WHERE id=%s FOR UPDATE", (proposal_id,)
        ).fetchone()
        if not proposal or proposal["state"] != "approved":
            return "not_approved"
        effect = c.execute(
            "SELECT * FROM outbox WHERE effect_id=%s FOR UPDATE", (proposal["effect_id"],)
        ).fetchone()
        try:
            if (
                proposal["payload_hash"] != digest(proposal["payload"])
                or proposal["payload"]["payload_hash"] != effect["hash"]
            ):
                raise Refused("approval_exact_content_mismatch")
            if proposal["detail"] != "grant_committed":
                if (
                    effect["status"] != "exhausted"
                    or effect["attempts"] != 3
                    or effect["max_attempts"] != 3
                ):
                    raise Refused("effect_changed_since_proposal")
                effect = {**effect, "max_attempts": 6, "authorization_proposal": proposal_id}
                check_grant(c, effect)
                event = c.execute(
                    "SELECT body FROM inbox WHERE tenant=%s AND event_id=%s",
                    (effect["tenant"], effect["event_id"]),
                ).fetchone()["body"]
                controller.source(event)
                account, mapping = controller.mapping(c, event)
                if (
                    account != effect["payload"]["canonical_account"]
                    or mapping[effect["destination"]] != effect["payload"]["destination_customer"]
                ):
                    raise Refused("mapping_changed_after_review")
                c.execute(
                    "UPDATE outbox SET max_attempts=6,authorization_proposal=%s WHERE effect_id=%s",
                    (proposal_id, effect["effect_id"]),
                )
                c.execute(
                    "UPDATE outbox SET status='pending',next_at=clock_timestamp(),detail='approved_bounded_recovery' WHERE effect_id=%s",
                    (effect["effect_id"],),
                )
                c.execute(
                    "UPDATE proposals SET detail='grant_committed' WHERE id=%s", (proposal_id,)
                )
            else:
                check_grant(c, effect)
        except Refused as exc:
            c.execute(
                "UPDATE proposals SET state='invalidated',detail=%s WHERE id=%s",
                (str(exc), proposal_id),
            )
            controller.audit(
                c,
                proposal,
                "operator_execution_invalidated",
                str(proposal_id),
                {"reason": str(exc)},
            )
            return "invalidated"
    status = controller.step(effect["effect_id"])
    with connect(controller.dsn) as c:
        controller.audit(
            c, proposal, "operator_execution_result", str(proposal_id), {"result": status}
        )
        if status == "verified":
            c.execute(
                "UPDATE proposals SET state='executed',detail='independent_receiver_verified' WHERE id=%s",
                (proposal_id,),
            )
        elif status in ("blocked", "exhausted"):
            c.execute(
                "UPDATE proposals SET state='invalidated',detail=%s WHERE id=%s",
                (status, proposal_id),
            )
    return status
