from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PermissionOverwrite:
    id: str
    type: int  # 0 = role, 1 = member
    allow: int = 0
    deny: int = 0


@dataclass
class Role:
    id: str
    name: str
    color: int = 0
    hoist: bool = False
    position: int = 0
    permissions: int = 0
    mentionable: bool = False


@dataclass
class Channel:
    id: str
    name: str
    type: int  # 0=text, 2=voice, 4=category
    position: int = 0
    parent_id: str | None = None
    topic: str | None = None
    nsfw: bool = False
    permission_overwrites: list[PermissionOverwrite] = field(default_factory=list)


@dataclass
class GuildSettings:
    system_channel_id: str | None = None
    system_channel_flags: int = 0
    default_message_notifications: int = 0
    explicit_content_filter: int = 0
    verification_level: int = 0
    afk_channel_id: str | None = None
    afk_timeout: int = 300


@dataclass
class GuildInfo:
    id: str
    name: str


@dataclass
class GuildStructure:
    guild: GuildInfo
    roles: list[Role] = field(default_factory=list)
    channels: list[Channel] = field(default_factory=list)
    settings: GuildSettings | None = None
