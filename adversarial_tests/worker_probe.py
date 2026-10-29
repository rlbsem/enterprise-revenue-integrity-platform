"""Real disposable worker process. Markers pause only the acceptance driver."""

import argparse
import json
import time
from pathlib import Path

from integrity.control import Controller


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("config")
    p.add_argument("mode")
    p.add_argument("input")
    p.add_argument("marker")
    args = p.parse_args()
    c = Controller(json.loads(Path(args.config).read_bytes()))
    if args.mode == "ingest":
        c.ingest(json.loads(Path(args.input).read_bytes()))
    else:
        c.step(args.input, timeout=15)
    Path(args.marker).write_text("normal_call_returned")
    while True:
        time.sleep(0.1)
