"""Offline evaluation report starter; provider calls are intentionally excluded."""
import json
from pathlib import Path
queries = [json.loads(line) for line in Path("queries.jsonl").read_text().splitlines() if line]
print(json.dumps({"queries": len(queries), "status": "fixture generated; connect pipeline before scoring"}, indent=2))
