import argparse
import json
from pathlib import Path
from .finance import publish

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("marker")
    a = p.parse_args()
    data = json.loads(Path(a.input).read_text())
    publish(a.output, data["snapshot"], data["rows"], data["snapshot"], crash_marker=a.marker)
