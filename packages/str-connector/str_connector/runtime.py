"""Run tap-str into BigQuery, then dbt, from a console connection."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from aria_runner.sdk import ReauthRequired, RunContext, RunResult

from str_connector.manifest import ENVIRONMENTS, KEY, STREAMS, manifest

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


PORTAL_SECRETS = ("TAP_STR_PORTAL_USERNAME", "TAP_STR_PORTAL_PASSWORD", "TAP_STR_PORTAL_TOTP_SECRET")


def selected_streams(config: dict[str, Any]) -> list[str]:
    """The connection's streams; all of them when none apply (e.g. a retired ``daily``)."""
    chosen = [s for s in config.get("streams") or [] if s in STREAMS]
    return chosen or list(STREAMS)


DBT_SELECTOR = "tag:str"


def table_name(stream: str, costar_property_id: str) -> str:
    return f"mlt_str__{stream}_{costar_property_id}"


def tap_env(ctx: RunContext, root: Path, streams: list[str]) -> dict[str, str]:
    """Environment for ``meltano run tap-str target-bigquery`` on this connection."""
    access_path = ctx.auth_option or "direct"
    if access_path == "direct":
        if not ctx.secret_env.get("TAP_STR_USERNAME") or not ctx.secret_env.get("TAP_STR_PASSWORD"):
            raise ReauthRequired("CoStar username and password are not saved on this connection")
    elif not all(ctx.secret_env.get(name) for name in PORTAL_SECRETS):
        raise ReauthRequired("Portal user ID, password and authenticator secret are not all saved on this connection")
    costar_property_id = str(ctx.config["property_id"])
    env = {
        "MELTANO_PROJECT_ROOT": str(root),
        "STR_PROJECT_ROOT": str(root),
        "TAP_STR_ACCESS_PATH": access_path,
        "TAP_STR_PROPERTY_ID": costar_property_id,
        "TAP_STR_BEGIN_DATE": str(ctx.config.get("begin_date") or "2025-08-01"),
        "TAP_STR_MONTHLY_BEGIN_DATE": str(ctx.config.get("monthly_begin_date") or "2024-01-01"),
        "TAP_STR_HEADLESS": "false",
        "TAP_STR__SELECT": json.dumps([f"{stream}.*" for stream in streams]),
        "TARGET_BIGQUERY_DATASET": f"bronze_{ctx.config['org_id']}",
        "TARGET_BIGQUERY_STREAM_MAPS": json.dumps(
            {stream: {"__alias__": table_name(stream, costar_property_id)} for stream in streams},
            separators=(",", ":"),
        ),
    }
    if ctx.config.get("community_views"):
        env["TAP_STR_COMMUNITY_VIEWS"] = json.dumps(list(ctx.config["community_views"]))
    if ctx.config.get("comp_set_id"):
        env["TAP_STR_COMP_SET_ID"] = str(ctx.config["comp_set_id"])
    return env


class StrConnector:
    key = KEY

    def manifest(self) -> dict[str, Any]:
        return manifest()

    def probe(self, ctx: RunContext) -> None:
        return None

    def run(self, ctx: RunContext) -> RunResult:
        root = project_root()
        meltano = str(root / ".venv" / "bin" / "meltano")
        org_id = str(ctx.config["org_id"])
        costar_property_id = str(ctx.config["property_id"])
        environments = list(ctx.config.get("environments") or ["development"])
        streams = selected_streams(ctx.config)
        env = os.environ.copy()
        env.update(ctx.secret_env)
        env.update(tap_env(ctx, root, streams))
        ctx.logger.info(
            "str %s costar %s via %s streams=%s %s..last Sunday environments=%s",
            ctx.connection_id,
            costar_property_id,
            env["TAP_STR_ACCESS_PATH"],
            ",".join(streams),
            env["TAP_STR_BEGIN_DATE"],
            ",".join(environments),
        )

        scrape_env = "development" if "development" in environments else environments[0]
        scrape_project = DEV_PROJECT if scrape_env == "development" else PROD_PROJECT
        env["TARGET_BIGQUERY_PROJECT"] = scrape_project
        ctx.run_command(
            [meltano, f"--environment={scrape_env}", "run", "tap-str", "target-bigquery"],
            env=env,
            cwd=root,
            step="extract",
        )

        bronze_tables = [f"bronze_{org_id}.{table_name(stream, costar_property_id)}" for stream in streams]
        targets = [e for e in ENVIRONMENTS if e in environments]
        if targets == list(ENVIRONMENTS):
            # One scrape serves both warehouses: copy its bronze into production.
            copy_env = dict(env)
            copy_env["DBT_BIGQUERY_PROJECT"] = DEV_PROJECT
            copy_env["DBT_BIGQUERY_DATASET"] = f"silver_{org_id}"
            _bigquery_auth(copy_env)
            spec = {
                "source_project": DEV_PROJECT,
                "dest_project": PROD_PROJECT,
                "dataset": f"bronze_{org_id}",
                "tables": [table.split(".", 1)[1] for table in bronze_tables],
            }
            ctx.run_command(
                [
                    meltano,
                    "--environment=development",
                    "invoke",
                    "dbt-bigquery:run-operation",
                    "copy_bronze_tables",
                    "--args",
                    json.dumps(spec, separators=(",", ":")),
                ],
                env=copy_env,
                cwd=root,
                step="copy",
            )
        for environment in targets:
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
                    "fiscal_year_start_month": int(ctx.config.get("fiscal_year_start_month") or 1),
                },
                separators=(",", ":"),
            )
            ctx.run_command(
                [
                    meltano,
                    f"--environment={environment}",
                    "invoke",
                    "dbt-bigquery:run",
                    "--select",
                    DBT_SELECTOR,
                    "--vars",
                    dbt_vars,
                ],
                env=dbt_env,
                cwd=root,
                step="transform",
            )
        return RunResult(
            metrics={
                "bronze_table": bronze_tables[0],
                "bronze_tables": bronze_tables,
                "environments": environments,
            },
        )
