"""Run tap-str into BigQuery, then dbt, from a console connection."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from aria_runner.sdk import ReauthRequired, RunContext, RunResult

from str_connector.manifest import KEY, manifest

DEV_PROJECT = "hospitalityops-dev"
PROD_PROJECT = "hospitalityops"


def project_root() -> Path:
    configured = os.environ.get("STR_PROJECT_ROOT")
    if configured:
        return Path(configured)
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "packages" / "tap-str").is_dir() and (candidate / "meltano.yml").is_file():
            return candidate
    raise FileNotFoundError("aria-str-tap not found; set STR_PROJECT_ROOT")


def _bool(value: object, default: bool = True) -> str:
    if value is None:
        return "true" if default else "false"
    return "true" if value else "false"


def _bigquery_auth(env: dict[str, str]) -> None:
    """Use a key file when one is configured. Otherwise use application default credentials."""
    keyfile = env.get("DBT_BIGQUERY_KEYFILE") or env.get("TARGET_BIGQUERY_CREDENTIALS_PATH") or ""
    if keyfile and Path(keyfile).is_file():
        env["DBT_BIGQUERY_KEYFILE"] = keyfile
        env["TARGET_BIGQUERY_CREDENTIALS_PATH"] = keyfile
        env["DBT_BIGQUERY_AUTH_METHOD"] = "service-account"
        return
    env.pop("DBT_BIGQUERY_KEYFILE", None)
    env.pop("TARGET_BIGQUERY_CREDENTIALS_PATH", None)
    env["DBT_BIGQUERY_AUTH_METHOD"] = "oauth"


class StrConnector:
    key = KEY

    def manifest(self) -> dict[str, Any]:
        return manifest()

    def probe(self, ctx: RunContext) -> None:
        return None

    def run(self, ctx: RunContext) -> RunResult:
        selected = set(ctx.config.get("streams") or ["daily"])
        if "daily" not in selected:
            return RunResult(metrics={"record_count": 0})

        root = project_root()
        meltano = str(root / ".venv" / "bin" / "meltano")
        org_id = str(ctx.config["org_id"])
        costar_property_id = str(ctx.config["property_id"])
        environments = list(ctx.config.get("environments") or ["development"])
        if not ctx.secret_env.get("TAP_STR_USERNAME") or not ctx.secret_env.get("TAP_STR_PASSWORD"):
            raise ReauthRequired("CoStar username and password are not saved on this connection")
        env = os.environ.copy()
        env.update(ctx.secret_env)
        env.update(
            {
                "MELTANO_PROJECT_ROOT": str(root),
                "STR_PROJECT_ROOT": str(root),
                "TAP_STR_PROPERTY_ID": costar_property_id,
                "TAP_STR_BEGIN_DATE": str(ctx.config.get("begin_date") or "2025-08-01"),
                "TAP_STR_END_DATE": str(ctx.config.get("end_date") or "2026-10-09"),
                "TAP_STR_INCLUDE_LAST_YEAR": _bool(ctx.config.get("include_last_year"), True),
                "TAP_STR_HEADLESS": "false",
                "TAP_STR__SELECT": json.dumps(["daily.*"]),
                "TARGET_BIGQUERY_DATASET": f"bronze_{org_id}",
                "TARGET_BIGQUERY_STREAM_MAPS": json.dumps(
                    {"daily": {"__alias__": f"mlt_str__daily_{costar_property_id}"}},
                    separators=(",", ":"),
                ),
            },
        )
        ctx.logger.info(
            "str %s costar %s %s..%s environments=%s",
            ctx.connection_id,
            costar_property_id,
            env["TAP_STR_BEGIN_DATE"],
            env["TAP_STR_END_DATE"],
            ",".join(environments),
        )

        scrape_env = "development" if "development" in environments else environments[0]
        scrape_project = DEV_PROJECT if scrape_env == "development" else PROD_PROJECT
        env["TARGET_BIGQUERY_PROJECT"] = scrape_project
        ctx.run_command(
            [meltano, f"--environment={scrape_env}", "run", "tap-str", "target-bigquery"],
            env=env,
            cwd=root,
        )

        for environment in [scrape_env]:
            aria_property_id = (
                ctx.config.get("production_property_id")
                if environment == "production"
                else ctx.config.get("development_property_id")
            )
            if not aria_property_id:
                msg = f"{environment}_property_id is required to build dbt models"
                raise ValueError(msg)
            project = PROD_PROJECT if environment == "production" else DEV_PROJECT
            dbt_env = dict(env)
            dbt_env["DBT_BIGQUERY_PROJECT"] = project
            dbt_env["DBT_BIGQUERY_DATASET"] = f"silver_{org_id}"
            _bigquery_auth(dbt_env)
            dbt_vars = json.dumps(
                {
                    "bq_project": project,
                    "org_id": org_id,
                    "source": "mlt_str",
                    "property_id": aria_property_id,
                    "costar_property_id": costar_property_id,
                },
                separators=(",", ":"),
            )
            ctx.run_command(
                [meltano, f"--environment={environment}", "invoke", "dbt-bigquery:run", "--vars", dbt_vars],
                env=dbt_env,
                cwd=root,
            )
        return RunResult(
            metrics={
                "bronze_table": f"bronze_{org_id}.mlt_str__daily_{costar_property_id}",
                "environments": environments,
            },
        )
