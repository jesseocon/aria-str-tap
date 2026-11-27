"""Write str_connector/catalog_tables.json: the STR marts people query (the org portfolio views
gold_{org}.{mart}), their column types from BigQuery metadata (no rows), and their descriptions
and keys from transform/models/marts/schema.yml. The connector's catalog() reads it.

    python packages/str-connector/scripts/capture_catalog.py hospitalityops.gold_<org>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml
from google.cloud import bigquery

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parents[1] / "str_connector" / "catalog_tables.json"
sys.path.insert(0, str(ROOT / "packages/str-connector"))
from str_connector.manifest import PORTFOLIO_MARTS  # noqa: E402


def main(dataset: str) -> None:
    project, ds = dataset.split(".")
    client = bigquery.Client(project=project)
    docs = {m["name"]: m for m in yaml.safe_load((ROOT / "transform/models/marts/schema.yml").read_text())["models"]}
    tables = {}
    for mart in PORTFOLIO_MARTS:
        schema = client.get_table(f"{project}.{ds}.{mart}").schema
        model = docs.get(mart, {})
        described = {c["name"]: c.get("description", "") for c in model.get("columns", [])}
        keys: list[str] = []
        for test in model.get("tests", []):
            combo = (test.get("dbt_utils.unique_combination_of_columns") or {}).get("arguments", {})
            keys = keys or list(combo.get("combination_of_columns", []))
        tables[mart] = {
            "description": " ".join((model.get("description") or "").split()),
            "primary_key": keys,
            "columns": [
                {"name": f.name, "type": f.field_type, "nullable": f.mode != "REQUIRED",
                 **({"description": " ".join(described[f.name].split())} if described.get(f.name) else {})}
                for f in schema
            ],
        }
    OUT.write_text(json.dumps({"tables": tables}, indent=1, sort_keys=True) + "\n")
    print(f"{len(tables)} marts, {sum(len(t['columns']) for t in tables.values())} columns")


if __name__ == "__main__":
    main(sys.argv[1])
