from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Callable

from discord_to_fluxer.discord_api import DiscordAPI
from discord_to_fluxer.fluxer_api import FluxerAPI
from discord_to_fluxer.models import Channel, GuildSettings, GuildStructure, Role
from discord_to_fluxer.permissions import mask_permissions

log = logging.getLogger(__name__)

LogFn = Callable[[str], None]
ProgressFn = Callable[[int, int], None]  # (current, total)


def _normalize_channel_name(name: str) -> str:
    """Normalize a channel name for matching.

    Only lowercases and strips whitespace. Fluxer's server-side normalization
    is inconsistent (sometimes strips special chars, sometimes doesn't), so
    we keep it minimal and fall back to fuzzy matching.
    """
    return name.lower().strip()


def _reduce_name(name: str) -> str:
    """Reduce a name to its alphanumeric core for fuzzy comparison."""
    name = name.lower().strip()
    name = name.replace(" ", "-")
    return re.sub(r"[^a-z0-9\-_]", "", name).strip("-")


def _fuzzy_channel_match(a: str, b: str) -> bool:
    """Check if two channel names refer to the same channel.

    Handles the case where one side stripped special characters and the other didn't.
    e.g. 'in_bed[nsfw][rp]' should match 'in_bednsfwrp'.
    """
    na = _normalize_channel_name(a)
    nb = _normalize_channel_name(b)
    if na == nb:
        return True
    ra = _reduce_name(a)
    rb = _reduce_name(b)
    return ra == rb and ra != ""


def _channel_key(ch: Channel, parent_name: str | None) -> tuple[str, int, str | None]:
    """Unique key: (lowercased_name, type, lowercased_parent_name)."""
    norm_name = _normalize_channel_name(ch.name)
    norm_parent = _normalize_channel_name(parent_name) if parent_name else None
    return (norm_name, ch.type, norm_parent)


def _parent_name_map(channels: list[Channel]) -> dict[str | None, str | None]:
    """Map channel parent_id -> parent category name (or None)."""
    cats = {ch.id: ch.name for ch in channels if ch.type == 4}
    result: dict[str | None, str | None] = {None: None}
    for pid, name in cats.items():
        result[pid] = name
    return result


@dataclass
class DiffResult:
    """Result of diffing source vs destination structures."""
    matched_roles: list[tuple[Role, Role]] = field(default_factory=list)
    unsynced_roles: list[Role] = field(default_factory=list)
    matched_channels: list[tuple[Channel, Channel]] = field(default_factory=list)
    unsynced_channels: list[Channel] = field(default_factory=list)


def diff_structures(source: GuildStructure, dest: GuildStructure) -> DiffResult:
    """Compare source and destination, returning matched and unsynced items."""
    result = DiffResult()

    # Roles — match by name, with fuzzy fallback for server-side transforms.
    dest_roles_by_name = {r.name: r for r in dest.roles}
    claimed_dst_role_ids: set[str] = set()
    for src_role in source.roles:
        dst = dest_roles_by_name.get(src_role.name)
        if dst:
            result.matched_roles.append((src_role, dst))
            claimed_dst_role_ids.add(dst.id)
        else:
            fuzzy_match = None
            for dst_role in dest.roles:
                if dst_role.id in claimed_dst_role_ids:
                    continue
                if _fuzzy_channel_match(src_role.name, dst_role.name):
                    fuzzy_match = dst_role
                    break
            if fuzzy_match:
                result.matched_roles.append((src_role, fuzzy_match))
                claimed_dst_role_ids.add(fuzzy_match.id)
            else:
                result.unsynced_roles.append(src_role)

    # Channels — match by (name, type, parent_category_name)
    # First try exact (lowercased) key match, then fuzzy for special chars.
    src_parents = _parent_name_map(source.channels)
    dst_parents = _parent_name_map(dest.channels)
    dest_chan_by_key = {
        _channel_key(ch, dst_parents.get(ch.parent_id)): ch
        for ch in dest.channels
    }
    # Track which dest channels have been claimed by fuzzy match.
    claimed_dst_ids: set[str] = set()
    for src_ch in source.channels:
        key = _channel_key(src_ch, src_parents.get(src_ch.parent_id))
        dst = dest_chan_by_key.get(key)
        if dst:
            result.matched_channels.append((src_ch, dst))
            claimed_dst_ids.add(dst.id)
        else:
            # Fuzzy fallback: find a dest channel with same type, matching
            # parent, and fuzzy name match.
            src_parent_name = src_parents.get(src_ch.parent_id)
            fuzzy_match = None
            for dst_ch in dest.channels:
                if dst_ch.id in claimed_dst_ids:
                    continue
                if dst_ch.type != src_ch.type:
                    continue
                dst_parent_name = dst_parents.get(dst_ch.parent_id)
                parents_match = (
                    (src_parent_name is None and dst_parent_name is None)
                    or (src_parent_name is not None and dst_parent_name is not None
                        and _fuzzy_channel_match(src_parent_name, dst_parent_name))
                )
                if parents_match and _fuzzy_channel_match(src_ch.name, dst_ch.name):
                    fuzzy_match = dst_ch
                    break
            if fuzzy_match:
                result.matched_channels.append((src_ch, fuzzy_match))
                claimed_dst_ids.add(fuzzy_match.id)
            else:
                result.unsynced_channels.append(src_ch)

    return result


def sync(
    discord: DiscordAPI,
    fluxer: FluxerAPI,
    source_guild_id: str,
    dest_guild_id: str,
    source: GuildStructure | None = None,
    dest: GuildStructure | None = None,
    log_fn: LogFn | None = None,
    progress_fn: ProgressFn | None = None,
    selected_role_names: set[str] | None = None,
    selected_channel_ids: set[str] | None = None,
) -> DiffResult:
    """Run a full sync from Discord source to Fluxer destination.

    If source/dest structures are provided, uses them directly (and mutates
    dest in-place as items are created). Otherwise fetches fresh.
    Returns the diff result (post-sync) so the UI can update.
    progress_fn is called after each item is created so the UI can refresh.
    """
    def emit(msg: str) -> None:
        log.info(msg)
        if log_fn:
            log_fn(msg)

    progress_count = 0
    progress_total = 0
    failure_count = 0

    def notify_progress() -> None:
        nonlocal progress_count
        progress_count += 1
        if progress_fn:
            progress_fn(progress_count, progress_total)

    if source is None:
        emit("Fetching Discord guild structure...")
        source = discord.fetch_structure(source_guild_id)
        emit(f"  {len(source.roles)} roles, {len(source.channels)} channels")

    if dest is None:
        emit("Fetching Fluxer guild structure...")
        dest = fluxer.fetch_structure(dest_guild_id)
        emit(f"  {len(dest.roles)} roles, {len(dest.channels)} channels")

    diff = diff_structures(source, dest)

    # Filter to only user-selected items if provided.
    if selected_role_names is not None:
        diff.unsynced_roles = [r for r in diff.unsynced_roles if r.name in selected_role_names]
    if selected_channel_ids is not None:
        diff.unsynced_channels = [ch for ch in diff.unsynced_channels if ch.id in selected_channel_ids]

    progress_total = len(diff.unsynced_roles) + len(diff.unsynced_channels)

    if diff.unsynced_roles or diff.unsynced_channels:
        emit(f"{len(diff.unsynced_roles)} roles and {len(diff.unsynced_channels)} channels to create")
    else:
        emit("All roles and channels matched — checking positions...")

    # Build role name -> source Role map for permission remapping.
    src_role_by_id: dict[str, Role] = {r.id: r for r in source.roles}
    # Build role name -> fluxer ID map (start with existing matches).
    role_map: dict[str, str] = {
        src.name: dst.id for src, dst in diff.matched_roles
    }

    # --- Sync roles -------------------------------------------------------

    # Update matched roles that differ (hoist, color, permissions, mentionable).
    for src_role, dst_role in diff.matched_roles:
        masked = mask_permissions(src_role.permissions)
        updates: dict = {}
        if src_role.hoist != dst_role.hoist:
            updates["hoist"] = src_role.hoist
        if src_role.color != dst_role.color:
            updates["color"] = src_role.color
        if masked != mask_permissions(dst_role.permissions):
            updates["permissions"] = masked
        if src_role.mentionable != dst_role.mentionable:
            updates["mentionable"] = src_role.mentionable
        if updates:
            fields = ", ".join(updates.keys())
            emit(f"  Updating role: {src_role.name} ({fields})")
            try:
                fluxer.update_role(dest_guild_id, dst_role.id, **updates)
            except Exception as e:
                emit(f"  FAILED to update role: {src_role.name} \u2014 {e}")
                failure_count += 1

    # Create missing roles (skip @everyone — it always exists).
    for role in diff.unsynced_roles:
        if role.name == "@everyone":
            continue
        masked = mask_permissions(role.permissions)
        emit(f"  Creating role: {role.name} (perms={masked:#x})")
        try:
            created = fluxer.create_role(
                dest_guild_id,
                name=role.name,
                color=role.color,
                hoist=role.hoist,
                permissions=masked,
                mentionable=role.mentionable,
            )
            role_map[role.name] = created.id
            dest.roles.append(created)
        except Exception as e:
            emit(f"  FAILED to create role: {role.name} \u2014 {e}")
            failure_count += 1
        notify_progress()

    # Update role positions.
    position_updates = []
    for src_role in source.roles:
        fluxer_id = role_map.get(src_role.name)
        if fluxer_id and src_role.name != "@everyone":
            position_updates.append({"id": fluxer_id, "position": src_role.position})
    if position_updates:
        emit("Updating role positions...")
        try:
            fluxer.update_role_positions(dest_guild_id, position_updates)
        except Exception as e:
            emit(f"  Warning: role position update failed: {e}")

    # --- Sync channels ----------------------------------------------------

    # Need to know which source categories map to which parent names.
    src_parents = _parent_name_map(source.channels)

    # Build channel name -> fluxer ID map for parent references.
    channel_map: dict[str, str] = {}
    for src_ch, dst_ch in diff.matched_channels:
        if src_ch.type == 4:  # category
            channel_map[src_ch.name] = dst_ch.id

    # Create categories first.
    categories = [ch for ch in diff.unsynced_channels if ch.type == 4]
    children = [ch for ch in diff.unsynced_channels if ch.type != 4]

    for cat in sorted(categories, key=lambda c: c.position):
        emit(f"  Creating category: {cat.name} (pos={cat.position})")
        try:
            overwrites = _remap_overwrites(cat, src_role_by_id, role_map, emit)
            created = fluxer.create_channel(
                dest_guild_id,
                name=cat.name,
                type=4,
                position=cat.position,
                permission_overwrites=overwrites,
            )
            channel_map[cat.name] = created.id
            dest.channels.append(created)
        except Exception as e:
            emit(f"  FAILED to create category: {cat.name} \u2014 {e}")
            failure_count += 1
        notify_progress()

    # Create child channels.
    for ch in sorted(children, key=lambda c: c.position):
        parent_name = src_parents.get(ch.parent_id)
        parent_fluxer_id = channel_map.get(parent_name) if parent_name else None
        type_label = {0: "#", 2: "\U0001f508"}.get(ch.type, "?")
        emit(f"  Creating channel: {type_label} {ch.name} (pos={ch.position})")
        try:
            overwrites = _remap_overwrites(ch, src_role_by_id, role_map, emit)
            created = fluxer.create_channel(
                dest_guild_id,
                name=ch.name,
                type=ch.type,
                position=ch.position,
                parent_id=parent_fluxer_id,
                topic=ch.topic,
                nsfw=ch.nsfw,
                bitrate=ch.bitrate,
                user_limit=ch.user_limit,
                permission_overwrites=overwrites,
            )
            channel_map[ch.name] = created.id
            dest.channels.append(created)
        except Exception as e:
            emit(f"  FAILED to create channel: {type_label} {ch.name} \u2014 {e}")
            failure_count += 1
        notify_progress()

    # Update channel positions.
    # Re-fetch destination to get current state including new channels.
    dest_refreshed = fluxer.fetch_structure(dest_guild_id)
    dst_parents = _parent_name_map(dest_refreshed.channels)
    dst_chan_by_key = {
        _channel_key(ch, dst_parents.get(ch.parent_id)): ch
        for ch in dest_refreshed.channels
    }
    # Build dest category name -> dest ID for parent_id in position entries.
    dst_cat_by_name = {
        ch.name: ch.id for ch in dest_refreshed.channels if ch.type == 4
    }
    pos_updates = []
    for src_ch in source.channels:
        key = _channel_key(src_ch, src_parents.get(src_ch.parent_id))
        dst_ch = dst_chan_by_key.get(key)
        if dst_ch:
            entry: dict = {"id": dst_ch.id, "position": src_ch.position}
            if src_ch.type == 4:
                entry["parent_id"] = None
            else:
                src_parent_name = src_parents.get(src_ch.parent_id)
                entry["parent_id"] = dst_cat_by_name.get(src_parent_name) if src_parent_name else None
            pos_updates.append(entry)

    # Fluxer enforces voice channels below text within each category
    # (Discord removed this constraint). Re-sort and re-number.
    dst_type_by_id = {ch.id: ch.type for ch in dest_refreshed.channels}
    by_parent: dict[str | None, list[dict]] = {}
    for entry in pos_updates:
        by_parent.setdefault(entry.get("parent_id"), []).append(entry)
    pos_updates = []
    for entries in by_parent.values():
        entries.sort(key=lambda e: (
            0 if dst_type_by_id.get(e["id"], 0) != 2 else 1,
            e["position"],
        ))
        for i, ent in enumerate(entries):
            ent["position"] = i
        pos_updates.extend(entries)

    if pos_updates:
        emit(f"Updating {len(pos_updates)} channel positions...")
        try:
            fluxer.update_channel_positions(dest_guild_id, pos_updates)
        except Exception as e:
            emit(f"  Warning: channel position update failed: {e}")

    # --- Sync guild settings ------------------------------------------------

    if source.settings and dest.settings:
        # Build channel ID remap from matched channels.
        chan_id_map: dict[str, str] = {src_ch.id: dst_ch.id for src_ch, dst_ch in diff.matched_channels}

        patch: dict[str, object] = {}

        # Remap channel-reference settings through the channel map.
        src_system_ch = source.settings.system_channel_id
        if src_system_ch is not None:
            remapped = chan_id_map.get(src_system_ch)
            if remapped is None:
                emit(f"  Warning: system_channel_id {src_system_ch} has no match on dest, skipping")
            elif remapped != dest.settings.system_channel_id:
                patch["system_channel_id"] = remapped
        elif dest.settings.system_channel_id is not None:
            patch["system_channel_id"] = None

        src_afk_ch = source.settings.afk_channel_id
        if src_afk_ch is not None:
            remapped = chan_id_map.get(src_afk_ch)
            if remapped is None:
                emit(f"  Warning: afk_channel_id {src_afk_ch} has no match on dest, skipping")
            elif remapped != dest.settings.afk_channel_id:
                patch["afk_channel_id"] = remapped
        elif dest.settings.afk_channel_id is not None:
            patch["afk_channel_id"] = None

        # Direct-copy enum/int settings.
        if source.settings.system_channel_flags != dest.settings.system_channel_flags:
            patch["system_channel_flags"] = source.settings.system_channel_flags
        if source.settings.default_message_notifications != dest.settings.default_message_notifications:
            patch["default_message_notifications"] = source.settings.default_message_notifications
        if source.settings.explicit_content_filter != dest.settings.explicit_content_filter:
            patch["explicit_content_filter"] = source.settings.explicit_content_filter
        if source.settings.verification_level != dest.settings.verification_level:
            patch["verification_level"] = source.settings.verification_level
        if source.settings.afk_timeout != dest.settings.afk_timeout:
            patch["afk_timeout"] = source.settings.afk_timeout

        if patch:
            emit(f"Updating guild settings: {', '.join(patch.keys())}")
            try:
                fluxer.update_guild_settings(dest_guild_id, **patch)
            except Exception as e:
                emit(f"  Warning: guild settings update failed: {e}")
        else:
            emit("Guild settings already match")

    if failure_count:
        emit(f"Sync complete with {failure_count} failure(s)")
    else:
        emit("Sync complete!")

    # Return updated diff.
    dest_final = fluxer.fetch_structure(dest_guild_id)
    return diff_structures(source, dest_final)


def _remap_overwrites(
    ch: Channel,
    src_role_by_id: dict[str, Role],
    role_map: dict[str, str],
    emit: LogFn,
) -> list[dict]:
    """Translate Discord permission overwrites to Fluxer format using role name mapping."""
    result = []
    for ow in ch.permission_overwrites:
        if ow.type == 1:
            emit(f"    Skipping member-specific overwrite {ow.id} on {ch.name}")
            continue
        src_role = src_role_by_id.get(ow.id)
        if not src_role:
            emit(f"    Warning: unknown role ID {ow.id} in overwrite on {ch.name}")
            continue
        fluxer_role_id = role_map.get(src_role.name)
        if not fluxer_role_id:
            emit(f"    Warning: no Fluxer role for '{src_role.name}', skipping overwrite on {ch.name}")
            continue
        masked_allow = mask_permissions(ow.allow)
        masked_deny = mask_permissions(ow.deny)
        emit(f"    Perm on {ch.name}: {src_role.name} allow={masked_allow:#x} deny={masked_deny:#x}")
        result.append({
            "id": fluxer_role_id,
            "type": 0,
            "allow": str(masked_allow),
            "deny": str(masked_deny),
        })
    return result
