import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('setup_wizard', Path(__file__).parents[1] / 'deploy/setup_wizard.py')
wizard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wizard)


class SetupTests(unittest.TestCase):
    def test_credentials_use_stdin_and_expected_destinations(self):
        values = {k: k + '-test-' + 'x'*30 for k in ('qa', 'ci', 'server', 'source')}
        with patch.object(wizard, 'run_command', return_value='') as command, patch.object(wizard, 'api', return_value={}):
            wizard.configure(values, False, lambda text: None)
        for call in command.call_args_list:
            argv = str(call.args[0])
            for value in values.values():
                self.assertNotIn(value, argv)
        ci = [c for c in command.call_args_list if c.args[0][:3] == ['gh', 'secret', 'set']]
        self.assertEqual([c.args[0][3] for c in ci], ['FA_OPENAI_CI_KEY', 'FA_SOURCE_TOKEN'])
        remote = command.call_args_list[-1]
        self.assertEqual(json.loads(remote.args[1]), {'openai-key': values['qa'], 'github-token': values['server']})
        self.assertNotIn('restart', str(command.call_args_list))
        compile(wizard.REMOTE_INSTALL, '<remote installer>', 'exec')

    def test_failed_validation_does_not_save_credentials(self):
        values = {k: 'x'*30 for k in ('qa', 'ci', 'server', 'source')}
        with patch.object(wizard, 'run_command', return_value='') as command, patch.object(wizard, 'api', side_effect=RuntimeError('HTTP 401')):
            with self.assertRaises(RuntimeError):
                wizard.configure(values, True, lambda text: None)
        self.assertEqual(command.call_count, 2)


if __name__ == '__main__':
    unittest.main()
