from __future__ import annotations

import httpx

GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"


class GoogleCalendarClient:
    async def exchange_code(
        self, code: str, redirect_uri: str, client_id: str, client_secret: str
    ) -> dict:
        """Authorization Code를 Access/Refresh Token으로 교환."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                GOOGLE_OAUTH_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
            response.raise_for_status()
            return response.json()

    async def refresh_access_token(
        self, refresh_token: str, client_id: str, client_secret: str
    ) -> dict:
        """Refresh Token으로 Access Token 갱신.

        Google은 이 응답에 refresh_token을 다시 내려주지 않는 경우가 대부분이다 —
        호출부는 기존에 저장돼 있던 refresh_token을 그대로 유지해야 한다.
        """
        async with httpx.AsyncClient() as client:
            response = await client.post(
                GOOGLE_OAUTH_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
            response.raise_for_status()
            return response.json()

    async def get_userinfo(self, access_token: str) -> dict:
        """Access Token으로 사용자 이메일/이름 조회."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            response.raise_for_status()
            return response.json()

    async def create_event(
        self, access_token: str, event: dict, calendar_id: str = "primary"
    ) -> dict:
        """캘린더 이벤트 생성."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{GOOGLE_CALENDAR_API_BASE}/calendars/{calendar_id}/events",
                headers={"Authorization": f"Bearer {access_token}"},
                json=event,
            )
            response.raise_for_status()
            return response.json()
