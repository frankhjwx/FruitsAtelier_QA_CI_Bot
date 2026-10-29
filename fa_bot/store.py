import datetime as dt
import sqlite3
import time
from pathlib import Path

from .config import MONTH_CAP, DEVELOPMENT_CAP, QA_CAP


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
          PRAGMA journal_mode=WAL;
          CREATE TABLE IF NOT EXISTS charges (
            id TEXT PRIMARY KEY, month TEXT, kind TEXT, amount REAL, created REAL);
          CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, owner TEXT, channel TEXT, branch TEXT,
            request TEXT, status TEXT, run_id INTEGER, url TEXT, created REAL);
        ''')

    def reserve(self, key, kind, amount):
        if kind not in ('qa', 'dev') or amount <= 0:
            raise ValueError('Invalid reservation')
        month = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if self.db.execute('SELECT 1 FROM charges WHERE id=?', (key,)).fetchone():
                raise ValueError('This request has already been processed.')
            total, category = self.db.execute(
                'SELECT coalesce(sum(amount),0), coalesce(sum(CASE WHEN kind=? THEN amount ELSE 0 END),0) FROM charges WHERE month=?',
                (kind, month)).fetchone()
            cap = QA_CAP if kind == 'qa' else DEVELOPMENT_CAP
            if total + amount > MONTH_CAP or category + amount > cap:
                raise ValueError('Monthly budget exhausted; please contact the maintainer.')
            self.db.execute('INSERT INTO charges VALUES (?,?,?,?,?)',
                            (key, month, kind, amount, time.time()))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def add_job(self, key, owner, channel, branch, request):
        with self.db:
            self.db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,NULL,NULL,?)',
                            (key, str(owner), str(channel), branch, request,
                             'dispatching', time.time()))

    def settle(self, key, amount):
        if amount < 0:
            raise ValueError('Invalid usage cost')
        with self.db:
            self.db.execute('UPDATE charges SET amount=min(amount,?) WHERE id=?', (amount, key))

    def update(self, key, status, run_id=None, url=None):
        with self.db:
            self.db.execute('UPDATE jobs SET status=?,run_id=coalesce(?,run_id),url=coalesce(?,url) WHERE id=?',
                            (status, run_id, url, key))

    def job(self, key):
        return self.db.execute('SELECT * FROM jobs WHERE id=?', (key,)).fetchone()

    def pending(self):
        return self.db.execute("SELECT * FROM jobs WHERE status IN ('dispatching','queued','running','building')").fetchall()

    def repair_active(self):
        # An uncertain dispatch may still be executing and must retain the slot.
        return self.db.execute("SELECT 1 FROM jobs WHERE status IN ('dispatching','queued','running','building','dispatch_uncertain','build_dispatch_uncertain') LIMIT 1").fetchone() is not None

    def latest_job(self):
        return self.db.execute('SELECT * FROM jobs ORDER BY created DESC, rowid DESC LIMIT 1').fetchone()

    def recent_jobs(self, owner):
        return self.db.execute('SELECT count(*) FROM jobs WHERE owner=? AND created>?',
                               (str(owner), time.time() - 86400)).fetchone()[0]
