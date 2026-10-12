"""Offline by default. --live explicitly opts into one paid synthetic API request."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python/src"))
from project_j.probe import live_probe, preflight

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--live", action="store_true")
parser.add_argument("--effort", choices=["low", "medium"], default="low")
parser.add_argument("--account-label")
parser.add_argument("--project-label")
args = parser.parse_args()
if args.live and (not args.account_label or not args.project_label):
    parser.error("--live requires nonsecret --account-label and --project-label; no keys in labels")
result = (
    live_probe(args.effort, args.account_label, args.project_label) if args.live else preflight()
)
print(json.dumps(result, ensure_ascii=False, indent=2))
if args.live and not result.get("api_smoke_ok"):
    raise SystemExit(2)
