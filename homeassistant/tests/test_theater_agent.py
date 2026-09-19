"""Real HA lifecycle and local control; no household network I/O."""
import asyncio
import time
from copy import deepcopy
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
import pytest
from homeassistant import config_entries
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from custom_components.theater_agent.client import (
    TheaterClient, TheaterError, TheaterAuthError, TheaterUnknownError, normalize_url, validate_flags)
from custom_components.theater_agent.const import DOMAIN, FLAGS
from custom_components.theater_agent.coordinator import TheaterCommandError
from custom_components.home_butler.theater import LocalTheaterRelay

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")
SUMMARY = {"flags": {"kef_link": True, "tv_screen_auto": False, "tv_avr_sync": True},
           "health": {"api": "ok"}, "devices": {}, "logs": []}


@pytest.fixture
async def controller(hass):
    client = Mock(async_summary=AsyncMock(return_value=deepcopy(SUMMARY)), async_set_flags=AsyncMock())
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN,
        data={"url": "http://theater.invalid:8080", "api_key": "test-key"})
    entry.add_to_hass(hass)
    with patch("custom_components.theater_agent.TheaterClient", return_value=client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield entry, client
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


def switches(hass, entry):
    registry = er.async_get(hass)
    return {f: registry.async_get_entity_id("switch", DOMAIN, f"{entry.entry_id}_{f}") for f in FLAGS}


def frame(action="set_flags", payload=None):
    return {"type": "theater_command", "request_id": "a" * 32, "action": action,
        "payload": {"kef_link": False} if payload is None and action == "set_flags" else payload,
        "expires_at": time.time() + 30}


async def test_setup_without_render_or_writes(hass, controller):
    entry, client = controller
    ids = switches(hass, entry)
    assert len(set(ids.values())) == 3 and None not in ids.values()
    assert {f: hass.states.get(e).state for f, e in ids.items()} == {
        "kef_link": "on", "tv_screen_auto": "off", "tv_avr_sync": "on"}
    assert not hass.config_entries.async_entries("home_butler")
    client.async_set_flags.assert_not_called()
    assert client.async_summary.await_count == 1


async def test_switch_partial_write_then_readback(hass, controller):
    entry, client = controller
    client.async_summary.return_value["flags"]["kef_link"] = False
    await hass.services.async_call("switch", "turn_off", {
        "entity_id": switches(hass, entry)["kef_link"]}, blocking=True)
    client.async_set_flags.assert_awaited_once_with({"kef_link": False})
    assert client.async_summary.await_count == 2
    assert hass.states.get(switches(hass, entry)["kef_link"]).state == "off"


async def test_poll_change_failure_recovery(hass, controller):
    entry, client = controller
    client.async_summary.return_value["flags"]["tv_screen_auto"] = True
    await entry.runtime_data.async_refresh()
    assert hass.states.get(switches(hass, entry)["tv_screen_auto"]).state == "on"
    client.async_summary.side_effect = TheaterError("Offline")
    await entry.runtime_data.async_refresh()
    assert all(hass.states.get(e).state == "unavailable" for e in switches(hass, entry).values())
    client.async_summary.side_effect = None
    await entry.runtime_data.async_refresh()
    assert hass.states.get(switches(hass, entry)["tv_screen_auto"]).state == "on"
    client.async_set_flags.assert_not_called()


@pytest.mark.parametrize("phase", ["write", "read"])
async def test_unknown_never_replayed(hass, controller, phase):
    entry, client = controller
    if phase == "write":
        client.async_set_flags.side_effect = TheaterUnknownError("Unknown write")
    else:
        client.async_summary.side_effect = TheaterError("Read failed")
    with pytest.raises(TheaterCommandError) as error:
        await entry.runtime_data.async_set_flags({"kef_link": False})
    assert error.value.status == "unknown"
    assert hass.states.get(switches(hass, entry)["kef_link"]).state == "unavailable"
    client.async_summary.side_effect = None
    await entry.runtime_data.async_refresh()
    assert client.async_set_flags.await_count == 1


async def test_mismatch_publishes_actual_flags(hass, controller):
    entry, client = controller
    with pytest.raises(TheaterCommandError) as error:
        await entry.runtime_data.async_set_flags({"kef_link": False})
    assert error.value.status == "unknown"
    assert hass.states.get(switches(hass, entry)["kef_link"]).state == "on"
    client.async_set_flags.assert_awaited_once()


async def test_invalid_expired_busy_no_write(hass, controller):
    entry, client = controller
    for bad in ({}, {"kef_link": 1}, {"unknown": True}, None):
        with pytest.raises(TheaterCommandError):
            await entry.runtime_data.async_set_flags(bad)
    with pytest.raises(TheaterCommandError):
        await entry.runtime_data.async_set_flags({"kef_link": False}, expires_at=time.time()-1)
    async with entry.runtime_data.command_lock:
        with pytest.raises(TheaterCommandError, match="busy"):
            await entry.runtime_data.async_set_flags({"kef_link": False})
    client.async_set_flags.assert_not_called()


async def test_poll_and_write_lock(hass, controller):
    entry, client = controller
    started, release = asyncio.Event(), asyncio.Event()
    async def blocked_read():
        started.set()
        await release.wait()
        return deepcopy(SUMMARY)
    client.async_summary.side_effect = blocked_read
    poll = asyncio.create_task(entry.runtime_data.async_refresh())
    await started.wait()
    with pytest.raises(TheaterCommandError, match="busy"):
        await entry.runtime_data.async_set_flags({"kef_link": False})
    release.set()
    await poll
    client.async_set_flags.assert_not_called()


async def test_cancelled_write_unavailable(hass, controller):
    entry, client = controller
    client.async_set_flags.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await entry.runtime_data.async_set_flags({"kef_link": False})
    assert not entry.runtime_data.last_update_success
    assert not entry.runtime_data.command_lock.locked()


async def test_hb_shared_state_and_missing_entry(hass, controller):
    entry, client = controller
    client.async_summary.return_value["flags"]["kef_link"] = False
    relay = LocalTheaterRelay(hass, entry.entry_id)
    assert (await relay.execute(frame()))["status"] == "success"
    assert hass.states.get(switches(hass, entry)["kef_link"]).state == "off"
    assert (await relay.execute(frame("summary")))["result"] == client.async_summary.return_value
    assert (await LocalTheaterRelay(hass, "missing").execute(frame()))["status"] == "failed"
    client.async_set_flags.assert_awaited_once_with({"kef_link": False})


async def test_hb_unknown_and_allowlist(hass, controller):
    entry, client = controller
    relay = LocalTheaterRelay(hass, entry.entry_id)
    for bad in [frame("restart"), frame([]), frame(payload={"url": "http://other.invalid"}),
        {**frame(), "expires_at": time.time()-1}, {**frame(), "extra": True}, frame("summary", {"kef_link": True})]:
        assert (await relay.execute(bad))["status"] == "failed"
    client.async_set_flags.assert_not_called()
    client.async_set_flags.side_effect = TheaterUnknownError("Unknown write")
    assert (await relay.execute(frame()))["status"] == "unknown"
    client.async_set_flags.assert_awaited_once()


async def test_hb_options_clear_legacy(hass):
    local = MockConfigEntry(domain=DOMAIN, data={})
    local.add_to_hass(hass)
    hb = MockConfigEntry(domain="home_butler", data={"url": "https://example.invalid", "api_key": "x"*40},
        options={"theater_url": "http://old.invalid:8080", "theater_key": "old-key"})
    hb.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(hb.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {
        "theater_entry": local.entry_id, "theater_url": "http://old.invalid:8080", "theater_key": "old-key"})
    assert result["type"] == "create_entry"
    assert hb.options["theater_entry"] == local.entry_id
    assert hb.options["theater_url"] == hb.options["theater_key"] == ""


async def test_options_read_only_and_stable_entities(hass, controller):
    entry, client = controller
    before = switches(hass, entry)
    with patch("custom_components.theater_agent.config_flow.TheaterClient", return_value=client):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(result["flow_id"], {
            "url": "http://new-host.invalid:8080/", "api_key": "new-key"})
        await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    assert entry.options["url"] == "http://new-host.invalid:8080"
    assert switches(hass, entry) == before
    client.async_set_flags.assert_not_called()


@pytest.mark.parametrize("error,expected", [(TheaterAuthError("Auth"), "invalid_auth"),
    (TheaterError("Offline"), "cannot_connect"), (ValueError("Bad"), "invalid_input")])
async def test_config_errors(hass, error, expected):
    with patch("custom_components.theater_agent.config_flow.validate", side_effect=error):
        result = await hass.config_entries.flow.async_init(DOMAIN,
            context={"source": config_entries.SOURCE_USER}, data={"url": "http://theater.invalid", "api_key": "key"})
    assert result["errors"]["base"] == expected


async def test_reauth_updates_options(hass, controller):
    entry, client = controller
    with patch("custom_components.theater_agent.config_flow.TheaterClient", return_value=client):
        result = await hass.config_entries.flow.async_init(DOMAIN,
            context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id}, data=entry.data)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {
            "url": "http://theater.invalid:8080", "api_key": "replacement"})
        await hass.async_block_till_done()
    assert result["type"] == "abort" and result["reason"] == "reauth_successful"
    assert entry.data["api_key"] == entry.options["api_key"] == "replacement"
    client.async_set_flags.assert_not_called()


class Response:
    def __init__(self, data, status=200):
        self.status, self.data = status, data
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        return False
    async def json(self):
        if isinstance(self.data, Exception):
            raise self.data
        return self.data


async def test_client_fixed_paths():
    session = Mock()
    session.request.side_effect = [Response(deepcopy(SUMMARY)),
        Response({"success": True, "flags": {**SUMMARY["flags"], "kef_link": False}})]
    client = TheaterClient(session, "http://theater.invalid:8080/", "key")
    await client.async_summary()
    await client.async_set_flags({"kef_link": False})
    calls = session.request.call_args_list
    assert calls[0].args == ("GET", "http://theater.invalid:8080/summary")
    assert calls[1].args == ("POST", "http://theater.invalid:8080/flags")
    assert calls[1].kwargs["json"] == {"kef_link": False}
    assert all(call.kwargs["allow_redirects"] is False for call in calls)
    assert calls[1].kwargs["headers"] == {"x-api-key": "key"}


@pytest.mark.parametrize("status", [301, 400, 500])
async def test_client_post_unknown(status):
    session = Mock(request=Mock(return_value=Response({}, status)))
    with pytest.raises(TheaterUnknownError):
        await TheaterClient(session, "http://theater.invalid", "key").async_set_flags({"kef_link": False})
    assert session.request.call_count == 1


@pytest.mark.parametrize("data", [None, {}, {"flags": {"kef_link": 1}},
    {**SUMMARY, "health": None}, {**SUMMARY, "health": {"flags_error": "invalid configuration"}}])
async def test_client_bad_summary(data):
    with pytest.raises(TheaterError):
        await TheaterClient(Mock(request=Mock(return_value=Response(data))), "http://theater.invalid", "key").async_summary()


@pytest.mark.parametrize("error", [asyncio.TimeoutError(), aiohttp.ClientConnectionError("secret URL")])
async def test_client_safe_transport_errors(error):
    session = Mock(request=Mock(side_effect=error))
    with pytest.raises(TheaterUnknownError) as exc:
        await TheaterClient(session, "http://theater.invalid", "key").async_set_flags({"kef_link": False})
    assert "secret" not in str(exc.value)
    assert session.request.call_count == 1


def test_client_input_validation():
    for bad in ("ftp://host", "http://a:b@host", "http://host/a", "http://host?q=1", "http://host:bad", "http://ho st"):
        with pytest.raises(ValueError):
            normalize_url(bad)
    for bad in ({}, {"kef_link": 1}, {"unknown": True}):
        with pytest.raises(ValueError):
            validate_flags(bad)


@pytest.mark.parametrize("data", [None, {"success": False, "flags": SUMMARY["flags"]},
    {"success": True, "flags": {"kef_link": False}}, {"success": True, "flags": SUMMARY["flags"]},
    ValueError("Invalid JSON")])
async def test_client_unconfirmed_write_is_unknown(data):
    session = Mock(request=Mock(return_value=Response(data)))
    with pytest.raises(TheaterUnknownError):
        await TheaterClient(session, "http://theater.invalid", "key").async_set_flags({"kef_link": False})
    assert session.request.call_count == 1


@pytest.mark.parametrize("status", [401, 403])
async def test_client_auth_error(status):
    session = Mock(request=Mock(return_value=Response({}, status)))
    with pytest.raises(TheaterAuthError):
        await TheaterClient(session, "http://theater.invalid", "key").async_set_flags({"kef_link": False})
    assert session.request.call_count == 1


async def test_user_flow_is_read_only_and_single_entry(hass):
    client = Mock(async_summary=AsyncMock(return_value=deepcopy(SUMMARY)), async_set_flags=AsyncMock())
    with (patch("custom_components.theater_agent.config_flow.TheaterClient", return_value=client),
          patch("custom_components.theater_agent.async_setup_entry", return_value=True)):
        result = await hass.config_entries.flow.async_init(DOMAIN,
            context={"source": config_entries.SOURCE_USER}, data={"url": "http://theater.invalid/", "api_key": " key "})
        await hass.async_block_till_done()
        assert result["type"] == "create_entry"
        assert result["data"] == {"url": "http://theater.invalid", "api_key": "key"}
        second = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        assert second["type"] == "abort" and second["reason"] == "single_instance_allowed"
    client.async_set_flags.assert_not_called()


async def test_hb_re_resolves_reloaded_controller(hass, controller):
    entry, client = controller
    relay = LocalTheaterRelay(hass, entry.entry_id)
    old = entry.runtime_data
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data is not old
    with patch.object(old, "async_get_summary", side_effect=AssertionError("Stale controller")):
        assert (await relay.execute(frame("summary")))["status"] == "success"
    await hass.config_entries.async_unload(entry.entry_id)
    assert (await relay.execute(frame()))["status"] == "failed"
    # Restore entry for fixture cleanup; reloading never writes flags.
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    client.async_set_flags.assert_not_called()


async def test_local_offline_does_not_disable_home_butler(hass):
    local = MockConfigEntry(domain=DOMAIN, data={"url": "http://theater.invalid", "api_key": "key"})
    local.add_to_hass(hass)
    hb = MockConfigEntry(domain="home_butler", data={"url": "https://example.invalid", "api_key": "x"*40},
        options={"theater_entry": local.entry_id,
                 "theater_url": "http://legacy.invalid", "theater_key": "old-key"})
    hb.add_to_hass(hass)
    client = Mock(async_summary=AsyncMock(side_effect=TheaterError("Offline")))
    with (patch("custom_components.theater_agent.TheaterClient", return_value=client),
          patch("custom_components.home_butler.validate_connection", new=AsyncMock()),
          patch("custom_components.home_butler.OutboundLink.run", new=AsyncMock()),
          patch("custom_components.home_butler.TheaterRelay", side_effect=AssertionError("No fallback"))):
        assert not await hass.config_entries.async_setup(local.entry_id)
        assert await hass.config_entries.async_setup(hb.entry_id)
        await hass.async_block_till_done()
        link, _ = hass.data["home_butler"][hb.entry_id]
        assert isinstance(link.theater_commands, LocalTheaterRelay)
        assert (await link.theater_commands.execute(frame()))["status"] == "failed"
        assert await hass.config_entries.async_unload(hb.entry_id)
