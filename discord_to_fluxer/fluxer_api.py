from __future__ import annotations

import re

from discord_to_fluxer.api_client import APIClient, validate_snowflake
from discord_to_fluxer.models import (
    Channel,
    GuildInfo,
    GuildSettings,
    GuildStructure,
    PermissionOverwrite,
    Role,
)


def _strip_html_tags(text: str) -> str:
    """Remove HTML tags while preserving the text content between them."""
    # Strip script and style blocks entirely (content and tags).
    text = re.sub(r"<\s*script[^>]*>.*?</\s*script\s*>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<\s*style[^>]*>.*?</\s*style\s*>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Strip remaining HTML tags, keep content.
    text = re.sub(r"<[^>]*>", "", text)
    return text


def _sanitize_name(name: str) -> str:
    """Sanitize a role or channel name.

    Strips HTML tags only. Does NOT escape entities — the API expects plain
    text and the rendering layer handles display escaping.
    """
    name = _strip_html_tags(name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:100]


def _sanitize_topic(topic: str) -> str:
    """Sanitize a channel topic.

    Strips HTML tags only. Preserves all other characters verbatim.
    """
    topic = _strip_html_tags(topic)
    return topic[:1024]


class FluxerAPI:
    def __init__(self, token: str, base_url: str = "https://api.fluxer.app/v1",
                 log_fn=None) -> None:
        self._api = APIClient(base_url, token, log_fn=log_fn)

    def close(self) -> None:
        self._api.close()

    # -- read -------------------------------------------------------------

    def list_guilds(self) -> list[GuildInfo]:
        data = self._api.get("/users/@me/guilds").json()
        return [GuildInfo(id=g["id"], name=g["name"]) for g in data]

    def fetch_structure(self, guild_id: str) -> GuildStructure:
        validate_snowflake(guild_id, "guild_id")
        guild_data = self._api.get(f"/guilds/{guild_id}").json()
        guild = GuildInfo(id=guild_data.get("id", guild_id), name=guild_data.get("name", ""))

        settings = GuildSettings(
            system_channel_id=guild_data.get("system_channel_id"),
            system_channel_flags=guild_data.get("system_channel_flags", 0),
            default_message_notifications=guild_data.get("default_message_notifications", 0),
            explicit_content_filter=guild_data.get("explicit_content_filter", 0),
            verification_level=guild_data.get("verification_level", 0),
            afk_channel_id=guild_data.get("afk_channel_id"),
            afk_timeout=guild_data.get("afk_timeout", 300),
        )

        roles = self._fetch_roles(guild_id)
        channels = self._fetch_channels(guild_id)
        return GuildStructure(guild=guild, roles=roles, channels=channels, settings=settings)

    def _fetch_roles(self, guild_id: str) -> list[Role]:
        data = self._api.get(f"/guilds/{guild_id}/roles").json()
        return [
            Role(
                id=r["id"],
                name=r["name"],
                color=r.get("color", 0),
                hoist=r.get("hoist", False),
                position=r.get("position", 0),
                permissions=int(r.get("permissions", "0")),
                mentionable=r.get("mentionable", False),
            )
            for r in data
        ]

    def _fetch_channels(self, guild_id: str) -> list[Channel]:
        data = self._api.get(f"/guilds/{guild_id}/channels").json()
        channels: list[Channel] = []
        for ch in data:
            overwrites = [
                PermissionOverwrite(
                    id=ow["id"],
                    type=ow["type"],
                    allow=int(ow.get("allow", "0")),
                    deny=int(ow.get("deny", "0")),
                )
                for ow in ch.get("permission_overwrites", [])
            ]
            channels.append(
                Channel(
                    id=ch["id"],
                    name=ch["name"],
                    type=ch["type"],
                    position=ch.get("position", 0),
                    parent_id=ch.get("parent_id"),
                    topic=ch.get("topic"),
                    nsfw=ch.get("nsfw", False),
                    bitrate=ch.get("bitrate"),
                    user_limit=ch.get("user_limit"),
                    permission_overwrites=overwrites,
                )
            )
        return channels

    # -- write ------------------------------------------------------------

    def create_role(self, guild_id: str, *, name: str, color: int = 0,
                    permissions: int = 0) -> Role:
        validate_snowflake(guild_id, "guild_id")
        # Fluxer create role only accepts name, color, permissions.
        # hoist and mentionable must be set via update_role.
        body = {
            "name": _sanitize_name(name),
            "color": color,
            "permissions": str(permissions),
        }
        data = self._api.post(f"/guilds/{guild_id}/roles", json=body).json()
        return Role(
            id=data["id"],
            name=data["name"],
            color=data.get("color", 0),
            hoist=data.get("hoist", False),
            position=data.get("position", 0),
            permissions=int(data.get("permissions", "0")),
            mentionable=data.get("mentionable", False),
        )

    def update_role(self, guild_id: str, role_id: str, **fields) -> None:
        validate_snowflake(guild_id, "guild_id")
        validate_snowflake(role_id, "role_id")
        body = {}
        if "name" in fields:
            body["name"] = _sanitize_name(fields["name"])
        if "color" in fields:
            body["color"] = fields["color"]
        if "hoist" in fields:
            body["hoist"] = fields["hoist"]
        if "permissions" in fields:
            body["permissions"] = str(fields["permissions"])
        if "mentionable" in fields:
            body["mentionable"] = fields["mentionable"]
        if body:
            self._api.patch(f"/guilds/{guild_id}/roles/{role_id}", json=body)

    def update_role_positions(self, guild_id: str, positions: list[dict]) -> None:
        """positions: [{"id": role_id, "position": int}, ...]"""
        validate_snowflake(guild_id, "guild_id")
        if positions:
            self._api.patch(f"/guilds/{guild_id}/roles", json=positions)

    def create_channel(self, guild_id: str, *, name: str, type: int,
                       position: int | None = None,
                       parent_id: str | None = None, topic: str | None = None,
                       nsfw: bool = False,
                       bitrate: int | None = None,
                       user_limit: int | None = None,
                       permission_overwrites: list[dict] | None = None) -> Channel:
        validate_snowflake(guild_id, "guild_id")
        if parent_id is not None:
            validate_snowflake(parent_id, "parent_id")
        body: dict = {"name": _sanitize_name(name), "type": type}
        if position is not None:
            body["position"] = position
        if parent_id is not None:
            body["parent_id"] = parent_id
        if topic is not None:
            body["topic"] = _sanitize_topic(topic)
        if nsfw:
            body["nsfw"] = True
        if bitrate is not None:
            body["bitrate"] = bitrate
        if user_limit is not None:
            body["user_limit"] = user_limit
        if permission_overwrites:
            body["permission_overwrites"] = permission_overwrites
        data = self._api.post(f"/guilds/{guild_id}/channels", json=body).json()
        return Channel(
            id=data["id"],
            name=data["name"],
            type=data["type"],
            position=data.get("position", 0),
            parent_id=data.get("parent_id"),
            topic=data.get("topic"),
            nsfw=data.get("nsfw", False),
            bitrate=data.get("bitrate"),
            user_limit=data.get("user_limit"),
        )

    def update_channel_positions(self, guild_id: str, positions: list[dict]) -> None:
        """positions: [{"id": ch_id, "position": int}, ...]"""
        validate_snowflake(guild_id, "guild_id")
        if positions:
            self._api.patch(f"/guilds/{guild_id}/channels", json=positions)

    def update_guild_settings(self, guild_id: str, **fields) -> None:
        validate_snowflake(guild_id, "guild_id")
        if fields:
            self._api.patch(f"/guilds/{guild_id}", json=fields)

    def set_permission_overwrite(self, channel_id: str, overwrite_id: str,
                                 *, type: int, allow: int, deny: int) -> None:
        validate_snowflake(channel_id, "channel_id")
        validate_snowflake(overwrite_id, "overwrite_id")
        body = {"type": type, "allow": str(allow), "deny": str(deny)}
        self._api.put(f"/channels/{channel_id}/permissions/{overwrite_id}", json=body)
