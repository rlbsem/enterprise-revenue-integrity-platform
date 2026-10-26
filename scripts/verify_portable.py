"""Exercise the normal packaged image, original browser workflow and fresh restart."""

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4
import httpx

ROOT = Path(__file__).resolve().parents[1]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, indent=2, default=str) + "\n").encode())


def sources():
    names = subprocess.check_output(
        ["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT, text=True
    ).splitlines()
    return {
        n: hashlib.sha256((ROOT / n).read_text(encoding="utf-8").encode()).hexdigest()
        for n in sorted(set(names))
        if n.startswith(("src/", "web/", "dbt/", "scripts/", "tests/", ".github/"))
        or n
        in [
            "Dockerfile",
            ".dockerignore",
            "compose.yaml",
            "requirements.lock",
            "pyproject.toml",
            "package.json",
            "pnpm-lock.yaml",
        ]
    }


class Lane:
    def __init__(self, work, native):
        self.work, self.native = work, native
        work.mkdir(parents=True)
        if os.name != "nt":
            work.chmod(0o700)
        self.state = work / "state"
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self.project = "riportable" + uuid4().hex[:10]
        self.env = {
            **os.environ,
            "INTEGRITY_DEMO_PORT": str(self.port),
            "PYTHONUTF8": "1",
            "INTEGRITY_BROWSER_ORIGIN": self.origin,
        }
        self.process = self.log = None
        self.owned_ports = []
        self.built = False

    def compose(self, *args):
        return ["docker", "compose", "-f", str(ROOT / "compose.yaml"), "-p", self.project, *args]

    def run(self, command, name, timeout=900):
        result = subprocess.run(
            command,
            cwd=ROOT,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        if result.returncode:
            (self.work / (name + "-private.log")).write_bytes(result.stdout + result.stderr)
            raise RuntimeError(name + "_failed_inspect_private_log")
        return result.stdout

    def cli(self, *args):
        if self.native:
            command = [
                sys.executable,
                "-m",
                "integrity.portable",
                *args,
                "--state",
                str(self.state),
            ]
        else:
            command = self.compose(
                "exec", "-T", "demo", "python", "-m", "integrity.portable", *args
            )
        return self.run(command, "private_cli")

    def start(self):
        if self.native:
            self.log = (self.work / "runtime-private.log").open("ab")
            self.process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "integrity.portable",
                    "serve",
                    "--state",
                    str(self.state),
                    "--port",
                    str(self.port),
                ],
                cwd=ROOT,
                env=self.env,
                stdout=self.log,
                stderr=self.log,
            )
        else:
            if not self.built:
                self.run(self.compose("build", "demo"), "image_build", 1200)
                self.built = True
            self.run(
                self.compose("up", "-d", "--wait", "--wait-timeout", "360", "demo"), "startup", 390
            )
        deadline = time.monotonic() + 360
        with httpx.Client(trust_env=False, timeout=3) as c:
            while time.monotonic() < deadline:
                if self.native and self.process.poll() is not None:
                    raise RuntimeError("native_start_failed_inspect_private_log")
                try:
                    if c.get(self.origin + "/").status_code == 200:
                        try:
                            self.cli("credentials", "--user", "auditor")
                            return
                        except RuntimeError:
                            pass
                except httpx.HTTPError:
                    pass
                time.sleep(1)
        raise RuntimeError("startup_deadline_exceeded")

    def inspect(self):
        observed = json.loads(self.cli("inspect"))
        self.owned_ports = observed.pop("owned_ports")
        return observed

    def stop(self):
        if self.native:
            if self.process.poll() is None:
                self.cli("stop")
                self.process.wait(timeout=60)
            assert self.process.returncode == 0
            self.log.close()
            marker = json.loads((self.state / "shutdown.json").read_bytes())
            for port in self.owned_ports:
                with socket.socket() as s:
                    s.settimeout(2)
                    assert s.connect_ex(("127.0.0.1", port)) != 0, "owned_listener_survived"
        else:
            self.run(self.compose("stop", "--timeout", "60", "demo"), "stop", 75)
            cid = self.run(self.compose("ps", "-aq", "demo"), "cid").decode().strip()
            obj = json.loads(self.run(["docker", "inspect", cid], "stopped_inspection"))[0]
            assert (
                not obj["State"]["Running"]
                and obj["State"]["Pid"] == 0
                and obj["State"]["ExitCode"] == 0
            )
            self.run(
                ["docker", "cp", cid + ":/state/shutdown.json", str(self.work / "shutdown.json")],
                "shutdown_copy",
            )
            marker = json.loads((self.work / "shutdown.json").read_bytes())
        assert marker["owned_database_and_http_stopped"]
        return dict(
            clean_exit=True,
            owned_services_stopped=True,
            independent_listener_or_container_pid_check=True,
        )

    def cleanup(self):
        if self.native:
            if self.process and self.process.poll() is None:
                self.stop()
            return {"owned_process_exited": not self.process or self.process.poll() is not None}
        self.run(self.compose("down", "--volumes", "--remove-orphans"), "teardown", 120)
        inventories = {
            kind: self.run(
                [
                    "docker",
                    *command,
                    "--filter",
                    "label=com.docker.compose.project=" + self.project,
                ],
                "independent_inventory",
            )
            .decode()
            .split()
            for kind, command in [
                ("containers", ["ps", "-aq"]),
                ("networks", ["network", "ls", "-q"]),
                ("volumes", ["volume", "ls", "-q"]),
            ]
        }
        assert not any(inventories.values())
        return dict(verified_absent=True, independent_label_inventory=inventories)

    def isolation(self):
        if self.native:
            return dict(lane="native_loopback; Docker controls not tested here")
        cid = self.run(self.compose("ps", "-q", "demo"), "cid").decode().strip()
        obj = json.loads(self.run(["docker", "inspect", cid], "running_inspection"))[0]
        h = obj["HostConfig"]
        assert (
            obj["Config"]["User"] == "10001:10001" and h["ReadonlyRootfs"] and not h["Privileged"]
        )
        assert "ALL" in h["CapDrop"] and any(
            x.startswith("no-new-privileges") for x in h["SecurityOpt"]
        )
        assert h["PortBindings"] == {
            "8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(self.port)}]
        }
        assert h["Memory"] == 2048 * 1024**2 and h["PidsLimit"] == 512
        assert h["NetworkMode"] != "host" and h["PidMode"] != "host"
        for network in obj["NetworkSettings"]["Networks"]:
            n = json.loads(
                self.run(["docker", "network", "inspect", network], "network_inspection")
            )[0]
            assert (
                n["Driver"] == "bridge"
                and n["Options"]["com.docker.network.bridge.enable_icc"] == "false"
            )
        return dict(
            nonroot=True,
            read_only_root=True,
            no_new_privileges=True,
            capabilities_dropped=True,
            loopback_console_only=True,
            no_database_or_business_service_published=True,
            runtime_egress_firewall=False,
        )


def probe(lane):
    credential = json.loads(lane.cli("credentials", "--user", "auditor"))
    with httpx.Client(base_url=lane.origin, trust_env=False, headers={"Origin": lane.origin}) as c:
        assert c.get("/api/overview").status_code == 401
        assert c.get("/", headers={"Host": "unapproved.example"}).status_code == 400
        for path in ["/openapi.json", "/docs", "/test/faults", "/ingest"]:
            assert c.get(path).status_code == 404
        assert (
            c.post(
                "/api/login", json={k: credential[k] for k in ["username", "password"]}
            ).status_code
            == 200
        )
        assert c.get("/api/health").json()["worker_active"]
        assert c.post("/api/proposals", json={}).status_code == 403
        assert (
            c.post(
                "/api/login", headers={"Origin": "http://unapproved.example"}, json={}
            ).status_code
            == 403
        )
        return credential, c.cookies.get("integrity_session")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workspace", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--native", action="store_true")
    a = p.parse_args()
    if a.workspace.exists() or a.output.exists():
        raise ValueError("fresh_paths_required")
    a.output.mkdir(parents=True)
    source = sources()
    report = dict(
        source_commit=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        source_hash_encoding="UTF-8 with LF line endings",
        source_hashes=source,
        started_at=datetime.now(UTC).isoformat(),
        lane="native_local" if a.native else "actual_docker_compose",
        native_vendor_execution=False,
        cloud_resources=False,
    )
    lane = Lane(a.workspace, a.native)
    try:
        if not a.native:
            engine = (
                lane.run(["docker", "version", "--format", "{{.Server.Version}}"], "docker_version")
                .decode()
                .strip()
            )
            assert int(engine.split(".")[0]) >= 28
            report["docker_engine"] = engine
        lane.start()
        credential, cookie = probe(lane)
        initial = lane.inspect()
        expected = {
            n: h
            for n, h in source.items()
            if n.startswith(("src/", "dbt/", "web/"))
            and Path(n).suffix in [".py", ".sql", ".yml", ".html", ".js", ".css"]
        }
        assert initial["source_hashes"] == expected, "image_source_not_exact_checkout"
        report["isolation"] = lane.isolation()
        write(a.output / "initial.json", initial)
        write(a.output / "lifecycle.json", json.loads(lane.cli("lifecycle-evidence")))
        private_browser = lane.work / "browser-private"
        private_browser.mkdir()
        write(
            private_browser / "operator-credentials.json",
            {
                u: json.loads(lane.cli("credentials", "--user", u))
                for u in ["auditor", "operator", "approver", "reader_eu", "outsider"]
            },
        )
        if os.name != "nt":
            private_browser.chmod(0o700)
            (private_browser / "operator-credentials.json").chmod(0o600)
        lane.run(
            [
                "node",
                "scripts/browser_acceptance.cjs",
                str(private_browser),
                str(a.output / "browser"),
            ],
            "browser_acceptance",
            300,
        )
        after = lane.inspect()
        write(a.output / "recovered.json", after)
        assert initial["run"] == after["run"]
        report["normal_shutdown"] = lane.stop()
        lane.start()
        fresh, _ = probe(lane)
        restarted = lane.inspect()
        assert restarted["run"] != initial["run"] and fresh["password"] != credential["password"]
        with httpx.Client(
            base_url=lane.origin, trust_env=False, headers={"Origin": lane.origin}
        ) as c:
            assert (
                c.get("/api/me", headers={"Cookie": "integrity_session=" + cookie}).status_code
                == 401
            )
            assert (
                c.post(
                    "/api/login", json={k: credential[k] for k in ["username", "password"]}
                ).status_code
                == 401
            )
        assert [r["report"] for r in restarted["roles"]["auditor"]["runs"]] == [
            r["report"] for r in initial["roles"]["auditor"]["runs"]
        ]
        write(a.output / "restarted.json", restarted)
        report["fresh_restart"] = dict(
            old_session_rejected=True,
            old_credentials_rejected=True,
            new_run=True,
            financial_parity=True,
        )
        report["restarted_shutdown"] = lane.stop()
        report["status"] = "passed"
    except Exception as error:
        report["status"] = "failed"
        report["error_type"] = type(error).__name__
        raise
    finally:
        try:
            report["cleanup"] = lane.cleanup()
        except Exception as error:
            report["status"] = "failed"
            report["cleanup"] = {"verified_absent": False, "error_type": type(error).__name__}
        report["finished_at"] = datetime.now(UTC).isoformat()
        assert sources() == source, "source_changed_during_verification"
        write(a.output / "portable.json", report)
        write(
            a.output / "artifact-integrity.json",
            {
                f.relative_to(a.output).as_posix(): hashlib.sha256(f.read_bytes()).hexdigest()
                for f in a.output.rglob("*")
                if f.is_file() and f.name != "artifact-integrity.json"
            },
        )
    assert report["status"] == "passed", "packaged_acceptance_or_cleanup_failed"
    print(
        json.dumps({"status": report["status"], "lane": report["lane"], "cleanup_verified": True})
    )


if __name__ == "__main__":
    main()
