from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import discord
import pytest
import pytest_asyncio
from discord.ext import commands

from database import (
    ModerationActionStore,
    ModerationCaseStore,
    RelayConfigStore,
)


@pytest.fixture
def case_cog(tmp_path,monkeypatch):
    from cogs import cases
    monkeypatch.setattr(cases,'DB_PATH',str(tmp_path/'cases.db'))
    return cases.Cases(NS(get_cog=lambda _:None))


def interaction(guild_id=10,viewer_id=777,staff=True):
    permissions=discord.Permissions(moderate_members=staff,manage_guild=staff,manage_roles=staff)
    user=NS(id=viewer_id,roles=[],guild_permissions=permissions)
    guild=NS(id=guild_id,owner_id=888,members=[],name='Test',icon=None)
    return NS(guild=guild,guild_id=guild_id,user=user,permissions=permissions,response=NS(send_message=AsyncMock(),edit_message=AsyncMock(),send_modal=AsyncMock(),defer=AsyncMock()),followup=NS(send=AsyncMock()))


@pytest.mark.asyncio
async def test_case_lookup_is_private_scoped_and_supports_all_inputs(case_cog):
    target=NS(id=123456789012345678,name='tester',display_name='Tester',global_name=None)
    first=case_cog.cases.create_case(10,'ban',target.id,777,'Spam',status='resolved')
    other=case_cog.cases.create_case(20,'timeout',target.id,999,'Other server reason')
    ctx=interaction()
    ctx.guild.members=[target]
    for kwargs in ({'user':target},{'user_id':str(target.id)},{'query':'tester'},{'query':str(target.id)},{'query':f'<@{target.id}>'},{'case_id':first},{'query':f'case:{first}'},{'query':'#1'}):
        await case_cog.case.callback(case_cog,ctx,**kwargs)
        call=ctx.response.send_message.call_args
        assert call.kwargs['ephemeral']
        embed=call.kwargs['embed']
        assert 'Other server reason' not in str(embed.to_dict())
        assert 'Spam' in str(embed.to_dict())
    await case_cog.case.callback(case_cog,ctx,case_id=other)
    assert ctx.response.send_message.call_args.args==('Case not found in this server.',)
    assert case_cog.lookup(10,case_id=other) is None
    await case_cog.case.callback(case_cog,interaction(staff=False),case_id=first)
    # Normal members cannot retrieve case contents.
    denied=interaction(staff=False)
    await case_cog.case.callback(case_cog,denied,user_id=str(target.id))
    assert 'Only moderators' in denied.response.send_message.call_args.args[0]
    assert 'embed' not in denied.response.send_message.call_args.kwargs


@pytest.mark.asyncio
async def test_case_history_pagination_checks_permissions_again(case_cog):
    from cogs.cases import CaseHistoryView
    for index in range(8):
        case_cog.cases.create_case(10,'timeout',100,777,f'Reason {index}')
    ctx=interaction()
    view=CaseHistoryView(case_cog,10,777,100)
    assert len(view.embed().fields)==5 and view.pages==2
    assert await view.interaction_check(ctx)
    assert not await view.interaction_check(interaction(viewer_id=778))
    assert not await view.interaction_check(interaction(guild_id=20))
    assert not await view.interaction_check(interaction(staff=False))
    await view.children[-1].callback(ctx)
    next_view=ctx.response.edit_message.call_args.kwargs['view']
    assert next_view.page==1 and len(next_view.embed().fields)==3


@pytest.mark.asyncio
async def test_case_name_ambiguity_and_input_validation(case_cog):
    ctx=interaction()
    ctx.guild.members=[NS(id=1,name='duplicate',display_name='A',global_name=None),NS(id=2,name='duplicate',display_name='B',global_name=None)]
    await case_cog.case.callback(case_cog,ctx,query='duplicate')
    assert 'unique username' in ctx.response.send_message.call_args.args[0]
    await case_cog.case.callback(case_cog,ctx,user_id='not-an-id')
    assert 'numeric Discord user ID' in ctx.response.send_message.call_args.args[0]
    await case_cog.case.callback(case_cog,ctx,user_id='1',case_id=1)
    assert 'Choose one lookup' in ctx.response.send_message.call_args.args[0]
    await case_cog.case.callback(case_cog,ctx,query='99999999999999999999999999999')
    assert 'valid range' in ctx.response.send_message.call_args.args[0]
    case_cog.cases.add_name(10,100,'former-member')
    assert case_cog.resolve_name(ctx.guild,'former-member')=={100}
    case_cog.cases.add_name(20,200,'former-member')
    assert case_cog.resolve_name(ctx.guild,'former-member')=={100}


def test_legacy_case_import_is_idempotent_and_undo_updates_case(tmp_path):
    path=str(tmp_path/'actions.db')
    actions=ModerationActionStore(path)
    action=actions.create(10,'ban',100,777)
    cases=ModerationCaseStore(path)
    assert cases.import_legacy_actions()==1
    assert cases.import_legacy_actions()==0
    rows=cases.list_user_history(10,100)
    assert len(rows)==1 and rows[0]['action_id']==action
    # Creating the same linked case must not duplicate its history.
    assert cases.create_case(10,'ban',100,777,'Reason',action_id=action)==rows[0]['id']
    actions.complete(action,777)  # not claimed: must not mark anything undone
    assert cases.get_case(rows[0]['id'])['status']=='resolved'
    assert actions.claim(action)
    actions.complete(action,777)
    assert cases.get_case(rows[0]['id'])['status']=='undone'


@pytest_asyncio.fixture
async def configured_bot(tmp_path,monkeypatch):
    path=str(tmp_path/'bot.db')
    names=('setup_ui','autoroles','reaction_roles','rules','message_relay','leveling','moderation','cases','help','todo','Bookmarks','reminder','afk')
    for name in names:
        module=__import__('cogs.'+name,fromlist=['DB_PATH'])
        monkeypatch.setattr(module,'DB_PATH',path)
    from cogs import leveling, message_relay
    monkeypatch.setattr(leveling,'DATABASE_PATH',Path(path))
    monkeypatch.setattr(message_relay,'RELAY_STORE',RelayConfigStore(path,tmp_path/'legacy-relay.json'))
    async with commands.Bot(command_prefix='!',intents=discord.Intents.none(),help_command=None) as bot:
        for name in names:
            await bot.load_extension('cogs.'+name)
            if name == 'setup_ui':
                import sys
                setup_ui = sys.modules['cogs.setup_ui']
                monkeypatch.setattr(setup_ui, 'DB_PATH', path)
                bot.get_cog('SetupUICog').store = setup_ui.SetupConfigStore(path)
        yield bot


@pytest.mark.asyncio
async def test_configuration_commands_removed_but_actions_and_member_commands_remain(configured_bot):
    bot=configured_bot
    from cogs.setup_actions import REMOVED_SLASH
    for names in REMOVED_SLASH.values():
        for name in names:
            assert bot.tree.get_command(name) is None
    group=bot.tree.get_command('verify')
    assert {command.name for command in group.commands}=={'check','post'}
    assert bot.get_command('weekly_config') is None
    assert bot.get_command('level_role_reward') is None
    for name in ('setup','case','rules','setup_reactions','todo','bookmarks','reminder','afk'):
        assert bot.tree.get_command(name)
    from cogs.setup_ui import MODULES, ModuleView
    setup=bot.get_cog('SetupUICog')
    guild=NS(id=10,name='Test',get_role=lambda _:None,get_channel=lambda _:None)
    for module in MODULES:
        assert len(ModuleView(setup,module.key,guild).children)<=25
    # Re-registering a feature after reload must still prune its config commands.
    await bot.reload_extension('cogs.reaction_roles')
    assert bot.tree.get_command('reaction_role_add') is None
    assert bot.tree.get_command('setup_reactions')


@pytest.mark.asyncio
async def test_help_discovers_subcommands_context_menus_and_current_options(configured_bot):
    help=configured_bot.get_cog('HelpCog')
    names=help._slash_command_names()
    assert '/todo add' in names and '/verify post' in names and '/case' in names
    assert '/relay_setup' not in names and '/weekly_config' not in names
    assert '/todo add' in help._commands_by_cog()['Todo']
    embed=help._help_embed('todo edit')
    assert embed.title=='/todo edit'
    assert any('repeat' in field.name for field in embed.fields)
    assert 'Snooze' in help._help_embed('reminders').description
    assert 'case_id' in help._help_embed('case').description
    assert help._help_embed('relay_setup').title=='Command not found'
    assert len(await help.help_autocomplete(interaction(),'todo'))<=25
    pages=help._build_pages()
    assert any('Right-click actions' in title and 'Bookmark' in text and 'Remind Me' in text for title,text in pages)
    assert all(len(text)<=4096 for _,text in pages)
    configured_bot.remove_command('remind')
    assert '!remind' not in help._prefix_command_names()


@pytest.mark.asyncio
async def test_dashboard_weekly_schedule_reuses_permission_checks_and_persists(configured_bot):
    from cogs.setup_actions import ActionForm, invoke_action
    setup=configured_bot.get_cog('SetupUICog')
    feature=configured_bot.get_cog('Leveling')
    ctx=interaction()
    ctx.guild.name='Test'
    channel=Mock(spec=discord.TextChannel)
    channel.id=123
    channel.mention='<#123>'
    command=feature.weekly_config_slash
    form=ActionForm(setup,'leveling',ctx.guild,command,'Weekly schedule')
    await form._run(ctx)
    assert 'Fill:' in ctx.response.send_message.call_args.args[0]
    await invoke_action(setup,command,ctx,{'channel':channel,'day':3,'time':'18:30'})
    config=await feature._get_guild_config(10)
    assert (config['weekly_channel'],config['weekly_day'],config['weekly_hour'],config['weekly_minute'])==(123,3,18,30)
    denied=interaction(staff=False)
    with pytest.raises(discord.app_commands.CheckFailure):
        await invoke_action(setup,command,denied,{'channel':channel,'day':0,'time':'00:00'})
    assert (await feature._get_guild_config(10))['weekly_day']==3


@pytest.mark.asyncio
async def test_role_list_editor_preserves_legacy_roles_and_removal_clears_slots(configured_bot):
    from cogs.setup_actions import RoleCollectionView
    feature=configured_bot.get_cog('Autoroles')
    setup=configured_bot.get_cog('SetupUICog')
    ctx=interaction()
    guild=ctx.guild
    roles={id:discord.Role(guild=guild,state=NS(),data={'id':str(id),'name':f'Role {id}','position':position,'permissions':'0'}) for id,position in ((100,1),(101,2),(999,10))}
    guild.get_role=lambda id:roles.get(id)
    guild.me=NS(top_role=roles[999],guild_permissions=discord.Permissions(manage_roles=True))
    feature.store.set(10,'verification','verified_role_1',100)
    feature._set_role(10,'guild_verified_roles',101,True)
    assert feature.configured_role_ids(10,'verified')=={100,101}
    view=RoleCollectionView(setup,guild,'verification','verified')
    remove=view.children[2]
    remove._values=['100']
    await remove.callback(ctx)
    assert feature.configured_role_ids(10,'verified')=={101}
    assert feature.store.get(10,'verification','verified_role_1') is None
    feature.replace_role_ids(10,'verified',set(range(200,231)))
    view=RoleCollectionView(setup,guild,'verification','verified',page=1)
    assert len(view.children[2].options)==6


@pytest.mark.asyncio
async def test_rule_editor_uses_rule_picker_and_opens_existing_modal(configured_bot):
    from cogs.setup_actions import ActionForm, ActionRuleSelect, invoke_action
    setup=configured_bot.get_cog('SetupUICog')
    feature=configured_bot.get_cog('RulesCommand')
    feature.setup_store.set(10,'rules','enabled',True)
    rule=feature.rules_store.add(10,'Be kind','Treat others well','embed')
    ctx=interaction()
    command=feature.rule_edit
    form=ActionForm(setup,'rules',ctx.guild,command,'Edit rule')
    picker=ActionRuleSelect(form,command.parameters[0])
    picker.children[0]._values=[str(rule.rule_id)]
    await picker.children[0].callback(ctx)
    assert form.values['rule']==str(rule.rule_id)
    await invoke_action(setup,command,ctx,form.values)
    ctx.response.send_modal.assert_awaited_once()


@pytest.mark.asyncio
async def test_manual_mod_action_creates_case_even_with_log_channel_disabled(configured_bot):
    feature=configured_bot.get_cog('ModerationCog')
    guild=NS(id=10)
    target=NS(id=100,display_name='Target')
    moderator=NS(id=777)
    feature._get_log_channel=AsyncMock(return_value=None)
    await feature._send_mod_log(guild,'Kick',moderator,target,'Spam')
    rows=feature.cases.list_user_history(10,100)
    assert len(rows)==1 and rows[0]['case_type']=='kick' and rows[0]['reason']=='Spam'
    assert rows[0]['moderator_id']==777
    assert 'Target' in feature.cases.list_names(10,100)


@pytest.mark.asyncio
async def test_configuration_tools_keep_original_action_permissions(configured_bot):
    from cogs.setup_actions import invoke_action
    setup=configured_bot.get_cog('SetupUICog')
    command=configured_bot.get_cog('ReactionRoles').reaction_role_add
    ctx=interaction()
    ctx.user.guild_permissions.manage_roles=False
    ctx.permissions.manage_roles=False
    # Manage Server still opens the dashboard, but Manage Roles is required by this action.
    assert setup.has_setup_access(ctx)
    with pytest.raises(discord.app_commands.MissingPermissions):
        await invoke_action(setup,command,ctx,{'emoji':'⭐','role':NS(id=100)})
