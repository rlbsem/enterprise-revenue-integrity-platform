"""Authenticated local operator API. Browser principals never receive source/worker credentials."""

import hashlib
import json
import secrets
import threading
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID
from typing import Literal

import httpx
import psycopg
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import Field

from .contracts import Closed, canonical
from .control import Controller, Refused
from .db import connect
from . import operations, security

WEB = Path(__file__).resolve().parents[2] / "web"


class Login(Closed):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=128)


class Proposal(Closed):
    effect_id: str = Field(min_length=36, max_length=36)
    reason: str = Field(min_length=1, max_length=500, pattern=r"\S")


class Decision(Closed):
    result: Literal["approved", "rejected"]
    reason: str = Field(min_length=1, max_length=500, pattern=r"\S")


class Worker:
    def __init__(self, controller):
        self.controller = controller
        self.stop = threading.Event()
        self.last_tick = None
        self.error = None
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def loop(self):
        while not self.stop.is_set():
            try:
                with connect(self.controller.dsn) as c:
                    proposals = c.execute(
                        "SELECT p.id FROM proposals p JOIN outbox o ON o.effect_id=p.effect_id WHERE p.state='approved' AND (p.detail<>'grant_committed' OR o.next_at<=clock_timestamp() OR o.status='verified')"
                    ).fetchall()
                for row in proposals:
                    operations.execute(self.controller, row["id"])
                self.controller.drain()
                from .jobs import schedule_approved, run_one

                schedule_approved(self.controller)
                run_one(self.controller, self.controller.config["console_workspace"])
                self.error = None
            except Exception as exc:
                self.error = type(exc).__name__
            self.last_tick = datetime.now(UTC).isoformat()
            self.stop.wait(1)


def create_app(config, origin="http://127.0.0.1:8582", start_worker=True):
    controller = Controller(config)
    worker = Worker(controller)

    @asynccontextmanager
    async def lifespan(app):
        if start_worker:
            worker.thread.start()
        yield
        worker.stop.set()
        if start_worker:
            worker.thread.join(timeout=15)

    app = FastAPI(
        title="Revenue Integrity",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.worker = worker

    def secure(response):
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "no-referrer",
                "Permissions-Policy": "camera=(),microphone=(),geolocation=()",
                "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
            }
        )
        return response

    @app.middleware("http")
    async def boundaries(request: Request, call_next):
        if (
            request.method not in ("GET", "HEAD", "OPTIONS")
            and request.headers.get("origin") != origin
        ):
            return secure(JSONResponse(status_code=403, content={"detail": "same_origin_required"}))
        if "tenant" in request.query_params:
            return secure(
                JSONResponse(
                    status_code=403, content={"detail": "scope_is_bound_to_authenticated_identity"}
                )
            )
        if request.method in ("POST", "PUT", "PATCH") and len(await request.body()) > 32768:
            return secure(JSONResponse(status_code=413, content={"detail": "request_too_large"}))
        return secure(await call_next(request))

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return JSONResponse(status_code=422, content={"detail": "invalid_request_contract"})

    @app.exception_handler(Refused)
    async def refused(request, exc):
        reason = str(exc)
        status = (
            403 if "role_required" in reason else 404 if "not_found_in_scope" in reason else 409
        )
        return JSONResponse(status_code=status, content={"detail": reason})

    @app.exception_handler(psycopg.Error)
    async def database_refused(request, exc):
        return JSONResponse(
            status_code=403 if isinstance(exc, psycopg.errors.InsufficientPrivilege) else 409,
            content={"detail": "database_scope_or_integrity_guard_rejected"},
        )

    def current(request: Request):
        actor = security.session(config, request.cookies.get("integrity_session"))
        if not actor:
            raise HTTPException(401, "authenticated_local_operator_required")
        if request.method not in ("GET", "HEAD") and not secrets.compare_digest(
            hashlib.sha256(request.headers.get("X-CSRF-Token", "").encode()).hexdigest(),
            actor["csrf_hash"],
        ):
            raise HTTPException(403, "csrf_validation_failed")
        return actor

    def scopes(actor, entity=None):
        allowed = [tuple(x) for x in actor["scopes"]]
        if entity:
            allowed = [s for s in allowed if s[1] == entity]
            if not allowed:
                raise HTTPException(403, "legal_entity_not_authorized")
        return allowed

    def read(actor, query, args=()):
        with connect(actor["dsn"]) as c:
            return c.execute(query, args).fetchall()

    def public(value):
        names = {v["db_role"]: v["username"] for v in config["operators"].values()}
        if isinstance(value, dict):
            return {
                k: str(v) if k.endswith("_cents") and type(v) is int else public(v)
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [public(v) for v in value]
        return names.get(value, value) if isinstance(value, str) else value

    @app.post("/api/login")
    def login(body: Login, response: Response):
        issued = security.authenticate(config, body.username, body.password)
        if not issued:
            raise HTTPException(401, "invalid_or_temporarily_locked_login")
        token, csrf = issued
        response.set_cookie(
            "integrity_session", token, httponly=True, samesite="strict", max_age=1800, path="/"
        )
        response.set_cookie(
            "integrity_csrf", csrf, httponly=False, samesite="strict", max_age=1800, path="/"
        )
        return {"authenticated": True, "csrf": csrf}

    @app.post("/api/logout")
    def logout(response: Response, actor=Depends(current)):
        with connect(controller.dsn) as c:
            c.execute(
                "UPDATE sessions SET revoked=true WHERE session_hash=%s", (actor["session_hash"],)
            )
        response.delete_cookie("integrity_session")
        response.delete_cookie("integrity_csrf")
        return {"authenticated": False}

    @app.get("/api/me")
    def me(request: Request, actor=Depends(current)):
        csrf = request.cookies.get("integrity_csrf", "")
        if hashlib.sha256(csrf.encode()).hexdigest() != actor["csrf_hash"]:
            raise HTTPException(401, "csrf_cookie_required")
        return {
            "username": actor["username"],
            "role": actor["role"],
            "scopes": actor["scopes"],
            "csrf": csrf,
            "native_vendor_execution": False,
        }

    @app.get("/api/overview")
    def overview(entity: str | None = None, actor=Depends(current)):
        allowed = scopes(actor, entity)
        runs = read(
            actor,
            "SELECT DISTINCT ON(tenant,entity) id,tenant,entity,source_hash,period,report,created_at FROM financial_runs WHERE period->>'start'='2026-01-01' ORDER BY tenant,entity,created_at DESC",
        )
        runs = [r for r in runs if (r["tenant"], r["entity"]) in allowed]
        cases = [
            r
            for r in read(actor, "SELECT * FROM financial_cases ORDER BY state,category")
            if (r["tenant"], r["entity"]) in allowed
        ]
        effects = [
            r
            for r in read(
                actor,
                "SELECT effect_id,tenant,entity,event_id,destination,status,attempts,max_attempts,detail,hash,fence,lease_owner,lease_until,proof FROM outbox ORDER BY created_at",
            )
            if (r["tenant"], r["entity"]) in allowed
        ]
        for effect in effects:
            effect["record_ids"] = [r["id"] for r in (effect.pop("proof") or {}).get("records", [])]
        return public(
            {
                "currency": "USD",
                "money_encoding": "integer_cent_strings",
                "period": {"start": "2026-01-01", "end_exclusive": "2026-04-01"},
                "runs": runs,
                "cases": cases,
                "effects": effects,
                "worker": {
                    "last_tick": worker.last_tick,
                    "last_error": worker.error,
                    "active": worker.thread.is_alive(),
                },
                "jobs": [
                    r
                    for r in read(
                        actor, "SELECT * FROM reconciliation_jobs ORDER BY created_at DESC LIMIT 20"
                    )
                    if (r["tenant"], r["entity"]) in allowed
                ],
                "source_lane": "local_synthetic_contract_services",
            }
        )

    @app.get("/api/proposals")
    def proposals(entity: str | None = None, actor=Depends(current)):
        allowed = scopes(actor, entity)
        return public(
            [
                r
                for r in read(
                    actor,
                    "SELECT p.*,d.result,d.reason AS decision_reason,d.actor AS decided_by FROM proposals p LEFT JOIN decisions d ON d.proposal_id=p.id ORDER BY p.created_at DESC",
                )
                if (r["tenant"], r["entity"]) in allowed
            ]
        )

    @app.post("/api/proposals")
    def proposal(body: Proposal, actor=Depends(current)):
        try:
            effect = UUID(body.effect_id)
        except ValueError as exc:
            raise HTTPException(422, "invalid_effect_identity") from exc
        identifier = operations.propose(actor, effect, body.reason)
        return {"proposal_id": identifier, "state": "pending", "execution": False}

    @app.post("/api/proposals/{proposal_id}/decision")
    def decision(proposal_id: UUID, body: Decision, actor=Depends(current)):
        operations.decide(actor, proposal_id, body.result, body.reason)
        return {
            "proposal_id": str(proposal_id),
            "decision": body.result,
            "execution": "worker_revalidates_before_dispatch",
        }

    @app.post("/api/reconcile")
    def reconcile(actor=Depends(current)):
        if actor["role"] != "operator":
            raise HTTPException(403, "operator_role_required")
        with connect(actor["dsn"]) as c:
            identifiers = []
            for tenant, entity in scopes(actor):
                recent = c.execute(
                    "SELECT id FROM reconciliation_jobs WHERE tenant=%s AND entity=%s AND (state IN ('queued','running') OR created_at>clock_timestamp()-interval '30 seconds')",
                    (tenant, entity),
                ).fetchone()
                if recent:
                    raise HTTPException(409, "recheck_running_or_cooldown")
                identifiers.append(
                    str(
                        c.execute(
                            "INSERT INTO reconciliation_jobs(tenant,entity,created_by,reason) VALUES(%s,%s,session_user,'Operator requested source recheck') RETURNING id",
                            (tenant, entity),
                        ).fetchone()["id"]
                    )
                )
        return {"job_ids": identifiers, "state": "queued", "financial_mutations": False}

    @app.get("/api/lineage/{effect_id}")
    def lineage(effect_id: UUID, actor=Depends(current)):
        effect = read(actor, "SELECT * FROM outbox WHERE effect_id=%s", (effect_id,))
        if not effect:
            raise HTTPException(404, "effect_not_found_in_scope")
        row = effect[0]
        events = read(
            actor,
            "SELECT * FROM inbox WHERE tenant=%s AND event_id=%s",
            (row["tenant"], row["event_id"]),
        )
        attempts = read(
            actor, "SELECT * FROM attempts WHERE effect_id=%s ORDER BY id", (effect_id,)
        )
        with controller.client(row["destination"]) as client:
            proof = controller.read_proof(client, row)
        return public(
            {
                "intent": row,
                "canonical_payload_json": canonical(row["payload"]),
                "canonical_receipt_json": canonical(proof) if proof else None,
                "source_event": events[0] if events else None,
                "attempts": attempts,
                "fresh_independent_receipt": proof,
                "readback_observed_at": datetime.now(UTC).isoformat(),
                "native_vendor_execution": False,
            }
        )

    @app.get("/api/audit")
    def audit(entity: str | None = None, actor=Depends(current)):
        allowed = scopes(actor, entity)
        return public(
            [
                r
                for r in read(actor, "SELECT * FROM audit ORDER BY id DESC LIMIT 200")
                if (r["tenant"], r["entity"]) in allowed
            ]
        )

    @app.get("/api/health")
    def health(actor=Depends(current)):
        results = {}
        for name, lane in config["services"].items():
            try:
                with httpx.Client(trust_env=False, timeout=2) as client:
                    response = client.get(lane["url"] + "/health")
                results[name] = (
                    "ready"
                    if response.status_code == 200 and response.json().get("status") == "ready"
                    else "unavailable"
                )
            except httpx.HTTPError:
                results[name] = "unavailable"
        return {
            "services": results,
            "worker_active": worker.thread.is_alive(),
            "worker_last_tick": worker.last_tick,
            "local_only": True,
        }

    @app.get("/api/export")
    def export(entity: str | None = None, actor=Depends(current)):
        data = overview(entity, actor)
        return Response(
            json.dumps(data, default=str, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=revenue-integrity-report.json"},
        )

    @app.get("/")
    def page():
        return FileResponse(WEB / "index.html")

    @app.get("/app.css")
    def styles():
        return FileResponse(WEB / "app.css", media_type="text/css")

    @app.get("/app.js")
    def script():
        return FileResponse(WEB / "app.js", media_type="text/javascript")

    return app
