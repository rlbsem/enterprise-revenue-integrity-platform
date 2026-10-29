"""Execute independent PostgreSQL/HTTP probes; export no private logs or credentials."""

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def save(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2, default=str) + "\n", encoding="utf-8", newline="\n")


def source_hashes():
    # These exact paths also exist in the normal image plus read-only acceptance mounts.
    paths = [ROOT / "requirements.lock", ROOT / "pyproject.toml"]
    for folder in ["src", "dbt", "web", "scripts", "adversarial_tests"]:
        paths += [
            p
            for p in (ROOT / folder).rglob("*")
            if p.is_file()
            and p.suffix in [".py", ".sql", ".yml", ".js", ".cjs", ".css", ".html"]
            and not any(x in p.parts for x in ["__pycache__", "target", "logs"])
        ]
    return {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(
            p.read_text(encoding="utf-8").encode()
        ).hexdigest()
        for p in sorted(paths)
    }


def binding():
    return {
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "source_hash_encoding": "UTF-8 with LF line endings",
        "source_hashes": source_hashes(),
    }


def execute(work, output, stamp=None):
    if work.exists() or output.exists():
        raise ValueError("fresh_private_workspace_and_public_evidence_required")
    if os.environ.get("INTEGRITY_ADMIN_DSN"):
        raise ValueError("owned_disposable_cluster_required_no_external_admin_endpoint")
    output.mkdir(parents=True)
    (output / "cases").mkdir()
    work.mkdir(parents=True)
    if os.name != "nt":
        work.chmod(0o700)
    stamp = json.loads(stamp.read_bytes()) if stamp else binding()
    if stamp["source_hashes"] != source_hashes():
        raise ValueError("execution_source_binding_mismatch")
    started = datetime.now(UTC).isoformat()
    junit = work / "results-private.xml"
    with (work / "test-output-private.log").open("w", encoding="utf-8") as stream:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                str(ROOT / "adversarial_tests"),
                "-q",
                "-p",
                "no:cacheprovider",
                "--basetemp",
                str(work / "pytest"),
                "--junitxml",
                str(junit),
            ],
            cwd=ROOT,
            env={
                **os.environ,
                "INTEGRITY_ADVERSARIAL_EVIDENCE": str(output / "cases"),
                "PYTHONUTF8": "1",
            },
            stdout=stream,
            stderr=subprocess.STDOUT,
            timeout=1200,
        )
    suites = list(ET.parse(junit).getroot().iter("testsuite"))
    totals = {
        k: sum(int(s.attrib[k]) for s in suites) for k in ["tests", "failures", "errors", "skipped"]
    }
    cases = [
        {
            "name": c.attrib["name"],
            "class": c.attrib["classname"],
            "seconds": float(c.attrib["time"]),
            "outcome": (
                "failed"
                if c.find("failure") is not None
                else "error"
                if c.find("error") is not None
                else "skipped"
                if c.find("skipped") is not None
                else "passed"
            ),
        }
        for s in suites
        for c in s.findall("testcase")
    ]
    assert source_hashes() == stamp["source_hashes"], "source_changed_during_acceptance"
    status = (
        "passed"
        if result.returncode == 0 and not any(totals[k] for k in ["errors", "failures", "skipped"])
        else "failed"
    )
    save(
        output / "acceptance.json",
        {
            "status": status,
            "started_at": started,
            "completed_at": datetime.now(UTC).isoformat(),
            **stamp,
            "python": platform.python_version(),
            "platform": platform.system(),
            "suites": totals,
            "cases": cases,
            "execution": "actual_owned_PostgreSQL_and_loopback_HTTP",
            "native_vendor_execution": False,
            "synthetic": True,
            "independent_human_security_audit": False,
            "raw_test_output": "private_excluded_potential_runtime_credentials",
        },
    )
    save(
        output / "artifact-integrity.json",
        {
            p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(output.rglob("*"))
            if p.is_file() and p.name != "artifact-integrity.json"
        },
    )
    print(json.dumps({"status": status, **totals}))
    if status != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workspace", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--binding-file", type=Path)
    p.add_argument("--write-binding", action="store_true")
    a = p.parse_args()
    if a.write_binding:
        save(a.output, binding())
    else:
        if a.workspace is None:
            p.error("--workspace required")
        execute(a.workspace.resolve(), a.output.resolve(), a.binding_file)
