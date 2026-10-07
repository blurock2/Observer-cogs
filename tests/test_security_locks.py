import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from cogs.security import MODULE_KEY, SecurityCog
from cogs.setup_ui import SetupConfigStore


def make_cog(tmp_path):
    cog = object.__new__(SecurityCog)
    cog.store = SetupConfigStore(str(tmp_path / "bot.db"))
    cog.lock_tasks = {}
    cog._permission_lock = asyncio.Lock()
    cog.cases = SimpleNamespace(create_case=MagicMock())
    cog._alert = AsyncMock()
    return cog


def make_channel(guild, channel_id):
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.guild = guild
    channel.name = "private"
    channel.mention = "#private"
    state = {"overwrite": discord.PermissionOverwrite(
        view_channel=False, read_message_history=False, send_messages=True
    )}

    def read(_):
        return discord.PermissionOverwrite.from_pair(*state["overwrite"].pair())

    async def write(_, *, overwrite, reason):
        state["overwrite"] = overwrite or discord.PermissionOverwrite()

    channel.overwrites_for.side_effect = read
    channel.set_permissions = AsyncMock(side_effect=write)
    return channel, state


def make_interaction(guild):
    return SimpleNamespace(
        guild=guild, user=SimpleNamespace(id=10, mention="@mod"),
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


@pytest.mark.asyncio
async def test_lock_and_unlock_preserve_other_permission_overwrites(tmp_path):
    cog = make_cog(tmp_path)
    guild = SimpleNamespace(id=1, default_role=object())
    channel, state = make_channel(guild, 2)
    interaction = make_interaction(guild)
    await SecurityCog.lock.callback(cog, interaction, channel)
    assert state["overwrite"].send_messages is False
    assert state["overwrite"].view_channel is False
    assert state["overwrite"].read_message_history is False
    assert cog._get(1, "channel_locks")["2"]["send_messages"] is True

    await SecurityCog.unlock.callback(cog, interaction, channel)
    assert state["overwrite"].send_messages is True
    assert state["overwrite"].view_channel is False
    assert state["overwrite"].read_message_history is False
    assert cog._get(1, "channel_locks") == {}


@pytest.mark.asyncio
async def test_expired_lock_recovers_from_database_after_restart(tmp_path):
    original = make_cog(tmp_path)
    original.store.set(1, MODULE_KEY, "channel_locks", {
        "2": {"send_messages": True, "expires_at": time.time() - 10}
    })
    restarted = make_cog(tmp_path)
    guild = SimpleNamespace(id=1, default_role=object())
    channel, state = make_channel(guild, 2)
    state["overwrite"].send_messages = False
    guild.get_channel = lambda _: channel
    restarted.bot = SimpleNamespace(
        wait_until_ready=AsyncMock(), guilds=[guild]
    )
    await restarted._recover_locks()
    await asyncio.gather(*restarted.lock_tasks.values())
    assert state["overwrite"].send_messages is True
    assert state["overwrite"].view_channel is False
    assert restarted._get(1, "channel_locks") == {}
    assert restarted.lock_tasks == {}


@pytest.mark.asyncio
async def test_failed_lockdown_keeps_recovery_and_can_be_disabled(tmp_path):
    cog = make_cog(tmp_path)
    guild = SimpleNamespace(
        id=1, default_role=object(), verification_level=discord.VerificationLevel.low,
        edit=AsyncMock(),
    )
    first, first_state = make_channel(guild, 2)
    second, second_state = make_channel(guild, 3)
    guild.text_channels = [first, second]
    guild.get_channel = lambda channel_id: {2: first, 3: second}[channel_id]
    original_write = second.set_permissions.side_effect
    second.set_permissions.side_effect = discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"), "Missing permissions"
    )
    interaction = make_interaction(guild)
    await SecurityCog.lockdown.callback(cog, interaction, SimpleNamespace(value="enable"))
    assert first_state["overwrite"].send_messages is False
    assert len(cog._get(1, "lockdown_previous")["channels"]) == 2
    assert cog._get(1, "lockdown_enabled") is True

    # A failed restore must also keep only the outstanding channel for retry.
    await SecurityCog.lockdown.callback(cog, interaction, SimpleNamespace(value="disable"))
    assert first_state["overwrite"].send_messages is True
    assert [item["id"] for item in cog._get(1, "lockdown_previous")["channels"]] == [3]
    second.set_permissions.side_effect = original_write
    await SecurityCog.lockdown.callback(cog, interaction, SimpleNamespace(value="disable"))
    assert second_state["overwrite"].send_messages is True
    assert first_state["overwrite"].view_channel is False
    assert cog._get(1, "lockdown_previous") is None
    assert cog._get(1, "lockdown_enabled") is False


@pytest.mark.asyncio
async def test_disabling_without_saved_lockdown_does_not_change_verification(tmp_path):
    cog = make_cog(tmp_path)
    guild = SimpleNamespace(id=1, edit=AsyncMock())
    await SecurityCog.lockdown.callback(
        cog, make_interaction(guild), SimpleNamespace(value="disable")
    )
    guild.edit.assert_not_awaited()
