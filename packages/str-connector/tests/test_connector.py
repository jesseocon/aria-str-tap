"""STR connector manifest and the environment each login option gets."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from aria_runner.connectors import ConnectorManifest, PasswordAuth, validate_config
from aria_runner.sdk import ReauthRequired, RunContext

from str_connector.manifest import COMMUNITY_VIEWS, manifest
from str_connector.runtime import selected_streams, tap_env


def _manifest() -> ConnectorManifest:
    return ConnectorManifest.model_validate(manifest())


def _ctx(option: str | None, secrets: dict[str, str], **config: object) -> RunContext:
    return RunContext(
        run_id="r1",
        org_id="org",
        connection_id="c1",
        connector_key="str",
        config={"org_id": "42", "property_id": "1514908", "development_property_id": "p1", **config},
        params={},
        state={},
        work_dir=Path("/tmp"),
        session_path=None,
        logger=logging.getLogger("test"),
        secret_env=secrets,
        auth_option=option,
    )


PORTAL = {
    "TAP_STR_PORTAL_USERNAME": "gm01",
    "TAP_STR_PORTAL_PASSWORD": "pw",
    "TAP_STR_PORTAL_TOTP_SECRET": "JBSWY3DPEHPK3PXP",
}
DIRECT = {"TAP_STR_USERNAME": "u", "TAP_STR_PASSWORD": "p"}
STREAMS = ["str_series", "comp_set_members", "comp_set_response"]


def test_manifest_offers_direct_then_hilton_lobby_for_one_product() -> None:
    m = _manifest()
    assert [o.key for o in m.auth_options] == ["direct", "hilton_lobby"]
    # Connections saved before options existed resolve to direct, as before.
    assert m.auth_option(None).key == "direct"
    # Both ways in land on the same STR app, so neither picks a product variant.
    assert all(o.variant is None for o in m.auth_options)
    assert m.variant_streams == {}
    lobby = m.auth_option("hilton_lobby")
    assert isinstance(lobby.auth, PasswordAuth)
    assert [f.env for f in lobby.auth.fields] == list(PORTAL)
    assert manifest()["config_schema"]["properties"]["streams"]["default"] == STREAMS


def test_portfolio_marts_are_the_community_gold_models() -> None:
    marts = Path(__file__).parents[3] / "transform" / "models" / "marts"
    community = sorted(p.stem for p in marts.glob("mlt_mart__benchmark__*.sql"))
    assert sorted(manifest()["portfolio_marts"]) == community


def test_config_accepts_community_settings() -> None:
    config = validate_config(
        _manifest(),
        {
            "org_id": "42",
            "property_id": "1514908",
            "development_property_id": "p1",
            "streams": ["str_series", "comp_set_members"],
            "community_views": ["daily", "summary"],
            "comp_set_id": "1336406",
        },
    )
    assert config["monthly_begin_date"] == "2024-01-01"
    assert set(COMMUNITY_VIEWS) >= set(config["community_views"])


def test_selected_streams_fall_back_to_all_of_them() -> None:
    # A connection saved with the retired full stream ["daily"] gets every stream.
    assert selected_streams({"streams": ["daily"]}) == STREAMS
    assert selected_streams({}) == STREAMS
    assert selected_streams({"streams": ["daily", "comp_set_members"]}) == ["comp_set_members"]


@pytest.mark.parametrize(("option", "secrets"), [("direct", DIRECT), ("hilton_lobby", PORTAL)])
def test_every_login_gets_the_same_streams_and_settings(option, secrets) -> None:
    ctx = _ctx(option, secrets, community_views=["daily"], comp_set_id="1336406")
    env = tap_env(ctx, Path("/app/str"), STREAMS)
    assert env["TAP_STR_ACCESS_PATH"] == option
    assert "TAP_STR_VARIANT" not in env
    assert json.loads(env["TAP_STR__SELECT"]) == [f"{s}.*" for s in STREAMS]
    assert json.loads(env["TARGET_BIGQUERY_STREAM_MAPS"])["str_series"] == {"__alias__": "mlt_str__str_series_1514908"}
    assert json.loads(env["TAP_STR_COMMUNITY_VIEWS"]) == ["daily"]
    assert env["TAP_STR_COMP_SET_ID"] == "1336406"
    assert env["TAP_STR_MONTHLY_BEGIN_DATE"] == "2024-01-01"
    # Secrets come from ctx.secret_env at run time, never from tap_env.
    assert not any(value in env.values() for value in secrets.values())


@pytest.mark.parametrize(("option", "secrets"), [("direct", DIRECT), ("hilton_lobby", PORTAL), (None, DIRECT)])
def test_run_loads_then_builds_the_str_models(option, secrets, tmp_path, monkeypatch) -> None:
    from str_connector.runtime import StrConnector

    monkeypatch.setenv("STR_PROJECT_ROOT", str(tmp_path))
    commands: list[list[str]] = []
    ctx = _ctx(option, secrets, fiscal_year_start_month=7, streams=["daily"])
    ctx.run_command = lambda cmd, **_kw: commands.append(cmd)  # type: ignore[method-assign]
    result = StrConnector().run(ctx)

    load, dbt = commands
    assert load[-3:] == ["run", "tap-str", "target-bigquery"]
    assert dbt[dbt.index("--select") + 1] == "tag:str"
    dbt_vars = json.loads(dbt[dbt.index("--vars") + 1])
    assert dbt_vars["property_id"] == "p1"
    assert dbt_vars["costar_property_id"] == "1514908"
    assert dbt_vars["fiscal_year_start_month"] == 7
    assert result.metrics["bronze_tables"] == [f"bronze_42.mlt_str__{s}_1514908" for s in STREAMS]


@pytest.mark.parametrize(
    ("option", "secrets", "message"),
    [
        ("direct", {}, "CoStar username and password"),
        ("hilton_lobby", {"TAP_STR_PORTAL_USERNAME": "gm01"}, "authenticator secret"),
    ],
)
def test_missing_secrets_ask_for_reauth(option, secrets, message) -> None:
    with pytest.raises(ReauthRequired, match=message):
        tap_env(_ctx(option, secrets), Path("/app/str"), STREAMS)


def test_run_names_its_steps_and_declares_the_progress_contract(tmp_path, monkeypatch) -> None:
    from str_connector.runtime import StrConnector

    monkeypatch.setenv("STR_PROJECT_ROOT", str(tmp_path))
    steps: list[str | None] = []
    ctx = _ctx("direct", DIRECT)
    ctx.run_command = lambda _cmd, **kw: steps.append(kw.get("step"))  # type: ignore[method-assign]
    StrConnector().run(ctx)
    assert steps == ["extract", "transform"]
    assert _manifest().progress == "v1"


def test_both_warehouses_copy_bronze_then_build_each(tmp_path, monkeypatch) -> None:
    from str_connector.runtime import StrConnector

    monkeypatch.setenv("STR_PROJECT_ROOT", str(tmp_path))
    runs: list[tuple[list[str], str | None]] = []
    ctx = _ctx("direct", DIRECT, environments=["production", "development"], production_property_id="p2")
    ctx.run_command = lambda cmd, **kw: runs.append((cmd, kw.get("step")))  # type: ignore[method-assign]
    StrConnector().run(ctx)

    assert [step for _cmd, step in runs] == ["extract", "copy", "transform", "transform"]
    load, copy, dev, prod = (cmd for cmd, _step in runs)
    assert "--environment=development" in load
    spec = json.loads(copy[-1])
    assert spec == {
        "source_project": "hospitalityops-dev",
        "dest_project": "hospitalityops",
        "dataset": "bronze_42",
        "tables": [f"mlt_str__{s}_1514908" for s in STREAMS],
    }
    assert "--environment=development" in dev and json.loads(dev[-1])["property_id"] == "p1"
    assert "--environment=production" in prod
    prod_vars = json.loads(prod[-1])
    assert (prod_vars["bq_project"], prod_vars["property_id"]) == ("hospitalityops", "p2")


def test_production_only_scrapes_straight_into_production(tmp_path, monkeypatch) -> None:
    from str_connector.runtime import StrConnector

    monkeypatch.setenv("STR_PROJECT_ROOT", str(tmp_path))
    runs: list[tuple[list[str], str | None]] = []
    ctx = _ctx("direct", DIRECT, environments=["production"], production_property_id="p2")
    ctx.run_command = lambda cmd, **kw: runs.append((cmd, kw.get("step")))  # type: ignore[method-assign]
    StrConnector().run(ctx)

    assert [step for _cmd, step in runs] == ["extract", "transform"]
    assert all("--environment=production" in cmd for cmd, _step in runs)
