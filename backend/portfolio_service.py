"""Local visitor sessions, isolated conversations and durable Copilot budgets.

No Firebase client is constructed. SQLite transactions also coordinate workers
sharing the same persistent database. Daily budgets reset at midnight KST.
"""
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

KST = timezone(timedelta(hours=9))
VISITOR_COOKIE = 'batteryflow_portfolio_visitor'


class PortfolioService:
    mode = 'portfolio'

    def __init__(self, path, visitor_limit=5, global_limit=100, ip_limit=20, clock=time.time):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.visitor_limit, self.global_limit, self.ip_limit = visitor_limit, global_limit, ip_limit
        if min(visitor_limit, global_limit, ip_limit) < 1:
            raise ValueError('포트폴리오 질문 한도는 1 이상이어야 합니다.')
        self.clock = clock
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS config (name TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, uid TEXT, ip TEXT, expires REAL);
                CREATE TABLE IF NOT EXISTS usage (day TEXT, scope TEXT, used INTEGER, last REAL,
                    PRIMARY KEY(day, scope));
                CREATE TABLE IF NOT EXISTS chats (uid TEXT, id TEXT, document TEXT, expires REAL,
                    PRIMARY KEY(uid, id));
            ''')
            db.execute('INSERT OR IGNORE INTO config VALUES (?, ?)', ('secret', secrets.token_hex(32)))
            self.secret = db.execute('SELECT value FROM config WHERE name=?', ('secret',)).fetchone()[0].encode()

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def digest(self, value):
        return hmac.new(self.secret, value.encode(), hashlib.sha256).hexdigest()

    def start(self, visitor, host):
        uid, _, signature = (visitor or '').partition('.')
        if len(uid) != 32 or not hmac.compare_digest(self.digest('visitor:'+uid), signature):
            uid = uuid4().hex
        visitor = uid + '.' + self.digest('visitor:'+uid)
        token, now = secrets.token_urlsafe(32), self.clock()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM sessions WHERE expires <= ?', (now,))
            db.execute('DELETE FROM chats WHERE expires <= ?', (now,))
            oldest = (datetime.fromtimestamp(now, KST)-timedelta(days=7)).date().isoformat()
            db.execute('DELETE FROM usage WHERE day < ?', (oldest,))
            if db.execute('SELECT count(*) FROM sessions').fetchone()[0] >= 1000:
                raise HTTPException(429, '방문자가 많습니다. 잠시 후 다시 접속해 주세요.')
            # Keep one active session per browser; starting again cannot reset usage.
            db.execute('DELETE FROM sessions WHERE uid=?', (uid,))
            db.execute('INSERT INTO sessions VALUES (?, ?, ?, ?)',
                       (self.digest(token), uid, self.digest('ip:'+host), now+28800))
        return token, self.user(uid), visitor

    @staticmethod
    def user(uid):
        return {'uid': uid, 'name': '방문자', 'role': 'visitor'}

    def login(self, email, password):
        raise HTTPException(403, '포트폴리오 둘러보기 버튼을 이용하세요.')

    def verify(self, cookie, fresh=False):
        with self.db() as db:
            row = db.execute('SELECT uid FROM sessions WHERE token=? AND expires>?',
                             (self.digest(cookie or ''), self.clock())).fetchone()
        if not row:
            raise HTTPException(401, '포트폴리오 둘러보기로 다시 접속해 주세요.')
        return self.user(row[0])

    def logout(self, cookie):
        with self.db() as db:
            db.execute('DELETE FROM sessions WHERE token=?', (self.digest(cookie or ''),))

    def budget(self, uid, reserve=False):
        now = self.clock()
        today = datetime.fromtimestamp(now, KST)
        day = today.date().isoformat()
        reset = datetime.combine(today.date()+timedelta(days=1), datetime.min.time(), KST).isoformat()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            session = db.execute('SELECT ip FROM sessions WHERE uid=? AND expires>?', (uid, now)).fetchone()
            if not session:
                raise HTTPException(401, '포트폴리오에 다시 접속해 주세요.')
            scopes = [('visitor:'+uid, self.visitor_limit), ('ip:'+session[0], self.ip_limit), ('global', self.global_limit)]
            counts = [db.execute('SELECT used, last FROM usage WHERE day=? AND scope=?', (day, key)).fetchone() or (0, 0)
                      for key, _ in scopes]
            remaining = min(limit-row[0] for (_, limit), row in zip(scopes, counts))
            if reserve:
                if remaining <= 0:
                    raise HTTPException(429, '오늘의 포트폴리오 Copilot 질문 한도를 모두 사용했습니다. 한국 시간 자정에 초기화됩니다.')
                if counts[0][0] and now-counts[0][1] < 3:
                    raise HTTPException(429, '응답을 확인한 뒤 잠시 후 다시 질문해 주세요.')
                for key, _ in scopes:
                    db.execute('INSERT INTO usage VALUES (?, ?, 1, ?) ON CONFLICT(day, scope) DO UPDATE SET used=used+1,last=excluded.last', (day, key, now))
                remaining -= 1
            return {'limit': self.visitor_limit, 'used': counts[0][0]+int(reserve),
                    'remaining': max(0, remaining), 'resetsAt': reset}

    def list_conversations(self, uid):
        with self.db() as db:
            rows = db.execute('SELECT document FROM chats WHERE uid=? AND expires>? ORDER BY expires DESC LIMIT 30', (uid, self.clock())).fetchall()
        return [{k: doc[k] for k in ('id', 'title', 'updatedAt', 'revision')} for row in rows for doc in [json.loads(row[0])]]

    def conversation(self, uid, thread_id):
        with self.db() as db:
            row = db.execute('SELECT document FROM chats WHERE uid=? AND id=? AND expires>?', (uid, thread_id, self.clock())).fetchone()
        if not row:
            raise HTTPException(404, '대화를 찾을 수 없습니다. 보관 기간은 24시간입니다.')
        return json.loads(row[0])

    def save_conversation(self, uid, thread_id, revision, question, result):
        now = self.clock()
        stamp = datetime.fromtimestamp(now, timezone.utc).isoformat()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT document FROM chats WHERE uid=? AND id=? AND expires>?', (uid, thread_id, now)).fetchone()
            old = json.loads(row[0]) if row else {'messages': [], 'revision': 0}
            if old['revision'] != revision:
                raise HTTPException(409, '대화가 변경되었습니다. 다시 열어 주세요.')
            doc = {'id': thread_id, 'title': old.get('title', question[:60]), 'revision': revision+1, 'updatedAt': stamp,
                   'messages': old['messages']+[{'role':'user', 'text':question, 'at':stamp}, {'role':'assistant', **result, 'at':stamp}]}
            raw = json.dumps(doc, ensure_ascii=False)
            if len(raw.encode()) > 850_000:
                raise HTTPException(422, '대화 자료가 많습니다. 새 대화를 시작해 주세요.')
            db.execute('INSERT OR REPLACE INTO chats VALUES (?, ?, ?, ?)', (uid, thread_id, raw, now+86400))
        return doc

    def unavailable(self, *args, **kwargs):
        raise HTTPException(403, '포트폴리오에서는 실제 업무 기록을 조회하거나 변경하지 않습니다.')

    states = history = save_record = unavailable
