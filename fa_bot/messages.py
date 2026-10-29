"""Compact Discord messages with source links outside the answer body."""
import re
import discord
from .channel_policy import ignore_interaction
from .progress import bind_progress


def linked_message(text, label='Source'):
    urls = []

    def reference(url):
        if url not in urls:
            if len(urls) >= 5 or len(url) > 512:
                return ''
            urls.append(url)
        return f'[{urls.index(url) + 1}]'

    def markdown(match):
        title, url = match.groups()
        marker = reference(url)
        return marker if title.isdigit() else f'{title} {marker}'.strip()

    content = re.sub(r'\[([^\]]+)\]\(<?(https?://[^\s<>]+?)>?\)', markdown, text)
    content = re.sub(r'<?https?://[^\s<>]+>?',
                     lambda m: reference(m[0].strip('<>').rstrip('.,;:!?)')), content)
    view = discord.ui.View(timeout=None)
    for index, url in enumerate(urls, 1):
        view.add_item(discord.ui.Button(label=f'{label} {index}', url=url))
    return {'content': content.strip() or 'See the links below.',
            'view': view if urls else None, 'suppress_embeds': True}


def action_message(text, label=None, url=None):
    view = discord.ui.View(timeout=None)
    if url:
        view.add_item(discord.ui.Button(label=label, url=url))
    return {'content': text, 'view': view if url else None, 'suppress_embeds': True}


class DevelopmentOffer(discord.ui.View):
    def __init__(self, bot, owner, channel, request, sources=None, attachment_ids=(), localization=None):
        super().__init__(timeout=900)
        self.bot, self.owner, self.channel, self.request = bot, owner, channel, request
        self.attachment_ids = attachment_ids
        self.localization = localization
        self.pending = False
        self.submitted = False
        self.message = None
        self.confirm_button = discord.ui.Button(label='Modify project and test', style=discord.ButtonStyle.primary)
        self.confirm_button.callback = self.confirm
        self.add_item(self.confirm_button)
        if sources:
            for item in list(sources.children):
                sources.remove_item(item)
                item.row = 1
                self.add_item(item)

    async def confirm(self, interaction):
        if await ignore_interaction(interaction):
            return
        if interaction.user.id != self.owner or interaction.channel_id != self.channel:
            await interaction.response.send_message('Only the original requester can confirm this change.', ephemeral=True)
            return
        if self.pending or self.submitted or self.is_finished():
            await interaction.response.send_message('This confirmation is no longer available.', ephemeral=True)
            return
        self.pending = True
        try:
            await interaction.response.defer(thinking=True, ephemeral=True)
            try:
                options = {'attachment_ids': self.attachment_ids} if self.attachment_ids else {}
                if self.localization:
                    options['localization'] = self.localization
                status = await self.bot.start_job(interaction, self.request, **options)
            except ValueError as error:
                await interaction.followup.send(str(error), ephemeral=True)
                return
            self.submitted = True
            self.confirm_button.disabled = True
            self.stop()
            await interaction.followup.send(status, ephemeral=True)
            if interaction.message:
                if not await bind_progress(self.bot, str(interaction.id), interaction.message):
                    await interaction.message.edit(view=self)
        finally:
            self.pending = False

    async def on_timeout(self):
        self.confirm_button.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


def qa_message(reply, bot, owner, channel):
    result = linked_message(reply.text)
    if reply.change_request:
        result['view'] = DevelopmentOffer(bot, owner, channel, reply.change_request, result['view'], reply.attachment_ids, reply.localization)
    return result


def notify_owner(payload, owner):
    return {**payload, 'content': f'<@{int(owner)}> ' + payload['content'],
            'allowed_mentions': discord.AllowedMentions(users=[discord.Object(id=int(owner))], roles=False, everyone=False, replied_user=False)}
