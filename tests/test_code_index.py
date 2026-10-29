import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fa_bot.code_index import CodeIndex
import worker


class CodeIndexTests(unittest.TestCase):
    def test_worker_index_tracks_local_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'src').mkdir()
            (root/'src/Curve.cs').write_text('class Curve { void OldSymbol() {} }',encoding='utf-8')
            workspace = worker.Workspace(root)
            with patch.object(worker.subprocess,'check_output',side_effect=['commit-a','src/Curve.cs\n']):
                self.assertIn('OldSymbol',workspace.code_search('OldSymbol'))
            workspace.replace('src/Curve.cs','OldSymbol','NewSymbol')
            self.assertNotIn('OldSymbol',workspace.code_search('OldSymbol'))
            self.assertIn('NewSymbol',workspace.code_search('NewSymbol'))

    def test_symbol_body_bounds_and_modified_file_replaces_old_content(self):
        index = CodeIndex('commit-a')
        index.add('src/Curve.cs', '''namespace FA;
public class Curve {
    public bool TryMoveAnchor(int time) { return time > 0; }
    public void Unrelated() { }
}''')
        match = index.search('TryMoveAnchor')[0]
        self.assertEqual(match['symbol'], 'Curve.TryMoveAnchor')
        self.assertEqual(match['start'], 3)
        self.assertNotIn('Unrelated',match['text'])
        index.add('src/Curve.cs','class Curve { public void ReplacementSymbol() {} }')
        self.assertEqual(index.search('TryMoveAnchor'), [])
        self.assertIn('ReplacementSymbol', index.search('ReplacementSymbol')[0]['text'])

    def test_cache_roundtrip_and_commit_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'index.sqlite3'
            index = CodeIndex('commit-a')
            index.add('src/Curve.cs','class Curve { public void TryMoveAnchor() {} }')
            index.save(path)
            restored = CodeIndex.restore(path,'commit-a')
            self.assertEqual(restored.search('TryMoveAnchor'),index.search('TryMoveAnchor'))
            with self.assertRaisesRegex(ValueError,'version mismatch'):
                CodeIndex.restore(path,'commit-b')

    def test_query_is_data_and_payload_is_bounded(self):
        index = CodeIndex('commit-a')
        index.add('src/Curve.cs', 'class Curve { public void TryMoveAnchor() {} }')
        self.assertTrue(index.search('" TryMoveAnchor OR * ('))
        self.assertEqual(index.search('TryMoveAnchor',budget=1),[])
        self.assertEqual(index.search(''),[])
