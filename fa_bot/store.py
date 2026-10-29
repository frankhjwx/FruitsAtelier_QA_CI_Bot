import datetime as dt
import json
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
          CREATE TABLE IF NOT EXISTS qa_requests (id TEXT PRIMARY KEY, owner TEXT, created REAL);
          CREATE TABLE IF NOT EXISTS job_attachments (job_id TEXT PRIMARY KEY, ids TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS job_progress (
            job_id TEXT PRIMARY KEY, message_id TEXT NOT NULL, updated REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS job_cancellations (
            job_id TEXT PRIMARY KEY, created REAL NOT NULL, run_id INTEGER, building INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS reviews (
            job_id TEXT PRIMARY KEY, sha TEXT, state TEXT, url TEXT,
            number INTEGER, created REAL);
          CREATE TABLE IF NOT EXISTS charges (
            id TEXT PRIMARY KEY, month TEXT, kind TEXT, amount REAL, created REAL);
          CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, owner TEXT, channel TEXT, branch TEXT,
            request TEXT, status TEXT, run_id INTEGER, url TEXT, created REAL);
        ''')

    def reserve(self, key, kind, amount, owner=None):
        if kind not in ('qa', 'dev') or amount <= 0:
            raise ValueError('Invalid reservation')
        month = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if self.db.execute('SELECT 1 FROM charges WHERE id=?', (key,)).fetchone():
                raise ValueError('This request has already been processed.')
            if kind == 'qa' and owner is not None:
                count = self.db.execute('SELECT count(*) FROM qa_requests WHERE owner=? AND created>?',
                                        (str(owner), time.time() - 86400)).fetchone()[0]
                if count >= 200:
                    raise ValueError('Daily QA limit reached. Please try later.')
            total, category = self.db.execute(
                'SELECT coalesce(sum(amount),0), coalesce(sum(CASE WHEN kind=? THEN amount ELSE 0 END),0) FROM charges WHERE month=?',
                (kind, month)).fetchone()
            cap = QA_CAP if kind == 'qa' else DEVELOPMENT_CAP
            if total + amount > MONTH_CAP or category + amount > cap:
                raise ValueError('Monthly budget exhausted; please contact the maintainer.')
            self.db.execute('INSERT INTO charges VALUES (?,?,?,?,?)',
                            (key, month, kind, amount, time.time()))
            if kind == 'qa' and owner is not None:
                self.db.execute('INSERT INTO qa_requests VALUES (?,?,?)', (key, str(owner), time.time()))
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

    def bind_progress(self, key, message_id):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO job_progress VALUES (?,?,0)', (key, str(message_id)))

    def progress(self, key):
        return self.db.execute('SELECT * FROM job_progress WHERE job_id=?', (key,)).fetchone()

    def progress_attempt(self, key, now):
        with self.db:
            self.db.execute('UPDATE job_progress SET updated=? WHERE job_id=?', (now, key))

    def pending(self):
        return self.db.execute("""SELECT jobs.* FROM jobs LEFT JOIN job_cancellations c ON c.job_id=jobs.id
            WHERE status IN ('dispatching','queued','running','building','cancelling')
            OR (status='cancelled' AND c.created>?)""",(time.time()-600,)).fetchall()

    def cancellation(self, key):
        return self.db.execute('SELECT * FROM job_cancellations WHERE job_id=?',(key,)).fetchone()

    def request_cancel(self, key, owner, channel):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            job=self.job(key)
            if not job or job['owner']!=str(owner) or job['channel']!=str(channel):
                raise ValueError('Only the original requester in the original channel can cancel this task.')
            if job['status']=='cancelled':
                raise ValueError('This task is already cancelled.')
            if job['status'] not in ('dispatching','queued','running','building','dispatch_uncertain','build_dispatch_uncertain','cancelling'):
                raise ValueError('This task has already finished and cannot be cancelled.')
            self.db.execute('INSERT OR IGNORE INTO job_cancellations VALUES (?,?,?,?)',
                            (key,time.time(),job['run_id'],int(job['status']=='building')))
            self.db.execute("UPDATE jobs SET status='cancelling' WHERE id=?",(key,))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def repair_active(self):
        # An uncertain dispatch may still be executing and must retain the slot.
        return (self.db.execute("SELECT 1 FROM jobs WHERE status IN ('dispatching','queued','running','building','dispatch_uncertain','build_dispatch_uncertain','cancelling') LIMIT 1").fetchone() is not None or bool(self.active_reviews()))

    def latest_job(self):
        return self.db.execute('SELECT * FROM jobs ORDER BY created DESC, rowid DESC LIMIT 1').fetchone()

    def recent_jobs(self, owner):
        return self.db.execute('SELECT count(*) FROM jobs WHERE owner=? AND created>?',
                               (str(owner), time.time() - 86400)).fetchone()[0]

    def save_source(self, key, sha):
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO reviews VALUES (?,?,'ready',NULL,NULL,?)", (key, sha, time.time()))

    def review(self, key):
        return self.db.execute('SELECT * FROM reviews WHERE job_id=?', (key,)).fetchone()

    def active_reviews(self):
        return self.db.execute("SELECT * FROM reviews WHERE state IN ('requested','uncertain','opened')").fetchall()

    def review_state(self, key, state, url=None, number=None):
        with self.db:
            self.db.execute('UPDATE reviews SET state=?,url=coalesce(?,url),number=coalesce(?,number) WHERE job_id=?',
                            (state, url, number, key))

    def save_attachments(self, key, ids):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO job_attachments VALUES (?,?)',(key,json.dumps(list(ids))))

    def job_attachments(self, key):
        row=self.db.execute('SELECT ids FROM job_attachments WHERE job_id=?',(key,)).fetchone()
        return json.loads(row[0]) if row else []
