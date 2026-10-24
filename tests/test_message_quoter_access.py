from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from cogs.message_quoter import MessageQuoter
from cogs.setup_ui import get_visible_message_quoter_role_specs, message_quoter_role_number


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


@pytest.mark.parametrize(
    "saved_roles,expected_slots",
    [
        ({}, [1]),
        ({1: 111}, [1, 2]),
        ({2: 222}, [1, 2]),
        ({1: 111, 3: 333}, [1, 2, 3]),
        ({2: 222, 4: 444}, [1, 2, 4]),
        ({i: 100 + i for i in range(1, 11)}, list(range(1, 11))),
    ],
)
def test_quoter_role_slot_visibility_with_gaps(saved_roles, expected_slots):
    """Clearing an earlier slot must not hide a later restriction."""
    store = SimpleNamespace(
        get=lambda guild_id, module, key, default=None: saved_roles.get(
            int(key.rsplit("_", 1)[1]), default
        )
    )
    slots = get_visible_message_quoter_role_specs(
        store, SimpleNamespace(id=123)
    )

    assert [message_quoter_role_number(slot) for slot in slots] == expected_slots


def test_quoter_allows_every_member_when_no_roles_configured():
    member = MagicMock(spec=discord.Member)
    member.id = 123456
    member.roles = [SimpleNamespace(id=9876)]
    message = SimpleNamespace(author=member, guild=SimpleNamespace(id=456))

    cog = object.__new__(MessageQuoter)
    cog.config = SimpleNamespace(get_allowed_role_ids=lambda _: [])

    assert cog.can_use_quoter(message) is True
