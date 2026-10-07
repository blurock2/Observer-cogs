from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from cogs.message_quoter import MessageQuoter


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "linked_guild,channel_guild,view,history,private,member,expected",
    [
        (1, 1, True, True, False, True, True),
        (2, 2, True, True, False, True, False),
        (1, 2, True, True, False, True, False),
        (1, 1, False, True, False, True, False),
        (1, 1, True, False, False, True, False),
        (1, 1, True, True, True, False, False),
        (1, 1, True, True, True, True, True),
    ],
)
async def test_quote_requires_source_access(
    linked_guild, channel_guild, view, history, private, member, expected
):
    channel = MagicMock(spec=discord.Thread if private else discord.TextChannel)
    channel.guild = SimpleNamespace(id=channel_guild)
    channel.permissions_for.return_value = SimpleNamespace(
        view_channel=view, read_message_history=history, manage_threads=False
    )
    channel.fetch_message = AsyncMock()
    if private:
        channel.is_private.return_value = True
        channel.fetch_member = AsyncMock()
        if not member:
            channel.fetch_member.side_effect = discord.NotFound(
                SimpleNamespace(status=404, reason="Not Found"), "Not a thread member"
            )
    cog = object.__new__(MessageQuoter)
    cog.bot = SimpleNamespace(fetch_channel=AsyncMock(return_value=channel))
    cog.config = SimpleNamespace(
        is_enabled=lambda _: True, require_reply=lambda _: False
    )
    cog.can_use_quoter = lambda _: True
    cog.quote_message = AsyncMock()
    message = SimpleNamespace(
        guild=SimpleNamespace(id=1),
        author=SimpleNamespace(id=10, bot=False),
        content=f"https://discord.com/channels/{linked_guild}/20/30",
        channel=object(),
    )

    await cog.on_message(message)

    assert cog.quote_message.await_count == int(expected)
    assert channel.fetch_message.await_count == int(expected)
