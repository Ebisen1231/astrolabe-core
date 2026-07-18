"""SQLite接続とスキーマ(設計書§4)。ORMは使わない。"""

from __future__ import annotations

import sqlite3
from pathlib import Path


class LedgerError(Exception):
    """台帳の状態異常(未初期化・パス不正など)。"""


SCHEMA = """
-- 一次データ: 起きたことの記録(追記のみ)。他テーブルはここから再導出できる状態を保つ。
CREATE TABLE IF NOT EXISTS events(
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  ts         TEXT NOT NULL,
  type       TEXT NOT NULL,
  concept_id TEXT,
  payload    TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);

-- 導出: 概念(マップのノード)。derive.rebuild() 以外から書かない。
CREATE TABLE IF NOT EXISTS concepts(
  id           TEXT PRIMARY KEY,
  name         TEXT NOT NULL,
  kind         TEXT NOT NULL DEFAULT 'concept',
  status       TEXT NOT NULL DEFAULT 'unknown',
  confidence   REAL NOT NULL DEFAULT 0.0,
  summary      TEXT NOT NULL DEFAULT '',
  source_urls  TEXT NOT NULL DEFAULT '[]',
  first_seen   TEXT,
  last_touched TEXT
);

-- 導出: つながり(マップのエッジ)。derive.rebuild() 以外から書かない。
CREATE TABLE IF NOT EXISTS edges(
  src        TEXT NOT NULL,
  dst        TEXT NOT NULL,
  type       TEXT NOT NULL,
  weight     REAL NOT NULL DEFAULT 1.0,
  created_by TEXT,
  created_at TEXT,
  PRIMARY KEY (src, dst, type)
);

-- タスク: M0ではスキーマのみ。書き込みは常駐チューター(M3)から。
CREATE TABLE IF NOT EXISTS tasks(
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  concept_id  TEXT,
  title       TEXT NOT NULL,
  kind        TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'open',
  est_minutes INTEGER,
  evidence    TEXT,
  created_at  TEXT,
  done_at     TEXT
);

-- 学習者プロファイル(単一レコード)。更新は対応イベントと同一トランザクションで行う。
CREATE TABLE IF NOT EXISTS profile(
  id          INTEGER PRIMARY KEY CHECK (id = 1),
  interests   TEXT NOT NULL DEFAULT '{}',
  goals       TEXT NOT NULL DEFAULT '',
  background  TEXT NOT NULL DEFAULT '',
  time_budget TEXT NOT NULL DEFAULT ''
);

-- 朝の報告のアーカイブ
CREATE TABLE IF NOT EXISTS daily_reports(
  date           TEXT PRIMARY KEY,
  items          TEXT NOT NULL,
  map_delta_text TEXT NOT NULL DEFAULT '',
  html_path      TEXT
);

-- 日次の実測トークン使用量。run_id単位で冪等に記録し、日次集計できる。
CREATE TABLE IF NOT EXISTS llm_usage(
  usage_date TEXT NOT NULL,
  run_id     TEXT NOT NULL,
  model_role TEXT NOT NULL,
  tokens     INTEGER NOT NULL CHECK(tokens >= 0),
  PRIMARY KEY(usage_date, run_id, model_role)
);
"""


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(path: Path | str) -> sqlite3.Connection:
    """スキーマを作成して接続を返す。既存DBに対しては何も壊さない(IF NOT EXISTS)。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(p)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def ensure_initialized(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='events'"
    ).fetchone()
    if row is None:
        raise LedgerError("台帳が初期化されていない。先に `astrolabe init` を実行する")


def open_ledger(path: Path | str) -> sqlite3.Connection:
    """既存の台帳を開く。存在しなければ作らずにエラーにする(暗黙生成はしない)。"""
    p = Path(path)
    if not p.exists():
        raise LedgerError(f"台帳ファイルが見つからない: {p} — 先に `astrolabe init` を実行する")
    conn = connect(p)
    ensure_initialized(conn)
    return conn
