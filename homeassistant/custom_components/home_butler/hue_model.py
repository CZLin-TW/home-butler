"""Pure Hue catalogue projection; same UUIDs and presentation as the legacy agent."""

def _hue_resource_items(payload: dict) -> list[dict]:
    data = payload.get("data", []) if isinstance(payload, dict) else []
    return [item for item in data if isinstance(item, dict)]


def _hue_name(item: dict) -> str:
    metadata = item.get("metadata") or {}
    return str(metadata.get("name") or item.get("id") or "")


def _hue_owner(item: dict) -> dict:
    owner = item.get("owner") or {}
    return owner if isinstance(owner, dict) else {}


def _hue_grouped_light_for_container(container: dict, grouped_lights: list[dict]) -> str:
    for service in container.get("services") or []:
        if isinstance(service, dict) and service.get("rtype") == "grouped_light" and service.get("rid"):
            return str(service.get("rid"))

    container_id = str(container.get("id") or "")
    container_type = str(container.get("type") or "")
    for grouped in grouped_lights:
        owner = _hue_owner(grouped)
        if owner.get("rtype") == container_type and owner.get("rid") == container_id:
            return str(grouped.get("id") or "")
    return ""


def _hue_area_from_container(container: dict, grouped_light_id: str, kind_label: str) -> dict:
    return {
        "id": grouped_light_id,
        "resource_type": "grouped_light",
        "hue_resource_id": str(container.get("id") or ""),
        "hue_resource_type": str(container.get("type") or ""),
        "hue_name": _hue_name(container),
        "kind": kind_label,
        "owner_type": str(container.get("type") or ""),
        "owner_id": str(container.get("id") or ""),
    }


def _hue_grouped_state(grouped: dict) -> dict:
    """Pull current on/brightness out of a grouped_light resource for the dashboard."""
    on = grouped.get("on") if isinstance(grouped.get("on"), dict) else {}
    dimming = grouped.get("dimming") if isinstance(grouped.get("dimming"), dict) else {}
    brightness = dimming.get("brightness")
    return {
        "on": bool(on.get("on")) if "on" in on else None,
        "brightness": float(brightness) if isinstance(brightness, (int, float)) else None,
    }


def _hue_device_light_ids(device: dict) -> list[str]:
    ids: list[str] = []
    for service in device.get("services") or []:
        if isinstance(service, dict) and service.get("rtype") == "light" and service.get("rid"):
            ids.append(str(service.get("rid")))
    return ids


def _hue_container_light_ids(
    container: dict,
    containers_by_key: dict[tuple[str, str], dict],
    devices_by_id: dict[str, dict],
    lights_by_id: dict[str, dict],
    seen_containers: set[tuple[str, str]] | None = None,
) -> list[str]:
    container_key = (str(container.get("type") or ""), str(container.get("id") or ""))
    if seen_containers is None:
        seen_containers = set()
    if container_key in seen_containers:
        return []
    seen_containers.add(container_key)

    ids: list[str] = []
    seen_light_ids: set[str] = set()

    def add_light(light_id: str) -> None:
        if light_id and light_id in lights_by_id and light_id not in seen_light_ids:
            seen_light_ids.add(light_id)
            ids.append(light_id)

    def owner_matches_container(item: dict) -> bool:
        owner = _hue_owner(item)
        return (
            str(owner.get("rtype") or "") == container_key[0]
            and str(owner.get("rid") or "") == container_key[1]
        )

    for child in container.get("children") or []:
        if not isinstance(child, dict):
            continue
        rid = str(child.get("rid") or "")
        rtype = str(child.get("rtype") or "")
        if rtype == "light":
            add_light(rid)
        elif rtype == "device":
            for light_id in _hue_device_light_ids(devices_by_id.get(rid, {})):
                add_light(light_id)
        elif rtype in ("room", "zone"):
            nested = containers_by_key.get((rtype, rid))
            if nested:
                for light_id in _hue_container_light_ids(
                    nested,
                    containers_by_key,
                    devices_by_id,
                    lights_by_id,
                    seen_containers,
                ):
                    add_light(light_id)

    # Some Bridge payloads expose membership through device owner rather than
    # room.children. Keep this fallback broad so effects still appear if Hue
    # changes the container shape slightly.
    for device in devices_by_id.values():
        if owner_matches_container(device):
            for light_id in _hue_device_light_ids(device):
                add_light(light_id)
    for light_id, light in lights_by_id.items():
        owner = _hue_owner(light)
        if owner.get("rtype") != "device":
            continue
        device = devices_by_id.get(str(owner.get("rid") or ""), {})
        if device and owner_matches_container(device):
            add_light(light_id)
    return ids


def _hue_scene_has_palette(scene: dict) -> bool:
    palette = scene.get("palette")
    if not isinstance(palette, dict):
        return False
    for key in ("color", "color_temperature", "dimming"):
        values = palette.get(key)
        if isinstance(values, list) and values:
            return True
    return False


def _hue_scene_recall_action(scene: dict) -> str:
    # Palette scenes such as Hue Gallery color scenes behave closer to the Hue
    # App when started as dynamic_palette; if the Bridge rejects it, recall falls
    # back to active in _hue_recall_scene.
    if _hue_scene_has_palette(scene):
        return "dynamic_palette"
    return "active"


def _hue_scene_status(scene: dict) -> dict:
    """場景的啟用狀態（Hue API v2 的 scene.status）。

    home-butler 的自動夜燈用這個判斷「現在亮著的是不是那個夜燈場景」，因此不必去記
    「是誰開的燈」——bridge 自己會記，而且 agent recall、Hue 遙控器、Hue App 更新的
    是同一份欄位，天生一視同仁。

    - active: "static" / "dynamic_palette" / "inactive"。非 inactive = 燈目前就是這個場景。
    - last_recall: 這個場景最後一次被叫起來的時間（UTC ISO8601）。即使事後有人調亮度
      讓 active 掉回 inactive，這個時間戳仍在，可以比出「最近一次是誰把燈設成現在這樣」。

    舊韌體沒有 status 區塊時回空 dict；home-butler 端看到空的會退回舊的判斷方式。
    """
    status = scene.get("status") if isinstance(scene.get("status"), dict) else {}
    out: dict = {}
    active = status.get("active")
    if isinstance(active, str) and active:
        out["active"] = active
    last_recall = status.get("last_recall")
    if isinstance(last_recall, str) and last_recall:
        out["last_recall"] = last_recall
    return out


def _hue_scene_summary(scene: dict) -> dict:
    group = scene.get("group") if isinstance(scene.get("group"), dict) else {}
    return {
        "id": str(scene.get("id") or ""),
        "name": _hue_name(scene) or str(scene.get("id") or ""),
        "resource_type": "scene",
        "recall_action": _hue_scene_recall_action(scene),
        "dynamic_available": _hue_scene_has_palette(scene),
        "group_id": str(group.get("rid") or ""),
        "group_type": str(group.get("rtype") or ""),
        "status": _hue_scene_status(scene),
    }


def _hue_smart_scene_summary(scene: dict) -> dict:
    group = scene.get("group") if isinstance(scene.get("group"), dict) else {}
    return {
        "id": str(scene.get("id") or ""),
        "name": _hue_name(scene) or str(scene.get("id") or ""),
        "resource_type": "smart_scene",
        "recall_action": "activate",
        "dynamic_available": True,
        "group_id": str(group.get("rid") or ""),
        "group_type": str(group.get("rtype") or ""),
        "status": _hue_scene_status(scene),
    }


def _hue_scenes_for_container(container: dict, scenes: list[dict], smart_scenes: list[dict]) -> list[dict]:
    container_id = str(container.get("id") or "")
    container_type = str(container.get("type") or "")
    items: list[dict] = []
    for scene in scenes:
        group = scene.get("group") if isinstance(scene.get("group"), dict) else {}
        if str(group.get("rid") or "") == container_id and str(group.get("rtype") or "") == container_type:
            summary = _hue_scene_summary(scene)
            if summary["id"]:
                items.append(summary)
    for scene in smart_scenes:
        group = scene.get("group") if isinstance(scene.get("group"), dict) else {}
        if str(group.get("rid") or "") == container_id and str(group.get("rtype") or "") == container_type:
            summary = _hue_smart_scene_summary(scene)
            if summary["id"]:
                items.append(summary)
    items.sort(key=lambda item: (str(item.get("name") or ""), str(item.get("id") or "")))
    return items


_HUE_EFFECT_LABELS = {
    "no_effect": "無效果",
    "candle": "燭火",
    "fire": "火焰",
    "prism": "Prism",
    "sparkle": "Sparkle",
    "glisten": "Glisten",
    "opal": "Opal",
    "cosmos": "Cosmos",
    "enchant": "Enchant",
    "sunbeam": "Sunbeam",
    "underwater": "Underwater",
}


def _hue_effect_label(effect: str) -> str:
    effect = str(effect or "").strip()
    if effect in _HUE_EFFECT_LABELS:
        return _HUE_EFFECT_LABELS[effect]
    return effect.replace("_", " ").strip().title() or effect


def _hue_effect_values(block) -> set[str]:
    values: set[str] = set()
    keys = {"effect_values", "status_values", "action_values"}

    def walk(value) -> None:
        if isinstance(value, dict):
            for key, raw in value.items():
                if key in keys and isinstance(raw, list):
                    values.update(str(item).strip() for item in raw if str(item or "").strip())
                else:
                    walk(raw)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(block)
    return values


def _hue_effect_values_for_light(light: dict) -> set[str]:
    return (
        _hue_effect_values(light.get("effects"))
        | _hue_effect_values(light.get("effects_v2"))
        | _hue_effect_values(light.get("timed_effects"))
    )


def _hue_effect_options(light_ids: list[str], lights_by_id: dict[str, dict]) -> list[dict]:
    total = len(light_ids)
    if total <= 0:
        return []

    counts: dict[str, int] = {}
    for light_id in light_ids:
        for effect in _hue_effect_values_for_light(lights_by_id.get(light_id, {})):
            counts[effect] = counts.get(effect, 0) + 1

    def sort_key(item: tuple[str, int]) -> tuple[int, str]:
        effect, _count = item
        return (0 if effect == "no_effect" else 1, _hue_effect_label(effect).lower())

    options: list[dict] = []
    for effect, count in sorted(counts.items(), key=sort_key):
        if not effect:
            continue
        options.append({
            "key": effect,
            "label": _hue_effect_label(effect),
            "supported_count": count,
            "total_count": total,
            "partial": count < total,
        })
    return options


_HUE_NOTIFICATION_LABELS = {
    "breathe": "呼吸燈",
    "no_signal": "無訊號",
    "on_off": "閃爍",
    "on_off_color": "彩色閃爍",
    "alternating": "交替閃爍",
}


def _hue_notification_label(action: str) -> str:
    action = str(action or "").strip()
    if action in _HUE_NOTIFICATION_LABELS:
        return _HUE_NOTIFICATION_LABELS[action]
    return action.replace("_", " ").strip().title() or action


def _hue_action_values(block) -> set[str]:
    values: set[str] = set()

    def walk(value) -> None:
        if isinstance(value, dict):
            for key, raw in value.items():
                if key in ("action_values", "signal_values") and isinstance(raw, list):
                    values.update(str(item).strip() for item in raw if str(item or "").strip())
                else:
                    walk(raw)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(block)
    return values


def _hue_notification_options(grouped: dict) -> list[dict]:
    options: list[dict] = [{
        "key": "alert:breathe",
        "label": "呼吸燈",
        "kind": "alert",
        "action": "breathe",
    }]

    seen = {"alert:breathe"}
    for action in sorted(_hue_action_values(grouped.get("alert"))):
        key = f"alert:{action}"
        if action == "none" or key in seen:
            continue
        seen.add(key)
        options.append({
            "key": key,
            "label": _hue_notification_label(action),
            "kind": "alert",
            "action": action,
        })

    for action in sorted(_hue_action_values(grouped.get("signaling"))):
        key = f"signaling:{action}"
        if action in ("no_signal", "none") or key in seen:
            continue
        seen.add(key)
        options.append({
            "key": key,
            "label": _hue_notification_label(action),
            "kind": "signaling",
            "action": action,
        })
    return options


def _hue_container_for_grouped_light_id(grouped_light_id: str, resources: dict[str, list[dict]]) -> dict:
    grouped_lights = resources.get("grouped_light", [])
    grouped_by_id = {str(item.get("id") or ""): item for item in grouped_lights}
    for resource in ("room", "zone", "bridge_home"):
        for container in resources.get(resource, []):
            if _hue_grouped_light_for_container(container, grouped_lights) == grouped_light_id:
                return container

    grouped = grouped_by_id.get(grouped_light_id) or {}
    owner = _hue_owner(grouped)
    owner_key = (str(owner.get("rtype") or ""), str(owner.get("rid") or ""))
    for resource in ("room", "zone", "bridge_home"):
        for container in resources.get(resource, []):
            if (str(container.get("type") or ""), str(container.get("id") or "")) == owner_key:
                return container
    return {}


def _hue_effect_payloads_for_light(light: dict, effect: str) -> list[dict]:
    payloads: list[dict] = []
    if effect in _hue_effect_values(light.get("effects")):
        payloads.append({"effects": {"effect": effect}})
    if effect in _hue_effect_values(light.get("effects_v2")):
        # Newer Hue firmware exposes richer effect families through effects_v2. The
        # API shape has evolved, so try the documented action form first and keep a
        # plain effect fallback for bridges that surface the simpler schema.
        payloads.append({"effects_v2": {"action": {"effect": effect}}})
        payloads.append({"effects_v2": {"effect": effect}})
    if effect in _hue_effect_values(light.get("timed_effects")):
        payloads.append({"timed_effects": {"effect": effect}})
    return payloads


def list_areas(resources) -> dict:
    grouped_lights = resources['grouped_light']
    grouped_by_id = {str(item.get('id') or ''): item for item in grouped_lights}
    lights_by_id = {str(item.get('id') or ''): item for item in resources.get('light', [])}
    devices_by_id = {str(item.get('id') or ''): item for item in resources.get('device', [])}
    containers_by_key = {(str(container.get('type') or ''), str(container.get('id') or '')): container for resource in ('room', 'zone', 'bridge_home') for container in resources.get(resource, [])}
    used_group_ids: set[str] = set()
    areas: list[dict] = []
    for resource, label in (('room', '房間'), ('zone', '區域'), ('bridge_home', '全家')):
        for container in resources.get(resource, []):
            grouped_light_id = _hue_grouped_light_for_container(container, grouped_lights)
            if not grouped_light_id:
                continue
            used_group_ids.add(grouped_light_id)
            area = _hue_area_from_container(container, grouped_light_id, label)
            grouped = grouped_by_id.get(grouped_light_id) or {}
            area['grouped_light_name'] = _hue_name(grouped)
            area.update(_hue_grouped_state(grouped))
            light_ids = _hue_container_light_ids(container, containers_by_key, devices_by_id, lights_by_id)
            area['light_count'] = len(light_ids)
            area['scenes'] = _hue_scenes_for_container(container, resources.get('scene', []), resources.get('smart_scene', []))
            area['notifications'] = _hue_notification_options(grouped)
            area['effects'] = _hue_effect_options(light_ids, lights_by_id)
            areas.append(area)
    for grouped in grouped_lights:
        grouped_id = str(grouped.get('id') or '')
        if not grouped_id or grouped_id in used_group_ids:
            continue
        owner = _hue_owner(grouped)
        areas.append({'id': grouped_id, 'resource_type': 'grouped_light', 'hue_resource_id': grouped_id, 'hue_resource_type': 'grouped_light', 'hue_name': _hue_name(grouped), 'kind': '燈群', 'owner_type': str(owner.get('rtype') or ''), 'owner_id': str(owner.get('rid') or ''), 'grouped_light_name': _hue_name(grouped), **_hue_grouped_state(grouped), 'light_count': 0, 'scenes': [], 'notifications': _hue_notification_options(grouped), 'effects': []})
    areas.sort(key=lambda x: (str(x.get('kind') or ''), str(x.get('hue_name') or ''), str(x.get('id') or '')))
    return {'areas': areas, 'counts': {resource: len(items) for resource, items in resources.items()}}
