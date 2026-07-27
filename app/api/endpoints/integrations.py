from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_id
from app.core.config import settings
from app.db.session import get_db
from app.integrations.discord.client import DiscordRestClient
from app.integrations.slack.client import SlackClient
from app.integrations.slack.sync_service import (
    list_accessible_conversations,
    sync_accessible_conversations,
    sync_conversation_raw_events,
)
from app.repositories.integration_repository import (
    disconnect_integration_for_user,
    get_integration_by_id_for_user,
    get_or_create_user_integration,
    get_token_by_integration_id,
    list_integrations_by_user,
    upsert_token,
)
from app.repositories.raw_event_repository import list_raw_events_by_integration_ids

router = APIRouter(tags=["integrations"])

# Bot server messages need at least VIEW_CHANNEL + READ_MESSAGE_HISTORY.
_DISCORD_BOT_PERMISSIONS = 66560

_SLACK_USER_SCOPES = ",".join([
    "channels:history",
    "channels:read",
    "groups:history",
    "groups:read",
    "im:history",
    "im:read",
    "mpim:history",
    "mpim:read",
    "users:read",
])

# Event Subscriptions에 등록된 봇 이벤트(app_mention, message.channels,
# message.groups, message.im)를 실제로 수신하려면 봇 유저에게 이 스코프가
# 부여되어 있어야 한다. user_scope만 요청하면 봇은 아무 권한도 못 받아
# Slack이 이벤트를 아예 안 보낸다 (#26).
_SLACK_BOT_SCOPES = ",".join([
    "app_mentions:read",
    "channels:history",
    "groups:history",
    "im:history",
])


class SlackSyncRequest(BaseModel):
    integration_id: int = Field(gt=0)
    channel_id: str
    limit: int = Field(default=30, ge=1, le=200)


class SlackSyncAllRequest(BaseModel):
    integration_ids: list[int] | None = None
    conversation_limit: int = Field(default=100, ge=1, le=200)
    message_limit: int = Field(default=30, ge=1, le=200)


def _build_oauth_state(provider: str, app_user_id: int, secret: str) -> str:
    payload = {
        "provider": provider,
        "app_user_id": app_user_id,
        "nonce": secrets.token_urlsafe(16),
        "iat": int(time.time()),
    }
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest().encode()
    token = base64.urlsafe_b64encode(payload_bytes + b"." + signature).decode().rstrip("=")
    return token


def _parse_oauth_state(state: str, provider: str, secret: str) -> dict:
    try:
        padded = state + "=" * (-len(state) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode())
        payload_bytes, signature = decoded.rsplit(b".", 1)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {provider} OAuth state.") from exc

    expected = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest().encode()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=400, detail=f"Invalid {provider} OAuth state signature.")

    try:
        payload = json.loads(payload_bytes)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {provider} OAuth state payload.") from exc

    if payload.get("provider") != provider:
        raise HTTPException(status_code=400, detail=f"Unexpected {provider} OAuth state provider.")

    if int(time.time()) - int(payload.get("iat", 0)) > 600:
        raise HTTPException(status_code=400, detail=f"{provider} OAuth state expired.")

    return payload


def _build_slack_oauth_state(app_user_id: int) -> str:
    return _build_oauth_state("slack", app_user_id, settings.SLACK_CLIENT_SECRET)


def _parse_slack_oauth_state(state: str) -> dict:
    return _parse_oauth_state(state, "slack", settings.SLACK_CLIENT_SECRET)


@router.get("/integrations")
async def list_integrations(
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Return every active integration owned by the current user, across all providers."""
    integrations = await list_integrations_by_user(
        db=db,
        user_id=current_user_id,
        status="active",
    )
    return {
        "integrations": [
            {
                "provider": integration.provider,
                "integration_id": integration.id,
                "account_identifier": integration.account_identifier,
                "account_name": integration.account_name,
                "status": integration.status,
            }
            for integration in integrations
        ],
    }


@router.get("/integrations/slack/oauth-url")
def get_slack_oauth_url(
    current_user_id: int = Depends(get_current_user_id),
):
    """Return a user-scoped Slack OAuth URL for the current application user."""
    state = _build_slack_oauth_state(current_user_id)
    params = urlencode({
        "client_id": settings.SLACK_CLIENT_ID,
        "scope": _SLACK_BOT_SCOPES,
        "user_scope": _SLACK_USER_SCOPES,
        "redirect_uri": settings.SLACK_REDIRECT_URI,
        "state": state,
    })
    return {
        "url": f"https://slack.com/oauth/v2/authorize?{params}",
        "state": state,
        "user_id": current_user_id,
    }


@router.get("/integrations/slack/callback")
async def slack_oauth_callback(
    code: str,
    state: str,
    db: AsyncSession = Depends(get_db),
):
    """Exchange the Slack OAuth code and persist a user-scoped Slack integration."""
    state_payload = _parse_slack_oauth_state(state)
    app_user_id = int(state_payload["app_user_id"])
    client = SlackClient(token="")

    try:
        token_data = await client.exchange_code(
            code=code,
            client_id=settings.SLACK_CLIENT_ID,
            client_secret=settings.SLACK_CLIENT_SECRET,
            redirect_uri=settings.SLACK_REDIRECT_URI,
        )
    except Exception as exc:  # pragma: no cover - delegated SDK failure
        raise HTTPException(status_code=400, detail="Slack authorization code exchange failed.") from exc

    authed_user: dict = token_data.get("authed_user", {})
    access_token: str = token_data.get("access_token") or authed_user.get("access_token", "")
    slack_user_id: str = authed_user.get("id", "")
    team: dict = token_data.get("team", {})
    team_id: str = team.get("id", "")
    team_name: str = team.get("name", team_id)

    if not team_id or not slack_user_id or not access_token:
        raise HTTPException(status_code=400, detail="Slack user authorization data is missing.")

    account_identifier = f"{team_id}:{slack_user_id}"

    async with db.begin():
        integration = await get_or_create_user_integration(
            db=db,
            user_id=app_user_id,
            provider="slack",
            account_identifier=account_identifier,
            account_name=team_name,
            slack_team_id=team_id,
            slack_user_id=slack_user_id,
        )
        await upsert_token(
            db=db,
            integration_id=integration.id,
            access_token=access_token,
        )

    return RedirectResponse(
        url=f"{settings.FRONTEND_URL}/setup?provider=slack&status=connected",
        status_code=302,
    )


@router.get("/integrations/slack/connection")
async def get_slack_connection(
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Return the current user's Slack connection status."""
    integrations = await list_integrations_by_user(
        db=db,
        user_id=current_user_id,
        provider="slack",
        status="active",
    )
    return {
        "user_id": current_user_id,
        "connected": len(integrations) > 0,
        "integrations": [
            {
                "integration_id": integration.id,
                "account_identifier": integration.account_identifier,
                "account_name": integration.account_name,
                "slack_team_id": integration.slack_team_id,
                "slack_user_id": integration.slack_user_id,
                "status": integration.status,
            }
            for integration in integrations
        ],
    }


@router.delete("/integrations/slack/connection/{integration_id}")
async def disconnect_slack_connection(
    integration_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Disconnect the current user's Slack integration."""
    async with db.begin():
        integration = await disconnect_integration_for_user(
            db=db,
            user_id=current_user_id,
            integration_id=integration_id,
            provider="slack",
        )

    if not integration:
        raise HTTPException(status_code=404, detail="Slack integration not found for current user.")

    return {
        "status": "ok",
        "user_id": current_user_id,
        "integration_id": integration.id,
        "disconnected": True,
    }


@router.get("/integrations/slack/conversations")
async def get_slack_conversations(
    integration_id: int,
    limit: int = 100,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """List Slack conversations accessible to the connected user token."""
    integration = await get_integration_by_id_for_user(
        db=db,
        user_id=current_user_id,
        integration_id=integration_id,
        provider="slack",
    )
    if not integration:
        raise HTTPException(status_code=404, detail="Slack integration not found for current user.")

    token = await get_token_by_integration_id(db, integration.id)
    if not token or not token.access_token:
        raise HTTPException(status_code=400, detail="Slack token not found for integration.")

    conversations = await list_accessible_conversations(
        access_token=token.access_token,
        limit=limit,
    )
    return {
        "integration_id": integration.id,
        "count": len(conversations),
        "conversations": conversations,
    }


@router.post("/integrations/slack/sync")
async def sync_slack_conversation(
    payload: SlackSyncRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Fetch recent Slack messages for one conversation and store them as raw events."""
    integration = await get_integration_by_id_for_user(
        db=db,
        user_id=current_user_id,
        integration_id=payload.integration_id,
        provider="slack",
    )
    if not integration:
        raise HTTPException(status_code=404, detail="Slack integration not found for current user.")

    token = await get_token_by_integration_id(db, integration.id)
    if not token or not token.access_token:
        raise HTTPException(status_code=400, detail="Slack token not found for integration.")

    result = await sync_conversation_raw_events(
        db=db,
        integration_id=integration.id,
        access_token=token.access_token,
        channel_id=payload.channel_id,
        limit=payload.limit,
    )
    await db.commit()

    return {
        "status": "ok",
        "integration_id": integration.id,
        **result,
    }


@router.post("/integrations/slack/sync-all")
async def sync_all_slack_connections(
    payload: SlackSyncAllRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Sync recent messages across all selected Slack connections for the current user."""
    integrations = await list_integrations_by_user(
        db=db,
        user_id=current_user_id,
        provider="slack",
        status="active",
    )
    if payload.integration_ids:
        requested_ids = set(payload.integration_ids)
        integrations = [integration for integration in integrations if integration.id in requested_ids]

    if not integrations:
        raise HTTPException(status_code=404, detail="No active Slack integrations found for current user.")

    integration_results: list[dict] = []
    total_messages = 0

    for integration in integrations:
        token = await get_token_by_integration_id(db, integration.id)
        if not token or not token.access_token:
            integration_results.append({
                "integration_id": integration.id,
                "account_name": integration.account_name,
                "slack_team_id": integration.slack_team_id,
                "slack_user_id": integration.slack_user_id,
                "processed_messages": 0,
                "conversation_count": 0,
                "skipped": True,
                "reason": "missing_token",
            })
            continue

        result = await sync_accessible_conversations(
            db=db,
            integration_id=integration.id,
            access_token=token.access_token,
            conversation_limit=payload.conversation_limit,
            message_limit=payload.message_limit,
        )
        total_messages += result["processed_messages"]
        integration_results.append({
            **result,
            "account_name": integration.account_name,
            "slack_team_id": integration.slack_team_id,
            "slack_user_id": integration.slack_user_id,
        })

    await db.commit()

    return {
        "status": "ok",
        "user_id": current_user_id,
        "integration_count": len(integration_results),
        "processed_messages": total_messages,
        "integrations": integration_results,
    }


@router.get("/integrations/slack/raw-events")
async def list_slack_raw_events(
    integration_ids: list[int] | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """List recent Slack raw events merged across the current user's active Slack integrations."""
    integrations = await list_integrations_by_user(
        db=db,
        user_id=current_user_id,
        provider="slack",
        status="active",
    )
    if integration_ids:
        allowed_ids = set(integration_ids)
        integrations = [integration for integration in integrations if integration.id in allowed_ids]

    if not integrations:
        return {
            "user_id": current_user_id,
            "count": 0,
            "raw_events": [],
        }

    integration_by_id = {integration.id: integration for integration in integrations}
    raw_events = await list_raw_events_by_integration_ids(
        db=db,
        integration_ids=list(integration_by_id.keys()),
        provider="slack",
        limit=limit,
    )

    return {
        "user_id": current_user_id,
        "count": len(raw_events),
        "raw_events": [
            {
                "id": raw_event.id,
                "integration_id": raw_event.integration_id,
                "provider_event_id": raw_event.provider_event_id,
                "event_type": raw_event.event_type,
                "received_at": raw_event.received_at.isoformat(),
                "payload": raw_event.payload,
                "account_name": integration_by_id[raw_event.integration_id].account_name,
                "account_identifier": integration_by_id[raw_event.integration_id].account_identifier,
                "slack_team_id": integration_by_id[raw_event.integration_id].slack_team_id,
                "slack_user_id": integration_by_id[raw_event.integration_id].slack_user_id,
            }
            for raw_event in raw_events
        ],
    }


@router.get("/integrations/discord/oauth-url")
def get_discord_oauth_url(
    current_user_id: int = Depends(get_current_user_id),
):
    """Return the Discord bot installation URL."""
    state = _build_oauth_state("discord", current_user_id, settings.DISCORD_CLIENT_SECRET)
    params = urlencode({
        "client_id": settings.DISCORD_CLIENT_ID,
        "permissions": _DISCORD_BOT_PERMISSIONS,
        "scope": "bot identify guilds",
        "redirect_uri": settings.DISCORD_REDIRECT_URI,
        "response_type": "code",
        "state": state,
    })
    return {"url": f"https://discord.com/oauth2/authorize?{params}", "state": state, "user_id": current_user_id}


@router.get("/integrations/discord/callback")
async def discord_oauth_callback(
    code: str,
    state: str,
    db: AsyncSession = Depends(get_db),
):
    """Exchange the Discord OAuth code and persist guild/token data."""
    state_payload = _parse_oauth_state(state, "discord", settings.DISCORD_CLIENT_SECRET)
    app_user_id: int = int(state_payload["app_user_id"])

    client = DiscordRestClient(bot_token=settings.DISCORD_BOT_TOKEN)

    try:
        token_data = await client.exchange_code(
            code=code,
            redirect_uri=settings.DISCORD_REDIRECT_URI,
            client_id=settings.DISCORD_CLIENT_ID,
            client_secret=settings.DISCORD_CLIENT_SECRET,
        )
    except Exception as exc:  # pragma: no cover - delegated SDK failure
        raise HTTPException(status_code=400, detail="Discord authorization code exchange failed.") from exc

    access_token: str = token_data["access_token"]
    refresh_token: str | None = token_data.get("refresh_token")
    expires_in: int | None = token_data.get("expires_in")
    expires_at: datetime | None = (
        datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        if expires_in
        else None
    )

    guild: dict | None = token_data.get("guild")
    if not guild:
        raise HTTPException(status_code=400, detail="Discord guild information is missing. Retry the bot install flow.")

    guild_id: str = guild["id"]
    guild_name: str = guild.get("name", guild_id)

    async with db.begin():
        integration = await get_or_create_user_integration(
            db=db,
            user_id=app_user_id,
            provider="discord",
            account_identifier=guild_id,
            account_name=guild_name,
        )
        await upsert_token(
            db=db,
            integration_id=integration.id,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
        )

    return RedirectResponse(
        url=f"{settings.FRONTEND_URL}/setup?provider=discord&status=connected",
        status_code=302,
    )
