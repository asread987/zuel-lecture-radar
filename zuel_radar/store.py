# -*- coding: utf-8 -*-
"""持久化层：文章去重记录、每用户运行状态、推送日志（SQLite）。

多用户隔离方式：
    - articles 表全局共享（同一条讲座对所有用户只抓一次、只抽一次字段）；
    - user_articles 表记录「某用户是否已收到某条」，实现按用户维度的去重；
    - user_state 表记录每位用户的上次推送日期，用于「间隔 N 天」判定。
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    uid         TEXT PRIMARY KEY,
    source_key  TEXT NOT NULL,
    source_name TEXT,
    title       TEXT,
    url         TEXT,
    published   TEXT,
    event_date  TEXT,
    is_econ     INTEGER DEFAULT 0,
    kind        TEXT DEFAULT 'other',
    first_seen  TEXT,
    payload     TEXT
);
CREATE INDEX IF NOT EXISTS idx_articles_pub ON articles(published);
CREATE INDEX IF NOT EXISTS idx_articles_src ON articles(source_key);

CREATE TABLE IF NOT EXISTS user_articles (
    user_id   TEXT NOT NULL,
    uid       TEXT NOT NULL,
    sent_at   TEXT,
    PRIMARY KEY (user_id, uid)
);

CREATE TABLE IF NOT EXISTS user_state (
    user_id        TEXT PRIMARY KEY,
    last_push_date TEXT,
    last_run_at    TEXT,
    last_status    TEXT,
    last_count     INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS push_log (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id  TEXT,
    run_at   TEXT,
    count    INTEGER,
    channel  TEXT,
    ok       INTEGER,
    detail   TEXT,
    report   TEXT
);
"""


class Store:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ---------- 文章 ----------
    def known_uids(self) -> set[str]:
        with self._lock:
            return {r[0] for r in self._conn.execute("SELECT uid FROM articles")}

    def save_articles(self, lectures) -> None:
        now = dt.datetime.now().isoformat(timespec="seconds")
        rows = []
        for lec in lectures:
            d = lec.to_dict()
            rows.append((
                d["uid"], d["source_key"], d["source_name"], d["title"], d["url"],
                d.get("published"), d.get("event_date"), 1 if d["is_econ"] else 0,
                d.get("kind", "other"), now,
                __import__("json").dumps(d, ensure_ascii=False),
            ))
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO articles "
                "(uid,source_key,source_name,title,url,published,event_date,is_econ,kind,first_seen,payload) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
            self._conn.commit()

    def sent_uids(self, user_id: str) -> set[str]:
        with self._lock:
            return {r[0] for r in self._conn.execute(
                "SELECT uid FROM user_articles WHERE user_id=?", (user_id,))}

    def get_payloads(self, uids: list[str]) -> dict[str, dict]:
        """取回已抓取过的文章详情，避免多用户之间重复渲染抓取。"""
        import json as _json
        out: dict[str, dict] = {}
        if not uids:
            return out
        with self._lock:
            for i in range(0, len(uids), 400):
                chunk = uids[i:i + 400]
                q = ("SELECT uid,payload FROM articles WHERE uid IN (%s)"
                     % ",".join("?" * len(chunk)))
                for r in self._conn.execute(q, chunk):
                    try:
                        out[r["uid"]] = _json.loads(r["payload"] or "{}")
                    except Exception:  # noqa: BLE001
                        continue
        return out

    def mark_sent(self, user_id: str, uids: list[str]) -> None:
        now = dt.datetime.now().isoformat(timespec="seconds")
        with self._lock:
            self._conn.executemany(
                "INSERT OR IGNORE INTO user_articles (user_id,uid,sent_at) VALUES (?,?,?)",
                [(user_id, u, now) for u in uids])
            self._conn.commit()

    def stats(self) -> dict:
        with self._lock:
            a = self._conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
            e = self._conn.execute("SELECT COUNT(*) FROM articles WHERE is_econ=1").fetchone()[0]
            u = self._conn.execute("SELECT COUNT(*) FROM user_articles").fetchone()[0]
        return {"articles": a, "econ": e, "deliveries": u}

    # ---------- 用户状态 ----------
    def get_state(self, user_id: str) -> dict:
        with self._lock:
            r = self._conn.execute(
                "SELECT * FROM user_state WHERE user_id=?", (user_id,)).fetchone()
        if not r:
            return {"user_id": user_id, "last_push_date": None, "last_run_at": None,
                    "last_status": None, "last_count": 0}
        return dict(r)

    def set_state(self, user_id: str, *, last_push_date: str | None = None,
                  status: str | None = None, count: int | None = None) -> None:
        now = dt.datetime.now().isoformat(timespec="seconds")
        cur = self.get_state(user_id)
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO user_state "
                "(user_id,last_push_date,last_run_at,last_status,last_count) VALUES (?,?,?,?,?)",
                (user_id,
                 last_push_date if last_push_date is not None else cur.get("last_push_date"),
                 now,
                 status if status is not None else cur.get("last_status"),
                 count if count is not None else cur.get("last_count", 0)))
            self._conn.commit()

    # ---------- 推送日志 ----------
    def log_push(self, user_id: str, count: int, channel: str, ok: bool,
                 detail: str = "", report: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO push_log (user_id,run_at,count,channel,ok,detail,report) "
                "VALUES (?,?,?,?,?,?,?)",
                (user_id, dt.datetime.now().isoformat(timespec="seconds"),
                 count, channel, 1 if ok else 0, detail[:500], report))
            self._conn.commit()

    def recent_logs(self, limit: int = 20, user_id: str | None = None) -> list[dict]:
        with self._lock:
            if user_id:
                rows = self._conn.execute(
                    "SELECT * FROM push_log WHERE user_id=? ORDER BY id DESC LIMIT ?",
                    (user_id, limit)).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM push_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
