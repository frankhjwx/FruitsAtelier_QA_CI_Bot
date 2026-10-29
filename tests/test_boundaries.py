import importlib.util
import io
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import unittest

from fa_bot.knowledge import Knowledge
from fa_bot.store import Store

spec = importlib.util.spec_from_file_location('worker', Path(__file__).parents[1] / 'worker.py')
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class BudgetTests(unittest.TestCase):
    def test_reservation_survives_restart_and_rejects_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.sqlite3'
            first = Store(path)
            first.reserve('1', 'dev', .5)
            second = Store(path)
            with self.assertRaises(ValueError):
                second.reserve('1', 'dev', .5)
            first.db.close()
            second.db.close()

    def test_monthly_category_cap_is_atomic(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'state.sqlite3')
            store.reserve('1', 'dev', 80)
            with self.assertRaises(ValueError):
                store.reserve('2', 'dev', .5)
            store.reserve('3', 'qa', .02)
            store.settle('3', .001)
            self.assertAlmostEqual(store.db.execute("SELECT amount FROM charges WHERE id='3'").fetchone()[0], .001)
            store.db.close()


class WorkspaceTests(unittest.TestCase):
    def test_paths_and_build_configuration_cannot_be_modified(self):
        with tempfile.TemporaryDirectory() as folder:
            ws = worker.Workspace(folder)
            for path in ('../secret.md', '/etc/key.md', 'src/../../secret.cs', '.github/workflows/run.yml',
                         'src/Foo.csproj', 'scripts/test.py', 'macOS/AGENTS.md', 'src/foo:bar.cs'):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    ws.path(path, write=True)

    def test_replace_requires_unique_evidence_and_limits_files(self):
        with tempfile.TemporaryDirectory() as folder:
            ws = worker.Workspace(folder)
            ws.replace('src/Test.cs', '', 'abc abc')
            with self.assertRaises(ValueError):
                ws.replace('src/Test.cs', 'abc', 'def')
            ws.replace('src/Test.cs', 'abc abc', 'def')
            self.assertEqual(ws.path('src/Test.cs').read_text(), 'def')
            for number in range(7):
                ws.replace(f'src/T{number}.cs', '', 'example')
            with self.assertRaises(ValueError):
                ws.replace('src/TooMany.cs', '', 'example')


class RetrievalTests(unittest.TestCase):
    def test_public_archive_is_read_without_extraction(self):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode='w:gz') as archive:
            for name, text in [('root/docs/USER_MANUAL.md', 'FSlider anchors and handles'),
                               ('root/.env', 'secret should never be indexed')]:
                raw = text.encode()
                item = tarfile.TarInfo(name)
                item.size = len(raw)
                archive.addfile(item, io.BytesIO(raw))
        kb = Knowledge()
        kb.load(data.getvalue(), 'abc123', [])
        result = kb.search('FSlider')
        self.assertEqual(len(result), 1)
        self.assertIn('/blob/abc123/docs/', result[0]['url'])
        self.assertEqual(kb.search('secret'), [])


if __name__ == '__main__':
    unittest.main()
