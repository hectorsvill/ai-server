"""
Async SQLite database (WAL mode) for metrics, events, decisions, and actions.

Schema:
  metrics   — time-series snapshots (system, docker, security)
  events    — anomaly/alert records
  decisions — AI reasoning outputs
  actions   — every executed (or dry-run) action with outcome
  approvals — pending human approvals linked to actions
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import aiosqlite

from guardian.core.config import cfg
from guardian.core.logger import get_logger

log = get_logger(__name__)

DB_PATH = Path(cfg.service.data_dir) / "guardian.db"

_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS metrics (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    REAL    NOT NULL,
    metric_type  TEXT    NOT NULL,
    data         TEXT    NOT NULL   -- JSON
);
CREATE INDEX IF NOT EXISTS idx_metrics_ts ON metrics(timestamp);
CREATE INDEX IF NOT EXISTS idx_metrics_type ON metrics(metric_type, timestamp);

CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    REAL    NOT NULL,
    severity     TEXT    NOT NULL,  -- info | warning | critical
    category     TEXT    NOT NULL,  -- system | docker | security | network
    title        TEXT    NOT NULL,
    description  TEXT    NOT NULL,
    resolved     INTEGER DEFAULT 0,
    resolved_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_sev ON events(severity, resolved);

CREATE TABLE IF NOT EXISTS decisions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    REAL    NOT NULL,
    context      TEXT    NOT NULL,  -- JSON metrics snapshot
    reasoning    TEXT    NOT NULL,  -- LLM raw output
    summary      TEXT    NOT NULL,
    confidence   REAL    NOT NULL,
    actions      TEXT    NOT NULL,  -- JSON array of proposed actions
    model        TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS actions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       REAL    NOT NULL,
    decision_id     INTEGER REFERENCES decisions(id),
    action_type     TEXT    NOT NULL,
    parameters      TEXT    NOT NULL,  -- JSON
    risk_level      TEXT    NOT NULL,  -- low | medium | high | critical
    status          TEXT    NOT NULL,  -- pending|approved|denied|executing|completed|failed|dry_run
    dry_run         INTEGER DEFAULT 0,
    result          TEXT,              -- JSON outcome
    approval_token  TEXT    UNIQUE,
    approved_by     TEXT,
    executed_at     REAL,
    completed_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_actions_ts ON actions(timestamp);
CREATE INDEX IF NOT EXISTS idx_actions_status ON actions(status);
CREATE INDEX IF NOT EXISTS idx_actions_token ON actions(approval_token);
"""


async def init_db() -> None:
    """Create schema if not present, enable WAL."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_DDL)
        await db.commit()
    log.info("database_ready", path=str(DB_PATH))


# ── Write helpers ─────────────────────────────────────────────────────────────

async def insert_metric(metric_type: str, data: dict[str, Any]) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO metrics(timestamp, metric_type, data) VALUES(?,?,?)",
            (time.time(), metric_type, json.dumps(data)),
        )
        await db.commit()
        return cur.lastrowid  # type: ignore[return-value]


async def insert_event(
    severity: str,
    category: str,
    title: str,
    description: str,
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO events(timestamp,severity,category,title,description) VALUES(?,?,?,?,?)",
            (time.time(), severity, category, title, description),
        )
        await db.commit()
        log.info("event_recorded", severity=severity, category=category, title=title)
        return cur.lastrowid  # type: ignore[return-value]


async def resolve_event(event_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE events SET resolved=1, resolved_at=? WHERE id=?",
            (time.time(), event_id),
        )
        await db.commit()


async def insert_decision(
    context: dict,
    reasoning: str,
    summary: str,
    confidence: float,
    actions: list[dict],
    model: str,
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO decisions(timestamp,context,reasoning,summary,confidence,actions,model)
               VALUES(?,?,?,?,?,?,?)""",
            (time.time(), json.dumps(context), reasoning, summary,
             confidence, json.dumps(actions), model),
        )
        await db.commit()
        return cur.lastrowid  # type: ignore[return-value]


async def insert_action(
    decision_id: int | None,
    action_type: str,
    parameters: dict,
    risk_level: str,
    status: str,
    dry_run: bool = False,
    approval_token: str | None = None,
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO actions(timestamp,decision_id,action_type,parameters,
                                   risk_level,status,dry_run,approval_token)
               VALUES(?,?,?,?,?,?,?,?)""",
            (time.time(), decision_id, action_type, json.dumps(parameters),
             risk_level, status, int(dry_run), approval_token),
        )
        await db.commit()
        return cur.lastrowid  # type: ignore[return-value]


async def update_action(
    action_id: int,
    status: str,
    result: dict | None = None,
    approved_by: str | None = None,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        now = time.time()
        await db.execute(
            """UPDATE actions SET status=?, result=?, approved_by=?, completed_at=?
               WHERE id=?""",
            (status, json.dumps(result) if result else None, approved_by, now, action_id),
        )
        await db.commit()


# ── Read helpers ──────────────────────────────────────────────────────────────

async def get_latest_metrics(metric_type: str, limit: int = 1) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM metrics WHERE metric_type=? ORDER BY timestamp DESC LIMIT ?",
            (metric_type, limit),
        )
        rows = await cur.fetchall()
        return [{"id": r["id"], "timestamp": r["timestamp"],
                 "data": json.loads(r["data"])} for r in rows]


async def get_recent_events(
    limit: int = 50,
    severity: str | None = None,
    unresolved_only: bool = False,
) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        q = "SELECT * FROM events WHERE 1=1"
        params: list = []
        if severity:
            q += " AND severity=?"
            params.append(severity)
        if unresolved_only:
            q += " AND resolved=0"
        q += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        cur = await db.execute(q, params)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def get_pending_approvals() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM actions WHERE status='pending' ORDER BY timestamp ASC"
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def get_action_by_token(token: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM actions WHERE approval_token=?", (token,)
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_recent_decisions(limit: int = 10) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id,timestamp,summary,confidence,model FROM decisions ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def get_recent_actions(limit: int = 50) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM actions ORDER BY timestamp DESC LIMIT ?", (limit,)
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def purge_old_metrics(retention_days: int) -> int:
    """Delete metrics older than retention_days. Returns rows deleted."""
    cutoff = time.time() - retention_days * 86400
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM metrics WHERE timestamp < ?", (cutoff,))
        await db.commit()
        return cur.rowcount  # type: ignore[return-value]
