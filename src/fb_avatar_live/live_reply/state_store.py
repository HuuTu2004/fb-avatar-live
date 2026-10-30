from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Iterable

from .types import ReplyDecision, SpeechJob, StoredComment


def source_of(identifier: str) -> str:
    if identifier.startswith("tiktok:"):
        return "tiktok"
    if identifier.startswith("facebook-personal:"):
        return "facebook_personal"
    if identifier.startswith("dashboard-"):
        return "dashboard"
    if identifier.startswith("sales-demo-"):
        return "internal"
    if identifier.startswith("demo:"):
        return "demo"
    if identifier.replace("_", "").isdigit():
        return "facebook_page"
    return "demo"


class LiveStateStore:
    """Owns comment state and the speech outbox in one SQLite transaction."""

    def __init__(self, path: Path, *, comment_ttl: float = 180.0):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.lock = threading.RLock()
        self.comment_ttl = comment_ttl
        self._migrate()
        self._recover()

    def _migrate(self) -> None:
        with self.lock, self.db:
            self.db.execute("""CREATE TABLE IF NOT EXISTS comments (
                id TEXT PRIMARY KEY, author TEXT NOT NULL, message TEXT NOT NULL,
                created_time TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                added_at REAL NOT NULL)""")
            fields = {row[1] for row in self.db.execute("PRAGMA table_info(comments)")}
            for name, kind, default in (
                ("source", "TEXT", "''"), ("reason_code", "TEXT", "''"),
                ("reply_id", "TEXT", "NULL"), ("lease_until", "REAL", "0"),
                ("next_retry_at", "REAL", "0"), ("attempts", "INTEGER", "0"),
            ):
                if name not in fields:
                    self.db.execute(f"ALTER TABLE comments ADD COLUMN {name} {kind} DEFAULT {default}")
            rows = self.db.execute("SELECT id FROM comments WHERE source='' OR source IS NULL").fetchall()
            self.db.executemany("UPDATE comments SET source=? WHERE id=?",
                                [(source_of(row["id"]), row["id"]) for row in rows])
            self.db.execute("UPDATE comments SET status='legacy_processed' WHERE status='processed'")
            self.db.execute("""CREATE TABLE IF NOT EXISTS replies (
                id TEXT PRIMARY KEY, action TEXT NOT NULL, reason_code TEXT NOT NULL,
                utterances_json TEXT NOT NULL, source_ids_json TEXT NOT NULL,
                prompt_version TEXT NOT NULL, model_id TEXT NOT NULL,
                status TEXT NOT NULL, created_at REAL NOT NULL)""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS reply_comments (
                reply_id TEXT NOT NULL, comment_id TEXT NOT NULL,
                relation TEXT NOT NULL, PRIMARY KEY(reply_id, comment_id))""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS speech_jobs (
                id TEXT PRIMARY KEY, reply_id TEXT NOT NULL, chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL, is_background INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                next_retry_at REAL NOT NULL DEFAULT 0, lease_until REAL NOT NULL DEFAULT 0,
                created_at REAL NOT NULL)""")
            self.db.execute("CREATE INDEX IF NOT EXISTS comments_waiting ON comments(status, next_retry_at, added_at)")
            self.db.execute("CREATE INDEX IF NOT EXISTS jobs_waiting ON speech_jobs(status, next_retry_at, created_at)")

    def _recover(self) -> None:
        with self.lock, self.db:
            self.db.execute("UPDATE comments SET status='deferred', lease_until=0 WHERE status='claimed'")
            self.db.execute("UPDATE speech_jobs SET status='pending', lease_until=0 WHERE status='leased'")
            uncertain = [row["reply_id"] for row in self.db.execute(
                "SELECT DISTINCT reply_id FROM speech_jobs WHERE status='feeding'")]
            self.db.execute("UPDATE speech_jobs SET status='uncertain' WHERE status='feeding'")
            for reply_id in uncertain:
                self.db.execute("UPDATE replies SET status='uncertain' WHERE id=?", (reply_id,))
                self.db.execute("UPDATE comments SET status='uncertain' WHERE reply_id=?", (reply_id,))

    def record(self, comments: Iterable) -> int:
        values = [(str(c.id), str(c.author), str(c.message)[:1000], str(c.created_time),
                   source_of(str(c.id)), time.time()) for c in comments]
        with self.lock, self.db:
            before = self.db.total_changes
            self.db.executemany("""INSERT OR IGNORE INTO comments
                (id, author, message, created_time, source, added_at)
                VALUES (?, ?, ?, ?, ?, ?)""", values)
            return self.db.total_changes - before

    def claim_window(self, limit: int, now: float | None = None) -> list[StoredComment]:
        now = time.time() if now is None else now
        with self.lock, self.db:
            self.db.execute("""UPDATE comments SET status='deferred', lease_until=0,
                reason_code='claim_timeout' WHERE status='claimed' AND lease_until < ?""", (now,))
            self.db.execute("""UPDATE comments SET status='expired', reason_code='wait_timeout'
                WHERE status IN ('pending','deferred') AND added_at < ?""", (now - self.comment_ttl,))
            rows = self.db.execute("""SELECT id, author, message, created_time, source FROM comments
                WHERE status IN ('pending','deferred') AND next_retry_at <= ?
                AND id NOT LIKE 'sales-demo-%'
                ORDER BY CASE WHEN source='dashboard' THEN 0 ELSE 1 END, added_at LIMIT ?""",
                (now, limit)).fetchall()
            self.db.executemany("UPDATE comments SET status='claimed', lease_until=? WHERE id=?",
                                [(now + 300, row["id"]) for row in rows])
        return [StoredComment(**dict(row)) for row in rows]

    def release_window(self, ids: Iterable[str], *, reason: str = "temporary_error") -> None:
        now = time.time()
        with self.lock, self.db:
            for identifier in ids:
                row = self.db.execute("SELECT attempts FROM comments WHERE id=? AND status='claimed'",
                                      (identifier,)).fetchone()
                if row is None:
                    continue
                attempts = row["attempts"] + 1
                self.db.execute("""UPDATE comments SET status=?, reason_code=?, attempts=?,
                    next_retry_at=?, lease_until=0 WHERE id=?""",
                    ("failed" if attempts >= 5 else "deferred", reason, attempts,
                     now + min(60, 2 ** attempts), identifier))

    def commit(self, decision: ReplyDecision, window: list[StoredComment]) -> str | None:
        available = {item.id for item in window}
        dispositions = [decision.selected_ids, decision.related_ids, decision.deferred_ids, decision.ignored_ids]
        all_ids = [item for group in dispositions for item in group]
        if len(all_ids) != len(set(all_ids)) or set(all_ids) != available:
            raise ValueError("Decision must account for each claimed comment exactly once")
        if decision.action == "speak" and not decision.utterances:
            raise ValueError("Speech decision must contain utterances")
        if decision.action != "speak" and decision.utterances:
            raise ValueError("Non-speech decision cannot contain utterances")
        if len(decision.utterances) > 2:
            raise ValueError("At most two utterances per reply")
        reply_id = uuid.uuid4().hex if decision.utterances else None
        now = time.time()
        with self.lock, self.db:
            for identifier in available:
                state = self.db.execute("SELECT status FROM comments WHERE id=?", (identifier,)).fetchone()
                if state is None or state["status"] != "claimed":
                    raise RuntimeError("Comment claim was lost")
            if reply_id:
                self.db.execute("""INSERT INTO replies VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                (reply_id, decision.action, decision.reason_code,
                                 json.dumps(decision.utterances, ensure_ascii=False),
                                 json.dumps(decision.source_ids, ensure_ascii=False),
                                 decision.prompt_version, decision.model_id, "queued", now))
                for index, utterance in enumerate(decision.utterances):
                    self.db.execute("""INSERT INTO speech_jobs
                        (id, reply_id, chunk_index, text, is_background, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)""",
                        (f"{reply_id}-{index:02d}", reply_id, index, utterance, int(not available), now))
            for relation, ids, status in (
                ("selected", decision.selected_ids, "queued" if reply_id else "withheld"),
                ("related", decision.related_ids, "queued" if reply_id else "withheld"),
                ("deferred", decision.deferred_ids, "deferred"),
                ("ignored", decision.ignored_ids, "ignored"),
            ):
                for identifier in ids:
                    self.db.execute("""UPDATE comments SET status=?, reason_code=?, reply_id=?,
                        lease_until=0 WHERE id=?""",
                        (status, decision.reason_code, reply_id if relation in {"selected", "related"} else None,
                         identifier))
                    if reply_id and relation in {"selected", "related"}:
                        self.db.execute("INSERT INTO reply_comments VALUES (?, ?, ?)",
                                        (reply_id, identifier, relation))
        return reply_id

    def claim_speech_job(self, now: float | None = None) -> SpeechJob | None:
        now = time.time() if now is None else now
        with self.lock, self.db:
            row = self.db.execute("""SELECT * FROM speech_jobs AS job WHERE job.status='pending'
                AND job.next_retry_at <= ? AND NOT EXISTS (
                    SELECT 1 FROM speech_jobs AS earlier WHERE earlier.reply_id=job.reply_id
                    AND earlier.chunk_index < job.chunk_index AND earlier.status!='fed_to_encoder')
                ORDER BY job.is_background, job.created_at, job.chunk_index LIMIT 1""", (now,)).fetchone()
            if row is None:
                return None
            self.db.execute("UPDATE speech_jobs SET status='leased', lease_until=? WHERE id=?",
                            (now + 120, row["id"]))
        return SpeechJob(row["id"], row["reply_id"], row["chunk_index"], row["text"], bool(row["is_background"]))

    def mark_feeding(self, job_id: str) -> None:
        with self.lock, self.db:
            self.db.execute("UPDATE speech_jobs SET status='feeding', lease_until=0 WHERE id=?", (job_id,))

    def record_media_event(self, job_id: str, success: bool | None) -> None:
        with self.lock, self.db:
            row = self.db.execute("SELECT reply_id, attempts, status FROM speech_jobs WHERE id=?", (job_id,)).fetchone()
            if row is None or row["status"] in {"fed_to_encoder", "cancelled", "uncertain"}:
                return
            if success is None:
                self.db.execute("UPDATE speech_jobs SET status='cancelled' WHERE id=?", (job_id,))
                self.db.execute("UPDATE replies SET status='cancelled' WHERE id=?", (row["reply_id"],))
                return
            if success:
                self.db.execute("UPDATE speech_jobs SET status='fed_to_encoder' WHERE id=?", (job_id,))
            elif row["status"] == "feeding":
                # A feeder failure may already have sent some speech. Replaying it
                # automatically could repeat a private or sensitive answer on air.
                self.db.execute("UPDATE speech_jobs SET status='uncertain' WHERE id=?", (job_id,))
                self.db.execute("UPDATE replies SET status='uncertain' WHERE id=?", (row["reply_id"],))
                self.db.execute("UPDATE comments SET status='uncertain' WHERE reply_id=?", (row["reply_id"],))
                self.db.execute("""UPDATE speech_jobs SET status='cancelled' WHERE reply_id=?
                    AND status='pending'""", (row["reply_id"],))
                return
            else:
                attempts = row["attempts"] + 1
                self.db.execute("""UPDATE speech_jobs SET status=?, attempts=?, next_retry_at=?
                    WHERE id=?""", ("failed" if attempts >= 3 else "pending", attempts,
                                      time.time() + min(60, 2 ** attempts), job_id))
            remaining = self.db.execute("""SELECT COUNT(*) FROM speech_jobs WHERE reply_id=?
                AND status!='fed_to_encoder'""", (row["reply_id"],)).fetchone()[0]
            if not remaining:
                self.db.execute("UPDATE replies SET status='fed_to_encoder' WHERE id=?", (row["reply_id"],))
                self.db.execute("UPDATE comments SET status='fed_to_encoder' WHERE reply_id=?", (row["reply_id"],))
            elif not success and attempts >= 3:
                partial = self.db.execute("""SELECT COUNT(*) FROM speech_jobs WHERE reply_id=?
                    AND status='fed_to_encoder'""", (row["reply_id"],)).fetchone()[0]
                terminal = "uncertain" if partial else "failed"
                self.db.execute("UPDATE replies SET status=? WHERE id=?", (terminal, row["reply_id"]))
                self.db.execute("UPDATE comments SET status=? WHERE reply_id=?", (terminal, row["reply_id"]))
                self.db.execute("""UPDATE speech_jobs SET status='cancelled' WHERE reply_id=?
                    AND status='pending'""", (row["reply_id"],))

    def cancel_pending_background(self) -> list[str]:
        with self.lock, self.db:
            rows = self.db.execute("""SELECT reply_id FROM speech_jobs
                WHERE is_background=1 AND status IN ('pending','leased')
                ORDER BY created_at, chunk_index""").fetchall()
            reply_ids = list(dict.fromkeys(row["reply_id"] for row in rows))
            self.db.execute("""UPDATE speech_jobs SET status='cancelled'
                WHERE is_background=1 AND status IN ('pending','leased')""")
            self.db.executemany("UPDATE replies SET status='cancelled' WHERE id=?",
                                [(reply_id,) for reply_id in reply_ids])
            return reply_ids

    def job_status(self, job_id: str) -> str | None:
        with self.lock:
            row = self.db.execute("SELECT status FROM speech_jobs WHERE id=?", (job_id,)).fetchone()
            return row["status"] if row else None

    def background_backlog(self) -> int:
        with self.lock:
            return self.db.execute("""SELECT COUNT(*) FROM speech_jobs WHERE is_background=1
                AND status IN ('pending','leased','feeding')""").fetchone()[0]

    def viewer_jobs_waiting(self) -> bool:
        with self.lock:
            return bool(self.db.execute("""SELECT 1 FROM speech_jobs WHERE is_background=0
                AND status IN ('pending','leased') LIMIT 1""").fetchone())

    def recent_replies(self, limit: int = 8) -> list[str]:
        with self.lock:
            rows = self.db.execute("""SELECT utterances_json FROM replies
                WHERE status IN ('queued','fed_to_encoder') ORDER BY created_at DESC LIMIT ?""", (limit,)).fetchall()
        return [" ".join(json.loads(row[0])) for row in reversed(rows)]

    def close(self) -> None:
        with self.lock:
            self.db.close()
