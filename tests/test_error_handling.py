from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest
from discord import app_commands
from discord.ext import commands

from error_handling import (ErrorHandledModal, ErrorHandledView, error_message,
                            handle_interaction_error, handle_prefix_error)


def interaction(done=False):
    return SimpleNamespace(
        extras={}, id=42, user=SimpleNamespace(id=1), guild_id=2, channel_id=3,
        command=SimpleNamespace(name='relay', qualified_name='relay'),
        response=SimpleNamespace(is_done=Mock(return_value=done), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('done', [False, True])
async def test_reply_before_and_after_acknowledgement(done, caplog):
    i = interaction(done)
    error = RuntimeError('private database details')
    await handle_interaction_error(i, app_commands.CommandInvokeError(i.command, error))
    sender = i.followup.send if done else i.response.send_message
    sender.assert_awaited_once()
    assert sender.call_args.kwargs['ephemeral'] is True
    assert 'private database details' not in sender.call_args.args[0]
    assert 'private database details' in caplog.text
    assert 'guild=2' in caplog.text
    # Local/cog/tree dispatch must not duplicate a reply.
    await handle_interaction_error(i, error)
    sender.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_error_reply_is_logged(caplog):
    i = interaction()
    i.response.send_message.side_effect = discord.NotFound(
        SimpleNamespace(status=404, reason='Not Found'), 'Expired interaction')
    await handle_interaction_error(i, RuntimeError('failure'))
    assert 'Could not report interaction error reference=42' in caplog.text


@pytest.mark.asyncio
async def test_ui_boundaries():
    i = interaction()
    await ErrorHandledView().on_error(i, RuntimeError('button failure'), object())
    i.response.send_message.assert_awaited_once()
    i = interaction(True)
    await ErrorHandledModal(title='Test').on_error(i, RuntimeError('modal failure'))
    i.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_prefix_and_hybrid_errors():
    ctx = SimpleNamespace(interaction=None, command=SimpleNamespace(qualified_name='test'),
                          author=SimpleNamespace(id=1), guild=None,
                          channel=SimpleNamespace(id=3), message=SimpleNamespace(id=42),
                          send=AsyncMock())
    await handle_prefix_error(ctx, RuntimeError('failure'))
    await handle_prefix_error(ctx, RuntimeError('failure'))
    ctx.send.assert_awaited_once()
    i = interaction(True)
    ctx = SimpleNamespace(interaction=i)
    await handle_prefix_error(ctx, commands.BadArgument('bad value'))
    i.followup.send.assert_awaited_once()


def test_expected_errors_have_specific_messages():
    assert 'manage roles' in error_message(app_commands.BotMissingPermissions(['manage_roles']))
    assert '5.0 seconds' in error_message(app_commands.CommandOnCooldown(app_commands.Cooldown(1, 10), 5))
    assert 'required role' in error_message(app_commands.CheckFailure())
