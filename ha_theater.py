"""Whether the theater relay goes through Home Assistant instead of the PC agent.

Off by default: the PC agent path stays until the HA integration is installed
and configured at home. Flipping this is the only switch; both sides keep
working independently so either can be rolled back on its own.
"""
import os


def enabled():
    return os.environ.get("THEATER_VIA_HA", "").strip().lower() in ("1", "true", "yes", "on")
