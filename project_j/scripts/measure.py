"""Post-run analysis of a measurements file; does not collect or write runtime data."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python/src"))
from project_j.latency import MeasurementRun, summarize

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("measurements", type=Path)
args = parser.parse_args()
run = MeasurementRun.model_validate_json(args.measurements.read_text())
print(json.dumps(summarize(run), ensure_ascii=False, indent=2, allow_nan=False))
