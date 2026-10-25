import pytest

from integrity.control import Controller
from integrity.harness import LocalStack


@pytest.fixture(scope="session")
def runtime(tmp_path_factory):
    with LocalStack(tmp_path_factory.mktemp("stack-parent") / "run") as stack:
        yield stack


@pytest.fixture
def stack(runtime):
    runtime.reset()
    return runtime


@pytest.fixture
def controller(stack):
    return Controller(stack.config)


def pytest_report_header(config):
    return "Actual PostgreSQL + three independent HTTP subprocesses; synthetic local acceptance"
