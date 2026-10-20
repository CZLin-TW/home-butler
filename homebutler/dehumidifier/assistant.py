"""The butler's dehumidifier actions (control, query, auto mode), with the old system's wording."""
from __future__ import annotations

from homebutler.dehumidifier import rules
from homebutler.dehumidifier.service import Dehumidifiers, Refused, norm


def _pick(service: Dehumidifiers, wanted, scope="single") -> tuple[list, str | None]:
    units = service.units()
    if not units:
        return [], "❌ 找不到除濕機設備"
    target = norm(wanted)
    if norm(scope) in ("all", "全部", "全家", "所有") or target in ("全部", "全家", "所有"):
        return units, None
    if target:
        exact = [u for u in units if norm(u.name) == target]
        if len(exact) == 1:
            return exact, None
        loose = [u for u in units if target in norm(u.name) or target == norm(u.location)
                 or (norm(u.location) and norm(u.location) in target)]
        if len(loose) == 1:
            return loose, None
        if loose:
            return [], f"❌ 找到多台可能的除濕機（{'、'.join(u.name for u in loose)}），請指定設備名稱"
        return [], f"❌ 找不到除濕機「{wanted}」，請確認設備名稱"
    if len(units) == 1:
        return units, None
    return [], f"❌ 有多台除濕機（{'、'.join(u.name for u in units)}），請指定要設定哪一台，或說「全家除濕機」"


def actions(service: Dehumidifiers) -> dict:
    def control(turn, d):
        found, error = _pick(service, d.get("device_name"))
        if error:
            return error
        name = found[0].name
        try:
            result = service.control(name, d.get("power") or ("on" if d.get("mode") or d.get("humidity") else None),
                                     d.get("mode"), d.get("humidity"))
        except Refused as problem:
            return f"❌ {problem}"
        return ("✅ " if result["status"] == "success" else "❌ ") + result["message"]

    def query(turn, d):
        found, error = _pick(service, d.get("device_name"))
        if error:
            return error
        unit = found[0]
        driver = service.driver(unit)
        status = driver.status() if driver else {"error": "缺少控制資訊"}
        if "error" in status:
            return f"❌ 無法取得{unit.name}狀態：{status['error']}"
        fields = driver.fields(status)
        return f"💧 {unit.name}：{'開啟' if fields['power'] else '關閉'}｜模式：{fields['mode'] or '未知'}｜目標濕度：{fields['target'] or '未設定'}"

    def auto(turn, d):
        on = str(d.get("auto_mode", "on")).strip().lower() not in ("off", "false", "0", "關", "關閉")
        threshold, duration = d.get("threshold"), d.get("duration_min")
        if on and threshold is not None and not rules.THRESHOLD_MIN <= int(threshold) <= rules.THRESHOLD_MAX:
            return "❌ 自動除濕目標濕度需介於 30%～80%"
        if duration is not None and float(duration) < 0:
            return "❌ 自動除濕等待時間不能小於 0 分鐘"
        found, error = _pick(service, d.get("device_name"), d.get("scope", "single"))
        if error:
            return error
        good, bad = [], []
        for unit in found:
            try:
                view = service.set_rule(unit.name, on, d.get("sensor_name") or None, int(duration) if duration is not None else None,
                                        int(threshold) if threshold is not None else None)
            except Refused as problem:
                bad.append(f"⚠️ {problem}")
                continue
            if not on:
                good.append(f"✅ {unit.name} 已關閉自動除濕模式")
                continue
            rule = view["rule"]
            wait = "立即" if rule["duration_min"] == 0 else f"持續 {rule['duration_min']} 分鐘"
            line = f"✅ {unit.name} 已開啟自動除濕模式，目標 {rule['threshold']}%，使用 {rule['sensor_name']}，{wait}"
            line += (f"，目前濕度 {view['sensor_humidity']}%" if view["sensor_humidity"] is not None
                     else "；感測器目前沒有即時濕度，會等下一次回報後開始判斷")
            good.append(line)
        if bad and not good:
            return "❌ 自動除濕模式未設定：\n" + "\n".join(bad)
        return "\n".join(good + bad) or "❌ 自動除濕模式未設定"

    def listed():
        return [{"name": u.name, "kind": "dehumidifier", "location": u.location} for u in service.units()]

    return {"control_dehumidifier": control, "query_dehumidifier": query, "set_dehumidifier_auto": auto, "_devices": listed}

