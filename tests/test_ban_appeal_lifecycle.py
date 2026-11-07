from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from cogs.moderation import (
    BanAppealDecisionModal,
    BanAppealReviewView,
    ModerationCog,
)


def _review_fixture(*, target=None, decision="accepted"):
    appeal = {
        "id": 1,
        "guild_id": 100,
        "target_id": 200,
        "ban_case_id": 300,
        "appeal_case_id": 400,
        "staff_channel_id": 500,
        "status": "pending",
    }
    ban_case = {
        "id": 300,
        "target_id": 200,
        "moderator_id": 20,
        "appeal_message_id": None,
        "action_id": None,
    }
    target = target or SimpleNamespace(send=AsyncMock())
    staff_message = SimpleNamespace(embeds=[], edit=AsyncMock())
    channel = SimpleNamespace(fetch_message=AsyncMock(return_value=staff_message))
    moderator = SimpleNamespace(
        id=10,
        mention="<@10>",
        guild_permissions=SimpleNamespace(
            administrator=True, ban_members=True, moderate_members=True
        ),
    )
    guild = SimpleNamespace(
        id=100,
        owner_id=10,
        name="Test guild",
        get_channel=Mock(return_value=channel),
        fetch_channel=AsyncMock(return_value=channel),
        fetch_ban=AsyncMock(return_value=SimpleNamespace(user=target)),
        unban=AsyncMock(),
    )
    interaction = SimpleNamespace(
        guild=guild,
        user=moderator,
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    cog = object.__new__(ModerationCog)
    cog.bot = SimpleNamespace(
        get_user=Mock(return_value=target),
        fetch_user=AsyncMock(return_value=target),
        user=SimpleNamespace(id=999),
    )
    cog.appeals = SimpleNamespace(
        get_by_staff_message=Mock(return_value=appeal),
        claim_review=Mock(return_value=True),
        set_status=Mock(),
        release_review=Mock(),
        mark_controls_disabled=Mock(),
    )
    cog.cases = SimpleNamespace(
        get_case=Mock(return_value=ban_case),
        set_case_status=Mock(),
        create_case=Mock(return_value=700),
    )
    cog.actions = SimpleNamespace(create=Mock(return_value=600), mark_undone=Mock())
    cog._check_mod_perms = AsyncMock(return_value=True)
    cog._send_mod_log = AsyncMock()
    cog._update_ban_appeal_dm = AsyncMock(return_value=True)
    return cog, interaction, target, staff_message, appeal, ban_case


@pytest.mark.asyncio
async def test_accept_defers_before_network_io_unbans_and_sends_custom_dm():
    cog, interaction, target, staff_message, appeal, _ = _review_fixture()
    sequence = []

    async def defer(*, ephemeral=False):
        sequence.append("defer")

    async def fetch_ban(_):
        assert sequence == ["defer", "fetch_staff_message"]
        sequence.append("fetch_ban")
        return SimpleNamespace(user=target)

    async def fetch_staff_message(_):
        assert sequence == ["defer"]
        sequence.append("fetch_staff_message")
        return staff_message

    channel = interaction.guild.get_channel.return_value
    channel.fetch_message.side_effect = fetch_staff_message
    interaction.response.defer.side_effect = defer
    interaction.guild.fetch_ban.side_effect = fetch_ban

    await cog.review_ban_appeal(
        interaction, "accepted", 900, "Your appeal was reviewed."
    )

    assert sequence == ["defer", "fetch_staff_message", "fetch_ban"]
    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    interaction.guild.unban.assert_awaited_once()
    cog.appeals.set_status.assert_called_once_with(
        appeal["id"], "accepted", reviewer_id=interaction.user.id
    )
    target.send.assert_awaited_once()
    dm = target.send.await_args.args[0]
    assert "appealed successfully" in dm
    assert "Your appeal was reviewed." in dm
    assert staff_message.edit.await_count == 1
    assert staff_message.edit.await_args.kwargs["view"] is None
    interaction.followup.send.assert_awaited()


@pytest.mark.asyncio
async def test_reject_keeps_ban_and_sends_custom_dm():
    cog, interaction, target, staff_message, appeal, _ = _review_fixture()

    await cog.review_ban_appeal(
        interaction, "rejected", 900, "Please provide more evidence next time."
    )

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    interaction.guild.fetch_ban.assert_not_awaited()
    interaction.guild.unban.assert_not_awaited()
    cog.appeals.set_status.assert_called_once_with(
        appeal["id"], "rejected", reviewer_id=interaction.user.id
    )
    dm = target.send.await_args.args[0]
    assert "declined" in dm
    assert "Please provide more evidence next time." in dm
    assert staff_message.edit.await_count == 1


@pytest.mark.asyncio
async def test_duplicate_review_does_not_repeat_unban_or_decision():
    cog, interaction, target, staff_message, _, _ = _review_fixture()
    cog.appeals.claim_review.return_value = False

    await cog.review_ban_appeal(interaction, "accepted", 900, "")

    interaction.guild.fetch_ban.assert_not_awaited()
    interaction.guild.unban.assert_not_awaited()
    cog.appeals.set_status.assert_not_called()
    target.send.assert_not_awaited()
    staff_message.edit.assert_not_awaited()
    interaction.followup.send.assert_awaited()


@pytest.mark.asyncio
async def test_permission_denial_does_not_claim_or_process_appeal():
    cog, interaction, target, staff_message, _, _ = _review_fixture()
    interaction.user.id = 11
    interaction.guild.owner_id = 10
    interaction.user.guild_permissions = SimpleNamespace(
        administrator=False, ban_members=False, moderate_members=False
    )
    cog._check_mod_perms.return_value = False

    await cog.review_ban_appeal(interaction, "accepted", 900, "")

    cog.appeals.claim_review.assert_not_called()
    interaction.guild.unban.assert_not_awaited()
    target.send.assert_not_awaited()
    staff_message.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_staff_message_does_not_claim_appeal():
    cog, interaction, _, _, _, _ = _review_fixture()
    channel = interaction.guild.get_channel.return_value
    channel.fetch_message.side_effect = discord.NotFound(
        response=Mock(status=404, reason="Not Found"), message="missing"
    )

    await cog.review_ban_appeal(interaction, "accepted", 900, "")

    cog.appeals.claim_review.assert_not_called()
    interaction.guild.unban.assert_not_awaited()


@pytest.mark.asyncio
async def test_precommit_discord_failure_releases_review_for_retry():
    cog, interaction, target, staff_message, appeal, _ = _review_fixture()
    interaction.guild.fetch_ban.side_effect = discord.Forbidden(
        response=Mock(status=403, reason="Forbidden"), message="forbidden"
    )

    await cog.review_ban_appeal(interaction, "accepted", 900, "")

    cog.appeals.release_review.assert_called_once_with(appeal["id"])
    cog.appeals.set_status.assert_not_called()
    interaction.guild.unban.assert_not_awaited()
    target.send.assert_not_awaited()
    staff_message.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_lost_unban_response_is_idempotent_on_retry():
    cog, interaction, target, staff_message, appeal, _ = _review_fixture()
    ban_still_exists = True

    async def unban_then_lose_response(*args, **kwargs):
        nonlocal ban_still_exists
        ban_still_exists = False
        raise RuntimeError("simulated lost response after Discord applied unban")

    async def fetch_ban(_):
        if not ban_still_exists:
            raise discord.NotFound(
                response=Mock(status=404, reason="Not Found"), message="not banned"
            )
        return SimpleNamespace(user=target)

    interaction.guild.unban.side_effect = unban_then_lose_response
    interaction.guild.fetch_ban.side_effect = fetch_ban

    await cog.review_ban_appeal(interaction, "accepted", 900, "")
    cog.appeals.release_review.assert_called_once_with(appeal["id"])
    cog.appeals.set_status.assert_not_called()
    assert ban_still_exists is False

    cog.appeals.release_review.reset_mock()
    await cog.review_ban_appeal(interaction, "accepted", 900, "Retry message")

    interaction.guild.unban.assert_awaited_once()
    cog.appeals.set_status.assert_called_once_with(
        appeal["id"], "accepted", reviewer_id=interaction.user.id
    )
    assert staff_message.edit.await_count == 1
    assert "Retry message" in target.send.await_args.args[0]


@pytest.mark.asyncio
async def test_dm_failure_does_not_block_staff_message_update():
    cog, interaction, target, staff_message, _, _ = _review_fixture()
    target.send.side_effect = RuntimeError("simulated DM transport failure")

    await cog.review_ban_appeal(interaction, "rejected", 900, "Custom rejection")

    assert staff_message.edit.await_count == 1
    assert interaction.followup.send.await_args.args[0].find("couldn't DM") >= 0


@pytest.mark.asyncio
async def test_staff_message_edit_failure_does_not_reverse_committed_decision():
    cog, interaction, target, staff_message, appeal, _ = _review_fixture()
    staff_message.edit.side_effect = RuntimeError("simulated message edit failure")

    await cog.review_ban_appeal(interaction, "rejected", 900, "")

    cog.appeals.set_status.assert_called_once_with(
        appeal["id"], "rejected", reviewer_id=interaction.user.id
    )
    target.send.assert_awaited_once()
    cog.appeals.release_review.assert_not_called()
    assert "secondary logging or UI updates failed" in interaction.followup.send.await_args.args[0]


def test_review_view_is_persistent_and_decision_modal_has_optional_message():
    cog, *_ = _review_fixture()
    view = BanAppealReviewView(cog)

    assert view.timeout is None
    assert view.is_persistent()
    assert {item.custom_id for item in view.children} == {
        "moderation:appeal_accept",
        "moderation:appeal_reject",
    }

    modal = BanAppealDecisionModal(cog, "accepted", 900)
    assert len(modal.children) == 1
    assert modal.children[0].required is False
    assert modal.children[0].max_length == 1500


@pytest.mark.asyncio
async def test_decision_modal_passes_optional_message_to_review():
    cog = SimpleNamespace(review_ban_appeal=AsyncMock())
    modal = BanAppealDecisionModal(cog, "rejected", 900)
    modal.message._value = "Please appeal again with evidence."
    interaction = SimpleNamespace()

    await modal.on_submit(interaction)

    args = cog.review_ban_appeal.await_args.args
    assert args[0] is interaction
    assert args[1:] == (
        "rejected",
        900,
        "Please appeal again with evidence.",
    )

@pytest.mark.asyncio
async def test_committed_decision_repairs_stale_staff_controls_on_duplicate_click():
    cog, interaction, target, staff_message, appeal, _ = _review_fixture()
    appeal["status"] = "accepted"
    cog.appeals.claim_review.return_value = False

    await cog.review_ban_appeal(interaction, "accepted", 900, "")

    interaction.guild.unban.assert_not_awaited()
    target.send.assert_not_awaited()
    assert staff_message.edit.await_count == 1
    assert staff_message.edit.await_args.kwargs["view"] is None
    assert "already accepted" in interaction.followup.send.await_args.args[0]


@pytest.mark.asyncio
async def test_missing_target_dm_does_not_prevent_rejection_record_or_ui_update():
    cog, interaction, _, staff_message, appeal, _ = _review_fixture()
    cog.bot.get_user.return_value = None
    cog.bot.fetch_user.side_effect = discord.NotFound(
        response=Mock(status=404, reason="Not Found"), message="unknown user"
    )

    await cog.review_ban_appeal(interaction, "rejected", 900, "Decision note")

    cog.appeals.set_status.assert_called_once_with(
        appeal["id"], "rejected", reviewer_id=interaction.user.id
    )
    assert staff_message.edit.await_count == 1
    assert "couldn't DM" in interaction.followup.send.await_args.args[0]


@pytest.mark.asyncio
async def test_unavailable_staff_channel_does_not_claim_appeal():
    cog, interaction, _, _, _, _ = _review_fixture()
    interaction.guild.get_channel.return_value = None
    interaction.guild.fetch_channel.side_effect = discord.Forbidden(
        response=Mock(status=403, reason="Forbidden"), message="forbidden"
    )

    await cog.review_ban_appeal(interaction, "accepted", 900, "")

    cog.appeals.claim_review.assert_not_called()
    interaction.guild.unban.assert_not_awaited()


@pytest.mark.asyncio
async def test_case_database_failure_after_decision_does_not_leave_stale_controls():
    cog, interaction, target, staff_message, appeal, _ = _review_fixture()
    cog.cases.set_case_status.side_effect = RuntimeError("simulated case DB failure")

    await cog.review_ban_appeal(interaction, "rejected", 900, "")

    cog.appeals.set_status.assert_called_once_with(
        appeal["id"], "rejected", reviewer_id=interaction.user.id
    )
    target.send.assert_awaited_once()
    assert staff_message.edit.await_count == 1
    assert "secondary logging or UI updates failed" in interaction.followup.send.await_args.args[0]


@pytest.mark.asyncio
async def test_decision_dm_disables_everyone_mentions_and_stays_under_limit():
    cog, interaction, target, _, _, _ = _review_fixture()
    note = "@everyone " + ("reviewed " * 170)

    await cog.review_ban_appeal(interaction, "rejected", 900, note)

    content = target.send.await_args.args[0]
    mentions = target.send.await_args.kwargs["allowed_mentions"]
    assert len(content) <= 1950
    assert mentions.everyone is False
    assert mentions.users is False
    assert mentions.roles is False


@pytest.mark.asyncio
async def test_accept_and_reject_buttons_open_the_correct_decision_modal():
    cog, interaction, *_ = _review_fixture()
    interaction.message = SimpleNamespace(id=900)
    interaction.response.send_modal = AsyncMock()
    view = BanAppealReviewView(cog)

    await view.accept(interaction)
    accepted_modal = interaction.response.send_modal.await_args.args[0]
    assert isinstance(accepted_modal, BanAppealDecisionModal)
    assert accepted_modal.decision == "accepted"
    assert accepted_modal.staff_message_id == 900

    interaction.response.send_modal.reset_mock()
    await view.reject(interaction)
    rejected_modal = interaction.response.send_modal.await_args.args[0]
    assert isinstance(rejected_modal, BanAppealDecisionModal)
    assert rejected_modal.decision == "rejected"
    assert rejected_modal.staff_message_id == 900


@pytest.mark.asyncio
async def test_unban_not_found_uses_target_id_without_unbound_user():
    cog, interaction, *_ = _review_fixture()
    interaction.user = Mock(spec=discord.Member)
    interaction.user.id = 10
    interaction.user.roles = []
    sequence = []

    async def defer(*, ephemeral=False):
        sequence.append("defer")

    async def check_permissions(_):
        assert sequence == ["defer"]
        sequence.append("permission_check")
        return True

    async def fetch_ban(_):
        assert sequence == ["defer", "permission_check"]
        sequence.append("fetch_ban")
        raise discord.NotFound(
            response=Mock(status=404, reason="Not Found"), message="Unknown Ban"
        )

    interaction.response.defer.side_effect = defer
    cog._check_mod_perms.side_effect = check_permissions
    interaction.guild.fetch_ban.side_effect = fetch_ban

    await ModerationCog.unban.callback(cog, interaction, "805687087784394773", None)

    assert sequence == ["defer", "permission_check", "fetch_ban"]
    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    interaction.followup.send.assert_awaited_once()
    response = interaction.followup.send.await_args.args[0]
    assert "805687087784394773" in response
    assert "not banned" in response
    cog.actions.create.assert_not_called()


@pytest.mark.asyncio
async def test_unban_denied_permission_is_checked_after_defer():
    cog, interaction, *_ = _review_fixture()
    interaction.user = Mock(spec=discord.Member)
    interaction.user.id = 10
    interaction.user.roles = []
    sequence = []

    async def defer(*, ephemeral=False):
        sequence.append("defer")

    async def deny_permissions(_):
        assert sequence == ["defer"]
        sequence.append("permission_check")
        return False

    interaction.response.defer.side_effect = defer
    cog._check_mod_perms.side_effect = deny_permissions

    await ModerationCog.unban.callback(cog, interaction, "805687087784394773", None)

    assert sequence == ["defer", "permission_check"]
    interaction.guild.fetch_ban.assert_not_awaited()
    interaction.followup.send.assert_awaited_once()
    assert "do not have permission" in interaction.followup.send.await_args.args[0]

# Permission regression matrix: native Discord permissions alone must never grant
# access to the appeal-review controls. Only configured moderator roles/head mods
# (or the configured bot owner) pass mod_perms_check.
@pytest.mark.parametrize(
    "user_id,administrator,ban_members,moderate_members,server_owner",
    [
        (101, False, False, False, False),
        (102, True, False, False, False),
        (103, False, True, False, False),
        (104, False, False, True, False),
        (105, True, True, False, False),
        (106, True, False, True, False),
        (107, False, True, True, False),
        (108, True, True, True, False),
        (109, False, False, False, True),
        (110, True, False, False, True),
        (111, False, True, False, True),
        (112, False, False, True, True),
        (113, True, True, False, True),
        (114, True, False, True, True),
        (115, False, True, True, True),
        (116, True, True, True, True),
        (117, False, False, False, False),
        (118, True, False, False, False),
        (119, False, True, False, False),
        (120, False, False, True, False),
    ],
)
@pytest.mark.asyncio
async def test_review_buttons_reject_unconfigured_users_across_permission_matrix(
    user_id, administrator, ban_members, moderate_members, server_owner
):
    cog, interaction, *_ = _review_fixture()
    interaction.user.id = user_id
    interaction.guild.owner_id = user_id if server_owner else 999
    interaction.user.guild_permissions = SimpleNamespace(
        administrator=administrator,
        ban_members=ban_members,
        moderate_members=moderate_members,
    )
    cog._check_mod_perms.return_value = False
    interaction.message = SimpleNamespace(id=900)
    interaction.response.send_modal = AsyncMock()
    view = BanAppealReviewView(cog)

    await view.accept(interaction)
    await view.reject(interaction)

    assert interaction.response.send_modal.await_count == 0
    assert interaction.response.send_message.await_count == 2
    assert all(
        "Only configured moderators" in call.args[0]
        for call in interaction.response.send_message.await_args_list
    )


@pytest.mark.parametrize(
    "user_id,administrator,ban_members,moderate_members,server_owner",
    [
        (201, False, False, False, False),
        (202, True, False, False, False),
        (203, False, True, False, False),
        (204, False, False, True, False),
        (205, True, True, False, False),
        (206, True, False, True, False),
        (207, False, True, True, False),
        (208, True, True, True, False),
        (209, False, False, False, True),
        (210, True, False, False, True),
        (211, False, True, False, True),
        (212, False, False, True, True),
        (213, True, True, False, True),
        (214, True, False, True, True),
        (215, False, True, True, True),
        (216, True, True, True, True),
        (217, False, False, False, False),
        (218, True, False, False, False),
        (219, False, True, False, False),
        (220, False, False, True, False),
    ],
)
@pytest.mark.asyncio
async def test_modal_submission_rechecks_permissions_before_claiming_appeal(
    user_id, administrator, ban_members, moderate_members, server_owner
):
    cog, interaction, target, staff_message, *_ = _review_fixture()
    interaction.user.id = user_id
    interaction.guild.owner_id = user_id if server_owner else 999
    interaction.user.guild_permissions = SimpleNamespace(
        administrator=administrator,
        ban_members=ban_members,
        moderate_members=moderate_members,
    )
    cog._check_mod_perms.return_value = False

    await cog.review_ban_appeal(interaction, "accepted", 900, "unauthorized attempt")

    cog.appeals.claim_review.assert_not_called()
    cog.appeals.set_status.assert_not_called()
    interaction.guild.unban.assert_not_awaited()
    target.send.assert_not_awaited()
    staff_message.edit.assert_not_awaited()
    interaction.followup.send.assert_awaited_once()
    assert "Only configured moderators" in interaction.followup.send.await_args.args[0]
