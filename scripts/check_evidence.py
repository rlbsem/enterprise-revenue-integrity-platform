"""Offline immutable evidence, local links and public-file safety checks."""

import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    verified = 0
    for manifest in (ROOT / "docs/evidence").glob("**/manifest.json"):
        expected = json.loads(manifest.read_text())
        actual = {
            p.name
            for p in manifest.parent.iterdir()
            if p.is_file() and p != manifest and p.name != "README.md"
        }
        if actual != set(expected):
            raise ValueError("evidence_inventory_mismatch:" + manifest.as_posix())
        for name, value in expected.items():
            if hashlib.sha256((manifest.parent / name).read_bytes()).hexdigest() != value:
                raise ValueError("evidence_hash_mismatch:" + name)
            verified += 1
    links = 0
    fingerprints = 0
    for verification in (ROOT / "docs/evidence").glob("**/verification.json"):
        record = json.loads(verification.read_text())
        if record.get("source_hash_encoding") != "UTF-8 with LF line endings":
            raise ValueError("unsupported_source_fingerprint_encoding")
        for path, expected_hash in record["source_hashes"].items():
            content = subprocess.check_output(
                ["git", "show", record["source_commit"] + ":" + path], cwd=ROOT
            )
            if hashlib.sha256(content).hexdigest() != expected_hash:
                raise ValueError("source_revision_fingerprint_mismatch:" + path)
            fingerprints += 1
    for document in [ROOT / "README.md", *ROOT.glob("docs/**/*.md")]:
        text = document.read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)]+)\)", text):
            if "://" in target or target.startswith("#"):
                continue
            path = (document.parent / target.split("#")[0]).resolve()
            if not path.is_relative_to(ROOT) or not path.exists():
                raise ValueError("missing_local_link:" + target)
            links += 1
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    for name in filter(None, tracked):
        if name.startswith((".local/", ".venv/")) or Path(name).suffix in (
            ".pem",
            ".key",
            ".sqlite",
            ".db",
        ):
            raise ValueError("private_file_in_inventory:" + name)
        data = (ROOT / name).read_text(encoding="utf-8", errors="ignore")
        if "-----BEGIN " + "PRIVATE KEY" in data or re.search(r"(?:AKIA|ASIA)[A-Z0-9]{16}", data):
            raise ValueError("credential_pattern_detected:" + name)
        if name.startswith("docs/") and name.endswith(".json"):
            if re.search(
                r'"(?:control_dsn|password|access_token|api_secret)"\s*:', data
            ) or re.search(r"[A-Za-z]:\\\\Users\\\\", data):
                raise ValueError("private_runtime_content_detected:" + name)
    print(
        json.dumps(
            {
                "evidence_files_verified": verified,
                "local_links_verified": links,
                "public_content_check": "passed",
                "source_revision_fingerprints": fingerprints,
            }
        )
    )


if __name__ == "__main__":
    main()
