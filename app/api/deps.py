from __future__ import annotations

from fastapi import Header, HTTPException

from app.core.config import settings


def get_current_user_id(
    x_cochat_user_id: str | None = Header(default=None, alias="X-Cochat-User-Id"),
) -> int:
    """Temporary auth stub until real login/session middleware is wired in."""
    if not x_cochat_user_id:
        if settings.MASTER_USER_ID > 0:
            return settings.MASTER_USER_ID
        raise HTTPException(status_code=401, detail="Missing X-Cochat-User-Id header.")

    try:
        user_id = int(x_cochat_user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid X-Cochat-User-Id header.") from exc

    if user_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid X-Cochat-User-Id header.")

    return user_id
