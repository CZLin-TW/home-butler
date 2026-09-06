"""Standalone paid intent evaluation. Never imports app/config/handlers or Sheets.

prepare is offline; run explicitly sends at most 120 non-retried Messages calls.
The durable ledger claims a trial BEFORE networking; interrupted/failed trials
are never resent. Report includes model output text, never thinking or API keys.
"""

import argparse
import ast
from collections import Counter
from datetime import datetime, timezone
import hashlib
import itertools
import json
import os
from pathlib import Path
import random
import statistics
import sys
import time
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from device_voice import SYSTEM, SCHEMA, device_catalog, validate_actions, VoicePolicyError
from evals.sonnet_cases import CASES, ROWS

MODEL = "claude-sonnet-5"
LIMIT = 120
SEED = 20260906
GROUPS = {
    "A": {"thinking": {"type": "adaptive"}, "effort": "high"},
    "B": {"thinking": {"type": "adaptive"}, "effort": "medium"},
    "C": {"thinking": {"type": "disabled"}, "effort": "medium"},
}
PRICING = {"input": 2, "output": 10, "cache_read": 0.2, "cache_5m": 2.5, "cache_1h": 4}
PRICE_SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def prompt_constants():
    # Only compile whitelisted constant assignments; importing prompt.py would
    # import Sheets/config/SDKs. No production service is started by this harness.
    names = {"SYSTEM_PROMPT", "ACTION_SCHEMA", "ACTION_NAMES", "ARG_KEY_TYPES", "DEFAULT_STYLE"}
    tree = ast.parse((ROOT / "prompt.py").read_text(encoding="utf-8"))
    nodes = [n for n in tree.body if isinstance(n, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id in names for t in n.targets)]
    env = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "<prompt constants>", "exec"), env)
    return env


def request_base(case):
    if case["route"] == "devices":
        return {"model": MODEL, "max_tokens": 2000, "system": SYSTEM,
                "messages": [{"role": "user", "content": json.dumps(
                    {"devices": device_catalog(ROWS), "request": case["text"]}, ensure_ascii=False)}],
                "output_config": {"format": {"type": "json_schema", "schema": SCHEMA}}}
    const = prompt_constants()
    device_info = "、".join(
        f"{r['名稱']}（類型：{r['類型']}，位置：{r['位置']}"
        + (f"，按鈕：{r['按鈕']}" if r.get("按鈕") else "")
        + (f"，控制類型：{r['控制類型']}" if r.get("控制類型") else "") + "）" for r in ROWS)
    system = const["SYSTEM_PROMPT"].format(
        user_style=const["DEFAULT_STYLE"], family_info="測試使用者（測試帳號）；測試家人（測試帳號）",
        current_user="測試使用者", food_info="牛奶：2 瓶，2026-09-10 到期（模擬資料）",
        todo_info="測試使用者：吃藥，2026-09-07 08:00，私人（每天週期產生；模擬資料）",
        device_info=device_info, lighting_info="客廳、主臥",
        schedule_info="主臥冷氣：control_ac，power=off，2026-09-06 22:00，待執行（模擬資料）",
        today="2026-09-06（日）", now_time="19:00", app_version="1.39.0")
    return {"model": MODEL, "max_tokens": 4000, "system": system,
            "messages": [{"role": "user", "content": case["text"]}],
            "output_config": {"format": {"type": "json_schema", "schema": const["ACTION_SCHEMA"]}}}


def make_manifest():
    bases = {case["id"]: request_base(case) for case in CASES}
    trials = []
    # Six permutations balanced across cases; shuffled cases avoid a fixed
    # simple-to-hard sequence. Sequential execution avoids concurrent contention.
    permutations = list(itertools.permutations(GROUPS))
    rng = random.Random(SEED)
    for repeat in range(2):
        cases = list(CASES)
        rng.shuffle(cases)
        for i, case in enumerate(cases):
            order = permutations[(i + repeat * 3) % len(permutations)]
            for group in order:
                body = json.loads(json.dumps(bases[case["id"]]))
                body["thinking"] = GROUPS[group]["thinking"]
                body["output_config"]["effort"] = GROUPS[group]["effort"]
                trials.append({"id": f"{case['id']}-{repeat + 1}-{group}", "case_id": case["id"],
                               "repeat": repeat + 1, "group": group, "request": body})
    assert len(trials) == LIMIT
    source_files = ["prompt.py", "device_voice.py", "evals/sonnet_cases.py", "evals/sonnet_effort.py"]
    return {"model": MODEL, "limit": LIMIT, "seed": SEED, "cases": CASES, "groups": GROUPS,
            "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in source_files},
            "pricing_usd_per_million": PRICING, "pricing_source": PRICE_SOURCE, "pricing_checked": "2026-09-06",
            "trials": trials}


def schema_matches(value, schema):
    """Validate the exact object/array/string/enum subset used by both schemas."""
    kind = schema["type"]
    if kind == "object":
        props = schema["properties"]
        return (isinstance(value, dict) and set(schema.get("required", [])) <= set(value)
                and not set(value) - set(props)
                and all(schema_matches(v, props[k]) for k, v in value.items()))
    if kind == "array":
        return isinstance(value, list) and all(schema_matches(v, schema["items"]) for v in value)
    if kind == "string":
        return isinstance(value, str) and ("enum" not in schema or value in schema["enum"])
    raise ValueError(f"Unsupported schema type: {kind}")


def normalized(key, value):
    value = unicodedata.normalize("NFC", value).strip()
    if key == "device_name" and value.endswith("電風扇"):
        return value[:-3] + "電扇"
    if key == "weekdays":
        return ",".join(sorted(x.strip() for x in value.strip("[]").split(",")))
    return value


def flatten(entry):
    args = entry["args"]
    if len({arg["key"] for arg in args}) != len(args):
        raise ValueError("duplicate argument")
    return {"action": entry["action"], "data": {a["key"]: normalized(a["key"], a["value"]) for a in args}}


def matches(actual, expected):
    if actual["action"] != expected["action"]:
        return False
    data, required, optional = actual["data"], expected["required"], expected["optional"]
    return (set(required) <= set(data) and not set(data) - set(required) - set(optional)
            and all(data[k] == normalized(k, v) for k, v in required.items())
            and all(data[k] == normalized(k, optional[k]) for k in set(data) - set(required)))


def score(case, response):
    text = "".join(b.get("text", "") for b in response.get("content", []) if b.get("type") == "text")
    result = {"pass": False, "schema_valid": False, "reason": "", "text": text,
              "stop_reason": response.get("stop_reason"), "policy_accepted": None}
    if response.get("stop_reason") != "end_turn":
        result["reason"] = "incomplete_or_refused_response"
        return result
    try:
        parsed = json.loads(text)
        schema = request_base(case)["output_config"]["format"]["schema"]
        if not schema_matches(parsed, schema):
            raise ValueError("schema")
        result["schema_valid"] = True
        actions = [flatten(entry) for entry in parsed["actions"]]
    except (ValueError, TypeError, KeyError):
        result["reason"] = "invalid_json_schema_or_duplicate_args"
        return result
    result["actions"] = actions
    if case["route"] == "devices":
        try:
            validate_actions(parsed, ROWS)
            result["policy_accepted"] = True
        except VoicePolicyError:
            result["policy_accepted"] = False
    outcome = case["outcome"]
    if outcome == "actions":
        expected = case["expected"]
        result["pass"] = len(actions) == len(expected) and any(
            all(matches(a, e) for a, e in zip(order, expected)) for order in itertools.permutations(actions))
        if case["route"] == "devices":
            result["pass"] = result["pass"] and result["policy_accepted"]
    elif outcome in {"clarify", "confirm_stop"}:
        if case["route"] == "devices":
            result["pass"] = bool(actions) and all(a["action"] == "unclear" and not a["data"] for a in actions)
        else:
            clarification = parsed["reply"] + " ".join(a["data"].get("message", "") for a in actions)
            result["pass"] = all(a["action"] == "unclear" for a in actions) and any(
                marker in clarification for marker in ("？", "?", "請問", "確認", "是否"))
            if outcome == "confirm_stop":
                result["pass"] = result["pass"] and any(w in clarification for w in ("吃藥", "週期", "永久"))
    elif outcome == "unsupported":
        result["pass"] = bool(actions) and all(a["action"] == "unsupported" and not a["data"] for a in actions)
    elif outcome == "unknown_device":
        # Production asks model to retain unknown names and lets policy reject;
        # either that route or a clarification is acceptable, never another room.
        result["pass"] = len(actions) == 1 and not result["policy_accepted"] and (
            actions[0]["action"] == "unclear" or matches(actions[0], expect_unknown_device()))
    result["reason"] = "ok" if result["pass"] else "intent_or_parameters_mismatch"
    return result


def expect_unknown_device():
    return {"action": "control_ir", "required": {"device_name": "書房電扇", "button": "開"}, "optional": {}}


def usage_cost(usage):
    if not isinstance(usage, dict) or "input_tokens" not in usage or "output_tokens" not in usage:
        return None
    cache = usage.get("cache_creation") or {}
    created = usage.get("cache_creation_input_tokens", 0)
    one_hour = cache.get("ephemeral_1h_input_tokens", 0)
    return (usage["input_tokens"] * PRICING["input"] + usage["output_tokens"] * PRICING["output"]
            + usage.get("cache_read_input_tokens", 0) * PRICING["cache_read"]
            + (created - one_hour) * PRICING["cache_5m"] + one_hour * PRICING["cache_1h"]) / 1_000_000


def append_event(path, event):
    with path.open("a", encoding="utf-8") as out:
        out.write(json.dumps(event, ensure_ascii=False) + "\n")
        out.flush()
        os.fsync(out.fileno())


def read_events(path):
    if not path.exists():
        return []
    # Fail closed on damaged ledger; do not silently skip a possibly claimed call.
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def api_key():
    value = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not value and sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                value = winreg.QueryValueEx(key, "ANTHROPIC_API_KEY")[0].strip()
        except FileNotFoundError:
            pass
    return value


def prepare(out):
    out.mkdir(parents=True, exist_ok=True)
    manifest = make_manifest()
    target = out / "manifest.json"
    if target.exists():
        saved = json.loads(target.read_text(encoding="utf-8"))
        if digest(saved) != digest(manifest):
            raise RuntimeError("Manifest/source changed; preserve ledger and review before continuing")
    else:
        with target.open("x", encoding="utf-8") as file:
            json.dump(manifest, file, ensure_ascii=False, indent=2)
    return manifest


def run(out, manifest, *, supplied_key=None):
    # A supplied secret stays in this process and never enters os.environ/files.
    key = api_key() if supplied_key is None else supplied_key.strip()
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured; zero new API calls")
    import httpx
    ledger = out / "attempts.jsonl"
    events = read_events(ledger)
    claimed = [e["id"] for e in events if e["event"] == "started"]
    known = {t["id"] for t in manifest["trials"]}
    if len(claimed) != len(set(claimed)) or not set(claimed) <= known or len(claimed) > LIMIT:
        raise RuntimeError("Invalid budget ledger")
    case_map = {c["id"]: c for c in manifest["cases"]}
    errors = 0
    # One HTTP request per claim: no SDK, retries, redirect following or fallback.
    with httpx.Client(transport=httpx.HTTPTransport(retries=0), timeout=60,
                      follow_redirects=False, trust_env=False) as client:
        for trial in manifest["trials"]:
            if trial["id"] in claimed:
                continue
            if len(claimed) >= LIMIT:
                break
            append_event(ledger, {"event": "started", "id": trial["id"], "at": timestamp()})
            claimed.append(trial["id"])
            start = time.perf_counter()
            record = {"event": "completed", "id": trial["id"], "case_id": trial["case_id"],
                      "group": trial["group"], "repeat": trial["repeat"], "at": timestamp(),
                      "pass": False, "reason": "transport_error", "usage": None, "cost_usd": None}
            try:
                response = client.post("https://api.anthropic.com/v1/messages", json=trial["request"],
                                       headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
                record["elapsed_s"] = time.perf_counter() - start
                record["http_status"] = response.status_code
                record["request_id"] = response.headers.get("request-id")
                if response.status_code == 200:
                    body = response.json()
                    record["usage"] = body.get("usage")
                    record["cost_usd"] = usage_cost(record["usage"])
                    record["response_model"] = body.get("model")
                    record.update(score(case_map[trial["case_id"]], body))
                    errors = 0
                else:
                    record["reason"] = f"http_{response.status_code}"
                    try:
                        error = response.json().get("error", {})
                        record["error_type"] = error.get("type")
                        record["error_message"] = str(error.get("message", "")).replace(key, "[REDACTED]")[:1000]
                    except (ValueError, AttributeError):
                        pass
                    errors += 1
            except Exception as exc:
                record.setdefault("elapsed_s", time.perf_counter() - start)
                record["error_type"] = type(exc).__name__  # no exception text/headers/key
                errors += 1
            append_event(ledger, record)
            print(f"{len(claimed)}/{LIMIT} {trial['id']} {record['elapsed_s']:.2f}s {record['reason']}", flush=True)
            report(out, manifest)
            if record.get("http_status") in {400, 401, 402, 403, 404} or errors >= 3:
                print("Stopped after configuration/authentication or consecutive transport errors; no retries.", flush=True)
                break


def report(out, manifest):
    events = read_events(out / "attempts.jsonl")
    started = [e for e in events if e["event"] == "started"]
    records = [e for e in events if e["event"] == "completed"]
    complete_ids = {e["id"] for e in records}
    missing = [e["id"] for e in started if e["id"] not in complete_ids]
    summary = {"claimed_calls": len(started), "completed_calls": len(records), "limit": LIMIT,
               "unresolved_claims": missing, "cost_usd_known_usage": sum(r["cost_usd"] for r in records if r["cost_usd"] is not None),
               "unknown_usage_calls": sum(r["cost_usd"] is None for r in records) + len(missing), "groups": {}}
    for group in manifest["groups"]:
        summary["groups"][group] = {}
        for route in ("all", "full", "devices"):
            case_ids = {c["id"] for c in manifest["cases"] if route == "all" or c["route"] == route}
            rows = [r for r in records if r["group"] == group and r["case_id"] in case_ids]
            # Report successful HTTP timing separately so fast 401/429 cannot win.
            durations = [r["elapsed_s"] for r in rows if r.get("http_status") == 200]
            summary["groups"][group][route] = {
                "n": len(rows), "passed": sum(r["pass"] for r in rows),
                "median_s_http200": statistics.median(durations) if durations else None,
                "max_s_http200": max(durations) if durations else None,
                "schema_valid": sum(r.get("schema_valid", False) for r in rows),
                "cost_usd_known_usage": sum(r["cost_usd"] for r in rows if r["cost_usd"] is not None),
                "failures": dict(Counter(r["reason"] for r in rows if not r["pass"]))}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Sonnet 5 effort 初步實測", "", f"已占用 {len(started)}/{LIMIT} 次額度；取得 {len(records)} 筆結果。",
             "固定 20 個合成案例，每組每例 2 次；完整入口與家電入口各 10 例。", "",
             "| 組別 | 範圍 | 正確／已回報 | HTTP 200 中位秒數 | HTTP 200 最慢秒數 | 已知用量估計 USD |",
             "| --- | --- | --- | --- | --- | --- |"]
    for group, scopes in summary["groups"].items():
        for scope, stats in scopes.items():
            median = stats['median_s_http200']
            maximum = stats['max_s_http200']
            lines.append(f"| {group} | {scope} | {stats['passed']}/{stats['n']} | "
                         f"{median if median is None else round(median, 3)} | "
                         f"{maximum if maximum is None else round(maximum, 3)} | {stats['cost_usd_known_usage']:.5f} |")
    lines.extend(["", "A=adaptive/high；B=adaptive/medium；C=disabled/medium。",
                  "尚未完成的組別不可當作完整比較；兩次重複不足以證明低錯誤率。",
                  "完整 HTTP 回覆時間含網路及服務排隊，不含 Siri、Sheets、設備或第二次文案呼叫。",
                  "保留兩個入口原本的 4000／2000 token 上限；不啟用 prompt cache，初次 schema 編譯可能影響個別耗時。",
                  "只記錄文字輸出及 usage，不記錄思考內容或金鑰；截斷回覆一律不算成功。",
                  f"已知用量估计合計 USD {summary['cost_usd_known_usage']:.5f}；另有 {summary['unknown_usage_calls']} 次未取得用量，不能視為免費。",
                  f"費率為 2026-09-06 標準全球 API 定價，未含稅／折扣：[官方來源]({PRICE_SOURCE})。", "",
                  "## 未通過案例（需人工核對語意，評分器不改動預先答案）", ""])
    for record in records:
        if not record["pass"]:
            lines.extend([f"### {record['id']}: {record['reason']}", "", "```json", record.get("text", "無文字結果"), "```", ""])
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "run", "report"])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--prompt-key", action="store_true", help="Ask for a masked, in-memory key in a local window")
    args = parser.parse_args()
    if args.prompt_key and args.command != "run":
        parser.error("--prompt-key is only available with run")
    if args.command == "report":
        manifest = json.loads((args.out / "manifest.json").read_text(encoding="utf-8"))
        print(json.dumps(report(args.out, manifest), ensure_ascii=False))
        return
    # Exclusive runner lock protects the shared call budget even across processes.
    args.out.mkdir(parents=True, exist_ok=True)
    lock = args.out / "runner.lock"
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(fd, str(os.getpid()).encode())
        manifest = prepare(args.out)
        if args.command == "run":
            if args.prompt_key:
                from evals.secret_prompt import prompt_key
                key = prompt_key()
                if not key:
                    print("Key entry cancelled; zero new API calls.")
                    return
                try:
                    run(args.out, manifest, supplied_key=key)
                finally:
                    key = None
            else:
                run(args.out, manifest)
        summary = report(args.out, manifest)
        print(f"Prepared {LIMIT} trials; claimed={summary['claimed_calls']}, completed={summary['completed_calls']}")
    finally:
        os.close(fd)
        lock.unlink()


if __name__ == "__main__":
    main()
