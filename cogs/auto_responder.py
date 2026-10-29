from __future__ import annotations

import re
from time import monotonic
from typing import Any

import discord
from discord.ext import commands

from cogs.config import is_bot_owner
from cogs.setup_ui import DB_PATH
from database import connect_sqlite
from error_handling import ErrorHandledModal, ObserverCog

MAX_RESPONSES = 30
MAX_TRIGGERS = 5


class AutoResponderStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        with connect_sqlite(db_path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS auto_responses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                triggers TEXT NOT NULL,
                sub_triggers TEXT NOT NULL,
                reply TEXT NOT NULL,
                style TEXT NOT NULL DEFAULT 'text',
                responder_name TEXT NOT NULL DEFAULT 'Auto response',
                cooldown_seconds INTEGER NOT NULL DEFAULT 0,
                UNIQUE(guild_id, id)
            )""")
            columns = {row["name"] for row in db.execute("PRAGMA table_info(auto_responses)")}
            if "responder_name" not in columns:
                db.execute("ALTER TABLE auto_responses ADD COLUMN responder_name TEXT NOT NULL DEFAULT 'Auto response'")
            if "cooldown_seconds" not in columns:
                db.execute("ALTER TABLE auto_responses ADD COLUMN cooldown_seconds INTEGER NOT NULL DEFAULT 0")
            db.execute("CREATE INDEX IF NOT EXISTS idx_auto_responses_guild ON auto_responses(guild_id)")

    def list(self, guild_id: int) -> list[dict[str, Any]]:
        with connect_sqlite(self.db_path) as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM auto_responses WHERE guild_id=? ORDER BY id", (guild_id,)
            ).fetchall()]

    def add(self, guild_id: int, triggers: list[str], subs: list[str], reply: str, style: str, name: str, cooldown: int) -> bool:
        with connect_sqlite(self.db_path) as db:
            count = db.execute("SELECT COUNT(*) FROM auto_responses WHERE guild_id=?", (guild_id,)).fetchone()[0]
            if count >= MAX_RESPONSES:
                return False
            db.execute("INSERT INTO auto_responses(guild_id,triggers,sub_triggers,reply,style,responder_name,cooldown_seconds) VALUES(?,?,?,?,?,?,?)",
                       (guild_id, "\n".join(triggers), "\n".join(subs), reply, style, name, cooldown))
            return True

    def delete(self, guild_id: int, response_id: int) -> bool:
        with connect_sqlite(self.db_path) as db:
            return db.execute("DELETE FROM auto_responses WHERE guild_id=? AND id=?", (guild_id, response_id)).rowcount > 0

    def update(self, guild_id: int, response_id: int, triggers: list[str], subs: list[str], reply: str, style: str, name: str, cooldown: int) -> bool:
        with connect_sqlite(self.db_path) as db:
            return db.execute(
                "UPDATE auto_responses SET triggers=?, sub_triggers=?, reply=?, style=?, responder_name=?, cooldown_seconds=? WHERE guild_id=? AND id=?",
                ("\n".join(triggers), "\n".join(subs), reply, style, name, cooldown, guild_id, response_id),
            ).rowcount > 0


def _terms(value: str) -> list[str]:
    return list(dict.fromkeys(part.strip() for part in value.splitlines() if part.strip()))


class ResponseDetailsModal(ErrorHandledModal):
    def __init__(self, cog: "AutoResponder", triggers: list[str], guild_id: int, response: dict[str, Any] | None = None):
        super().__init__(title="Edit auto response" if response else "Set auto response reply")
        self.cog = cog
        self.triggers = triggers
        self.guild_id = guild_id
        self.response_id = int(response["id"]) if response else None
        self.name = discord.ui.TextInput(label="Responder name", default=response["responder_name"] if response else None, max_length=50, required=True)
        self.subs = discord.ui.TextInput(label="Variation words (one per line)", default=response["sub_triggers"] if response else None, max_length=500, required=False, style=discord.TextStyle.paragraph)
        self.reply = discord.ui.TextInput(label="Reply text", default=response["reply"] if response else None, max_length=1800, required=True, style=discord.TextStyle.paragraph)
        self.style = discord.ui.TextInput(label="Style: text, embed, or card", default=response["style"] if response else "text", max_length=10, required=True)
        self.cooldown = discord.ui.TextInput(label="Cooldown (seconds, 0â€“3600)", default=str(response["cooldown_seconds"]) if response else "0", max_length=4, required=True)
        for item in (self.name, self.subs, self.reply, self.style, self.cooldown):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        subs = _terms(self.subs.value)
        style = self.style.value.strip().lower()
        try:
            cooldown = int(self.cooldown.value.strip())
        except ValueError:
            cooldown = -1
        if len(subs) > 20 or any(len(term.split()) != 1 for term in subs):
            await interaction.response.send_message("Variation triggers must each be a single word (up to 20).", ephemeral=True)
            return
        if style not in {"text", "embed", "card"}:
            await interaction.response.send_message("Style must be text, embed, or card.", ephemeral=True)
            return
        if not self.name.value.strip() or not 0 <= cooldown <= 3600:
            await interaction.response.send_message("Enter a responder name and a cooldown from 0 to 3600 seconds.", ephemeral=True)
            return
        if interaction.guild_id != self.guild_id:
            await interaction.response.send_message("This response belongs to another server.", ephemeral=True)
            return
        if self.response_id is not None:
            saved = self.cog.store.update(self.guild_id, self.response_id, self.triggers, subs, self.reply.value.strip(), style, self.name.value.strip(), cooldown)
        else:
            saved = self.cog.store.add(self.guild_id, self.triggers, subs, self.reply.value.strip(), style, self.name.value.strip(), cooldown)
        if not saved:
            await interaction.response.send_message("This server already has 30 auto responses.", ephemeral=True)
            return
        await interaction.response.send_message("Auto response updated." if self.response_id is not None else "Auto response saved. It sends when at least two distinct triggers or variations match a message.", ephemeral=True)


class TriggerWordsModal(ErrorHandledModal):
    def __init__(self, cog: "AutoResponder", guild_id: int, response: dict[str, Any] | None = None):
        super().__init__(title="Edit trigger words" if response else "Enter trigger words")
        self.cog = cog
        self.guild_id = guild_id
        self.response = response
        self.trigger_inputs = []
        old_triggers = response["triggers"].splitlines() if response else []
        for index in range(MAX_TRIGGERS):
            field = discord.ui.TextInput(
                label=f"Trigger word {index + 1}",
                placeholder="One word only",
                max_length=50,
                required=index == 0,
                default=old_triggers[index] if index < len(old_triggers) else None,
            )
            self.trigger_inputs.append(field)
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        triggers = list(dict.fromkeys(field.value.strip() for field in self.trigger_inputs if field.value.strip()))
        if not 1 <= len(triggers) <= MAX_TRIGGERS or any(len(term.split()) != 1 for term in triggers):
            await interaction.response.send_message("Enter 1â€“5 triggers, with exactly one word in each field.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Trigger words saved for this response. Continue to set its reply and style.",
            view=ResponseDetailsView(self.cog, self.guild_id, triggers, self.response),
            ephemeral=True,
        )


class ResponseDetailsView(discord.ui.View):
    def __init__(self, cog: "AutoResponder", guild_id: int, triggers: list[str], response: dict[str, Any] | None = None):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild_id = guild_id
        self.triggers = triggers
        self.response = response

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id == self.guild_id and interaction.guild is not None and (
            interaction.user.id == interaction.guild.owner_id or is_bot_owner(interaction.user)
        ):
            return True
        await interaction.response.send_message("Only this server's owner can manage auto responses.", ephemeral=True)
        return False

    @discord.ui.button(label="Set reply and style", style=discord.ButtonStyle.primary)
    async def continue_setup(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(ResponseDetailsModal(self.cog, self.triggers, self.guild_id, self.response))


class ResponseManagementView(discord.ui.View):
    def __init__(self, cog: "AutoResponder", guild_id: int, response: dict[str, Any]):
        super().__init__(timeout=600)
        self.cog = cog
        self.guild_id = guild_id
        self.response = response

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id == self.guild_id and interaction.guild is not None and (
            interaction.user.id == interaction.guild.owner_id or is_bot_owner(interaction.user)
        ):
            return True
        await interaction.response.send_message("Only this server's owner can manage auto responses.", ephemeral=True)
        return False

    @discord.ui.button(label="Edit response", style=discord.ButtonStyle.primary)
    async def edit_response(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        current = next((row for row in self.cog.store.list(self.guild_id) if int(row["id"]) == int(self.response["id"])), None)
        if current is None:
            await interaction.response.send_message("That auto response no longer exists.", ephemeral=True)
            return
        await interaction.response.send_modal(TriggerWordsModal(self.cog, self.guild_id, current))

    @discord.ui.button(label="Delete response", style=discord.ButtonStyle.danger)
    async def delete_response(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.cog.store.delete(self.guild_id, int(self.response["id"]))
        await interaction.response.edit_message(
            content=self.cog.panel_text(self.guild_id),
            view=AutoResponderPanelView(self.cog, self.guild_id),
        )

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            content=self.cog.panel_text(self.guild_id),
            view=AutoResponderPanelView(self.cog, self.guild_id),
        )


class AutoResponderPanelView(discord.ui.View):
    def __init__(self, cog: "AutoResponder", guild_id: int, page: int = 0):
        super().__init__(timeout=600)
        self.cog = cog
        self.guild_id = guild_id
        responses = cog.store.list(guild_id)
        self.page = max(0, min(page, max(0, (len(responses) - 1) // 25)))
        if responses:
            select = discord.ui.Select(
                placeholder="Select an auto response",
                options=[
                    discord.SelectOption(
                        label=f'#{row["id"]} {row["responder_name"]}: {", ".join(row["triggers"].splitlines())}'[:100],
                        value=str(row["id"]),
                    )
                    for row in responses[self.page * 25 : (self.page + 1) * 25]
                ],
            )
            select.callback = self._select
            self.add_item(select)
        if len(responses) > 25:
            previous = discord.ui.Button(label="Previous", style=discord.ButtonStyle.secondary, disabled=self.page == 0)
            previous.callback = self._previous
            next_button = discord.ui.Button(label="Next", style=discord.ButtonStyle.secondary, disabled=self.page >= (len(responses) - 1) // 25)
            next_button.callback = self._next
            self.add_item(previous)
            self.add_item(next_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id == self.guild_id and (
            interaction.user.id == interaction.guild.owner_id
            or is_bot_owner(interaction.user)
        ):
            return True
        await interaction.response.send_message("Only this server's owner can manage auto responses.", ephemeral=True)
        return False

    @discord.ui.button(label="Add response", style=discord.ButtonStyle.success)
    async def add_response(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(TriggerWordsModal(self.cog, self.guild_id))

    async def _select(self, interaction: discord.Interaction) -> None:
        response_id = int(interaction.data["values"][0])
        response = next((row for row in self.cog.store.list(self.guild_id) if int(row["id"]) == response_id), None)
        if response is None:
            await interaction.response.send_message("That auto response no longer exists.", ephemeral=True)
            return
        await interaction.response.edit_message(
            content=self.cog.response_text(response),
            view=ResponseManagementView(self.cog, self.guild_id, response),
        )

    async def _previous(self, interaction: discord.Interaction) -> None:
        page = self.page - 1
        await interaction.response.edit_message(content=self.cog.panel_text(self.guild_id, page), view=AutoResponderPanelView(self.cog, self.guild_id, page))

    async def _next(self, interaction: discord.Interaction) -> None:
        page = self.page + 1
        await interaction.response.edit_message(content=self.cog.panel_text(self.guild_id, page), view=AutoResponderPanelView(self.cog, self.guild_id, page))


class AutoResponder(ObserverCog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = AutoResponderStore(DB_PATH)
        self._last_response_at: dict[tuple[int, int], float] = {}

    def panel_text(self, guild_id: int, page: int = 0) -> str:
        responses = self.store.list(guild_id)
        lines = [
            f'`{row["id"]}` **{row["responder_name"][:24]}** Â· {", ".join(row["triggers"].splitlines())[:24]} Â· {row["cooldown_seconds"]}s'
            for row in responses[page * 25 : (page + 1) * 25]
        ]
        return "**Auto responses** â€” choose Add or select a response to edit or delete.\n" + (
            "\n".join(lines) if lines else "No responses configured yet."
        ) + f"\n\n{len(responses)}/{MAX_RESPONSES} responses used."

    @staticmethod
    def response_text(response: dict[str, Any]) -> str:
        triggers = ", ".join(response["triggers"].splitlines())
        variants = ", ".join(response["sub_triggers"].splitlines()) or "None"
        return (
            f'**{response["responder_name"]}** (`{response["id"]}`)\n'
            f'**Triggers:** {triggers}\n**Variations:** {variants}\n'
            f'**Style:** {response["style"]} Â· **Cooldown:** {response["cooldown_seconds"]}s\n'
            f'**Reply:** {response["reply"][:900]}'
        )

    async def show_panel(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or (
            interaction.user.id != interaction.guild.owner_id
            and not is_bot_owner(interaction.user)
        ):
            await interaction.response.send_message("Only this server's owner can configure auto responses.", ephemeral=True)
            return
        await interaction.response.send_message(
            self.panel_text(interaction.guild.id),
            view=AutoResponderPanelView(self, interaction.guild.id),
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot or not message.content:
            return
        body = message.content.casefold()
        for response in self.store.list(message.guild.id):
            cooldown_key = (message.guild.id, int(response["id"]))
            now = monotonic()
            if now - self._last_response_at.get(cooldown_key, 0.0) < int(response["cooldown_seconds"]):
                continue
            terms = [*response["triggers"].splitlines(), *response["sub_triggers"].splitlines()]
            matched = {term.casefold() for term in terms if term and re.search(r"(?<!\w)" + re.escape(term.casefold()) + r"(?!\w)", body)}
            if len(matched) < 2:
                continue
            content = response["reply"]
            mentions = discord.AllowedMentions(users=True, roles=False, everyone=False)
            try:
                if response["style"] == "embed":
                    await message.reply(embed=discord.Embed(title=response["responder_name"], description=content), mention_author=False, allowed_mentions=mentions)
                elif response["style"] == "card":
                    view = discord.ui.LayoutView(timeout=None)
                    card = discord.ui.Container(
                        discord.ui.TextDisplay(f'## {response["responder_name"]}\n\n{content}')
                    )
                    view.add_item(card)
                    await message.reply(view=view, mention_author=False, allowed_mentions=mentions)
                else:
                    await message.reply(content, mention_author=False, allowed_mentions=mentions)
                self._last_response_at[cooldown_key] = now
            except (discord.Forbidden, discord.HTTPException):
                pass
            break


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AutoResponder(bot))

