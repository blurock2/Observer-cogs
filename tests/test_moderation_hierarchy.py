from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from cogs.moderation import ModerationCog


@pytest.mark.asyncio
@pytest.mark.parametrize("actor_id,target_id,actor_rank,target_rank,bot_rank,allowed", [
    (10, 20, 5, 4, 10, True),
    (10, 20, 5, 5, 10, False),
    (10, 20, 5, 6, 10, False),
    (10, 99, 5, 1, 10, False),
    (99, 20, 1, 6, 10, True),
    (99, 20, 1, 10, 10, False),
    (10, 10, 5, 5, 10, False),
])
async def test_moderation_respects_actor_bot_and_owner_hierarchy(
    actor_id, target_id, actor_rank, target_rank, bot_rank, allowed
):
    actor = MagicMock(spec=discord.Member)
    actor.id = actor_id
    actor.top_role = actor_rank
    target = actor if target_id == actor_id else MagicMock(spec=discord.Member)
    target.id = target_id
    target.top_role = target_rank
    interaction = SimpleNamespace(
        user=actor,
        guild=SimpleNamespace(owner_id=99, me=SimpleNamespace(top_role=bot_rank)),
        response=SimpleNamespace(send_message=AsyncMock()),
    )
    cog = object.__new__(ModerationCog)
    assert await cog._validate_target(interaction, target, "ban") is allowed
