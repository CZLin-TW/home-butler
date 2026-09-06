"""
API Key 認證
保護 /api/* /notify* /switchbot/* 端點，避免公開 Render URL 被任意呼叫。

LINE webhook (/callback) 與健康檢查 (/) 不套用此認證——
前者由 X-Line-Signature 驗證，後者需保持公開供 UptimeRobot 使用。
"""

import secrets
from fastapi import Header, HTTPException, status
from config import HOME_BUTLER_API_KEY, DEVICE_VOICE_API_KEY


def verify_api_key(x_api_key: str = Header(default="")):
    """
    FastAPI dependency：檢查 X-API-Key header。

    fail-closed：若伺服器端 HOME_BUTLER_API_KEY 未設定，所有受保護端點直接回 503，
    避免「忘記設環境變數導致繼續無防護」的灰色地帶。
    """
    if not HOME_BUTLER_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server misconfiguration: HOME_BUTLER_API_KEY not set",
        )

    # constant-time compare 防 timing attack
    if not x_api_key or not secrets.compare_digest(x_api_key, HOME_BUTLER_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key header",
        )


def verify_device_voice_key(x_api_key: str = Header(default="")):
    """Dedicated capability, never add it to verify_api_key's accepted keys.

    Reusing the owner key would give shortcut recipients full API access, so
    refuse that configuration. The owner API remains independently protected.
    """
    if (not HOME_BUTLER_API_KEY or len(DEVICE_VOICE_API_KEY) < 32
            or secrets.compare_digest(DEVICE_VOICE_API_KEY.encode(), HOME_BUTLER_API_KEY.encode())):
        raise HTTPException(status_code=503, detail="Device voice access is not configured")
    if not x_api_key or not secrets.compare_digest(x_api_key.encode(), DEVICE_VOICE_API_KEY.encode()):
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key header")
