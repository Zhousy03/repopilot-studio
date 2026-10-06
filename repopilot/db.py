"""Small single-operator application database, separate from Graph checkpoints."""
from __future__ import annotations
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def uid() -> str:
    return uuid4().hex


class Store:
    def __init__(self, root: Path):
        self.path = root / 'studio.sqlite3'
        self.lock = threading.RLock()
        with self.connect() as con:
            con.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS objects (
              kind TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL,
              updated TEXT NOT NULL, PRIMARY KEY (kind,id));
            CREATE TABLE IF NOT EXISTS events (
              seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
              event TEXT NOT NULL, data TEXT NOT NULL, at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS events_run ON events(run_id,seq);
            ''')

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=15)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def put(self, kind: str, data: dict) -> dict:
        with self.lock, self.connect() as con:
            con.execute('INSERT OR REPLACE INTO objects VALUES (?,?,?,?)',
                        (kind,data['id'],json.dumps(data,ensure_ascii=False),now()))
        return data

    def get(self, kind: str, id: str) -> dict:
        with self.connect() as con:
            row = con.execute('SELECT payload FROM objects WHERE kind=? AND id=?',(kind,id)).fetchone()
        if not row: raise KeyError(f'{kind} 不存在')
        return json.loads(row['payload'])

    def update(self, kind: str, id: str, changes: dict) -> dict:
        with self.lock:
            return self.put(kind, self.get(kind,id) | changes)

    def list(self, kind: str, limit: int = 5000) -> list[dict]:
        with self.connect() as con:
            rows = con.execute('SELECT payload FROM objects WHERE kind=? ORDER BY updated DESC LIMIT ?',
                               (kind,limit)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def delete(self, kind: str, id: str):
        with self.lock, self.connect() as con:
            con.execute('DELETE FROM objects WHERE kind=? AND id=?',(kind,id))

    def event(self, run_id: str, event: str, data: dict) -> dict:
        at=now()
        with self.lock, self.connect() as con:
            seq=con.execute('INSERT INTO events(run_id,event,data,at) VALUES (?,?,?,?)',
                            (run_id,event,json.dumps(data,ensure_ascii=False),at)).lastrowid
        return {'seq':seq,'event':event,'data':data,'at':at}

    def events(self, run_id: str, after: int=0, limit: int=200) -> list[dict]:
        with self.connect() as con:
            rows=con.execute('SELECT * FROM events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?',
                             (run_id,after,limit)).fetchall()
        return [dict(r) | {'data':json.loads(r['data'])} for r in rows]

    def all_events(self, run_id: str) -> list[dict]:
        out=[]; seq=0
        while True:
            batch=self.events(run_id,seq,500)
            if not batch: return out
            out.extend(batch); seq=batch[-1]['seq']
