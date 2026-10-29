"""Validate independently executed acceptance records without rewriting prior evidence."""

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check(folder):
    inventory = json.loads((folder / "artifact-integrity.json").read_bytes())
    actual = {
        p.relative_to(folder).as_posix()
        for p in folder.rglob("*")
        if p.is_file() and p.name != "artifact-integrity.json"
    }
    assert set(inventory) == actual
    for name, h in inventory.items():
        path = (folder / name).resolve()
        assert path.is_relative_to(folder.resolve())
        assert hashlib.sha256(path.read_bytes()).hexdigest() == h
    report = json.loads((folder / "acceptance.json").read_bytes())
    assert report["status"] == "passed" and report["suites"]["tests"] >= 25
    assert not any(report["suites"][k] for k in ["failures", "errors", "skipped"])
    assert len(report["cases"]) == report["suites"]["tests"] and all(
        c["outcome"] == "passed" for c in report["cases"]
    )
    assert report["native_vendor_execution"] is False and report["synthetic"]
    for path, h in report["source_hashes"].items():
        original = subprocess.check_output(
            ["git", "show", report["source_commit"] + ":" + path], cwd=ROOT
        )
        assert hashlib.sha256(original).hexdigest() == h, "executed_source_binding_changed:" + path
        assert (
            hashlib.sha256((ROOT / path).read_text(encoding="utf-8").encode()).hexdigest() == h
        ), "current_execution_source_changed:" + path
    names = {c["name"] for c in report["cases"]}
    assert all(
        any(required in n for n in names)
        for required in [
            "populated_tenant_isolation",
            "real_http_scope_session",
            "competing_approved_retries",
            "owned_postgresql_restart",
            "direct_source_arithmetic",
            "native_control_restore",
            "receipt_before_confirmation",
            "inflight_dispatch",
        ]
    )
    for path in folder.rglob("*.json"):
        assert not re.search(
            r'"(?:control_dsn|password|access_token|api_secret|csrf_hash|session_hash)"\s*:',
            path.read_text(encoding="utf-8"),
        )
    preserved = json.loads((ROOT / "docs/evidence/adversarial/preserved-files.json").read_bytes())
    for path, h in preserved.items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == h, (
            "historical_evidence_changed:" + path
        )
    print(
        json.dumps(
            {
                "status": "passed",
                "tests": report["suites"]["tests"],
                "source_bindings": len(report["source_hashes"]),
                "preserved_evidence_files": len(preserved),
                "artifacts": len(inventory),
            }
        )
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--evidence", type=Path, default=ROOT / "docs/evidence/adversarial/run01")
    args = p.parse_args()
    check(args.evidence.resolve())
