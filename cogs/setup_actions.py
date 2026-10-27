"""Dashboard editors reuse feature validation without publishing config commands."""
from __future__ import annotations

import discord
from discord import app_commands

from cogs.setup_ui import (
    AdminOnlyView,
    ModuleView,
    build_module_embed,
    get_module,
    is_bot_owner,
)
from error_handling import ErrorHandledModal

# label, cog name, command attribute. These callbacks remain internal UI adapters.
ACTIONS = {
    'verification': [('Post verification panel','Autoroles','verify_setup'),('Check verification','Autoroles','verify_check')],
    'reaction_roles': [('Add / change emoji-role pair','ReactionRoles','reaction_role_add'),('Remove emoji-role pair','ReactionRoles','reaction_role_remove'),('Show pairs','ReactionRoles','reaction_role_list'),('Post reaction panel','ReactionRoles','setup_reactions_slash')],
    'rules': [('Create rule','RulesCommand','rule_add'),('Edit rule','RulesCommand','rule_edit'),('Remove rule','RulesCommand','rule_remove'),('Show rules','RulesCommand','rule_list')],
    'message_relay': [('Link source and target servers','MessageRelay','relay_setup'),('Add destination channel','MessageRelay','relay_add'),('Remove destination channel','MessageRelay','relay_remove'),('Add source channel','MessageRelay','relay_add_source'),('Remove source channel','MessageRelay','relay_remove_source')],
    'leveling': [('Weekly leaderboard schedule','Leveling','weekly_config_slash'),('Add / change reward role','Leveling','level_role_reward_slash')],
}
REMOVED_SLASH = {
    'autoroles': ('autorole',),
    'reaction_roles': ('reaction_role_add','reaction_role_remove'),
    'rules': ('rule_add','rule_edit','rule_remove'),
    'message_relay': ('relay_setup','relay_add','relay_remove','relay_add_source','relay_remove_source'),
    'leveling': ('weekly_config','level_role_reward'),
}


def remove_setup_commands(bot, module):
    for name in REMOVED_SLASH.get(module, ()):
        bot.tree.remove_command(name)
    if module == 'leveling':
        for name in ('weekly_config','level_role_reward'):
            bot.remove_command(name)
    if module == 'autoroles':
        group = bot.tree.get_command('verify')
        if isinstance(group,app_commands.Group):
            for name in ('enable','disable','channel','message','verified-role','unverified-role'):
                group.remove_command(name)


async def invoke_action(cog, command, interaction, values):
    if not cog.has_setup_access(interaction):
        raise app_commands.CheckFailure('Setup access is required.')
    if not await command._check_can_run(interaction):
        raise app_commands.CheckFailure('The action permission check failed.')
    await command.callback(command.binding,interaction,**values)


class ActionMenu(AdminOnlyView):
    def __init__(self,cog,module_key,guild):
        super().__init__(timeout=600)
        self.cog,self.module_key,self.guild=cog,module_key,guild
        self.actions=ACTIONS[module_key]
        for label,cog_name,attribute in self.actions:
            button=discord.ui.Button(label=label)
            async def callback(interaction,cog_name=cog_name,attribute=attribute,label=label):
                feature=self.cog.bot.get_cog(cog_name)
                if feature is None:
                    await interaction.response.send_message('This feature is not loaded. Check the bot startup logs.',ephemeral=True)
                    return
                command=getattr(feature,attribute)
                if not command.parameters:
                    await invoke_action(self.cog,command,interaction,{})
                else:
                    view=ActionForm(self.cog,self.module_key,self.guild,command,label)
                    await interaction.response.edit_message(embed=view.embed(),view=view)
            button.callback=callback
            self.add_item(button)
        if module_key=='leveling':
            button=discord.ui.Button(label='Show / remove reward roles')
            button.callback=self._rewards
            self.add_item(button)
        back=discord.ui.Button(label='Back to settings',style=discord.ButtonStyle.secondary)
        back.callback=self._back
        self.add_item(back)

    async def _rewards(self,interaction):
        feature=self.cog.bot.get_cog('Leveling')
        if feature is None:
            await interaction.response.send_message('Leveling is not loaded.',ephemeral=True)
            return
        view=RewardRolesView(self.cog,self.guild,feature)
        await view.refresh(interaction)

    async def _back(self,interaction):
        await interaction.response.edit_message(embed=build_module_embed(self.cog.store,self.guild,get_module(self.module_key)),view=ModuleView(self.cog,self.module_key,self.guild))


class ActionValueModal(ErrorHandledModal):
    def __init__(self,parent,parameter):
        super().__init__(title=parameter.display_name[:45])
        self.parent,self.parameter=parent,parameter
        value=parent.values.get(parameter.name)
        self.input=discord.ui.TextInput(label=parameter.display_name[:45],placeholder=str(parameter.description)[:100],default=str(value) if value is not None else None,required=parameter.required,max_length=1000)
        self.add_item(self.input)

    async def on_submit(self,interaction):
        if not self.parent.cog.has_setup_access(interaction):
            raise app_commands.CheckFailure('Setup access is required.')
        raw=str(self.input.value).strip()
        parameter=self.parameter
        if not raw and not parameter.required:
            self.parent.values.pop(parameter.name,None)
        else:
            try:
                if parameter.type==discord.AppCommandOptionType.integer:
                    value=int(raw)
                elif parameter.type==discord.AppCommandOptionType.number:
                    value=float(raw)
                elif parameter.type==discord.AppCommandOptionType.boolean:
                    if raw.lower() not in {'true','false','yes','no','on','off'}:
                        raise ValueError
                    value=raw.lower() in {'true','yes','on'}
                else:
                    value=raw
            except ValueError:
                await interaction.response.send_message('Enter a valid number or true/false as requested.',ephemeral=True)
                return
            self.parent.values[parameter.name]=value
        await self.parent.refresh(interaction)


class ActionObjectSelect(discord.ui.View):
    # This temporary selector enforces the same setup access as its parent.
    def __init__(self,parent,parameter):
        super().__init__(timeout=600)
        self.parent,self.parameter=parent,parameter
        if parameter.type==discord.AppCommandOptionType.role:
            select=discord.ui.RoleSelect(placeholder='Choose a role')
        else:
            select=discord.ui.ChannelSelect(placeholder='Choose a text channel',channel_types=[discord.ChannelType.text])
        async def selected(interaction):
            object_id=select.values[0].id
            value=interaction.guild.get_role(object_id) if parameter.type==discord.AppCommandOptionType.role else interaction.guild.get_channel(object_id)
            if value is None:
                await interaction.response.send_message('That item is no longer available.',ephemeral=True)
                return
            self.parent.values[parameter.name]=value
            await self.parent.refresh(interaction)
        select.callback=selected
        self.add_item(select)

    async def interaction_check(self,interaction):
        return await self.parent.interaction_check(interaction)

    async def on_error(self,interaction,error,item):
        from error_handling import handle_interaction_error
        await handle_interaction_error(interaction,error,source='setup selector')


class ActionRuleSelect(AdminOnlyView):
    def __init__(self,parent,parameter,page=0):
        super().__init__(timeout=600)
        self.cog,self.parent,self.parameter=parent.cog,parent,parameter
        self.rows=parent.command.binding.rules_store.list(parent.guild.id,limit=-1)
        pages=max(1,(len(self.rows)+24)//25)
        self.page=max(0,min(page,pages-1))
        entries=self.rows[self.page*25:self.page*25+25]
        if entries:
            select=discord.ui.Select(placeholder='Choose a configured rule',options=[discord.SelectOption(label=rule.title[:100],value=str(rule.rule_id)) for rule in entries])
            async def selected(interaction):
                parent.values[parameter.name]=select.values[0]
                await parent.refresh(interaction)
            select.callback=selected
            self.add_item(select)
        for label,offset,disabled in [('Previous',-1,self.page==0),('Next',1,self.page>=pages-1)]:
            button=discord.ui.Button(label=label,disabled=disabled)
            async def navigate(interaction,offset=offset):
                await interaction.response.edit_message(view=ActionRuleSelect(parent,parameter,self.page+offset))
            button.callback=navigate
            self.add_item(button)
        back=discord.ui.Button(label='Back')
        back.callback=parent.refresh
        self.add_item(back)


class ActionForm(AdminOnlyView):
    def __init__(self,cog,module_key,guild,command,label,values=None):
        super().__init__(timeout=600)
        self.cog,self.module_key,self.guild,self.command,self.label=cog,module_key,guild,command,label
        self.values=values or {}
        for parameter in command.parameters:
            value=self.values.get(parameter.name)
            display=getattr(value,'mention',str(value)) if value is not None else ('Required' if parameter.required else 'Optional')
            button=discord.ui.Button(label=f'{parameter.display_name}: {display}'[:80])
            async def callback(interaction,parameter=parameter):
                if self.module_key == 'rules' and parameter.name == 'rule':
                    view = ActionRuleSelect(self, parameter)
                    await interaction.response.edit_message(embed=discord.Embed(title='Choose a rule'),view=view)
                elif parameter.type in {discord.AppCommandOptionType.role,discord.AppCommandOptionType.channel}:
                    await interaction.response.edit_message(embed=discord.Embed(title=f'Choose {parameter.display_name}'),view=ActionObjectSelect(self,parameter))
                else:
                    await interaction.response.send_modal(ActionValueModal(self,parameter))
            button.callback=callback
            self.add_item(button)
        run=discord.ui.Button(label='Apply',style=discord.ButtonStyle.success)
        run.callback=self._run
        self.add_item(run)
        back=discord.ui.Button(label='Back',style=discord.ButtonStyle.secondary)
        back.callback=self._back
        self.add_item(back)

    def embed(self):
        embed=discord.Embed(title=self.label,description='Fill the fields, then press Apply. Optional fields may be left unset.',color=0x96EDF1)
        for parameter in self.command.parameters:
            value=self.values.get(parameter.name)
            display=getattr(value,'mention',str(value)) if value is not None else 'Not selected'
            embed.add_field(name=parameter.display_name,value=f'{str(parameter.description)[:300]}\n**{display}**'[:1000],inline=False)
        return embed

    async def refresh(self,interaction):
        await interaction.response.edit_message(embed=self.embed(),view=ActionForm(self.cog,self.module_key,self.guild,self.command,self.label,self.values))

    async def _run(self,interaction):
        missing=[p.display_name for p in self.command.parameters if p.required and p.name not in self.values]
        if missing:
            await interaction.response.send_message('Fill: '+', '.join(missing),ephemeral=True)
            return
        await invoke_action(self.cog,self.command,interaction,self.values)

    async def _back(self,interaction):
        await interaction.response.edit_message(embed=discord.Embed(title='Configuration tools'),view=ActionMenu(self.cog,self.module_key,self.guild))


ROLE_LISTS={'autorole':(('Member join roles','member'),('Bot join roles','bot')),'verification':(('Verified roles','verified'),('Unverified roles','unverified'))}


class RoleCollectionView(AdminOnlyView):
    def __init__(self,cog,guild,module_key,kind=None,page=0):
        super().__init__(timeout=600)
        self.cog,self.guild,self.module_key=cog,guild,module_key
        self.kind=kind or ROLE_LISTS[module_key][0][1]
        self.page=page
        self.feature=cog.bot.get_cog('Autoroles')
        choose=discord.ui.Select(placeholder='Choose a role list',options=[discord.SelectOption(label=label,value=value,default=value==self.kind) for label,value in ROLE_LISTS[module_key]],row=0)
        async def switch(interaction):
            await RoleCollectionView(cog,guild,module_key,choose.values[0]).refresh(interaction)
        choose.callback=switch
        self.add_item(choose)
        add=discord.ui.RoleSelect(placeholder='Add roles to this list',max_values=25,row=1)
        async def add_roles(interaction):
            roles=[guild.get_role(role.id) for role in add.values]
            errors=[str(role.id) if role else 'Missing role' for role in roles if role is None or role.is_default() or role.managed or guild.me is None or role>=guild.me.top_role]
            if errors or not guild.me or not guild.me.guild_permissions.manage_roles:
                await interaction.response.send_message('Observer needs Manage Roles and must be above all selected assignable roles.',ephemeral=True)
                return
            ids=self.feature.configured_role_ids(guild.id,self.kind)
            ids.update(role.id for role in roles)
            if self.module_key=='verification':
                other='unverified' if self.kind=='verified' else 'verified'
                if ids & self.feature.configured_role_ids(guild.id,other):
                    await interaction.response.send_message('Verified and unverified role lists must not overlap.',ephemeral=True)
                    return
            self.feature.replace_role_ids(guild.id,self.kind,ids)
            await self.refresh(interaction)
        add.callback=add_roles
        self.add_item(add)
        ids=sorted(self.feature.configured_role_ids(guild.id,self.kind))
        pages=max(1,(len(ids)+24)//25)
        self.page=max(0,min(page,pages-1))
        visible=ids[self.page*25:self.page*25+25]
        if visible:
            remove=discord.ui.Select(placeholder='Remove configured roles',max_values=len(visible),options=[discord.SelectOption(label=(guild.get_role(id).name if guild.get_role(id) else f'Missing role {id}')[:100],value=str(id)) for id in visible],row=2)
            async def remove_roles(interaction):
                ids=self.feature.configured_role_ids(guild.id,self.kind)-{int(value) for value in remove.values}
                self.feature.replace_role_ids(guild.id,self.kind,ids)
                await self.refresh(interaction)
            remove.callback=remove_roles
            self.add_item(remove)
        for label,offset,disabled in [('Previous',-1,self.page==0),('Next',1,self.page>=pages-1)]:
            button=discord.ui.Button(label=label,disabled=disabled,row=3)
            async def navigate(interaction,offset=offset):
                await RoleCollectionView(cog,guild,module_key,self.kind,self.page+offset).refresh(interaction)
            button.callback=navigate
            self.add_item(button)
        back=discord.ui.Button(label='Back to settings',row=3)
        async def go_back(interaction):
            await interaction.response.edit_message(embed=build_module_embed(cog.store,guild,get_module(module_key)),view=ModuleView(cog,module_key,guild))
        back.callback=go_back
        self.add_item(back)

    async def refresh(self,interaction):
        ids=sorted(self.feature.configured_role_ids(self.guild.id,self.kind))
        view=RoleCollectionView(self.cog,self.guild,self.module_key,self.kind,self.page)
        embed=discord.Embed(title=f'{self.kind.title()} roles',description=('\n'.join(f'<@&{id}>' for id in ids[view.page*25:view.page*25+25]) or 'No roles configured.')+'\nChoose roles above to add or remove them.',color=0x96EDF1)
        await interaction.response.edit_message(embed=embed,view=view)


class RewardRolesView(AdminOnlyView):
    def __init__(self,cog,guild,feature):
        super().__init__(timeout=600)
        self.cog,self.guild,self.feature=cog,guild,feature

    async def refresh(self,interaction):
        config=await self.feature._get_guild_config(self.guild.id)
        rewards=config['role_rewards']
        self.clear_items()
        if rewards:
            entries=sorted(rewards.items(),key=lambda item:int(item[0]))[:25]
            select=discord.ui.Select(placeholder='Remove reward mappings',max_values=len(entries),options=[discord.SelectOption(label=f'Level {level}',value=level,description=(self.guild.get_role(role_id).name if self.guild.get_role(role_id) else f'Missing role {role_id}')[:100]) for level,role_id in entries])
            async def remove(interaction):
                if not is_bot_owner(interaction.user) and not interaction.user.guild_permissions.manage_guild:
                    raise app_commands.CheckFailure('Manage Server required.')
                current=await self.feature._get_guild_config(self.guild.id)
                mappings=current['role_rewards']
                for level in select.values:
                    mappings.pop(level,None)
                await self.feature._set_guild_config(self.guild.id,role_rewards=mappings)
                await self.refresh(interaction)
            select.callback=remove
            self.add_item(select)
        back=discord.ui.Button(label='Back')
        async def go_back(interaction):
            await interaction.response.edit_message(embed=discord.Embed(title='Leveling tools'),view=ActionMenu(self.cog,'leveling',self.guild))
        back.callback=go_back
        self.add_item(back)
        description='\n'.join(f'Level {level}: <@&{role_id}>' for level,role_id in sorted(rewards.items(),key=lambda item:int(item[0]))[:25]) or 'No reward roles.'
        await interaction.response.edit_message(embed=discord.Embed(title='Reward roles',description=description+'\nShowing up to 25; removing entries exposes the next mappings.'),view=self)


async def open_setup_action(cog,module_key,key,interaction):
    if key=='manage_roles':
        if cog.bot.get_cog('Autoroles') is None:
            await interaction.response.send_message('Autoroles is not loaded.',ephemeral=True)
            return
        await RoleCollectionView(cog,interaction.guild,module_key).refresh(interaction)
    elif module_key=='verification' and key in {'post_panel','check'}:
        feature=cog.bot.get_cog('Autoroles')
        if feature is None:
            await interaction.response.send_message('Verification is not loaded.',ephemeral=True)
            return
        await invoke_action(cog,getattr(feature,'verify_setup' if key=='post_panel' else 'verify_check'),interaction,{})
    else:
        await interaction.response.edit_message(embed=discord.Embed(title='Configuration tools',description='Choose an editor or panel action.'),view=ActionMenu(cog,module_key,interaction.guild))
