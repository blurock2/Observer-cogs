"""Shared validation and owner-only controls for personal member tools."""
from __future__ import annotations

import discord

from error_handling import ErrorHandledView


def migrate_columns(db, table, definitions):
    columns = {row['name'] for row in db.execute(f'PRAGMA table_info({table})')}
    for name, definition in definitions.items():
        if name not in columns:
            db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')


def clean_text(value, maximum):
    text = value.strip()
    if not text or len(text) > maximum:
        raise ValueError(f'Use between 1 and {maximum} characters.')
    return text


class ConfirmClearView(ErrorHandledView):
    def __init__(self, user_id, callback):
        super().__init__(timeout=60)
        self.user_id, self.callback = user_id, callback

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message('Only the owner can confirm this.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Confirm clear', style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        count = self.callback()
        self.stop()
        await interaction.response.edit_message(content=f'Cleared {count} items.', view=None)

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        self.stop()
        await interaction.response.edit_message(content='Cancelled.', view=None)
