"""Machines HA reads on the home network and passes on (computers.py). No network: the session is faked."""
import asyncio

import aiohttp
import pytest

from custom_components.home_butler import computers


def test_configuration_lines_are_checked():
    assert computers.parse("mac-mini=http://192.168.1.20:8792/metrics\n\n") == [
        {"source": "mac-mini", "url": "http://192.168.1.20:8792/metrics"}]
    assert computers.parse("") == []
    for bad in ("Mac=http://192.168.1.20:8792/metrics", "mac=https://192.168.1.20:8792/metrics",
                "mac=http://192.168.1.20:8792/other", "mac=http://192.168.1.20/metrics", "mac=http://8.8.8.8:8792/metrics",
                "mac=http://user:pw@192.168.1.20:8792/metrics", "mac=http://192.168.1.20:8792/metrics?x=1",
                "mac=http://192.168.1.20:8792/metrics\nmac=http://192.168.1.21:8792/metrics"):
        with pytest.raises(ValueError):
            computers.parse(bad)
    text = "a=http://10.0.0.1:1/metrics\nb=http://10.0.0.2:2/metrics"
    assert computers.unparse(computers.parse(text)) == text


class Answer:
    def __init__(self, status, body):
        self.status, self.body = status, body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self, content_type=None):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


class Session:
    def __init__(self):
        self.answers = []

    def get(self, url, timeout=None):
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


async def test_reports_are_passed_on_once_per_sample_and_dropped_when_old():
    session, notified, now = Session(), [], [1000]
    reader = computers.ComputerReader(session, [{"source": "mac-mini", "url": "http://10.0.0.1:8792/metrics"}],
                                      lambda: notified.append(1), clock=lambda: now[0])
    body = {"sampled_at": 990, "heartbeat": {"ip": "10.0.0.1", "cpu_pct": 1, "ram_pct": 2},
            "extras": {"watchdogs": [{"name": "funnel", "healthy": True}]}}
    session.answers = [Answer(200, body), Answer(200, body), Answer(200, {**body, "sampled_at": 1050})]
    await reader.read_once()
    await reader.read_once()
    assert len(notified) == 1                                   # same sample: nothing new to send
    assert reader.snapshot() == [{"source": "mac-mini", "sampled_at": 990, "heartbeat": body["heartbeat"],
                                  "watchdogs": [{"name": "funnel", "healthy": True}]}]
    await reader.read_once()
    assert len(notified) == 2
    now[0] = 1050 + 301
    assert reader.snapshot() == []                              # too old: the card should go grey


async def test_failures_keep_the_last_report_and_are_logged_once(caplog):
    session = Session()
    reader = computers.ComputerReader(session, [{"source": "mac-mini", "url": "http://10.0.0.1:8792/metrics"}],
                                      lambda: None, clock=lambda: 1000)
    session.answers = [Answer(200, {"sampled_at": 999, "heartbeat": {}}), Answer(403, None),
                       aiohttp.ClientError("down"), Answer(200, ValueError("not json"))]
    for _ in range(4):
        await reader.read_once()
    assert [r["sampled_at"] for r in reader.snapshot()] == [999]
    assert caplog.text.count("could not be read") == 1
