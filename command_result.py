"""Machine-readable command outcome; human wording is not a success signal."""
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class CommandResult:
    status: Literal["success", "failed", "unknown"]
    message: str

    @classmethod
    def success(cls, message):
        # Means the provider accepted the command, not physical readback (IR has none).
        return cls("success", message)

    @classmethod
    def failed(cls, message):
        # A failed multi-step command may already have changed part of the device state.
        return cls("failed", message)

    @classmethod
    def unknown(cls, message):
        return cls("unknown", message)

    @classmethod
    def provider_failure(cls, result, message):
        if result.get("uncertain"):
            # Legacy assistant checks the error marker before showing its planned reply.
            return cls.unknown("❌ 指令結果未確認，請檢查設備狀態。系統不會自動重送。")
        return cls.failed(message)
