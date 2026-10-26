"""Validate immutable packaged proof, direct RLS observations, independent receipts and finance."""

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from integrity.contracts import digest  # noqa: E402
from integrity.finance import oracle, oracle_period  # noqa: E402


def check(folder, failed=False):
    manifest = json.loads((folder / "artifact-integrity.json").read_bytes())
    assert set(manifest) == {
        p.relative_to(folder).as_posix()
        for p in folder.rglob("*")
        if p.is_file() and p.name != "artifact-integrity.json"
    }
    for name, h in manifest.items():
        assert not Path(name).is_absolute() and ".." not in Path(name).parts
        raw = (folder / name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == h, name
        if name.endswith(".json"):
            assert not re.search(
                r'"(?:control_dsn|password|access_token|token|csrf)"\s*:', raw.decode()
            )
            assert not re.search(
                r"postgresql?://|-----BEGIN .*PRIVATE KEY|(?:AKIA|ASIA)[A-Z0-9]{16}", raw.decode()
            )
    report = json.loads((folder / "portable.json").read_bytes())
    assert not report["native_vendor_execution"] and not report["cloud_resources"]
    assert re.fullmatch(r"[a-f0-9]{40}", report["source_commit"])
    for name, h in report["source_hashes"].items():
        blob = subprocess.check_output(
            ["git", "show", report["source_commit"] + ":" + name], cwd=ROOT
        )
        assert hashlib.sha256(blob).hexdigest() == h, name
    if failed:
        assert report["status"] == "failed"
        if report["lane"] == "actual_docker_compose":
            assert report["cleanup"]["verified_absent"]
            assert not any(report["cleanup"]["independent_label_inventory"].values())
        else:
            assert report["cleanup"]["owned_process_exited"]
        return {"status": "preserved_failure", "source_commit": report["source_commit"]}
    assert report["status"] == "passed"
    assert report["normal_shutdown"]["clean_exit"] and report["restarted_shutdown"]["clean_exit"]
    assert all(report["fresh_restart"].values())
    if report["lane"] == "actual_docker_compose":
        assert report["cleanup"]["verified_absent"] and not any(
            report["cleanup"]["independent_label_inventory"].values()
        )
        assert all(
            report["isolation"][k]
            for k in [
                "nonroot",
                "read_only_root",
                "no_new_privileges",
                "capabilities_dropped",
                "loopback_console_only",
                "no_database_or_business_service_published",
            ]
        )
        assert not report["isolation"]["runtime_egress_firewall"]
    else:
        assert report["cleanup"]["owned_process_exited"]
    initial = json.loads((folder / "initial.json").read_bytes())
    after = json.loads((folder / "recovered.json").read_bytes())
    restarted = json.loads((folder / "restarted.json").read_bytes())
    for snapshot in [initial, after, restarted]:
        assert len(snapshot["rls"]) == 4 and all(r["relrowsecurity"] for r in snapshot["rls"])
        assert all(r["restricted_role"] for r in snapshot["roles"].values())
        roles = snapshot["roles"]
        assert roles["operator"]["write_permissions"] == {"propose": True, "decide": False}
        assert roles["approver"]["write_permissions"] == {"propose": False, "decide": True}
        assert roles["auditor"]["write_permissions"] == {"propose": False, "decide": False}
        assert all(not roles["outsider"][k] for k in ["outbox", "runs", "cases", "decisions"])
        assert all(r["entity"] == "EU" for r in roles["reader_eu"]["outbox"])

    def totals(snapshot):
        latest = {
            (r["tenant"], r["entity"]): r["report"] for r in snapshot["roles"]["auditor"]["runs"]
        }
        return {
            key: sum(r[key] for v in latest.values() for r in v["period_measures"])
            for key in [
                "billing_net_cents",
                "erp_net_cents",
                "allocated_cash_cents",
                "period_end_arr_cents",
            ]
        }

    assert (
        totals(initial)
        == totals(restarted)
        == dict(
            billing_net_cents=3900000,
            erp_net_cents=3750000,
            allocated_cash_cents=1650000,
            period_end_arr_cents=12000000,
        )
    )
    assert totals(after) == dict(
        billing_net_cents=4560000,
        erp_net_cents=4410000,
        allocated_cash_cents=1650000,
        period_end_arr_cents=14640000,
    )
    assert any(
        r["state"] == "open" and r["amount_cents"] == -150000
        for r in after["roles"]["auditor"]["cases"]
    )
    assert [r["result"] for r in after["roles"]["auditor"]["decisions"]] == ["rejected", "approved"]
    recovered = [
        o for o in after["roles"]["auditor"]["outbox"] if o["event_id"] == "sf-recovery-v1"
    ]
    assert len(recovered) == 2 and all(o["status"] == "verified" for o in recovered)
    for outbox in after["roles"]["auditor"]["outbox"]:
        assert outbox["status"] == "verified"
        observed = next(
            r
            for r in after["receivers"][outbox["destination"]]["effects"]
            if r["effect_id"] == outbox["effect_id"]
        )
        assert observed["hash"] == outbox["hash"] == digest(outbox["payload"])
        assert observed["payload"] == outbox["payload"] == observed["receipt"]["payload"]
        assert observed["receipt"]["records_hash"] == digest(observed["receipt"]["records"])
        for record in observed["receipt"]["records"]:
            stored = next(
                r["body"]
                for r in after["receivers"][outbox["destination"]]["records"]
                if r["kind"] == record["kind"] and r["id"] == record["id"]
            )
            assert stored == record
    for service in ["stripe", "netsuite"]:
        assert {r["effect_id"] for r in initial["receivers"][service]["effects"]} == {
            r["effect_id"] for r in restarted["receivers"][service]["effects"]
        }
    for snapshot in [initial, restarted]:
        exhausted = [
            r
            for r in snapshot["roles"]["auditor"]["outbox"]
            if r["event_id"] == "sf-recovery-v1" and r["destination"] == "stripe"
        ]
        assert len(exhausted) == 1
        assert exhausted[0]["status"] == "exhausted"
        assert exhausted[0]["attempts"] == exhausted[0]["max_attempts"] == 3
    life = json.loads((folder / "lifecycle.json").read_bytes())
    assert digest(life["source_snapshot"]) == life["source_hash"]
    assert life["current_release"]["rows"] == oracle(life["source_snapshot"])
    assert life["current_measures"] == oracle_period(life["source_snapshot"])
    assert (
        life["six_model_exact_equivalence"] and len(life["portability"]["model_comparisons"]) == 6
    )
    assert all(r["all_typed_values_equal"] for r in life["portability"]["model_comparisons"])
    for name in ["incremental", "independent_full"]:
        assert life[name]["models"] == 6 and life[name]["tests"] >= 15
        assert all(n["status"] in ["success", "pass"] for n in life[name]["results"])
    browser = json.loads((folder / "browser/browser.json").read_bytes())
    assert len(browser["cases"]) >= 43 and {
        "rejection did not dispatch",
        "actual approved effect and dependent finance verified",
        "recovery budget persisted",
        "original 1500 variance still open",
        "EU principal cannot read US",
        "caller cannot override tenant",
    } <= set(browser["cases"])
    assert browser["initial"] == {"billing_cents": "3900000", "erp_cents": "3750000"}
    assert browser["final"] == {"billing_cents": "4560000", "erp_cents": "4410000"}
    assert not browser["errors"] and len(browser["accessibility"]) == 10
    assert all(not r["violations"] for r in browser["accessibility"])
    assert len(browser["screenshots"]) == 5
    for name, h in browser["screenshots"].items():
        assert hashlib.sha256((folder / "browser" / name).read_bytes()).hexdigest() == h
    return dict(
        status="passed",
        lane=report["lane"],
        source_commit=report["source_commit"],
        browser_checks=len(browser["cases"]),
        totals_after=totals(after),
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("folder", type=Path)
    p.add_argument("--failed", action="store_true")
    a = p.parse_args()
    print(json.dumps(check(a.folder, a.failed)))
