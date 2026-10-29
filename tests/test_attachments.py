import asyncio
import io
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import wave
import zipfile

from PIL import Image
from fa_bot.attachments import AttachmentStore, cleanup, inspect_file, archive_excerpt, TTL, MAX_FILE
from fa_bot.qa import answer, context_bytes
from fa_bot.conversation import reply_context
from test_flow import Session, Knowledge
from test_conversation import message


class Download:
    status = 200
    def __init__(self, data):
        self.data = data
        self.content = self
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        pass
    async def iter_chunked(self, size):
        yield self.data


class AttachmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_capture_scope_context_and_expiry_do_not_refresh(self):
        with tempfile.TemporaryDirectory() as temp:
            store = AttachmentStore(temp)
            session = NS(get=lambda *a, **k: Download(b'osu file format v14\n[General]\nMode:2'))
            attachment = NS(filename='../../sample.osu', size=44, url='https://cdn.discordapp.com/attachments/1/2/sample.osu')
            await store.capture(session, 1, 2, 3, [attachment])
            folder = next(Path(temp).iterdir())
            created = int(folder.name.split('_')[0])/1000
            self.assertTrue((folder/'0.osu').exists())
            self.assertIn('Mode:2', str(store.read(1, 2, [3])))
            self.assertNotIn('Mode:2', str(store.read(9, 2, [3])))
            self.assertNotIn('Mode:2', str(store.read(1, 9, [3])))
            with patch('fa_bot.attachments.time.time', return_value=created+TTL+1):
                self.assertIn('expired', str(store.read(1, 2, [3])))
            self.assertEqual(cleanup(temp, now=created+TTL-120), 0)
            self.assertEqual(cleanup(temp, now=created+TTL+1), 1)
            self.assertFalse(folder.exists())

    def test_replacement_upload_excludes_expired_reply_attachments(self):
        with tempfile.TemporaryDirectory() as folder:
            store=AttachmentStore(folder)
            old=Path(folder)/f'{int((time.time()-TTL-10)*1000)}_1_2_3'
            fresh=Path(folder)/f'{int(time.time()*1000)}_1_2_4'
            old.mkdir(); fresh.mkdir()
            self.assertEqual(store.available_ids(1,2,[4,3]),[4])
            self.assertEqual(store.available_ids(99,2,[4]),[])

    async def test_reject_external_download_and_remove_partial_batch(self):
        with tempfile.TemporaryDirectory() as temp:
            store = AttachmentStore(temp)
            session = NS(get=lambda *a, **k: self.fail('External URL must not be fetched'))
            attachment = NS(filename='test.osu', size=1, url='https://example.com/secret')
            with self.assertRaisesRegex(ValueError, 'Discord-hosted'):
                await store.capture(session,1,2,3,[attachment])
            self.assertEqual(list(Path(temp).iterdir()), [])
            attachment.size = MAX_FILE+1
            with self.assertRaisesRegex(ValueError, '25 MiB'):
                await store.capture(session,1,2,3,[attachment])

    def test_archive_traversal_and_compression_bomb_are_not_expanded(self):
        def make(name, data):
            out = io.BytesIO()
            with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED) as z:
                z.writestr(name,data)
            return out.getvalue()
        with self.assertRaisesRegex(ValueError,'unsafe paths'):
            archive_excerpt(make('../secret.osu','test'))
        self.assertIn('compression limit', archive_excerpt(make('large.osu','x'*900000)))
        self.assertIn('Mode:2', archive_excerpt(make('map.osu','[General]\nMode:2')))

    def test_fa_archives_audio_image_and_log_have_distinct_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for ext in ('.faz','.fad','.fpt','.fap','.osz','.osk'):
                path=root/('sample'+ext)
                with zipfile.ZipFile(path,'w') as z:
                    z.writestr('project.catchdiff','{"Schema":1,"Name":"Example"}')
                self.assertIn('Example', str(inspect_file(path,path.name)))
            audio=root/'test.wav'
            with wave.open(str(audio),'wb') as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000); w.writeframes(b'\x00\x00'*8000)
            evidence=str(inspect_file(audio,audio.name))
            self.assertIn('8000',evidence)
            self.assertIn('no listening',evidence)
            image=root/'test.png'; Image.new('RGB',(32,32),'red').save(image)
            self.assertTrue(inspect_file(image,image.name)[1]['image_url'].startswith('data:image/jpeg;base64,'))
            log=root/'test.log'; log.write_text('start\n'+'x'*20000+'\nFatal: test error',encoding='utf-8')
            evidence=str(inspect_file(log,log.name))
            self.assertIn('partial file excerpt',evidence)
            self.assertIn('Fatal: test error',evidence)

    async def test_images_reach_both_model_calls_without_consuming_text_limit(self):
        session=Session()
        attachment={'type':'input_image','image_url':'data:image/jpeg;base64,'+'a'*70000,'detail':'high'}
        await answer(session,'fake',Knowledge(),'What is wrong?',attachments=[attachment])
        for call in session.calls:
            self.assertIn(attachment,call['input'][0]['content'])
        self.assertLess(context_bytes(session.calls[0]['input']),10000)

    async def test_reply_chain_preserves_attachment_identity_without_download(self):
        channel=NS(id=10)
        original=message(1,1,'This project fails',channel)
        original.attachments=[NS(filename='example.faz')]
        reply=message(2,9,'Which difficulty?',channel,original)
        current=message(3,1,'The first one',channel,reply)
        triggered,context=await reply_context(current,9)
        self.assertTrue(triggered)
        self.assertEqual(context[0]['attachment_id'],1)
