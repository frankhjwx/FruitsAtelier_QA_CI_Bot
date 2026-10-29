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


class KnowledgeReadTests(unittest.TestCase):
    def test_scoped_document_search_and_line_read(self):
        from fa_bot.knowledge import Knowledge, terms
        from collections import Counter
        knowledge=Knowledge()
        knowledge.sha='a'*40
        body='Header\nF03 timestamp measurement\nDetails'
        knowledge.chunks=[('docs/Tasks.md',f'https://github.com/frankhjwx/FruitsAtelier/blob/{knowledge.sha}/docs/Tasks.md#L61',body,Counter(terms(body)))]
        knowledge.df=Counter(terms(body))
        self.assertEqual(knowledge.search('F03',prefix='docs/')[0]['source'],'docs/Tasks.md')
        self.assertIn('62: F03 timestamp measurement',knowledge.read_evidence('docs/Tasks.md',62)[0]['text'])
        self.assertEqual(knowledge.read_evidence('../../etc/passwd',1),[])

    def test_code_read_tracks_requested_line_range(self):
        from fa_bot.knowledge import Knowledge
        knowledge=Knowledge(); knowledge.sha='a'*40
        knowledge.code_index=CodeIndex(knowledge.sha)
        knowledge.code_index.add('src/Selection.cs','class Selection {\n public int Count => 2;\n public int Other => 3;\n}')
        result=knowledge.read_evidence('src/Selection.cs',2)[0]
        self.assertIn('2:  public int Count => 2;',result['text'])
        self.assertNotIn('1: class',result['text'])
