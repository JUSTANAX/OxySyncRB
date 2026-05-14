import sqlite3
from datetime import datetime, timedelta
from config import DB_PATH


def get_conn():
    return sqlite3.connect(DB_PATH, isolation_level=None)


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS panels (
                user_id      INTEGER PRIMARY KEY,
                api_key      TEXT NOT NULL,
                connected_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS pet_snapshots (
                user_id     INTEGER,
                pet_kind    TEXT,
                quantity    INTEGER,
                recorded_at TEXT,
                PRIMARY KEY (user_id, pet_kind, recorded_at)
            );

            CREATE TABLE IF NOT EXISTS alert_thresholds (
                user_id       INTEGER PRIMARY KEY,
                threshold     INTEGER,
                enabled       INTEGER DEFAULT 1,
                last_notified TEXT
            );

            CREATE TABLE IF NOT EXISTS zp_keys (
                user_id  INTEGER PRIMARY KEY,
                api_key  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS zp_jobs (
                user_id  INTEGER PRIMARY KEY,
                job_id   TEXT NOT NULL,
                notified INTEGER DEFAULT 0,
                added_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS auto_unlock (
                user_id INTEGER PRIMARY KEY,
                enabled INTEGER DEFAULT 0
            );
        """)
        try:
            conn.execute("ALTER TABLE zp_jobs ADD COLUMN notified INTEGER DEFAULT 0")
        except Exception:
            pass


def get_panel(user_id: int) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT api_key FROM panels WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row[0] if row else None


def save_panel(user_id: int, api_key: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO panels (user_id, api_key) VALUES (?, ?)",
            (user_id, api_key),
        )


def get_alert(user_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT threshold, enabled, last_notified FROM alert_thresholds WHERE user_id = ?",
            (user_id,),
        ).fetchone()


def set_alert(user_id: int, threshold: int):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO alert_thresholds (user_id, threshold, enabled) VALUES (?, ?, 1) "
            "ON CONFLICT(user_id) DO UPDATE SET threshold = excluded.threshold, enabled = 1",
            (user_id, threshold),
        )


def toggle_alert(user_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT enabled FROM alert_thresholds WHERE user_id = ?", (user_id,)
        ).fetchone()
        new_val = 0 if (row and row[0]) else 1
        conn.execute(
            "UPDATE alert_thresholds SET enabled = ? WHERE user_id = ?",
            (new_val, user_id),
        )
        return bool(new_val)


def get_users_with_alerts() -> list:
    with get_conn() as conn:
        return conn.execute(
            "SELECT a.user_id, p.api_key, a.threshold, a.last_notified "
            "FROM alert_thresholds a JOIN panels p ON a.user_id = p.user_id "
            "WHERE a.enabled = 1 AND a.threshold IS NOT NULL"
        ).fetchall()


def update_alert_notified(user_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE alert_thresholds SET last_notified = ? WHERE user_id = ?",
            (datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), user_id),
        )


def save_pet_snapshot(user_id: int, pets: dict):
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:00")
    cutoff = (datetime.utcnow() - timedelta(days=8)).strftime("%Y-%m-%d %H:%M:00")
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM pet_snapshots WHERE user_id = ? AND recorded_at < ?",
            (user_id, cutoff),
        )
        for kind, data in pets.items():
            conn.execute(
                "INSERT OR REPLACE INTO pet_snapshots (user_id, pet_kind, quantity, recorded_at) "
                "VALUES (?, ?, ?, ?)",
                (user_id, kind, data["quantity"], now),
            )


# ─── ZeroPoint ───────────────────────────────────────────────────────────────

def get_zp_key(user_id: int) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT api_key FROM zp_keys WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row[0] if row else None


def save_zp_key(user_id: int, api_key: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO zp_keys (user_id, api_key) VALUES (?, ?)",
            (user_id, api_key),
        )


def get_zp_job(user_id: int) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT job_id FROM zp_jobs WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row[0] if row else None


def save_zp_job(user_id: int, job_id: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO zp_jobs (user_id, job_id, notified) VALUES (?, ?, 0) "
            "ON CONFLICT(user_id) DO UPDATE SET job_id = excluded.job_id, notified = 0",
            (user_id, job_id),
        )


def is_zp_job_notified(user_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT notified FROM zp_jobs WHERE user_id = ?", (user_id,)
        ).fetchone()
        return bool(row and row[0])


def set_zp_job_notified(user_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE zp_jobs SET notified = 1 WHERE user_id = ?", (user_id,)
        )


def get_auto_unlock(user_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT enabled FROM auto_unlock WHERE user_id = ?", (user_id,)
        ).fetchone()
        return bool(row and row[0])


def set_auto_unlock(user_id: int, enabled: bool):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO auto_unlock (user_id, enabled) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET enabled = excluded.enabled",
            (user_id, int(enabled)),
        )


def get_users_with_auto_unlock() -> list[tuple[int, str, str | None]]:
    """Returns [(user_id, ao_key, zp_key)] for users with auto-unlock enabled."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT a.user_id, p.api_key, z.api_key "
            "FROM auto_unlock a "
            "JOIN panels p ON a.user_id = p.user_id "
            "LEFT JOIN zp_keys z ON a.user_id = z.user_id "
            "WHERE a.enabled = 1"
        ).fetchall()


def clear_zp_job(user_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM zp_jobs WHERE user_id = ?", (user_id,))


# ─── Снапшоты петов ───────────────────────────────────────────────────────────

def get_pets_farmed(user_id: int, current_pets: dict, hours: float) -> dict | None:
    """Returns {pet_kind: farmed_count} for the given period. None if no data."""
    target = (datetime.utcnow() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:00")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT recorded_at FROM pet_snapshots "
            "WHERE user_id = ? AND recorded_at <= ? ORDER BY recorded_at DESC LIMIT 1",
            (user_id, target),
        ).fetchone()
        if not row:
            return None
        rows = conn.execute(
            "SELECT pet_kind, quantity FROM pet_snapshots WHERE user_id = ? AND recorded_at = ?",
            (user_id, row[0]),
        ).fetchall()
    past = {kind: qty for kind, qty in rows}
    return {kind: max(0, data["quantity"] - past.get(kind, 0)) for kind, data in current_pets.items()}
