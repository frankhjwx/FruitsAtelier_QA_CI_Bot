"""Silence support and development entry points in announcement channels."""
from discord import InteractionType

IGNORED_CHANNELS = {
    'announcements and infos': frozenset({'information', 'releases', 'arrivals', 'tutorials', 'todo'}),
}


def ignored_channel(channel):
    candidate = getattr(channel, 'parent', None) or channel
    category = getattr(candidate, 'category', None)
    names = IGNORED_CHANNELS.get(getattr(category, 'name', '').casefold(), ())
    return getattr(candidate, 'name', '').casefold() in names



async def ignore_interaction(interaction):
    if not ignored_channel(getattr(interaction, 'channel', None)):
        return False
    command = interaction.type == InteractionType.application_command
    await interaction.response.defer(ephemeral=True, thinking=command)
    if command:
        await interaction.delete_original_response()
    return True
