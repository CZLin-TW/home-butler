"""One interface over both brands, so the auto rule never deals with brand details.

In auto mode the machine must not stop by its own reading (the air around it is drier than the room):
Panasonic runs 連續除濕, which ignores its own target; LG runs 智慧除濕 (its 快速除濕 falls back to smart
mode by itself, which looked like a person changing it) with its own target set 10% below the rule's.

`expected_on`/`expected_off` are what the machine should report after this service's command;
`diverged` compares that with what it does report, to notice a person taking over.
"""
from __future__ import annotations

from homebutler.dehumidifier import lg, panasonic

LG_AUTO_MODE = "智慧除濕"
LG_AUTO_OFFSET = 10
PANASONIC_AUTO_MODE = "連續除濕"


def diverged(expected: dict | None, actual: dict | None) -> bool:
    """Power differs; or, while on, the mode or a target we set differs. A value the machine did not
    report never counts."""
    if expected is None or actual is None:
        return False
    if expected.get("power") != actual.get("power"):
        return True
    if actual.get("power"):
        if expected.get("mode") is not None and actual.get("mode") and actual["mode"] != expected["mode"]:
            return True
        if expected.get("target") is not None and actual.get("target") is not None and actual["target"] != expected["target"]:
            return True
    return False


def ok(result: dict) -> bool:
    return bool(result.get("success"))


def outcome(results: list[dict]) -> str:
    """success / failed / unknown for a series of commands (stopped at the first that did not succeed)."""
    for result in results:
        if not ok(result):
            return "unknown" if result.get("uncertain") else "failed"
    return "success"


class PanasonicDriver:
    def __init__(self, client: panasonic.PanasonicClient, auth: str, gwid: str):
        self.client, self.auth, self.gwid = client, auth, gwid

    def status(self) -> dict:
        return self.client.status(self.auth, self.gwid)

    def read(self, status: dict) -> dict:
        return {"power": status.get("0x00") == "1", "mode": str(status.get("0x01", "")), "target": None}

    def fields(self, status: dict) -> dict:
        """For the card: power, mode label, the machine's own target ("55%")."""
        return {"power": status.get("0x00") == "1", "mode": panasonic.MODE_LABELS.get(str(status.get("0x01", "")), ""),
                "target": panasonic.HUMIDITY_LABELS.get(str(status.get("0x04", "")), "")}

    def expected_on(self, threshold: int) -> dict:
        return {"power": True, "mode": str(panasonic.MODES[PANASONIC_AUTO_MODE]), "target": None}

    def expected_off(self) -> dict:
        return {"power": False, "mode": None, "target": None}

    def fire_on(self, threshold: int) -> list[dict]:
        first = self.client.power(self.auth, self.gwid, True)
        return [first, self.client.set_mode(self.auth, self.gwid, PANASONIC_AUTO_MODE)] if ok(first) else [first]

    def fire_off(self) -> list[dict]:
        return [self.client.power(self.auth, self.gwid, False)]

    def align(self, threshold: int) -> list[dict]:
        """Only the mode, never the power."""
        return [self.client.set_mode(self.auth, self.gwid, PANASONIC_AUTO_MODE)]

    def power(self, on: bool) -> dict:
        return self.client.power(self.auth, self.gwid, on)

    def set_mode(self, mode: str) -> dict:
        return self.client.set_mode(self.auth, self.gwid, mode)

    def set_humidity(self, humidity: int) -> dict:
        return self.client.set_humidity(self.auth, self.gwid, humidity)


class LGDriver:
    def __init__(self, client: lg.LGClient, device_id: str):
        self.client, self.device_id = client, device_id

    def status(self) -> dict:
        return self.client.status(self.device_id)

    def read(self, status: dict) -> dict:
        target = lg.dig(status, lg.TARGET)
        try:
            target = int(target) if target is not None else None
        except (TypeError, ValueError):
            target = None
        return {"power": lg.dig(status, lg.POWER) == lg.POWER_ON, "mode": lg.dig(status, lg.JOB_MODE), "target": target}

    def fields(self, status: dict) -> dict:
        target, mode = lg.dig(status, lg.TARGET), lg.dig(status, lg.JOB_MODE)
        return {"power": lg.dig(status, lg.POWER) == lg.POWER_ON, "mode": lg.MODE_LABELS.get(mode, mode or ""),
                "target": f"{target}%" if target is not None else ""}

    def auto_target(self, threshold: int) -> int:
        return lg.snap(int(threshold) - LG_AUTO_OFFSET)

    def expected_on(self, threshold: int) -> dict:
        return {"power": True, "mode": lg.MODES[LG_AUTO_MODE], "target": self.auto_target(threshold)}

    def expected_off(self) -> dict:
        return {"power": False, "mode": None, "target": None}

    def fire_on(self, threshold: int) -> list[dict]:
        first = self.client.power(self.device_id, True)
        return [first, *self.align(threshold)] if ok(first) else [first]

    def fire_off(self) -> list[dict]:
        return [self.client.power(self.device_id, False)]

    def align(self, threshold: int) -> list[dict]:
        mode = self.client.set_mode(self.device_id, LG_AUTO_MODE)
        return [mode, self.client.set_humidity(self.device_id, self.auto_target(threshold))] if ok(mode) else [mode]

    def power(self, on: bool) -> dict:
        return self.client.power(self.device_id, on)

    def set_mode(self, mode: str) -> dict:
        return self.client.set_mode(self.device_id, mode)

    def set_humidity(self, humidity: int) -> dict:
        return self.client.set_humidity(self.device_id, humidity)
