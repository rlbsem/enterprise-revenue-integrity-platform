"""One reproducible local acceptance command. Writes NEW evidence only."""

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command, output):
    with output.open("w", encoding="utf-8") as stream:
        result = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f"verification_failed:{output.name}:exit_{result.returncode}")


def source_hashes():
    files = [
        ROOT / "pyproject.toml",
        ROOT / "requirements.lock",
        ROOT / "package.json",
        ROOT / "pnpm-lock.yaml",
    ]
    for directory in ("src", "tests", "scripts", "dbt", "web"):
        files.extend(
            p
            for p in (ROOT / directory).rglob("*")
            if p.suffix in (".py", ".sql", ".yml", ".js", ".cjs", ".css", ".html")
        )
    return {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(
            p.read_text(encoding="utf-8").encode()
        ).hexdigest()
        for p in sorted(files)
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    workspace, output = Path(args.workspace).resolve(), Path(args.output).resolve()
    if workspace.exists() or output.exists():
        raise ValueError("fresh_workspace_and_evidence_paths_required")
    workspace.mkdir(parents=True)
    output.mkdir(parents=True)
    import os

    started_hashes = source_hashes()
    started_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    os.environ["INTEGRITY_TEST_EVIDENCE"] = str(output)
    run([sys.executable, "-m", "ruff", "check", "src", "tests", "scripts"], workspace / "lint.log")
    run(
        [sys.executable, "-m", "ruff", "format", "--check", "src", "tests", "scripts"],
        workspace / "format.log",
    )
    run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--basetemp=" + str(workspace / "pytest"),
            "--junitxml=" + str(workspace / "junit.xml"),
        ],
        workspace / "tests.log",
    )
    suite = ET.parse(workspace / "junit.xml").getroot().find("testsuite")
    cases = [
        {
            "name": e.attrib["name"],
            "class": e.attrib["classname"],
            "seconds": float(e.attrib["time"]),
            "outcome": "failed"
            if e.find("failure") is not None
            else "error"
            if e.find("error") is not None
            else "skipped"
            if e.find("skipped") is not None
            else "passed",
        }
        for e in suite.findall("testcase")
    ]
    run(
        [
            sys.executable,
            "-m",
            "integrity.demo",
            "--workspace",
            str(workspace / "demo"),
            "--output",
            str(output / "demo.json"),
        ],
        workspace / "demo.log",
    )
    run(
        [
            sys.executable,
            "-m",
            "integrity.finance_demo",
            "--workspace",
            str(workspace / "finance-demo"),
            "--output",
            str(output / "finance.json"),
        ],
        workspace / "finance.log",
    )
    if (
        source_hashes() != started_hashes
        or subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        != started_commit
    ):
        raise ValueError("source_changed_during_acceptance")
    if int(suite.attrib["skipped"]):
        raise ValueError("required_acceptance_tests_skipped")
    record = {
        "executed_at": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "os": platform.system(),
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "working_tree_source_hashes_authoritative": True,
        "source_hash_encoding": "UTF-8 with LF line endings",
        "source_hashes": started_hashes,
        "test_count": int(suite.attrib["tests"]),
        "failures": int(suite.attrib["failures"]),
        "errors": int(suite.attrib["errors"]),
        "skipped": int(suite.attrib["skipped"]),
        "cases": cases,
        "native_vendor_execution": False,
        "cloud_resources": False,
        "independent_final_audit": "pending_external_review",
    }
    (output / "verification.json").write_text(json.dumps(record, indent=2) + "\n")
    manifest = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.is_file()
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(
        json.dumps(
            {"tests": record["test_count"], "failures": record["failures"], "evidence": str(output)}
        )
    )


if __name__ == "__main__":
    main()
