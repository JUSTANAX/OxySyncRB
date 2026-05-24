import sqlite3
from datetime import datetime, timedelta
from config import DB_PATH

_conn: sqlite3.Connection | None = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
    return _conn


def init_db():
    conn = _get_conn()
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
            last_notified TEXT,
            triggered     INTEGER DEFAULT 0
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
            user_id        INTEGER PRIMARY KEY,
            enabled        INTEGER DEFAULT 0,
            interval_hours REAL DEFAULT 3.0,
            last_run_at    TEXT
        );

        CREATE TABLE IF NOT EXISTS watched_pets (
            user_id     INTEGER,
            filter_text TEXT,
            PRIMARY KEY (user_id, filter_text)
        );

        CREATE TABLE IF NOT EXISTS auto_enable_pet (
            user_id        INTEGER PRIMARY KEY,
            enabled        INTEGER DEFAULT 0,
            last_notified  TEXT
        );

        CREATE TABLE IF NOT EXISTS autopilot_config (
            user_id       INTEGER PRIMARY KEY,
            main_account  TEXT,
            pet_id        TEXT,
            config_id     INTEGER,
            running       INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS autopilot_pets (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            pet_id  TEXT NOT NULL,
            UNIQUE (user_id, pet_id)
        );

        CREATE TABLE IF NOT EXISTS autopilot_queue (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            account_id   TEXT NOT NULL,
            username     TEXT NOT NULL,
            status       TEXT DEFAULT 'pending',
            activated_at TEXT
        );
    """)
    # Migrations for existing databases
    for stmt in (
        "ALTER TABLE zp_jobs ADD COLUMN notified INTEGER DEFAULT 0",
        "ALTER TABLE alert_thresholds ADD COLUMN triggered INTEGER DEFAULT 0",
        "ALTER TABLE auto_unlock ADD COLUMN interval_hours REAL DEFAULT 3.0",
        "ALTER TABLE auto_unlock ADD COLUMN last_run_at TEXT",
        "ALTER TABLE autopilot_config ADD COLUMN config_id INTEGER",
        "ALTER TABLE autopilot_config ADD COLUMN started_at TEXT",
        "ALTER TABLE autopilot_config ADD COLUMN batch_size INTEGER DEFAULT 10",
        "ALTER TABLE autopilot_config ADD COLUMN check_interval INTEGER DEFAULT 30",
        "ALTER TABLE autopilot_config ADD COLUMN last_checked_at TEXT",
        "ALTER TABLE autopilot_config ADD COLUMN stuck_timeout INTEGER DEFAULT 10",
        "ALTER TABLE autopilot_queue ADD COLUMN activated_at TEXT",
    ):
        try:
            conn.execute(stmt)
        except Exception:
            pass

    # Migrate watched_pets from old schema (pet_kind + label) to new (filter_text)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(watched_pets)").fetchall()]
        if "label" in cols:
            conn.executescript("""
                DROP TABLE watched_pets;
                CREATE TABLE watched_pets (
                    user_id     INTEGER,
                    filter_text TEXT,
                    PRIMARY KEY (user_id, filter_text)
                );
            """)
    except Exception:
        pass


# ─── Panels ──────────────────────────────────────────────────────────────────

def get_panel(user_id: int) -> str | None:
    conn = _get_conn()
    row = conn.execute(
        "SELECT api_key FROM panels WHERE user_id = ?", (user_id,)
    ).fetchone()
    return row[0] if row else None


def save_panel(user_id: int, api_key: str):
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO panels (user_id, api_key) VALUES (?, ?)",
        (user_id, api_key),
    )


# ─── Alerts ──────────────────────────────────────────────────────────────────

def get_alert(user_id: int):
    conn = _get_conn()
    return conn.execute(
        "SELECT threshold, enabled, last_notified FROM alert_thresholds WHERE user_id = ?",
        (user_id,),
    ).fetchone()


def set_alert(user_id: int, threshold: int):
    conn = _get_conn()
    conn.execute(
        "INSERT INTO alert_thresholds (user_id, threshold, enabled) VALUES (?, ?, 1) "
        "ON CONFLICT(user_id) DO UPDATE SET threshold = excluded.threshold, enabled = 1",
        (user_id, threshold),
    )


def toggle_alert(user_id: int) -> bool:
    conn = _get_conn()
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
    conn = _get_conn()
    return conn.execute(
        "SELECT a.user_id, p.api_key, a.threshold, a.last_notified, COALESCE(a.triggered, 0) "
        "FROM alert_thresholds a JOIN panels p ON a.user_id = p.user_id "
        "WHERE a.enabled = 1 AND a.threshold IS NOT NULL"
    ).fetchall()


def update_alert_notified(user_id: int):
    conn = _get_conn()
    conn.execute(
        "UPDATE alert_thresholds SET last_notified = ? WHERE user_id = ?",
        (datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), user_id),
    )


def set_alert_triggered(user_id: int, triggered: bool):
    conn = _get_conn()
    conn.execute(
        "UPDATE alert_thresholds SET triggered = ? WHERE user_id = ?",
        (int(triggered), user_id),
    )


# ─── Pet snapshots ────────────────────────────────────────────────────────────

def save_pet_snapshot(user_id: int, pets: dict):
    conn = _get_conn()
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:00")
    cutoff = (datetime.utcnow() - timedelta(days=8)).strftime("%Y-%m-%d %H:%M:00")
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


def get_pets_farmed(user_id: int, current_pets: dict, hours: float) -> dict | None:
    conn = _get_conn()
    target = (datetime.utcnow() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:00")
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


# ─── ZeroPoint keys & jobs ───────────────────────────────────────────────────

def get_zp_key(user_id: int) -> str | None:
    conn = _get_conn()
    row = conn.execute(
        "SELECT api_key FROM zp_keys WHERE user_id = ?", (user_id,)
    ).fetchone()
    return row[0] if row else None


def save_zp_key(user_id: int, api_key: str):
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO zp_keys (user_id, api_key) VALUES (?, ?)",
        (user_id, api_key),
    )


def get_zp_job(user_id: int) -> str | None:
    conn = _get_conn()
    row = conn.execute(
        "SELECT job_id FROM zp_jobs WHERE user_id = ?", (user_id,)
    ).fetchone()
    return row[0] if row else None


def save_zp_job(user_id: int, job_id: str):
    conn = _get_conn()
    conn.execute(
        "INSERT INTO zp_jobs (user_id, job_id, notified) VALUES (?, ?, 0) "
        "ON CONFLICT(user_id) DO UPDATE SET job_id = excluded.job_id, notified = 0",
        (user_id, job_id),
    )


def clear_zp_job(user_id: int):
    conn = _get_conn()
    conn.execute("DELETE FROM zp_jobs WHERE user_id = ?", (user_id,))


def get_all_users_with_zp_jobs() -> list[tuple[int, str, str]]:
    """Returns [(user_id, zp_key, job_id)] for all users with an unnotified zp job."""
    conn = _get_conn()
    return conn.execute(
        "SELECT j.user_id, z.api_key, j.job_id "
        "FROM zp_jobs j "
        "JOIN zp_keys z ON j.user_id = z.user_id "
        "WHERE j.notified = 0"
    ).fetchall()


# ─── Auto-unlock ─────────────────────────────────────────────────────────────

def get_auto_unlock(user_id: int) -> bool:
    conn = _get_conn()
    row = conn.execute(
        "SELECT enabled FROM auto_unlock WHERE user_id = ?", (user_id,)
    ).fetchone()
    return bool(row and row[0])


def set_auto_unlock(user_id: int, enabled: bool):
    conn = _get_conn()
    conn.execute(
        "INSERT INTO auto_unlock (user_id, enabled) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET enabled = excluded.enabled",
        (user_id, int(enabled)),
    )


def get_users_due_for_auto_unlock() -> list[tuple[int, str, str | None]]:
    """Returns [(user_id, ao_key, zp_key)] for users whose unlock interval has elapsed."""
    conn = _get_conn()
    return conn.execute(
        "SELECT a.user_id, p.api_key, z.api_key "
        "FROM auto_unlock a "
        "JOIN panels p ON a.user_id = p.user_id "
        "LEFT JOIN zp_keys z ON a.user_id = z.user_id "
        "WHERE a.enabled = 1 "
        "AND (a.last_run_at IS NULL "
        "     OR datetime(a.last_run_at, "
        "                 '+' || CAST(CAST(COALESCE(a.interval_hours, 3.0) * 60 AS INTEGER) AS TEXT) || ' minutes') "
        "         <= datetime('now'))"
    ).fetchall()


def update_auto_unlock_last_run(user_id: int):
    conn = _get_conn()
    conn.execute(
        "UPDATE auto_unlock SET last_run_at = datetime('now') WHERE user_id = ?",
        (user_id,),
    )


def get_auto_unlock_interval(user_id: int) -> float:
    conn = _get_conn()
    row = conn.execute(
        "SELECT COALESCE(interval_hours, 3.0) FROM auto_unlock WHERE user_id = ?", (user_id,)
    ).fetchone()
    return row[0] if row else 3.0


def set_auto_unlock_interval(user_id: int, hours: float):
    conn = _get_conn()
    conn.execute(
        "UPDATE auto_unlock SET interval_hours = ? WHERE user_id = ?",
        (hours, user_id),
    )


# ─── Watched pets (filter strings) ───────────────────────────────────────────

def get_watched_pets(user_id: int) -> list[str]:
    """Returns list of filter strings (case-insensitive substrings of pet name)."""
    conn = _get_conn()
    return [r[0] for r in conn.execute(
        "SELECT filter_text FROM watched_pets WHERE user_id = ? ORDER BY filter_text",
        (user_id,),
    ).fetchall()]


def add_watched_pet(user_id: int, filter_text: str):
    conn = _get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO watched_pets (user_id, filter_text) VALUES (?, ?)",
        (user_id, filter_text.strip()),
    )


def clear_watched_pets(user_id: int):
    conn = _get_conn()
    conn.execute("DELETE FROM watched_pets WHERE user_id = ?", (user_id,))


def remove_watched_pet(user_id: int, filter_text: str):
    conn = _get_conn()
    conn.execute(
        "DELETE FROM watched_pets WHERE user_id = ? AND filter_text = ?",
        (user_id, filter_text),
    )


# ─── Auto-Enable-Pet ──────────────────────────────────────────────────────────

def get_auto_enable_pet(user_id: int) -> bool:
    conn = _get_conn()
    row = conn.execute(
        "SELECT enabled FROM auto_enable_pet WHERE user_id = ?", (user_id,)
    ).fetchone()
    return bool(row and row[0])


def set_auto_enable_pet(user_id: int, enabled: bool):
    conn = _get_conn()
    conn.execute(
        "INSERT INTO auto_enable_pet (user_id, enabled) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET enabled = excluded.enabled",
        (user_id, int(enabled)),
    )



# ─── Autopilot ────────────────────────────────────────────────────────────────

def get_autopilot_config(user_id: int) -> dict | None:
    conn = _get_conn()
    row = conn.execute(
        "SELECT main_account, pet_id, config_id, running, started_at, batch_size, "
        "check_interval, last_checked_at, stuck_timeout "
        "FROM autopilot_config WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "main_account": row[0], "pet_id": row[1], "config_id": row[2],
        "running": bool(row[3]), "started_at": row[4],
        "batch_size":      row[5] if row[5] is not None else 10,
        "check_interval":  row[6] if row[6] is not None else 30,
        "last_checked_at": row[7],
        "stuck_timeout":   row[8] if row[8] is not None else 10,
    }


def save_autopilot_main(user_id: int, main_account: str):
    conn = _get_conn()
    conn.execute(
        "INSERT INTO autopilot_config (user_id, main_account) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET main_account = excluded.main_account",
        (user_id, main_account),
    )


def save_autopilot_config_id(user_id: int, config_id: int):
    conn = _get_conn()
    conn.execute(
        "INSERT INTO autopilot_config (user_id, config_id) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET config_id = excluded.config_id",
        (user_id, config_id),
    )


def save_autopilot_pet(user_id: int, pet_id: str):
    conn = _get_conn()
    conn.execute(
        "INSERT INTO autopilot_config (user_id, pet_id) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET pet_id = excluded.pet_id",
        (user_id, pet_id),
    )


def save_autopilot_check_interval(user_id: int, seconds: int):
    conn = _get_conn()
    conn.execute(
        "INSERT INTO autopilot_config (user_id, check_interval) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET check_interval = excluded.check_interval",
        (user_id, seconds),
    )


def set_autopilot_last_checked(user_id: int):
    conn = _get_conn()
    conn.execute(
        "UPDATE autopilot_config SET last_checked_at = ? WHERE user_id = ?",
        (datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), user_id),
    )


def save_autopilot_batch_size(user_id: int, batch_size: int):
    conn = _get_conn()
    conn.execute(
        "INSERT INTO autopilot_config (user_id, batch_size) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET batch_size = excluded.batch_size",
        (user_id, batch_size),
    )


def set_autopilot_started_at(user_id: int):
    conn = _get_conn()
    conn.execute(
        "INSERT INTO autopilot_config (user_id, started_at) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET started_at = excluded.started_at",
        (user_id, datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")),
    )


def get_autopilot_pets(user_id: int) -> list[tuple[int, str]]:
    """Returns [(row_id, pet_id), ...] ordered by insertion."""
    conn = _get_conn()
    return conn.execute(
        "SELECT id, pet_id FROM autopilot_pets WHERE user_id = ? ORDER BY id",
        (user_id,),
    ).fetchall()


def add_autopilot_pet(user_id: int, pet_id: str) -> bool:
    """Returns False if already exists."""
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO autopilot_pets (user_id, pet_id) VALUES (?, ?)",
            (user_id, pet_id),
        )
        return True
    except Exception:
        return False


def remove_autopilot_pet(row_id: int):
    conn = _get_conn()
    conn.execute("DELETE FROM autopilot_pets WHERE id = ?", (row_id,))


def set_autopilot_running(user_id: int, running: bool):
    conn = _get_conn()
    conn.execute(
        "INSERT INTO autopilot_config (user_id, running) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET running = excluded.running",
        (user_id, int(running)),
    )


def get_users_with_autopilot_running() -> list[tuple]:
    conn = _get_conn()
    return conn.execute(
        "SELECT c.user_id, p.api_key "
        "FROM autopilot_config c JOIN panels p ON c.user_id = p.user_id "
        "WHERE c.running = 1"
    ).fetchall()


def add_autopilot_queue(user_id: int, entries: list[tuple]):
    """entries = [(account_id, username), ...]"""
    conn = _get_conn()
    conn.executemany(
        "INSERT INTO autopilot_queue (user_id, account_id, username) VALUES (?, ?, ?)",
        [(user_id, acc_id, username) for acc_id, username in entries],
    )


def clear_autopilot_queue(user_id: int):
    conn = _get_conn()
    conn.execute("DELETE FROM autopilot_queue WHERE user_id = ?", (user_id,))


def get_autopilot_pending_entries(user_id: int) -> list[tuple]:
    conn = _get_conn()
    return conn.execute(
        "SELECT id, account_id, username FROM autopilot_queue "
        "WHERE user_id = ? AND status = 'pending' ORDER BY id",
        (user_id,),
    ).fetchall()


def get_autopilot_active_entries(user_id: int) -> list[tuple]:
    conn = _get_conn()
    return conn.execute(
        "SELECT id, account_id, username FROM autopilot_queue "
        "WHERE user_id = ? AND status = 'active' ORDER BY id",
        (user_id,),
    ).fetchall()


def get_autopilot_active_count(user_id: int) -> int:
    conn = _get_conn()
    row = conn.execute(
        "SELECT COUNT(*) FROM autopilot_queue WHERE user_id = ? AND status = 'active'",
        (user_id,),
    ).fetchone()
    return row[0] if row else 0


def get_autopilot_done_count(user_id: int) -> int:
    conn = _get_conn()
    row = conn.execute(
        "SELECT COUNT(*) FROM autopilot_queue WHERE user_id = ? AND status = 'done'",
        (user_id,),
    ).fetchone()
    return row[0] if row else 0


def save_autopilot_stuck_timeout(user_id: int, minutes: int):
    conn = _get_conn()
    conn.execute(
        "INSERT INTO autopilot_config (user_id, stuck_timeout) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET stuck_timeout = excluded.stuck_timeout",
        (user_id, minutes),
    )


def get_autopilot_stuck_entries(user_id: int, timeout_seconds: int) -> list[tuple]:
    conn = _get_conn()
    cutoff = (datetime.utcnow() - timedelta(seconds=timeout_seconds)).strftime("%Y-%m-%d %H:%M:%S")
    return conn.execute(
        "SELECT id, account_id, username FROM autopilot_queue "
        "WHERE user_id = ? AND status = 'active' "
        "AND activated_at IS NOT NULL AND activated_at <= ?",
        (user_id, cutoff),
    ).fetchall()


def set_autopilot_entry_status(entry_id: int, status: str):
    conn = _get_conn()
    if status == "active":
        conn.execute(
            "UPDATE autopilot_queue SET status = ?, activated_at = ? WHERE id = ?",
            (status, datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), entry_id),
        )
    else:
        conn.execute(
            "UPDATE autopilot_queue SET status = ? WHERE id = ?",
            (status, entry_id),
        )
