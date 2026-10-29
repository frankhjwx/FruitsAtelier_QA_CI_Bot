import tempfile
import time
import unittest
from pathlib import Path

from fa_bot.store import Store


class DailyLimitsTests(unittest.TestCase):
    def test_qa_limit_is_durable_per_user_and_expires(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.sqlite3'
            store = Store(path)
            for i in range(2000):
                store.reserve(str(i), 'qa', .001, owner=1)
            store.db.close()
            store = Store(path)
            with self.assertRaisesRegex(ValueError, 'Daily QA limit'):
                store.reserve('blocked', 'qa', .001, owner=1)
            self.assertIsNone(store.db.execute("SELECT 1 FROM charges WHERE id='blocked'").fetchone())
            store.reserve('other-user', 'qa', .001, owner=2)
            with store.db:
                store.db.execute('UPDATE qa_requests SET created=? WHERE id=?', (time.time()-86401, '0'))
            store.reserve('new-slot', 'qa', .001, owner=1)
            store.db.close()
