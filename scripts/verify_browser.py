"""Start a fresh real console, run browser acceptance, and clean only this run's services."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--workspace", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--port", type=int, default=8583)
    a = p.parse_args()
    workspace = Path(a.workspace).resolve()
    if workspace.exists() or Path(a.output).exists():
        raise ValueError("fresh_browser_workspace_and_output_required")
    workspace.mkdir(parents=True)
    stop = workspace / "stop"
    node = shutil.which("node")
    if not node:
        raise ValueError("node_runtime_required")
    origin = f"http://127.0.0.1:{a.port}"
    with (workspace / "runner.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "integrity.console",
                "--workspace",
                str(workspace / "services"),
                "--port",
                str(a.port),
                "--stop-file",
                str(stop),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            ready = workspace / "services/operator-credentials.json"
            deadline = time.monotonic() + 300
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.2)
            if not ready.exists():
                raise RuntimeError("console_not_ready_see_private_runner_log")
            subprocess.run(
                [node, "scripts/browser_acceptance.cjs", str(ready.parent), a.output],
                env={**os.environ, "INTEGRITY_BROWSER_ORIGIN": origin},
                check=True,
            )
        finally:
            stop.touch()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=10)
                raise RuntimeError("console_graceful_cleanup_failed_review_private_workspace")
        if process.returncode:
            raise RuntimeError("console_failed_during_acceptance")
    print(json.dumps({"console_exited": True, "owned_services_cleanup": "completed"}))


if __name__ == "__main__":
    main()
