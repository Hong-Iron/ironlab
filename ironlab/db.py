"""SQLite 저장소. 사용자·작업·감사 로그를 보관한다."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    name            TEXT PRIMARY KEY,
    role            TEXT NOT NULL DEFAULT 'user',      -- user | admin
    uid             INTEGER NOT NULL UNIQUE,
    token_hash      TEXT,
    tailscale_login TEXT UNIQUE,
    cpus            REAL NOT NULL,
    mem_gb          INTEGER NOT NULL,
    disk_gb         INTEGER NOT NULL,
    max_vram_gb     REAL,                              -- NULL이면 GPU 전체까지 요청 가능
    ssh_pubkeys     TEXT NOT NULL DEFAULT '',
    disabled        INTEGER NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL,
    last_seen       REAL
);

CREATE TABLE IF NOT EXISTS jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user          TEXT NOT NULL REFERENCES users(name),
    name          TEXT NOT NULL,
    command       TEXT NOT NULL,
    workdir       TEXT NOT NULL DEFAULT '',
    vram_gb       REAL NOT NULL,
    cpus          REAL NOT NULL,
    mem_gb        INTEGER NOT NULL,
    exclusive     INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'queued',      -- queued|running|succeeded|failed|cancelled
    container_id  TEXT,
    exit_code     INTEGER,
    error         TEXT,
    peak_vram_gb  REAL NOT NULL DEFAULT 0,
    created_at    REAL NOT NULL,
    started_at    REAL,
    finished_at   REAL
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS llm_models (
    name        TEXT PRIMARY KEY,                    -- 표시 이름 (예: qwen3.8-27b)
    file        TEXT NOT NULL,                       -- models/llm 기준 GGUF 경로
    mmproj      TEXT NOT NULL DEFAULT '',            -- 이미지 입력용 projector (선택)
    ctx         INTEGER NOT NULL DEFAULT 32768,      -- 전체 컨텍스트 (동시 요청 수로 나눠 씀)
    parallel    INTEGER NOT NULL DEFAULT 2,          -- 동시에 처리할 요청 수
    vram_gb     REAL NOT NULL,                       -- 올렸을 때 차지하는 VRAM (예약량)
    extra_args  TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    measured_vram_gb REAL                            -- 올렸을 때 실제로 쓴 VRAM 의 최댓값 (잰 적 없으면 NULL)
);

CREATE TABLE IF NOT EXISTS llm_usage (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                REAL NOT NULL,
    user              TEXT NOT NULL,
    model             TEXT NOT NULL,
    source            TEXT NOT NULL,                 -- console | api | workspace
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    ms                INTEGER NOT NULL DEFAULT 0,
    ok                INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS llm_usage_ts ON llm_usage(ts);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    user    TEXT,
    kind    TEXT NOT NULL,
    detail  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS gens (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,                       -- 3d | image
    user        TEXT NOT NULL REFERENCES users(name),
    job_id      INTEGER REFERENCES jobs(id),         -- 이것을 만드는 GPU 작업
    title       TEXT NOT NULL,                       -- 목록에 보이는 이름 (올린 파일 이름이나 글의 앞부분)
    prompt      TEXT NOT NULL DEFAULT '',            -- 적은 글 가운데 첫째 (목록 제목에 쓴다)
    texts       TEXT NOT NULL DEFAULT '{}',          -- 적은 글 전부 (JSON: 글 칸 이름 → 글)
    params      TEXT NOT NULL DEFAULT '{}',          -- 설정 (JSON: 시드, 크기 ...)
    inputs      TEXT NOT NULL DEFAULT '[]',          -- 홈 기준 입력 파일 경로들 (JSON 배열)
    output      TEXT NOT NULL,                       -- 홈 기준 결과 파일 경로
    thumb       TEXT,                                -- 홈 기준 작은 미리보기 사진 경로 (없으면 NULL)
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS gens_user_kind ON gens(user, kind, id);

CREATE TABLE IF NOT EXISTS gen_presets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,
    user        TEXT NOT NULL REFERENCES users(name),
    name        TEXT NOT NULL,                       -- 사용자가 붙인 이름
    params      TEXT NOT NULL,                       -- 설정 (JSON)
    created_at  REAL NOT NULL,
    UNIQUE(kind, user, name)
);

CREATE TABLE IF NOT EXISTS flows (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user        TEXT NOT NULL REFERENCES users(name),
    name        TEXT NOT NULL,                       -- 사용자가 붙인 이름
    graph       TEXT NOT NULL,                       -- 노드와 연결 (JSON)
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    UNIQUE(user, name)
);

CREATE TABLE IF NOT EXISTS flow_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user        TEXT NOT NULL REFERENCES users(name),
    name        TEXT NOT NULL DEFAULT '',            -- 실행할 때의 그래프 이름 (주인에게만 보인다)
    dir         TEXT NOT NULL,                       -- 홈 기준 실행 폴더. 노드의 결과 파일이 여기에 생긴다
    graph       TEXT NOT NULL,                       -- 실행한 그래프 그대로 (JSON)
    status      TEXT NOT NULL DEFAULT 'running',     -- running | succeeded | failed | cancelled
    created_at  REAL NOT NULL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS flow_runs_user ON flow_runs(user, id);

CREATE TABLE IF NOT EXISTS flow_nodes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES flow_runs(id),
    user        TEXT NOT NULL,
    node        TEXT NOT NULL,                       -- 그래프 안의 노드 번호 (n1, n2 ...)
    type        TEXT NOT NULL,                       -- 노드 종류 (text, picture, image, mesh, score, song)
    sig         TEXT NOT NULL,                       -- 종류·설정·입력의 지문. 같으면 지난 결과를 다시 쓴다
    status      TEXT NOT NULL DEFAULT 'waiting',     -- waiting | queued | running | succeeded | reused | failed | cancelled | skipped
    job_id      INTEGER REFERENCES jobs(id),         -- 이 노드를 만드는 GPU 작업
    outputs     TEXT NOT NULL DEFAULT '{}',          -- 결과 파일 (JSON: 출력 이름 → 홈 기준 경로)
    error       TEXT,
    reused_from INTEGER                              -- 결과를 다시 쓴 경우, 그 결과를 만든 실행 번호
);
CREATE INDEX IF NOT EXISTS flow_nodes_run ON flow_nodes(run_id);
CREATE INDEX IF NOT EXISTS flow_nodes_sig ON flow_nodes(user, sig);
"""


# 예전 버전이 만든 DB 에는 없는 열. 서버를 띄울 때 없으면 더한다 (표, 열, 형식)
ADDED_COLUMNS = [("llm_models", "measured_vram_gb", "REAL"),
                 ("gens", "texts", "TEXT NOT NULL DEFAULT '{}'")]


class DB:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        for table, column, decl in ADDED_COLUMNS:
            have = {row["name"] for row in self._conn.execute(f"PRAGMA table_info({table})")}
            if column not in have:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        self._move_gen3d_rows()

    def _move_gen3d_rows(self) -> None:
        """예전 판이 만든 gen3d 표(3D 생성 전용)의 줄을 종류 공용 표 gens 로 옮기고 gen3d 표를 없앤다."""
        if not self._conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='gen3d'").fetchone():
            return
        rows = self._conn.execute("SELECT * FROM gen3d ORDER BY id").fetchall()
        self._conn.execute("BEGIN")
        try:
            for r in rows:
                self._conn.execute(
                    """INSERT INTO gens(kind, user, job_id, title, prompt, params, inputs, output, thumb, created_at)
                       VALUES ('3d',?,?,?,'',?,?,?,?,?)""",
                    (r["user"], r["job_id"], r["title"], json.dumps({"seed": r["seed"]}),
                     json.dumps([r["input"]]), r["output"], r["thumb"], r["created_at"]))
            self._conn.execute("DROP TABLE gen3d")
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def one(self, sql: str, params: tuple | dict = ()) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def all(self, sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ── 감사 로그 ──
    def log_event(self, kind: str, user: str | None = None, detail: str = "") -> None:
        self.execute("INSERT INTO events(ts, user, kind, detail) VALUES (?,?,?,?)",
                     (time.time(), user, kind, detail))

    def recent_events(self, limit: int = 100) -> list[dict]:
        return self.all("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))

    # ── 사용자 ──
    def get_user(self, name: str) -> dict | None:
        return self.one("SELECT * FROM users WHERE name=?", (name,))

    def list_users(self) -> list[dict]:
        return self.all("SELECT * FROM users ORDER BY name")

    def next_uid(self, base: int) -> int:
        row = self.one("SELECT MAX(uid) AS m FROM users")
        return max(base + 1, (row["m"] or base) + 1)

    def close(self) -> None:
        with self._lock:
            self._conn.close()
