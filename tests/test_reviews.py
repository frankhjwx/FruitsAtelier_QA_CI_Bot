import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
from fa_bot.store import Store
from fa_bot.reviews import request_pr
import open_pr


class ReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_owner_of_latest_successful_package_can_request_pr(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(str(Path(folder)/'state.sqlite3'))
            try:
                store.add_job('123456789012345',1,2,'codex-discord-bug-fix','Fix the requested issue')
                store.update('123456789012345','success',99,'public')
                store.save_source('123456789012345','a'*40)
                bot=NS(store=store,github=NS(dispatch_pr=AsyncMock()))
                wrong=NS(user=NS(id=3),channel_id=2)
                with self.assertRaisesRegex(ValueError,'original requester'):
                    await request_pr(bot,wrong,'123456789012345')
                bot.github.dispatch_pr.assert_not_called()
                event=NS(user=NS(id=1),channel_id=2)
                result=await request_pr(bot,event,'123456789012345')
                self.assertIn('only the maintainer',result)
                self.assertTrue(store.repair_active())
                with self.assertRaises(ValueError):
                    await request_pr(bot,event,'123456789012345')
                self.assertEqual(bot.github.dispatch_pr.await_count,1)
            finally:
                store.db.close()


class PRWorkflowTests(unittest.TestCase):
    def test_opens_dev_pr_without_merging(self):
        run={'status':'completed','conclusion':'success','display_title':'FA preview 123456789012345','html_url':'public-run'}
        with patch.object(open_pr,'request_json',side_effect=[run,{'object':{'sha':'a'*40}},[],{'html_url':'pr-url'}]) as api:
            result=open_pr.propose('123456789012345','a'*40,'99','Fix issue','fake')
        self.assertEqual(result['html_url'],'pr-url')
        post=api.call_args_list[-1]
        self.assertTrue(post.args[0].endswith('/pulls'))
        self.assertEqual(post.args[2],'POST')
        self.assertEqual(post.args[3]['base'],'dev')
        self.assertEqual(post.args[3]['head'],'codex-discord-bug-fix')
        self.assertTrue(all('/merge' not in c.args[0] for c in api.call_args_list))

    def test_changed_branch_cannot_open_pr_for_old_package(self):
        run={'status':'completed','conclusion':'success','display_title':'FA preview 123456789012345'}
        with patch.object(open_pr,'request_json',side_effect=[run,{'object':{'sha':'b'*40}}]) as api:
            with self.assertRaisesRegex(ValueError,'changed'):
                open_pr.propose('123456789012345','a'*40,'99','Fix issue','fake')
        self.assertEqual(api.call_count,2)

    def test_failed_package_cannot_open_pr(self):
        with patch.object(open_pr,'request_json',return_value={'status':'completed','conclusion':'failure'}) as api:
            with self.assertRaises(ValueError):
                open_pr.propose('123456789012345','a'*40,'99','Fix issue','fake')
        self.assertEqual(api.call_count,1)
