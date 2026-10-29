"""Compact Discord messages with source links outside the answer body."""
import re
import discord


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
