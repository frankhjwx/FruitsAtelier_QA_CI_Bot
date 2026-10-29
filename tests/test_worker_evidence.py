import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import worker


class WorkerEvidenceTests(unittest.TestCase):
    def test_scoped_literal_search_and_pagination_reach_late_matches(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'docs').mkdir()
            (root/'src').mkdir()
            (root/'docs/noise.md').write_text('anchor unrelated\n'*80, encoding='utf-8')
            (root/'src/Editor.cs').write_text('MoveAnchor endpoint\n'*45 + 'unique tail\n', encoding='utf-8')
            workspace = worker.Workspace(root)
            with patch.object(worker.subprocess,'check_output',return_value='docs/noise.md\nsrc/Editor.cs\n'):
                first = workspace.search('MoveAnchor endpoint', 'src')
                second = workspace.search('MoveAnchor endpoint', 'src', 41)
                self.assertIn('45 matches; next start=41',first)
                self.assertNotIn('noise.md',first)
                self.assertIn('src/Editor.cs:45:',second)
                self.assertIn('next start=none',second)
                self.assertNotIn('src/Editor.cs:40:',second)
                with self.assertRaises(ValueError):
                    workspace.search('anchor','../outside')

    def test_read_range_and_notes_survive_compaction(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'Test.cs').write_text('\n'.join('line '+str(i) for i in range(400)),encoding='utf-8')
            workspace = worker.Workspace(root)
            text = workspace.read('Test.cs', 121, 200)
            self.assertIn('lines 121-320 of 400; next start=321',text)
            workspace.remember('Read Test.cs lines 121-320; edit endpoint swapping next.')
            history = []
            for i in range(8):
                history.extend([{'type':'function_call','name':'read','call_id':str(i)},
                                {'type':'function_call_output','call_id':str(i),'output':'x'*14000}])
            self.assertTrue(worker.compact_evidence(history))
            self.assertIn('endpoint swapping',workspace.notes)
