"""Fresh independent acceptance fixture; no imports from the existing tests."""

import json
import os
from pathlib import Path

import pytest

from integrity.harness import LocalStack


@pytest.fixture(scope="session")
def runtime(tmp_path_factory):
    with LocalStack(tmp_path_factory.mktemp("adversarial") / "stack") as stack:
        from integrity.db import connect

        if os.environ.get("INTEGRITY_ADVERSARIAL_EVIDENCE"):
            with connect(stack.admins["control"]) as c:
                version = c.execute("SHOW server_version").fetchone()["server_version"]
            (Path(os.environ["INTEGRITY_ADVERSARIAL_EVIDENCE"]) / "environment.json").write_text(
                json.dumps(
                    {
                        "PostgreSQL": version,
                        "owned_cluster": stack.own_cluster,
                        "independent_database_count": 4,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
                newline="\n",
            )
        yield stack


@pytest.fixture
def stack(runtime):
    runtime.reset()
    runtime.config["approved_scopes"] = [["northstar", "EU"], ["northstar", "US"]]
    return runtime


@pytest.fixture
def observe(request):
    def capture(**body):
        folder = os.environ.get("INTEGRITY_ADVERSARIAL_EVIDENCE")
        if folder:
            target = Path(folder) / (request.node.name + ".json")
            target.write_text(
                json.dumps(body, default=str, indent=2) + "\n", encoding="utf-8", newline="\n"
            )

    return capture
