import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from cogs.config import OBSERVER_SUPPORT_GUILD_ID
from cogs.setup_ui import ModuleView, build_module_embed, get_module, setting_visible
from error_reporting import ErrorIdFilter, ErrorReport, ErrorReporter


def record(message='secret token https://secret.invalid?token=xyz'):
    try:
        raise RuntimeError(message)
    except RuntimeError:
        import sys
        return logging.LogRecord('observer.security', logging.ERROR, '/private/file.py', 1,
                                 message, (), sys.exc_info())


def bot_with_channel(channel):
    guild = SimpleNamespace(id=OBSERVER_SUPPORT_GUILD_ID,
                            get_channel=Mock(return_value=channel), fetch_channel=AsyncMock())
    bot = SimpleNamespace(setup_store=SimpleNamespace(get=Mock(return_value=42)),
                          get_guild=Mock(return_value=guild), wait_until_ready=AsyncMock())
    return bot


def test_reports_never_include_raw_error_or_paths():
    r = record()
    ErrorIdFilter().filter(r)
    first = r.error_id
    ErrorIdFilter().filter(r)
    assert r.error_id == first
    report = ErrorReport.from_record(r)
    text = report.line()
    assert first in text
    assert 'RuntimeError' in text
    for secret in ['secret token', 'secret.invalid', 'xyz', '/private/file.py']:
        assert secret not in text


@pytest.mark.asyncio
async def test_setting_only_visible_in_support():
    module = get_module('bot')
    store = SimpleNamespace(get_module=Mock(return_value={}), get=Mock(return_value=None))
    cog = SimpleNamespace(store=store)
    for guild_id in [OBSERVER_SUPPORT_GUILD_ID, 123]:
        guild = SimpleNamespace(id=guild_id, name='Test', get_channel=Mock(return_value=None))
        view = ModuleView(cog, 'bot', guild)
        visible = any(item.custom_id == 'setup_bot_error_channel' for item in view.children)
        assert visible == (guild_id == OBSERVER_SUPPORT_GUILD_ID)
        embed = build_module_embed(store, guild, module)
        assert any(field.name == 'Global error channel' for field in embed.fields) == visible
    assert not setting_visible(123, 'bot', 'error_channel')
    assert setting_visible(123, 'bot', 'log_channel')


@pytest.mark.asyncio
async def test_routes_only_to_support_with_bounded_embed():
    channel = Mock(spec=discord.TextChannel)
    channel.guild = SimpleNamespace(id=OBSERVER_SUPPORT_GUILD_ID)
    channel.send = AsyncMock()
    bot = bot_with_channel(channel)
    reporter = ErrorReporter(bot, OBSERVER_SUPPORT_GUILD_ID)
    reports = [ErrorReport.from_record(record()) for _ in range(reporter.BATCH_SIZE)]
    await reporter.send_batch(reports)
    bot.setup_store.get.assert_called_once_with(OBSERVER_SUPPORT_GUILD_ID, 'bot', 'error_channel')
    channel.send.assert_awaited_once()
    embed = channel.send.call_args.kwargs['embed']
    assert len(embed) < 6000
    assert all(report.error_id in embed.description for report in reports)
    assert channel.send.call_args.kwargs['allowed_mentions'].everyone is False
    channel.send.reset_mock()
    channel.guild.id = 123
    await reporter.send_batch(reports)
    channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_delivery_failure_does_not_recurse(caplog):
    channel = Mock(spec=discord.TextChannel)
    channel.guild = SimpleNamespace(id=OBSERVER_SUPPORT_GUILD_ID)
    channel.send = AsyncMock(side_effect=RuntimeError('send failed'))
    bot = bot_with_channel(channel)
    async def wait_ready():
        await asyncio.Event().wait()
    bot.wait_until_ready = wait_ready
    reporter = ErrorReporter(bot, OBSERVER_SUPPORT_GUILD_ID)
    reporter.start()
    try:
        await reporter.send_batch([ErrorReport.from_record(record())])
        await asyncio.sleep(0)
        assert reporter.queue.empty()
        assert 'Could not send error reports' in caplog.text
        logging.getLogger('observer.test').error('background task failed')
        await asyncio.sleep(0)
        assert reporter.queue.qsize() == 1
    finally:
        await reporter.close()
    assert reporter.handler not in logging.getLogger().handlers


@pytest.mark.asyncio
async def test_disabled_channel_and_queue_overflow():
    bot = bot_with_channel(None)
    bot.setup_store.get.return_value = None
    reporter = ErrorReporter(bot, OBSERVER_SUPPORT_GUILD_ID)
    report = ErrorReport.from_record(record())
    await reporter.send_batch([report])
    bot.get_guild.assert_not_called()
    for _ in range(201):
        reporter.enqueue(report)
    assert reporter.queue.qsize() == 200
    assert reporter.dropped == 1


@pytest.mark.asyncio
async def test_selector_rejects_other_servers_and_missing_permissions():
    from cogs.setup_ui import ChannelSelectView
    store = SimpleNamespace(set=Mock(), get=Mock(return_value=None), get_module=Mock(return_value={}))
    cog = SimpleNamespace(store=store, has_setup_access=Mock(return_value=True))
    guild = SimpleNamespace(id=123)
    view = ChannelSelectView(cog, 'bot', 'error_channel', guild)
    i = SimpleNamespace(guild=guild, guild_id=123, response=SimpleNamespace(send_message=AsyncMock()))
    assert not await view.interaction_check(i)
    store.set.assert_not_called()
    channel = Mock(spec=discord.TextChannel)
    channel.permissions_for.return_value = SimpleNamespace(view_channel=True, send_messages=False, embed_links=True)
    guild = SimpleNamespace(id=OBSERVER_SUPPORT_GUILD_ID, me=object(), fetch_channel=AsyncMock(return_value=channel))
    view = ChannelSelectView(cog, 'bot', 'error_channel', guild)
    i = SimpleNamespace(guild=guild, data={'values': ['42']},
                        response=SimpleNamespace(defer=AsyncMock()),
                        followup=SimpleNamespace(send=AsyncMock()))
    await view._on_select(i)
    store.set.assert_not_called()
    assert 'Send Messages' in i.followup.send.call_args.args[0]


@pytest.mark.asyncio
async def test_command_id_matches_queued_report_and_expected_errors_are_skipped():
    from discord import app_commands
    from error_handling import handle_interaction_error
    bot = bot_with_channel(None)
    async def wait_ready():
        await asyncio.Event().wait()
    bot.wait_until_ready = wait_ready
    reporter = ErrorReporter(bot, OBSERVER_SUPPORT_GUILD_ID)
    reporter.start()
    try:
        def interaction():
            return SimpleNamespace(extras={}, id=42, user=SimpleNamespace(id=1), guild_id=123, channel_id=3,
                                   command=SimpleNamespace(qualified_name='relay'),
                                   response=SimpleNamespace(is_done=Mock(return_value=False), send_message=AsyncMock()))
        i = interaction()
        await handle_interaction_error(i, RuntimeError('private contents'))
        await asyncio.sleep(0)
        report = reporter.queue.get_nowait()
        assert report.error_id == i.extras['observer_error_id']
        assert report.error_id in i.response.send_message.call_args.args[0]
        assert report.guild_id == 123
        assert report.user_id == 1
        i = interaction()
        await handle_interaction_error(i, app_commands.CheckFailure())
        await asyncio.sleep(0)
        assert reporter.queue.empty()
        assert 'observer_error_id' not in i.extras
    finally:
        await reporter.close()
