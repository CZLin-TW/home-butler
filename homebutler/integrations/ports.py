"""What the household's rules need from the outside world, as small interfaces. The real connections
(LINE, Home Assistant) implement them; tests use fakes."""
from __future__ import annotations

from typing import Protocol


class Messenger(Protocol):
    def push(self, line_user_id: str, text: str) -> None:
        """Send a message to one person; raises if it was not accepted."""


class Lights(Protocol):
    def breathe(self, target: str) -> None:
        """Flash a lighting area once (Home Assistant decides how); raises if the result is unknown."""
