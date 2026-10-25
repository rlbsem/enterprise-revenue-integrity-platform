import copy
import json

import httpx
import pytest

from integrity.contracts import digest
from integrity.control import Controller, Refused
from integrity.finance import build, load, oracle, publish
from integrity.finance_demo import adjustment, execute_finance


@pytest.fixture(scope="session")
def financial_run(runtime, tmp_path_factory):
    runtime.reset()
    return execute_finance(runtime, tmp_path_factory.mktemp("financial"))


def mutate_export(snapshot, source, change):
    snapshot = copy.deepcopy(snapshot)
    export = next(
        x for x in snapshot["snapshots"] if x["service"] == source and x["entity"] == "US"
    )
    change(export["contents"])
    export["content_hash"] = digest(export["contents"])
    return snapshot


def test_real_financial_http_dbt_and_independent_full(financial_run):
    assert financial_run["all_four_models_exact_vs_independent_full"] is True
    for name in ("baseline_dbt", "corrected_dbt", "independent_dbt"):
        assert financial_run[name]["models"] == 4
        assert financial_run[name]["tests"] == 18
        assert all(r["status"] in ("pass", "success") for r in financial_run[name]["results"])
    rows = financial_run["final_release"]["rows"]
    assert sum(r["expected_invoice_cents"] for r in rows) == 4150000
    assert sum(r["billing_net_cents"] for r in rows) == 4150000
    assert sum(r["erp_net_cents"] for r in rows) == 4000000
    assert sum(r["allocated_cash_cents"] for r in rows) == 1650000
    assert sum(r["posting_variance_cents"] for r in rows) == -150000
    assert financial_run["final_release"]["state"] == "review_required"
    missing = financial_run["final_release"]["unbilled_commitments"]
    assert len(missing) == 1 and missing[0]["reason"] == "ambiguous_or_missing_mapping"


def test_backdated_revision_is_not_double_counted(financial_run):
    snapshot = financial_run["source_snapshot"]
    credits = [
        r
        for x in snapshot["snapshots"]
        if x["service"] == "stripe"
        for r in x["contents"]["source_events"]
        if r["kind"] == "credit"
    ]
    assert [r["revision"] for r in sorted(credits, key=lambda x: x["revision"])] == [1, 2]
    assert sum(r["credit_cents"] for r in oracle(snapshot)) == 200000
    assert sum(r["duplicate"] for r in financial_run["source_event_receipts"]) == 2


def test_arr_stock_stays_separate_from_invoice_and_cash_flows(financial_run):
    measures = financial_run["final_release"]["period_measures"]
    assert sum(r["period_end_arr_cents"] for r in measures) == 16800000
    assert sum(r["billing_net_cents"] for r in measures) == 4150000
    assert sum(r["allocated_cash_cents"] for r in measures) == 1650000
    bridge = financial_run["final_release"]["variance_bridge"]
    assert len(bridge["backdated_correction_lineage"]) == 2
    assert any(
        e["category"] == "unposted_adjustment_requires_review" and e["amount_cents"] == -150000
        for e in bridge["entries"]
    )


def test_equal_count_one_cent_corruption_is_not_published(financial_run, tmp_path):
    original = financial_run["source_snapshot"]

    def change(contents):
        posting = next(r for r in contents["records"] if r["kind"] == "posting")
        posting["amount_cents"] += 1

    changed = mutate_export(original, "netsuite", change)
    assert len(oracle(changed)) == len(oracle(original))
    assert oracle(changed) != oracle(original)
    with pytest.raises(Refused, match="independent_financial_parity_failed"):
        publish(tmp_path, changed, financial_run["final_release"]["rows"], changed)
    assert not (tmp_path / "CURRENT.json").exists()


def test_header_line_disagreement_blocks(financial_run):
    def change(contents):
        next(r for r in contents["records"] if r["kind"] == "invoice")["amount_cents"] += 1

    changed = mutate_export(financial_run["source_snapshot"], "stripe", change)
    with pytest.raises(Refused, match="invoice_header_line_variance"):
        oracle(changed)


def test_actual_failed_dbt_gate_leaves_previous_release(financial_run, tmp_path):
    snapshot = financial_run["source_snapshot"]
    release = tmp_path / "releases"
    publish(release, snapshot, financial_run["final_release"]["rows"], snapshot)
    previous = (release / "CURRENT.json").read_bytes()

    def change(contents):
        next(r for r in contents["records"] if r["kind"] == "invoice")["amount_cents"] += 1

    invalid = mutate_export(snapshot, "stripe", change)
    database = tmp_path / "invalid.duckdb"
    load(database, invalid)
    with pytest.raises(Refused, match="dbt_financial_gate_failed"):
        build(database, tmp_path / "failed-dbt")
    assert (release / "CURRENT.json").read_bytes() == previous


def test_orphan_financial_record_is_not_silently_dropped(financial_run):
    def change(contents):
        next(r for r in contents["records"] if r["kind"] == "payment_allocation")["invoice_id"] = (
            "unknown-invoice"
        )

    with pytest.raises(Refused, match="orphan_or_cross_scope_financial_reference"):
        oracle(mutate_export(financial_run["source_snapshot"], "stripe", change))


def test_duplicate_source_identity_blocks(financial_run):
    def change(contents):
        contents["records"].append(copy.deepcopy(contents["records"][0]))

    with pytest.raises(Refused, match="duplicate_source_identity"):
        oracle(mutate_export(financial_run["source_snapshot"], "stripe", change))


def test_missing_source_and_correction_history_block(financial_run):
    missing = copy.deepcopy(financial_run["source_snapshot"])
    missing["snapshots"].pop()
    with pytest.raises(Refused, match="source_coverage_gap"):
        oracle(missing)

    def change(contents):
        contents["source_events"] = [
            e for e in contents["source_events"] if e["event_id"] != "billing-credit-v1"
        ]

    with pytest.raises(Refused, match="source_correction_coverage_gap"):
        oracle(mutate_export(financial_run["source_snapshot"], "stripe", change))


def test_cross_entity_and_currency_blocks(financial_run):
    def change(contents):
        next(r for r in contents["records"] if r["kind"] == "invoice")["entity"] = "EU"

    with pytest.raises(Refused, match="record_scope_or_currency_mismatch"):
        oracle(mutate_export(financial_run["source_snapshot"], "stripe", change))

    def change_currency(contents):
        contents["records"][0]["currency"] = "CAD"

    with pytest.raises(Refused, match="record_scope_or_currency_mismatch"):
        oracle(mutate_export(financial_run["source_snapshot"], "stripe", change_currency))


def test_payment_lineage_total_is_independently_checked(financial_run):
    def change(contents):
        next(r for r in contents["records"] if r["kind"] == "payment")["amount_cents"] = 1

    with pytest.raises(Refused, match="payment_allocation_lineage_or_total_mismatch"):
        oracle(mutate_export(financial_run["source_snapshot"], "stripe", change))


def test_publication_failure_and_changed_sources_preserve_pointer(financial_run, tmp_path):
    snapshot = financial_run["source_snapshot"]
    rows = financial_run["final_release"]["rows"]
    publish(tmp_path, snapshot, rows, snapshot)
    before = (tmp_path / "CURRENT.json").read_bytes()
    changed = copy.deepcopy(snapshot)
    changed["mappings"][0]["revision"] += 1
    with pytest.raises(Refused, match="source_changed_during_reconciliation"):
        publish(tmp_path, snapshot, rows, changed)
    with pytest.raises(Refused, match="controlled_publication_interruption"):
        publish(tmp_path, snapshot, rows, snapshot, fail_before_pointer=True)
    assert (tmp_path / "CURRENT.json").read_bytes() == before
    pointer = json.loads(before)
    assert digest(json.loads((tmp_path / pointer["file"]).read_bytes())) == pointer["release"]


def test_actual_source_domain_permissions_and_invalid_corrections(stack):
    controller = Controller(stack.config)
    assert controller.ingest(stack.events[0])["status"] == "planned"
    controller.drain()
    with controller.client("stripe") as client:
        export = client.get("/export", params={"tenant": "northstar", "entity": "US"}).json()
        invoice = next(r for r in export["contents"]["records"] if r["kind"] == "invoice")
        body = adjustment(invoice["id"], "credit", "credit-v1", "credit", 100000)
        assert client.post("/test/source-events", json=body).status_code == 401
    service = stack.config["services"]["stripe"]
    headers = {"Authorization": "Bearer " + stack.injector}
    with httpx.Client(base_url=service["url"], headers=headers, trust_env=False) as client:
        assert client.post("/test/source-events", json=body).status_code == 200
        assert (
            client.post(
                "/test/source-events",
                json={**body, "event_id": "bad-domain", "kind": "credit_posting"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/test/source-events", json={**body, "event_id": "bad-scope", "entity": "EU"}
            ).status_code
            == 409
        )
        assert (
            client.post(
                "/test/source-events", json={**body, "event_id": "bad-revision"}
            ).status_code
            == 409
        )
        assert (
            client.post(
                "/test/source-events",
                json={
                    **body,
                    "event_id": "extra-credit",
                    "logical_id": "extra-credit",
                    "amount_cents": 3000000,
                },
            ).status_code
            == 422
        )
        # A legitimate DECREASE on a newer explicitly linked revision must not be blocked as regression.
        correction = {
            **body,
            "event_id": "credit-v2",
            "revision": 2,
            "corrects_event": "credit-v1",
            "amount_cents": 50000,
        }
        assert client.post("/test/source-events", json=correction).status_code == 200
