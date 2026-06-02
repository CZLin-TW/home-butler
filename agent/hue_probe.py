"""Philips Hue local API probe for the home-butler PC agent.

Run this on a PC in the same LAN as the Hue Bridge. It helps with the first
integration step: create a Hue application key, list resources, recall scenes,
and trigger a breathe alert on a light/grouped_light.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

httpx = None

try:
    import agent_config
except ImportError:
    agent_config = None


DEFAULT_DEVICE_TYPE = "home-butler#pc-agent"


def _config_value(name: str, default: str = "") -> str:
    if agent_config is None:
        return default
    return str(getattr(agent_config, name, default) or default).strip()


def _bridge_ip(args: argparse.Namespace) -> str:
    value = (args.bridge_ip or _config_value("HUE_BRIDGE_IP")).strip()
    if not value:
        raise SystemExit(
            "Missing Hue Bridge IP. Pass --bridge-ip 192.168.x.x or set "
            "HUE_BRIDGE_IP in agent_config.py."
        )
    return value.removeprefix("https://").removeprefix("http://").strip("/")


def _app_key(args: argparse.Namespace) -> str:
    value = (args.app_key or _config_value("HUE_APPLICATION_KEY")).strip()
    if not value:
        raise SystemExit(
            "Missing Hue application key. Run `python hue_probe.py --bridge-ip "
            "<ip> auth` after pressing the Bridge button, then save the returned "
            "username as HUE_APPLICATION_KEY in agent_config.py."
        )
    return value


def _client(bridge_ip: str, app_key: str | None = None) -> httpx.Client:
    global httpx
    if httpx is None:
        try:
            import httpx as _httpx
            httpx = _httpx
        except ImportError as e:
            raise SystemExit(
                "Missing dependency: httpx. Install agent requirements first:\n"
                "  python -m pip install -r requirements.txt"
            ) from e

    headers = {"Accept": "application/json"}
    if app_key:
        headers["hue-application-key"] = app_key
    return httpx.Client(
        base_url=f"https://{bridge_ip}",
        headers=headers,
        verify=False,
        timeout=10.0,
    )


def _request_json(client: httpx.Client, method: str, path: str, **kwargs: Any) -> Any:
    response = client.request(method, path, **kwargs)
    response.raise_for_status()
    payload = response.json()
    errors = payload.get("errors") if isinstance(payload, dict) else None
    if errors:
        raise RuntimeError(json.dumps(errors, ensure_ascii=False))
    return payload


def _resource_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        data = payload.get("data", [])
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    return []


def _name(item: dict[str, Any]) -> str:
    metadata = item.get("metadata") or {}
    return str(metadata.get("name") or item.get("id") or "")


def _owner(item: dict[str, Any]) -> str:
    owner = item.get("owner") or {}
    if not owner:
        return ""
    return f"{owner.get('rtype', '')}:{owner.get('rid', '')}"


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_auth(args: argparse.Namespace) -> None:
    bridge_ip = _bridge_ip(args)
    device_type = args.device_type or DEFAULT_DEVICE_TYPE
    with _client(bridge_ip) as client:
        payload = client.post(
            "/api",
            json={"devicetype": device_type, "generateclientkey": True},
        ).json()

    _print_json(payload)
    success = next((item.get("success") for item in payload if item.get("success")), None)
    if not success:
        print()
        print("If the response says link button not pressed, press the Hue Bridge button and run auth again.")
        return

    username = success.get("username", "")
    clientkey = success.get("clientkey", "")
    print()
    print("Add these to agent_config.py:")
    print(f'HUE_BRIDGE_IP = "{bridge_ip}"')
    print(f'HUE_APPLICATION_KEY = "{username}"')
    if clientkey:
        print(f'HUE_CLIENT_KEY = "{clientkey}"')


def cmd_list(args: argparse.Namespace) -> None:
    bridge_ip = _bridge_ip(args)
    app_key = _app_key(args)
    resources = args.resources or ["light", "grouped_light", "room", "zone", "scene", "smart_scene"]
    with _client(bridge_ip, app_key) as client:
        for resource in resources:
            payload = _request_json(client, "GET", f"/clip/v2/resource/{resource}")
            items = _resource_items(payload)
            print(f"\n[{resource}] {len(items)}")
            for item in sorted(items, key=_name):
                parts = [
                    f"name={_name(item)}",
                    f"id={item.get('id', '')}",
                    f"type={item.get('type', resource)}",
                ]
                owner = _owner(item)
                if owner:
                    parts.append(f"owner={owner}")
                print("- " + "  ".join(parts))


def cmd_get(args: argparse.Namespace) -> None:
    bridge_ip = _bridge_ip(args)
    app_key = _app_key(args)
    with _client(bridge_ip, app_key) as client:
        payload = _request_json(client, "GET", f"/clip/v2/resource/{args.resource}/{args.id}")
    _print_json(payload)


def cmd_breathe(args: argparse.Namespace) -> None:
    bridge_ip = _bridge_ip(args)
    app_key = _app_key(args)
    body = {"alert": {"action": "breathe"}}
    with _client(bridge_ip, app_key) as client:
        payload = _request_json(client, "PUT", f"/clip/v2/resource/{args.resource}/{args.id}", json=body)
    _print_json(payload)
    print(f"Triggered breathe on {args.resource}:{args.id}")


def cmd_scene(args: argparse.Namespace) -> None:
    bridge_ip = _bridge_ip(args)
    app_key = _app_key(args)
    body = {"recall": {"action": args.action}}
    with _client(bridge_ip, app_key) as client:
        payload = _request_json(client, "PUT", f"/clip/v2/resource/scene/{args.id}", json=body)
    _print_json(payload)
    print(f"Recalled scene {args.id} with action={args.action}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe Philips Hue Bridge local API.")
    parser.add_argument("--bridge-ip", default="", help="Hue Bridge LAN IP, e.g. 192.168.1.10")
    parser.add_argument("--app-key", default="", help="Hue application key (username from auth)")

    sub = parser.add_subparsers(dest="command", required=True)

    auth = sub.add_parser("auth", help="Create a Hue application key after pressing the Bridge button")
    auth.add_argument("--device-type", default=DEFAULT_DEVICE_TYPE)
    auth.set_defaults(func=cmd_auth)

    list_cmd = sub.add_parser("list", help="List Hue lights, grouped lights, rooms, zones, and scenes")
    list_cmd.add_argument("resources", nargs="*", help="Optional resources, e.g. light scene")
    list_cmd.set_defaults(func=cmd_list)

    get_cmd = sub.add_parser("get", help="Dump one Hue resource as JSON")
    get_cmd.add_argument("resource", help="Resource type, e.g. light, grouped_light, scene")
    get_cmd.add_argument("id", help="Resource ID")
    get_cmd.set_defaults(func=cmd_get)

    breathe = sub.add_parser("breathe", help="Trigger Hue breathe alert")
    breathe.add_argument("id", help="light or grouped_light ID")
    breathe.add_argument("--resource", choices=["light", "grouped_light"], default="grouped_light")
    breathe.set_defaults(func=cmd_breathe)

    scene = sub.add_parser("scene", help="Recall a Hue scene")
    scene.add_argument("id", help="Scene ID")
    scene.add_argument("--action", choices=["active", "dynamic_palette", "static"], default="active")
    scene.set_defaults(func=cmd_scene)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
        return 0
    except RuntimeError as e:
        print(f"Hue API error: {e}", file=sys.stderr)
        return 4
    except Exception as e:
        error_type = type(e).__name__
        if error_type == "ConnectError":
            print(f"Cannot connect to Hue Bridge: {e}", file=sys.stderr)
            return 2
        if error_type == "HTTPStatusError":
            response = getattr(e, "response", None)
            status = getattr(response, "status_code", "")
            text = getattr(response, "text", "")
            print(f"Hue API HTTP error: {status} {text}", file=sys.stderr)
            return 3
        raise


if __name__ == "__main__":
    raise SystemExit(main())
