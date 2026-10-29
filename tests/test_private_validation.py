import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

import validate_private


class TargetedValidationTests(unittest.TestCase):
    def test_targeted_comparison_is_the_private_gate(self):
        for before, after, expected in ((42,0,True),(0,0,False),(42,42,False)):
            with self.subTest(before=before,after=after), tempfile.TemporaryDirectory() as folder:
                root=Path(folder); source=root/'source'; source.mkdir()
                transfer=MagicMock()
                def download(path, **kwargs):
                    path.mkdir(parents=True)
                    return {'files':[],'expires':time.time()+7200}
                transfer.download.side_effect=download
                transfer.json.return_value={'base':'a'*40,'changes':{'src/Test.cs':'class Test {}'},
                                            'regression':{'target':'core','code':'same regression'}}
                env={'JOB_ID':'1234567890123456','ATTEMPT':'1','VALIDATION_PLATFORM':'windows',
                     'SOURCE_ROOT':str(source),'RUNNER_TEMP':folder,'TRANSFER_URL':'test',
                     'TRANSFER_FINGERPRINT':'test','TRANSFER_TOKEN':'test','FA_ACCEPTANCE':'false'}
                results=[{'build':{'exit':0},'run':{'exit':code}} for code in (before,after)]
                with patch.dict(os.environ,env), patch.object(validate_private,'Transfer',return_value=transfer), \
                        patch.object(validate_private.subprocess,'check_output',return_value='a'*40), \
                        patch.object(validate_private,'regression',side_effect=results) as regression, \
                        patch.object(validate_private,'run') as run:
                    validate_private.main()
                self.assertEqual(regression.call_count,2)
                self.assertEqual(regression.call_args_list[0].args[2],regression.call_args_list[1].args[2])
                run.assert_not_called()
                submitted=transfer.json.call_args.args[1]
                self.assertEqual(submitted['passed'],expected)
                self.assertNotIn('suites',submitted)
                self.assertFalse((root/'fa-private-1234567890123456-1').exists())
