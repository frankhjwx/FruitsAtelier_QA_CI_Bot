import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import repair_ci


class BenchmarkTests(unittest.TestCase):
    def test_verified_real_benchmark_never_publishes_or_injects_synthetic_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            transfer=MagicMock()
            transfer.download.return_value={'files':[], 'test_token':'test'}
            def generate(ws,*args,**kwargs):
                ws.replace('src/Test.cs','','class Test {}')
                ws.set_regression('core','regression')
                ws.handoff_candidate()
                ws.last_cost=.01
                return 'candidate',.01
            env={'GITHUB_ACTIONS':'true','JOB_ID':'1234567890123456',
                 'TASK_BRANCH':'codex-discord-bug-fix','SOURCE_ROOT':directory,
                 'GH_TOKEN':'test','BOT_ACTIONS_TOKEN':'test','FA_BENCHMARK':'true',
                 'FA_ACCEPTANCE':'false','TRANSFER_URL':'test','TRANSFER_FINGERPRINT':'test',
                 'TRANSFER_TOKEN':'test','RUNNER_TEMP':directory,'TASK_REQUEST':'Real requirement',
                 'OPENAI_API_KEY':'test'}
            previous=Path.cwd()
            try:
                os.chdir(root)
                with patch.dict(os.environ,env), patch.object(repair_ci.worker,'request_json',return_value=[]), \
                     patch.object(repair_ci.subprocess,'check_output',return_value='a'*40), \
                     patch.object(repair_ci,'Transfer',return_value=transfer), \
                     patch.object(repair_ci,'evidence_pack',return_value='evidence'), \
                     patch.object(repair_ci.worker,'generate',side_effect=generate), \
                     patch.object(repair_ci,'wait_validation'), patch.object(repair_ci,'verified',return_value=True), \
                     patch.object(repair_ci.worker,'publish') as publish:
                    repair_ci.main()
                publish.assert_not_called()
                report=json.loads((root/'benchmark-result.json').read_text())
                self.assertTrue(report['verified'])
                self.assertEqual(report['cost'],.01)
                self.assertFalse((root/'src/FruitsAtelier.Core/PapayaAcceptance.cs').exists())
            finally:
                os.chdir(previous)
