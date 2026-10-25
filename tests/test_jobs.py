from integrity import jobs
from integrity.control import Controller
from integrity.db import connect
from integrity.operations import propose, decide, execute
from test_security import exhausted


def test_verified_recovery_drives_durable_financial_recheck(
    stack, controller, tmp_path, monkeypatch
):
    effect = exhausted(stack, controller)
    proposal = propose(stack.operators["operator"], effect["effect_id"], "Scoped recovery")
    decide(stack.operators["approver"], proposal, "approved", "Exact source reviewed")
    assert execute(controller, proposal) == "verified"
    controller.drain()
    jobs.schedule_approved(controller)
    jobs.schedule_approved(controller)
    jobs.run_one(controller, tmp_path)
    with connect(controller.dsn) as c:
        rows = c.execute("SELECT * FROM reconciliation_jobs").fetchall()
        assert len(rows) == 1 and rows[0]["state"] == "verified"
        assert c.execute("SELECT count(*) AS n FROM financial_runs").fetchone()["n"] == 1
        c.execute(
            "INSERT INTO reconciliation_jobs(tenant,entity,reason) VALUES('northstar','US','Injected data-gate failure')"
        )
    before = {p: p.read_bytes() for p in tmp_path.rglob("CURRENT.json")}
    assert before

    def failed_gate(*args):
        raise ValueError("injected_data_gate_failure")

    monkeypatch.setattr(jobs, "build", failed_gate)
    jobs.run_one(Controller(stack.config), tmp_path)
    assert {p: p.read_bytes() for p in tmp_path.rglob("CURRENT.json")} == before
    with connect(controller.dsn) as c:
        assert (
            c.execute("SELECT detail FROM reconciliation_jobs WHERE state='failed'").fetchone()[
                "detail"
            ]
            == "injected_data_gate_failure"
        )
        assert c.execute("SELECT count(*) AS n FROM financial_runs").fetchone()["n"] == 1
