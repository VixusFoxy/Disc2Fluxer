from __future__ import annotations

from discord_to_fluxer.api_client import APIClient, validate_snowflake
from discord_to_fluxer.models import (
    Channel,
    GuildInfo,
    GuildSettings,
    GuildStructure,
    PermissionOverwrite,
    Role,
)

DISCORD_BASE = "https://discord.com/api/v10"

# Channel types we can translate.  Anything else is skipped.
_CHANNEL_TYPE_MAP = {
    0: 0,   # text -> text
    2: 2,   # voice -> voice
    4: 4,   # category -> category
    5: 0,   # announcement -> text
    13: 2,  # stage -> voice
    15: 0,  # forum -> text
}


class DiscordAPI:
    def __init__(self, token: str, log_fn=None) -> None:
        self._api = APIClient(DISCORD_BASE, token, log_fn=log_fn)
        self._log_fn = log_fn

    def close(self) -> None:
        self._api.close()

    def list_guilds(self) -> list[GuildInfo]:
        data = self._api.get("/users/@me/guilds").json()
        return [GuildInfo(id=g["id"], name=g["name"]) for g in data]

    def fetch_structure(self, guild_id: str) -> GuildStructure:
        validate_snowflake(guild_id, "guild_id")
        guild_data = self._api.get(f"/guilds/{guild_id}").json()
        guild = GuildInfo(id=guild_data["id"], name=guild_data["name"])

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
            mapped_type = _CHANNEL_TYPE_MAP.get(ch["type"])
            if mapped_type is None:
                if self._log_fn:
                    self._log_fn(f"  Skipping unsupported channel type {ch['type']}: {ch.get('name', '?')}")
                continue
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
                    type=mapped_type,
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
