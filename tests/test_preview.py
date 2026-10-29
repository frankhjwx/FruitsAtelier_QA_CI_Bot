import unittest
from unittest.mock import patch
from preview import wait_for_package

class PreviewTests(unittest.TestCase):
    def test_existing_run_is_reused_and_waited_until_complete(self):
        run = {'id': 7, 'display_title': 'FA preview 123456789012345', 'status': 'in_progress'}
        done = {**run, 'status': 'completed', 'conclusion': 'success', 'html_url': 'public'}
        with patch('preview.api', side_effect=[{'workflow_runs': [run]}, run, done]) as api, patch('preview.time.sleep') as sleep:
            wait_for_package('123456789012345', 'a'*40, 'codex-discord-bug-fix')
        self.assertTrue(all(len(call.args) == 1 for call in api.call_args_list))
        sleep.assert_called_once_with(20)

    def test_new_run_dispatches_once(self):
        done = {'id': 7, 'display_title': 'FA preview 123456789012345', 'status': 'completed', 'conclusion': 'failure', 'html_url': 'public'}
        with patch('preview.api', side_effect=[{'workflow_runs': []}, None, {'workflow_runs': [done]}]) as api:
            wait_for_package('123456789012345', 'a'*40, 'codex-discord-bug-fix')
        posts = [c for c in api.call_args_list if len(c.args) == 2]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].args[1]['inputs']['commit'], 'a'*40)

    def test_other_branch_is_rejected_before_dispatch(self):
        with patch('preview.api') as api, self.assertRaises(ValueError):
            wait_for_package('123456789012345', 'a'*40, 'dev')
        api.assert_not_called()
