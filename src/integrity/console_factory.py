import json
import os
from pathlib import Path
from .console_api import create_app


def factory():
    config = json.loads(Path(os.environ["INTEGRITY_CONSOLE_CONFIG"]).read_text())
    return create_app(config, origin=config["console_origin"])
