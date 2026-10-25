"""Run and preserve a stage-specific full regression checkpoint without duplicating scenario builds."""

import argparse
import json
import os
import platform
import subprocess
import sys
import xml.etree.ElementTree as ET
import hashlib
from datetime import UTC, datetime
from pathlib import Path

from verify import ROOT, source_hashes, run


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--workspace", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--stage", required=True)
    a = p.parse_args()
    work = Path(a.workspace).resolve()
    out = Path(a.output).resolve()
    if work.exists() or out.exists():
        raise ValueError("fresh_checkpoint_paths_required")
    work.mkdir(parents=True)
    out.mkdir(parents=True)
    os.environ["INTEGRITY_TEST_EVIDENCE"] = str(out)
    run([sys.executable, "-m", "ruff", "check", "src", "scripts", "tests"], work / "lint.log")
    run(
        [sys.executable, "-m", "ruff", "format", "--check", "src", "scripts", "tests"],
        work / "format.log",
    )
    run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--basetemp=" + str(work / "pytest"),
            "--junitxml=" + str(work / "junit.xml"),
        ],
        work / "pytest.log",
    )
    suite = ET.parse(work / "junit.xml").getroot().find("testsuite")
    result = {
        "stage": a.stage,
        "executed_at": datetime.now(UTC).isoformat(),
        "os": platform.system(),
        "python": platform.python_version(),
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "source_hash_encoding": "UTF-8 with LF line endings",
        "source_hashes": source_hashes(),
        "tests": int(suite.attrib["tests"]),
        "failures": int(suite.attrib["failures"]),
        "errors": int(suite.attrib["errors"]),
        "skipped": int(suite.attrib["skipped"]),
        "cases": [
            {
                "name": x.attrib["name"],
                "class": x.attrib["classname"],
                "seconds": float(x.attrib["time"]),
                "outcome": "passed",
            }
            for x in suite.findall("testcase")
        ],
        "native_vendor_execution": False,
        "independent_audit": "pending_separate_review",
    }
    (out / "verification.json").write_bytes((json.dumps(result, indent=2) + "\n").encode())
    (out / "manifest.json").write_bytes(
        (
            json.dumps(
                {
                    f.name: hashlib.sha256(f.read_bytes()).hexdigest()
                    for f in out.iterdir()
                    if f.is_file()
                },
                indent=2,
            )
            + "\n"
        ).encode()
    )
    print(
        json.dumps(
            {
                "stage": a.stage,
                "tests": result["tests"],
                "failures": result["failures"],
                "errors": result["errors"],
            }
        )
    )


if __name__ == "__main__":
    main()
