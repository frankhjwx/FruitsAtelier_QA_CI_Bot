import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import worker
from context_pack import evidence_pack, concise_failures


class ContextPackTests(unittest.TestCase):
    def test_repair_includes_test_from_windows_failure_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            name='tests/Checks/AnchorTests.cs'
            path=root/name
            path.parent.mkdir(parents=True)
            path.write_text('namespace Actual;\n// Preserve endpoint snapping behavior')
            ws=worker.Workspace(root)
            with patch.object(worker.subprocess,'check_output',side_effect=['sha',name,'',name]):
                feedback=concise_failures({'exit':1,'output':r'at Check in D:\a\source\tests\Checks\AnchorTests.cs:line 49'})
                result=evidence_pack(ws,'anchor',feedback)
            self.assertIn('Preserve endpoint snapping behavior',result)
            self.assertIn('FAILING CHECK SOURCE '+name,result)

    def test_failures_are_deduplicated_and_success_output_removed(self):
        results=[{'build':{'exit':1,'output':'error CS0117: missing Helper\n'*1000},
                  'other':{'exit':0,'output':'PASS secret content'}}]
        result=concise_failures(results)
        self.assertEqual(result.count('error CS0117'),1)
        self.assertNotIn('secret content',result)
        self.assertLess(len(result),3500)

    def test_initial_pack_contains_source_test_and_signatures_within_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            files={'src/FruitsAtelier.Core/Anchor.cs':
                   'namespace Actual.Domain;\nclass Curve { public bool MoveAnchor(int time) { '+ '/* anchor movement */ '*12+' return true; } }',
                   'tests/Anchor.cs':'class Checks { public void MoveAnchor() { '+'/* anchor test */ '*14+' } }',
                   'tests/AnchorSnapTests.cs':'class AnchorSnapTests {\n public void Dragging() { '+'/* preserve snapping */ '*14+' }\n}'}
            for name,body in files.items():
                path=root/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_text(body)
            ws=worker.Workspace(root)
            with patch.object(worker.subprocess,'check_output',side_effect=['sha','\n'.join(files)]):
                result=evidence_pack(ws,'move anchor through endpoint')
            self.assertIn('MoveAnchor',result)
            self.assertIn('tests/Anchor.cs',result)
            self.assertIn('namespace Actual.Domain;',result)
            self.assertIn('preserve snapping',result)
            self.assertLessEqual(len(result.encode()),18100)
