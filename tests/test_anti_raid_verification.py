import time
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import discord
import pytest

from cogs.anti_raid import AntiRaid
from cogs.autoroles import Autoroles


@pytest.fixture
def protection(tmp_path, monkeypatch):
    import cogs.anti_raid as raid
    import cogs.autoroles as roles
    path = str(tmp_path / 'bot.db')
    monkeypatch.setattr(raid, 'DB_PATH', path)
    monkeypatch.setattr(roles, 'DB_PATH', path)
    return AntiRaid(NS(user=NS(id=999))), Autoroles(NS())


def member(uid=5):
    guild = NS(id=1, owner_id=99, me=NS(top_role=10, guild_permissions=NS(moderate_members=True, ban_members=True)))
    m = NS(timed_out_until=None, id=uid, guild=guild, bot=False, top_role=1, roles=[],
           guild_permissions=NS(administrator=False, manage_guild=False, moderate_members=False, ban_members=False, manage_messages=False, kick_members=False),
           timeout=AsyncMock(), ban=AsyncMock(), created_at=discord.utils.utcnow()-timedelta(days=100),
           add_roles=AsyncMock(), remove_roles=AsyncMock())
    guild.get_member = lambda _: m
    return m


@pytest.mark.asyncio
async def test_join_burst_persists_hold_and_times_out(protection):
    raid, _ = protection
    m = member()
    raid.store.set(1, 'anti_raid', 'enabled', True)
    raid.store.set(1, 'anti_raid', 'join_threshold', 2)
    raid.alert = AsyncMock()
    await raid.on_member_join(m)
    m.timeout.assert_not_awaited()
    m.id = 6
    await raid.on_member_join(m)
    assert raid.get(1, 'hold_until') > time.time()
    assert m.timeout.await_count == 2
    # A fresh cog sees the persisted deadline.
    assert AntiRaid(raid.bot).get(1, 'hold_until') > time.time()


@pytest.mark.asyncio
async def test_hierarchy_staff_and_failed_action_do_not_create_cases(protection):
    raid, _ = protection
    m = member()
    m.top_role = 20
    assert 'not applied' in await raid.restrict(m, 'test', ban=True)
    m.ban.assert_not_awaited()
    m.top_role = 1
    m.guild_permissions.manage_guild = True
    assert await raid.restrict(m, 'test', ban=True) == 'exempt'
    m.ban.assert_not_awaited()


@pytest.mark.asyncio
async def test_hold_blocks_verification_without_role_changes(protection):
    raid, verification = protection
    m = member()
    verification.store.set(1, 'verification', 'enabled', True)
    raid.store.set(1, 'anti_raid', 'hold_until', time.time()+60)
    verification.validation_errors = lambda _: []
    interaction = NS(guild=m.guild, user=m, followup=NS(send=AsyncMock()))
    await verification._verify_locked(interaction)
    m.add_roles.assert_not_awaited()
    m.remove_roles.assert_not_awaited()
    assert 'paused' in interaction.followup.send.call_args.args[0]
    await verification._verify_locked(interaction)
    assert 'wait' in interaction.followup.send.call_args.args[0]


@pytest.mark.asyncio
async def test_failed_grant_keeps_unverified_roles(protection):
    _, verification = protection
    m = member()
    m.guild.get_role = lambda _: NS(id=100)
    verification._roles = lambda _: ([100], [])
    verification.validation_errors = lambda _: []
    verification.store.set(1, 'verification', 'enabled', True)
    m.add_roles.side_effect = discord.Forbidden(NS(status=403, reason='Forbidden'), 'denied')
    interaction = NS(guild=m.guild, user=m, followup=NS(send=AsyncMock()))
    await verification._verify_locked(interaction)
    m.remove_roles.assert_not_awaited()
    assert 'Error ID' in interaction.followup.send.call_args.args[0]


@pytest.mark.asyncio
async def test_honeypot_defaults_off_and_timeout_then_opt_in_ban(protection):
    raid, _ = protection
    m = member()
    # Supply an actual Member mock for the listener's type check.
    from unittest.mock import Mock
    actual = Mock(spec=discord.Member)
    for name, value in vars(m).items():
        setattr(actual, name, value)
    message = NS(guild=m.guild, author=actual, webhook_id=None, channel=NS(id=50), delete=AsyncMock())
    raid.alert = AsyncMock()
    await raid.on_message(message)
    message.delete.assert_not_awaited()
    raid.store.set(1, 'anti_raid', 'honeypot_enabled', True)
    raid.store.set(1, 'anti_raid', 'honeypot_channel', 50)
    await raid.on_message(message)
    m.timeout.assert_awaited_once()
    m.ban.assert_not_awaited()
    raid.store.set(1, 'anti_raid', 'honeypot_ban', True)
    await raid.on_message(message)
    m.ban.assert_awaited_once()
