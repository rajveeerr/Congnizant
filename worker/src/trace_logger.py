"""SQLite-backed trace logger.

Writes one row per agent step. Phase 9 will sync the file to S3 after
each job completes; for now traces live at /tmp/agent_traces.db inside
the worker container and are wiped on container restart.
"""

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any


_SCHEMA = """
CREATE TABLE IF NOT EXISTS traces (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id       TEXT NOT NULL,
    agent_name   TEXT NOT NULL,
    step         TEXT NOT NULL,
    input        TEXT,
    output       TEXT,
    duration_ms  REAL,
    timestamp    TEXT NOT NULL,
    status       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_traces_job ON traces(job_id);
"""


class TraceLogger:
    def __init__(self, db_path: str = "/tmp/agent_traces.db") -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def log(
        self,
        job_id: str,
        agent_name: str,
        step: str,
        input_data: Any,
        output_data: Any,
        duration_ms: float,
        status: str = "ok",
    ) -> None:
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute(
                    "INSERT INTO traces "
                    "(job_id, agent_name, step, input, output, duration_ms, timestamp, status) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        job_id,
                        agent_name,
                        step,
                        json.dumps(input_data, default=str),
                        json.dumps(output_data, default=str),
                        duration_ms,
                        datetime.now(timezone.utc).isoformat(),
                        status,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def get_traces(self, job_id: str) -> list[dict]:
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.execute(
                "SELECT id, job_id, agent_name, step, input, output, "
                "duration_ms, timestamp, status "
                "FROM traces WHERE job_id = ? ORDER BY id",
                (job_id,),
            )
            rows = cur.fetchall()
        finally:
            conn.close()

        return [
            {
                "id": r[0],
                "job_id": r[1],
                "agent_name": r[2],
                "step": r[3],
                "input": json.loads(r[4]) if r[4] else None,
                "output": json.loads(r[5]) if r[5] else None,
                "duration_ms": r[6],
                "timestamp": r[7],
                "status": r[8],
            }
            for r in rows
        ]
