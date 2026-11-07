"""Supervisor's unhealthy reasons, read from HA's own Repairs list and passed on to HomeButler."""
import asyncio

from homeassistant.helpers import issue_registry as ir

from custom_components.home_butler import system_health
from custom_components.home_butler.transport import OutboundLink


async def test_only_active_supervisor_unhealthy_issues_are_reported(hass):
    assert system_health.unhealthy(hass) == []
    for domain, issue_id in [("hassio", "unhealthy_system_update_rollback"), ("hassio", "unhealthy_system_supervisor"),
                             ("hassio", "unsupported_system_os"), ("hassio", "unhealthy_system_Bad-Name"),
                             ("other", "unhealthy_system_docker")]:
        ir.async_create_issue(hass, domain, issue_id, is_fixable=False, severity=ir.IssueSeverity.CRITICAL,
                              translation_key="unhealthy")
    assert system_health.unhealthy(hass) == ["supervisor", "update_rollback"]
    ir.async_delete_issue(hass, "hassio", "unhealthy_system_supervisor")
    assert system_health.unhealthy(hass) == ["update_rollback"]


async def snapshots(capabilities):
    frames = []

    class Socket:
        reads = 0

        async def send_json(self, frame):
            frames.append(frame)

        async def receive_json(self):
            self.reads += 1
            if self.reads == 1:
                return {"type": "hello_ack", "protocol": 1, "capabilities": capabilities}
            link.closed = True
            return {"type": "snapshot_ack", "sequence": 1, "accepted": True}

    class Connection:
        async def __aenter__(self):
            return Socket()

        async def __aexit__(self, *args):
            return False

    class Session:
        def ws_connect(self, url, **kwargs):
            return Connection()

    link = OutboundLink(Session(), "https://example.invalid", "x" * 40, lambda: [],
                        system_health=lambda: ["update_rollback"])
    await asyncio.wait_for(link.run(), 2)
    return [f for f in frames if f["type"] == "snapshot"]


async def test_reasons_go_only_to_a_backend_that_accepts_them():
    assert (await snapshots(["system_health"]))[0]["unhealthy"] == ["update_rollback"]
    assert "unhealthy" not in (await snapshots([]))[0]
