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


def _ctx(option: str | None, variant: str | None, secrets: dict[str, str], **config: object) -> RunContext:
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
        variant=variant,
    )


PORTAL = {
    "TAP_STR_PORTAL_USERNAME": "gm01",
    "TAP_STR_PORTAL_PASSWORD": "pw",
    "TAP_STR_PORTAL_TOTP_SECRET": "JBSWY3DPEHPK3PXP",
}


def test_manifest_offers_direct_then_hilton_lobby() -> None:
    m = _manifest()
    assert [o.key for o in m.auth_options] == ["direct", "hilton_lobby"]
    # Connections saved before options existed resolve to direct, as before.
    assert m.auth_option(None).key == "direct"
    lobby = m.auth_option("hilton_lobby")
    assert lobby.variant == "community"
    assert isinstance(lobby.auth, PasswordAuth)
    assert [f.env for f in lobby.auth.fields] == list(PORTAL)
    assert m.variant_streams["community"] == ["str_series", "comp_set_members", "comp_set_response"]


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


def test_selected_streams_fall_back_to_the_variant() -> None:
    assert selected_streams({"streams": ["daily"]}, "full") == ["daily"]
    # A community connection created with the old default ["daily"] still gets its streams.
    assert selected_streams({"streams": ["daily"]}, "community") == [
        "str_series",
        "comp_set_members",
        "comp_set_response",
    ]
    assert selected_streams({"streams": ["daily", "comp_set_members"]}, "community") == ["comp_set_members"]


def test_direct_env_matches_the_old_daily_run() -> None:
    ctx = _ctx("direct", "full", {"TAP_STR_USERNAME": "u", "TAP_STR_PASSWORD": "p"})
    env = tap_env(ctx, Path("/app/str"), ["daily"])
    assert env["TAP_STR_ACCESS_PATH"] == "direct"
    assert env["TAP_STR_VARIANT"] == "full"
    assert json.loads(env["TAP_STR__SELECT"]) == ["daily.*"]
    assert json.loads(env["TARGET_BIGQUERY_STREAM_MAPS"]) == {"daily": {"__alias__": "mlt_str__daily_1514908"}}
    assert "TAP_STR_MONTHLY_BEGIN_DATE" not in env


def test_hilton_lobby_env() -> None:
    ctx = _ctx("hilton_lobby", "community", PORTAL, community_views=["daily"], comp_set_id="1336406")
    streams = ["str_series", "comp_set_members", "comp_set_response"]
    env = tap_env(ctx, Path("/app/str"), streams)
    assert env["TAP_STR_ACCESS_PATH"] == "hilton_lobby"
    assert env["TAP_STR_VARIANT"] == "community"
    assert json.loads(env["TAP_STR__SELECT"]) == [f"{s}.*" for s in streams]
    assert json.loads(env["TARGET_BIGQUERY_STREAM_MAPS"])["str_series"] == {"__alias__": "mlt_str__str_series_1514908"}
    assert json.loads(env["TAP_STR_COMMUNITY_VIEWS"]) == ["daily"]
    assert env["TAP_STR_COMP_SET_ID"] == "1336406"
    assert env["TAP_STR_MONTHLY_BEGIN_DATE"] == "2024-01-01"
    # Secrets come from ctx.secret_env at run time, never from tap_env.
    assert not any(value in env.values() for value in PORTAL.values())


@pytest.mark.parametrize(
    ("option", "variant", "secrets", "message"),
    [
        ("direct", "full", {}, "CoStar username and password"),
        ("hilton_lobby", "community", {"TAP_STR_PORTAL_USERNAME": "gm01"}, "authenticator secret"),
    ],
)
def test_missing_secrets_ask_for_reauth(option, variant, secrets, message) -> None:
    with pytest.raises(ReauthRequired, match=message):
        tap_env(_ctx(option, variant, secrets), Path("/app/str"), ["daily"])
