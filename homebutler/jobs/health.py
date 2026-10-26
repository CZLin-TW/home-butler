"""Health alerts on LINE, every 5 minutes. They only watch; nothing here controls a device.

Checks (each on its own; one failing never skips the others):
- each computer that reports its health: silent for more than AGENT_OFFLINE_ALERT_SECONDS (15 min); one that
  reports through HA is not judged while HA is away (that alert covers it);
- Home Assistant's connection to this service: down for more than 15 minutes (new: HA is now the only
  way to the devices, so its absence matters more than any one computer);
- the theater relay: 3 failed status reads in a row, checked only while HA is online and offers it;
- presence: any occupancy sensor HA shares (the camera zones, the room groups) unavailable for more than
  15 minutes while HA itself is connected (2026-10-26: the camera sat waiting for a manual reconnect for
  19 hours and no one knew; the living room's automations were idle all that time);
- the daily backup: no success for 30 hours.

Only a change between fine and not fine is announced (LINE's free plan allows 200 pushes a month, and
these run 288 times a day). The marker ("ok" or "bad:<when first seen>") is stored after the push went
out, so a failed push is retried and the recovery message can say how long it lasted. A check seen for
the first time and fine says nothing. Recipients: members who asked for alerts, else everyone active.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from homebutler.domain import members, system_state
from homebutler.store.models import Computer

log = logging.getLogger("homebutler.jobs.health")
HA_GRACE = timedelta(minutes=15)
PRESENCE_GRACE = timedelta(minutes=15)
THEATER_STREAK = 3


def humanize(seconds) -> str:
    if seconds is None:
        return "未知時間"
    seconds = max(0, int(seconds))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    if days:
        return f"{days} 天 {hours} 小時"
    if hours:
        return f"{hours} 小時 {rest // 60} 分"
    return f"{rest // 60} 分鐘"


class Health:
    def __init__(self, store, link, push, zone, clock, offline_seconds: int = 900, backup=None, theater=None):
        """`push(line_user_id, text)` raises if not sent; `theater()` raises unless the relay answered."""
        self.store, self.link, self.push, self.zone, self.clock = store, link, push, zone, clock
        self.offline = timedelta(seconds=max(300, offline_seconds))
        self.backup, self.theater = backup, theater
        self.theater_failures = 0
        self.ha_lost_at: datetime | None = None
        self.presence_lost: dict[str, datetime] = {}     # sensor name -> first seen unavailable

    def _report(self, key: str, bad: bool, bad_text: str, ok_text) -> None:
        now = self.clock()
        with self.store.transaction() as s:
            previous = system_state.get(s, "health:" + key)
            recipients = [m.line_user_id for m in members.alert_recipients(s)]
        was_bad = previous.startswith("bad")
        if previous and was_bad == bad:
            return
        marker = f"bad:{int(now.timestamp())}" if bad else "ok"
        if previous or bad:
            outage = None
            if was_bad and not bad:
                try:
                    outage = now.timestamp() - float(previous.partition(":")[2])
                except ValueError:
                    pass
            text = bad_text if bad else ok_text(outage)
            delivered = 0
            for target in recipients:
                try:
                    self.push(target, text)
                    delivered += 1
                except Exception:
                    log.warning("alert to one member not delivered")
            if recipients and not delivered:
                return                         # nothing went out: try again next time
        with self.store.transaction() as s:
            system_state.set_(s, "health:" + key, marker)

    def computers(self) -> None:
        now = self.clock()
        with self.store.transaction() as s:
            found = [(c.id, c.hostname or "電腦", c.last_heartbeat_at, c.source) for c in s.scalars(select(Computer))]
        ha_up = self.link.online()
        for computer_id, name, last, source in found:
            if last is None or (source and not ha_up):
                continue                       # reported through HA: HA's own alert covers its absence
            silent = now - last
            self._report(f"computer:{computer_id}", silent > self.offline,
                         f"🔴 電腦監控失聯\n\n{name}\n最後回報：{last.astimezone(self.zone).strftime('%m/%d %H:%M')}"
                         f"（{humanize(silent.total_seconds())}前）\n\n處理：確認那台電腦開著、監控程式在執行",
                         lambda outage, name=name: f"🟢 電腦監控恢復\n\n{name}\n失聯約 {humanize(outage)}")

    def home_assistant(self) -> None:
        now = self.clock()
        if self.link.online():
            self.ha_lost_at = None
            self._report("home_assistant", False, "", lambda outage: f"🟢 Home Assistant 已重新連上\n\n中斷約 {humanize(outage)}")
            return
        self.ha_lost_at = self.ha_lost_at or now
        if now - self.ha_lost_at >= HA_GRACE:
            self._report("home_assistant", True,
                         "🔴 Home Assistant 已超過 15 分鐘沒有連上 HomeButler\n\n"
                         "這段期間看不到也控制不了家電，排程會等到恢復（超過兩小時的會過期）。\n"
                         "處理：確認家裡的網路與 Mac mini、HA 是否正常", lambda outage: "")

    def presence(self) -> None:
        if not self.link.online():
            self.presence_lost = {}        # HA's own alert covers this, and no sensor can be judged
            return
        now = self.clock()
        down = [o["name"] for o in self.link.state().get("observations", [])
                if o.get("kind") == "occupancy" and not o.get("available")]
        self.presence_lost = {name: self.presence_lost.get(name, now) for name in down}
        long = [name for name, since in self.presence_lost.items() if now - since >= PRESENCE_GRACE]
        if long:
            self._report("presence", True,
                         f"🔴 空間感測無法使用\n\n{'、'.join(long)}\n已超過 15 分鐘沒有資料，這段期間依它動作的自動化（開關燈、冷氣）不會觸發。\n\n"
                         "處理：打開 Floor Presence 頁面看主相機；顯示需要手動連線時，按「使用已儲存帳密連線」", lambda outage: "")
        elif not down:
            self._report("presence", False, "", lambda outage: f"🟢 空間感測恢復\n\n中斷約 {humanize(outage)}")

    def theater_relay(self) -> None:
        if self.theater is None or not self.link.can("theater_relay"):
            self.theater_failures = 0
            return
        try:
            self.theater()
            self.theater_failures, reason = 0, ""
        except Exception as error:
            self.theater_failures += 1
            reason = str(error)[:120] or type(error).__name__
            if self.theater_failures < THEATER_STREAK:
                return
        self._report("theater", bool(reason), f"🔴 劇院服務無回應\n\nHA 在線，但劇院狀態連續讀取失敗\n錯誤：{reason}",
                     lambda outage: f"🟢 劇院服務恢復\n\n無回應約 {humanize(outage)}")

    def backups(self) -> None:
        if self.backup is None:
            return
        reason = self.backup.last_error or "還沒有任何一次成功"
        self._report("backup", self.backup.stale(),
                     f"⚠️ HomeButler 的資料庫備份已超過 30 小時沒有成功（{reason}）。"
                     "請確認 Google 雲端硬碟的備份資料夾與 homebutler-backup.json.gz 還在、仍共用給服務帳號。",
                     lambda outage: "✅ HomeButler 的資料庫備份恢復正常。")

    def run(self) -> None:
        for check in (self.computers, self.home_assistant, self.presence, self.theater_relay, self.backups):
            try:
                check()
            except Exception:
                log.exception("health check %s failed", check.__name__)
