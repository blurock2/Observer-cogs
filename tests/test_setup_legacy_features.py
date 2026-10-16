from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from cogs.setup_ui import MODULES, MainView, ModuleView, SetupConfigStore
from cogs.autoroles import Autoroles
from cogs.reminder import Reminder


@pytest.mark.asyncio
async def test_dashboard_pages_reach_all_modules(tmp_path):
    store = SetupConfigStore(str(tmp_path / 'bot.db'))
    cog = SimpleNamespace(store=store)
    guild = SimpleNamespace(id=123, name='Test', get_role=lambda _: None, get_channel=lambda _: None)
    seen = []
    for page in range(MainView(cog).page_count):
        view = MainView(cog, page)
        options = view.children[0].options
        assert 1 <= len(options) <= 25
        seen.extend(option.value for option in options)
        if view.page_count > 1:
            assert view.children[1].disabled == (page == 0)
            assert view.children[2].disabled == (page == view.page_count - 1)
    assert seen == [module.key for module in MODULES]
    for key in ('afk', 'autorole', 'verification', 'reminders'):
        assert key in seen
        assert len(ModuleView(cog, key, guild).children) <= 25


@pytest.mark.asyncio
async def test_reminder_fallback_requires_opt_in_and_member_access(tmp_path):
    cog = object.__new__(Reminder)
    cog._database_path = str(tmp_path / 'bot.db')
    store = SetupConfigStore(cog._database_path)
    member = SimpleNamespace(id=7, mention='<@7>')
    channel = SimpleNamespace(send=AsyncMock(), permissions_for=Mock(return_value=SimpleNamespace(view_channel=True)))
    guild = SimpleNamespace(get_channel_or_thread=lambda _: channel, get_member=lambda _: member)
    cog.bot = SimpleNamespace(get_guild=lambda _: guild)
    reminder = dict(id=1, user_id=7, guild_id=123, channel_id=456, content='task')
    assert not await cog._send_fallback(reminder)
    channel.send.assert_not_awaited()
    store.set(123, 'reminders', 'fallback_enabled', True)
    assert await cog._send_fallback(reminder)
    channel.send.assert_awaited_once()
    channel.send.reset_mock()
    channel.permissions_for.return_value.view_channel = False
    assert not await cog._send_fallback(reminder)
    channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_verification_panel_uses_dashboard_settings(tmp_path, monkeypatch):
    import cogs.autoroles as module
    monkeypatch.setattr(module, 'DB_PATH', str(tmp_path / 'bot.db'))
    cog = Autoroles(SimpleNamespace())
    channel = Mock(spec=discord.TextChannel)
    channel.id = 456
    channel.mention = '<#456>'
    channel.send = AsyncMock()
    guild = SimpleNamespace(id=123, get_channel=lambda _: channel)
    interaction = SimpleNamespace(guild=guild, channel=None, response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))
    cog.store.set(123, 'verification', 'channel', 456)
    cog.store.set(123, 'verification', 'message', 'Custom verification text')
    cog.store.set(123, 'verification', 'enabled', False)
    cog.validation_errors = lambda _: []
    await Autoroles.verify_setup.callback(cog, interaction)
    assert channel.send.call_args.args == ('Custom verification text',)
    assert cog._config(123)['enabled'] is True
