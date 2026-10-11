"""The automation report is opt-in, display-only, and can never produce a frame the backend rejects."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.home_butler.automations import AutomationLog, MAX_RUNS
from custom_components.home_butler.const import DOMAIN
from custom_components.home_butler.transport import OutboundLink

WHEN = datetime(2026, 10, 11, 7, 5, tzinfo=timezone.utc)


def event(entity_id="automation.demo_light", name="有人就開燈", source="state of binary_sensor.demo", fired=WHEN):
    return SimpleNamespace(data={"entity_id": entity_id, "name": name, "source": source}, time_fired=fired)


def states(*items):
    return SimpleNamespace(states=SimpleNamespace(async_all=lambda domain: list(items) if domain == "automation" else []))


def rule(entity_id, name, state="on", last=WHEN):
    return SimpleNamespace(entity_id=entity_id, name=name, state=state, attributes={"last_triggered": last})


def test_runs_are_bounded_and_unusable_events_are_dropped():
    log = AutomationLog(states())
    assert log.record(event())
    assert log.snapshot()["runs"] == [{"entity_id": "automation.demo_light", "name": "有人就開燈",
                                      "at": WHEN.timestamp(), "source": "state of binary_sensor.demo"}]
    for bad in (event(entity_id="light.demo"), event(entity_id="automation.Bad-Id"), event(entity_id=None),
                event(name=""), event(fired=None), SimpleNamespace(data=None, time_fired=WHEN)):
        assert not log.record(bad)
    assert log.record(event(name="名" * 400, source=None))
    last = log.snapshot()["runs"][-1]
    assert (len(last["name"]), last["source"]) == (160, "")
    for number in range(MAX_RUNS + 5):
        log.record(event(name=str(number)))
    runs = log.snapshot()["runs"]
    assert len(runs) == MAX_RUNS and runs[-1]["name"] == str(MAX_RUNS + 4)


def test_rules_report_enabled_state_and_tolerate_odd_values():
    log = AutomationLog(states(rule("automation.b", "關燈", "off", None), rule("automation.a", "開燈"),
                               rule("automation.c", "", "unavailable", "not a time"), rule("switch.x", "不是自動化")))
    assert log.snapshot()["rules"] == [
        {"entity_id": "automation.a", "name": "開燈", "enabled": True, "last_triggered": WHEN.timestamp()},
        {"entity_id": "automation.b", "name": "關燈", "enabled": False, "last_triggered": None},
        {"entity_id": "automation.c", "name": "automation.c", "enabled": False, "last_triggered": None}]


async def test_report_is_sent_only_to_a_backend_that_accepts_it():
    for capabilities, expected in ((["observations", "automation_log"], True), (["observations"], False), (None, False)):
        frames = []

        class Socket:
            reads = 0

            async def send_json(self, frame):
                frames.append(frame)

            async def receive_json(self):
                self.reads += 1
                if self.reads == 1:
                    return {"type": "hello_ack", "protocol": 1, **({"capabilities": capabilities} if capabilities else {})}
                link.closed = True
                return {"type": "snapshot_ack", "sequence": 1, "accepted": True}

        class Connection:
            async def __aenter__(self):
                return Socket()

            async def __aexit__(self, *args):
                return False

        link = OutboundLink(SimpleNamespace(ws_connect=lambda url, **kwargs: Connection()), "https://example.invalid",
                            "x" * 40, lambda: [], automations=lambda: {"rules": [], "runs": []})
        real_sleep = asyncio.sleep
        with patch("custom_components.home_butler.transport.asyncio.sleep", lambda delay: real_sleep(0)):
            await asyncio.wait_for(link.run(), 2)
        snapshot = next(frame for frame in frames if frame["type"] == "snapshot")
        assert ("automations" in snapshot) is expected
        if expected:
            assert snapshot["automations"] == {"rules": [], "runs": []}


async def test_sharing_is_off_until_chosen_and_then_follows_triggers(hass, enable_custom_integrations):
    from custom_components.home_butler import async_setup_entry, async_unload_entry
    hass.states.async_set("automation.demo_light", "on", {"friendly_name": "有人就開燈"})
    for chosen in (False, True):
        entry = MockConfigEntry(domain=DOMAIN, data={"url": "https://example.invalid", "api_key": "x" * 40, "sources": []},
                                options={"automation_log": chosen} if chosen else {})
        entry.add_to_hass(hass)
        with (patch("custom_components.home_butler.validate_connection", new=AsyncMock()),
              patch("custom_components.home_butler.OutboundLink.run", new=AsyncMock())):
            assert await async_setup_entry(hass, entry)
            link = hass.data[DOMAIN][entry.entry_id][0]
            assert (link.automations is not None) is chosen
            link.changed.clear()
            hass.bus.async_fire("automation_triggered", {"entity_id": "automation.demo_light", "name": "有人就開燈", "source": "state"})
            await hass.async_block_till_done()
            assert link.changed.is_set() is chosen
            if chosen:
                report = link.automations()
                assert [run["name"] for run in report["runs"]] == ["有人就開燈"]
                assert report["rules"] == [{"entity_id": "automation.demo_light", "name": "有人就開燈", "enabled": True, "last_triggered": None}]
            assert await async_unload_entry(hass, entry)


async def test_option_is_saved_and_kept_when_the_form_omits_it(hass, enable_custom_integrations):
    entry = MockConfigEntry(domain=DOMAIN, data={"url": "https://example.invalid", "api_key": "x" * 40, "sources": []})
    entry.add_to_hass(hass)
    for sent, expected in (({"automation_log": True}, True), ({}, True), ({"automation_log": False}, False)):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"export_entities": [], **sent})
        assert result["type"] == "create_entry"
        assert entry.options["automation_log"] is expected
