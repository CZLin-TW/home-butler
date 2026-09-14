"""One Hue route for UI, automation and reminders; no implicit provider fallback."""
import os


def ha_enabled():
    # Nonempty invalid values fail closed instead of reactivating the PC agent.
    return os.environ.get("HOME_ASSISTANT_HUE_ENABLED", "false").strip().lower() not in ("", "0", "false", "off")


async def send_command(command_type, payload, *, required_capability="hue", timeout=20.0):
    if required_capability != "hue" or not command_type.startswith("hue."):
        raise ValueError("Hue transport only")
    if not ha_enabled():
        from agent_ws import send_agent_command
        return await send_agent_command(command_type, payload, required_capability="hue", timeout=timeout)
    from home_assistant_api import link
    return await link.hue_command(command_type, payload)


# Synchronous reminder jobs share the ASGI loop without depending on nightlight rules.
_loop = None


def set_event_loop(loop):
    global _loop
    _loop = loop


def send_command_sync(command_type, payload):
    import asyncio
    if _loop is None:
        raise RuntimeError("Lighting event loop not ready")
    future = asyncio.run_coroutine_threadsafe(
        send_command(command_type, payload, required_capability="hue", timeout=20.0), _loop)
    try:
        message = future.result(timeout=28.0)
    except TimeoutError:
        future.cancel()
        raise
    if message.get("status") != "ok":
        raise RuntimeError(message.get("error") or "Hue command failed")
    result = message.get("result")
    return result if isinstance(result, dict) else {}
