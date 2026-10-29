"""Bounded, temporary Discord attachments; never execute or extract uploaded files."""
import asyncio
import base64
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import time
import warnings
import zipfile
from urllib.parse import urlsplit

import aiohttp
from PIL import Image, ImageOps
import mutagen
from mutagen.aac import AAC

MIB = 1024 * 1024
MAX_FILE = 25 * MIB
MAX_BATCH = 40 * MIB
MAX_CACHE = 500 * MIB
# Hourly deletion after 23 hours leaves one scheduling interval before 24 hours.
TTL = 23 * 3600
TEXT = {'.osu', '.catchdiff', '.catchproj', '.catchsync', '.osb', '.log', '.txt', '.json', '.ini'}
ARCHIVES = {'.osz', '.faz', '.fad', '.osk', '.fpt', '.fap', '.zip'}
IMAGES = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif'}
AUDIO = {'.mp3', '.ogg', '.wav', '.flac', '.aac', '.m4a'}
SUPPORTED = TEXT | ARCHIVES | IMAGES | AUDIO
DIRECTORY = re.compile(r'\d+_\d+_\d+_\d+')
Image.MAX_IMAGE_PIXELS = 16_000_000


def root_path():
    return Path(os.getenv('ATTACHMENT_DIR', str(Path(os.getenv('STATE_FILE', 'state/bot.sqlite3')).parent / 'attachments')))


def cleanup(root=None, now=None):
    root = Path(root) if root is not None else root_path()
    now = time.time() if now is None else now
    removed = 0
    if not root.exists():
        return removed
    if root.is_symlink():
        raise ValueError('Attachment root cannot be a symlink')
    for folder in root.iterdir():
        if not DIRECTORY.fullmatch(folder.name):
            continue
        created = int(folder.name.split('_')[0]) / 1000
        if now - created >= TTL - 60:
            if folder.is_symlink():
                folder.unlink()
            elif folder.is_dir():
                shutil.rmtree(folder)
            removed += 1
    return removed


def excerpt(data, limit=7000):
    # Include the end of logs and large compact JSON, and disclose the omitted middle.
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        text = data.decode('utf-16', errors='replace')
    else:
        text = data.decode('utf-8-sig', errors='replace')
    if '\x00' in text:
        raise ValueError('Expected a text file, but found binary data')
    if len(text) > limit:
        text = text[:limit // 2] + '\n[Middle omitted: partial file excerpt]\n' + text[-limit // 2:]
    return text


def archive_excerpt(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) > 2000:
            raise ValueError('Archive contains too many entries (maximum 2000)')
        selected = []
        for entry in entries:
            path = PurePosixPath(entry.filename.replace('\\', '/'))
            if path.is_absolute() or '..' in path.parts or ':' in entry.filename:
                raise ValueError('Archive contains unsafe paths')
            if entry.flag_bits & 1 or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Encrypted archives and symbolic links are not supported')
            if path.suffix.lower() in TEXT and not entry.is_dir():
                selected.append(entry)
        listing = '\n'.join(f'{e.filename[:160]} ({e.file_size} bytes)' for e in entries[:60])
        parts = [f'Archive inventory: {len(entries)} entries; first 60 shown.\n{listing}']
        remaining = 2 * MIB
        # Read only bounded text. Audio/images/nested archives stay unexpanded.
        for entry in selected[:8]:
            if entry.file_size > remaining or entry.file_size > MIB or entry.file_size > max(entry.compress_size, 1) * 200:
                parts.append(f'{entry.filename[:160]}: omitted (size or compression limit).')
                continue
            with archive.open(entry) as source:
                chunk = source.read(min(remaining, MIB) + 1)
            if len(chunk) > min(remaining, MIB):
                raise ValueError('Archive expansion limit exceeded')
            remaining -= len(chunk)
            parts.append(f'File {entry.filename[:160]}:\n{excerpt(chunk, 3000)}')
        parts.append('Only up to 8 text entries were inspected. Media and nested archives were not decoded. This is not an editor import test.')
        return '\n\n'.join(parts)[:18000]


def inspect_file(path, name):
    ext = path.suffix.lower()
    data = path.read_bytes()
    if ext in IMAGES:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                if source.format not in {'PNG', 'JPEG', 'WEBP', 'BMP', 'GIF'}:
                    raise ValueError('Unsupported image encoding')
                animated = getattr(source, 'is_animated', False)
                image = ImageOps.exif_transpose(source).convert('RGB')
                image.thumbnail((1536, 1536))
                output = io.BytesIO()
                image.save(output, format='JPEG', quality=75)
                if output.tell() > MIB:
                    raise ValueError('Image is too complex; upload a cropped screenshot')
        return [ {'type': 'input_text', 'text': f'User image: {name}. Resized for analysis.' + (' First frame only.' if animated else '')},
                 {'type': 'input_image', 'detail': 'high', 'image_url': 'data:image/jpeg;base64,' + base64.b64encode(output.getvalue()).decode()} ]
    if ext in AUDIO:
        audio = AAC(io.BytesIO(data)) if ext == '.aac' else mutagen.File(io.BytesIO(data))
        if audio is None:
            raise ValueError('Audio header could not be recognized')
        info = {key: getattr(audio.info, key, None) for key in ('length', 'sample_rate', 'channels', 'bitrate', 'bits_per_sample')}
        text = 'Audio metadata: ' + json.dumps(info) + '. Header parsing only: no listening, transcription, full decoding, waveform or playback verification was performed.'
    elif ext in ARCHIVES:
        text = archive_excerpt(data)
    else:
        text = excerpt(data)
    return [{'type': 'input_text', 'text': f'Untrusted user attachment {name}:\n{text}'}]


class AttachmentStore:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else root_path()
        self.lock = asyncio.Lock()

    async def capture(self, session, owner, channel, message, attachments):
        if not attachments:
            return
        if len(attachments) > 4:
            raise ValueError('Please attach at most 4 files per message.')
        if any(Path(a.filename).suffix.lower() not in SUPPORTED for a in attachments):
            raise ValueError('Unsupported attachment. Send an image, FA/ osu! project package, audio file, or text log. Video is not supported yet.')
        if any(a.size > MAX_FILE for a in attachments) or sum(a.size for a in attachments) > MAX_BATCH:
            raise ValueError('Attachments must be at most 25 MiB each and 40 MiB per message.')
        async with self.lock:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            await asyncio.to_thread(cleanup, self.root)
            used = sum(p.stat().st_size for p in self.root.glob('*/*') if p.is_file() and not p.is_symlink())
            if used + MAX_BATCH > MAX_CACHE:
                raise ValueError('Temporary attachment storage is full. Please try again later.')
            folder = self.root / f'{int(time.time()*1000)}_{int(owner)}_{int(channel)}_{int(message)}'
            folder.mkdir(mode=0o700)
            records = []
            total = 0
            try:
                async with asyncio.timeout(20):
                    for i, attachment in enumerate(attachments):
                        url = urlsplit(attachment.url)
                        if url.scheme != 'https' or url.hostname not in {'cdn.discordapp.com', 'media.discordapp.net'} or url.port not in (None, 443):
                            raise ValueError('Only Discord-hosted attachments are supported.')
                        name = attachment.filename[:180]
                        filename = str(i) + Path(attachment.filename).suffix.lower()
                        with (folder / filename).open('xb') as output:
                            os.chmod(folder / filename, 0o600)
                            size = 0
                            async with session.get(attachment.url, allow_redirects=False, timeout=aiohttp.ClientTimeout(total=15)) as response:
                                if response.status != 200:
                                    raise ValueError('Attachment download failed. Please upload it again.')
                                async for chunk in response.content.iter_chunked(65536):
                                    size += len(chunk)
                                    total += len(chunk)
                                    if size > MAX_FILE or total > MAX_BATCH:
                                        raise ValueError('Attachment download exceeds the size limit.')
                                    output.write(chunk)
                        records.append({'file': filename, 'name': name})
                (folder / 'manifest.json').write_text(json.dumps(records), encoding='utf-8')
                os.chmod(folder / 'manifest.json', 0o600)
            except BaseException:
                shutil.rmtree(folder)
                raise

    def read(self, owner, channel, ids):
        parts = []
        text_left = 22000
        images = 0
        for mid in list(dict.fromkeys(ids))[:3]:
            folders = sorted(self.root.glob(f'*_{int(owner)}_{int(channel)}_{int(mid)}'))
            valid = [f for f in folders if not f.is_symlink() and time.time() - int(f.name.split('_')[0])/1000 < TTL]
            if not valid:
                parts.append({'type': 'input_text', 'text': 'Earlier attachment is unavailable or expired. Ask for a fresh upload if it is essential.'})
                continue
            folder = valid[-1]
            try:
                records = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
                for record in records:
                    path = folder / record['file']
                    if path.parent != folder or path.is_symlink():
                        continue
                    if path.suffix in IMAGES and images >= 2:
                        parts.append({'type': 'input_text', 'text': 'Additional image omitted: at most 2 images are analyzed per question.'})
                        continue
                    try:
                        items = inspect_file(path, record['name'])
                    except Exception as error:
                        # Parser diagnostics may contain uploaded content; do not echo them.
                        items = [{'type': 'input_text', 'text': f'Could not parse {record["name"]}: {type(error).__name__}. Do not claim its contents were inspected.'}]
                    for item in items:
                        if item['type'] == 'input_image':
                            images += 1
                            parts.append(item)
                        elif text_left > 0:
                            text = item['text']
                            encoded = text.encode('utf-8')
                            item = {**item, 'text': encoded[:text_left].decode('utf-8', errors='ignore') + ('\n[Attachment context truncated]' if len(encoded) > text_left else '')}
                            text_left -= min(len(encoded), text_left)
                            parts.append(item)
            except FileNotFoundError:
                parts.append({'type': 'input_text', 'text': 'Attachment expired during cleanup; request a fresh upload if needed.'})
        return parts
