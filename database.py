from datetime import datetime, timedelta
from supabase import create_client, Client

_client: Client | None = None


def _get_client() -> Client:
    global _client
    if _client is None:
        from config import SUPABASE_URL, SUPABASE_KEY
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _client


def init_db():
    _get_client()


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _upsert_config(user_id: int, fields: dict):
    c = _get_client()
    result = c.table("autopilot_config").update(fields).eq("user_id", user_id).execute()
    if not result.data:
        try:
            c.table("autopilot_config").insert({"user_id": user_id, **fields}).execute()
        except Exception:
            pass


# ─── Panels ──────────────────────────────────────────────────────────────────

def get_panel(user_id: int) -> str | None:
    c = _get_client()
    result = c.table("panels").select("api_key").eq("user_id", user_id).execute()
    return result.data[0]["api_key"] if result.data else None


def save_panel(user_id: int, api_key: str):
    c = _get_client()
    c.table("panels").upsert({"user_id": user_id, "api_key": api_key}).execute()


# ─── Alerts ──────────────────────────────────────────────────────────────────

def get_alert(user_id: int):
    c = _get_client()
    result = c.table("alert_thresholds").select("threshold, enabled, last_notified").eq("user_id", user_id).execute()
    if not result.data:
        return None
    row = result.data[0]
    return (row["threshold"], row["enabled"], row["last_notified"])


def set_alert(user_id: int, threshold: int):
    c = _get_client()
    result = c.table("alert_thresholds").update({"threshold": threshold, "enabled": 1}).eq("user_id", user_id).execute()
    if not result.data:
        c.table("alert_thresholds").insert({"user_id": user_id, "threshold": threshold, "enabled": 1}).execute()


def toggle_alert(user_id: int) -> bool:
    c = _get_client()
    result = c.table("alert_thresholds").select("enabled").eq("user_id", user_id).execute()
    current = result.data[0]["enabled"] if result.data else 0
    new_val = 0 if current else 1
    c.table("alert_thresholds").update({"enabled": new_val}).eq("user_id", user_id).execute()
    return bool(new_val)


def get_users_with_alerts() -> list:
    c = _get_client()
    alerts_raw = c.table("alert_thresholds").select("user_id, threshold, last_notified, triggered").eq("enabled", 1).execute().data
    alerts = [a for a in alerts_raw if a.get("threshold") is not None]
    if not alerts:
        return []
    user_ids = [a["user_id"] for a in alerts]
    panels = c.table("panels").select("user_id, api_key").in_("user_id", user_ids).execute().data
    panels_map = {p["user_id"]: p["api_key"] for p in panels}
    return [
        (a["user_id"], panels_map[a["user_id"]], a["threshold"], a["last_notified"], a.get("triggered") or 0)
        for a in alerts if a["user_id"] in panels_map
    ]


def update_alert_notified(user_id: int):
    c = _get_client()
    c.table("alert_thresholds").update({"last_notified": _now_iso()}).eq("user_id", user_id).execute()


def set_alert_triggered(user_id: int, triggered: bool):
    c = _get_client()
    c.table("alert_thresholds").update({"triggered": int(triggered)}).eq("user_id", user_id).execute()


# ─── Pet snapshots ────────────────────────────────────────────────────────────

def save_pet_snapshot(user_id: int, pets: dict):
    c = _get_client()
    now = datetime.utcnow().replace(second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    cutoff = (datetime.utcnow() - timedelta(days=8)).strftime("%Y-%m-%dT%H:%M:%SZ")
    c.table("pet_snapshots").delete().eq("user_id", user_id).lt("recorded_at", cutoff).execute()
    records = [
        {"user_id": user_id, "pet_kind": kind, "quantity": data["quantity"], "recorded_at": now}
        for kind, data in pets.items()
    ]
    if records:
        c.table("pet_snapshots").upsert(records).execute()


def get_pets_farmed(user_id: int, current_pets: dict, hours: float) -> dict | None:
    c = _get_client()
    target = (datetime.utcnow() - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = c.table("pet_snapshots").select("recorded_at").eq("user_id", user_id).lte("recorded_at", target).order("recorded_at", desc=True).limit(1).execute()
    if not rows.data:
        return None
    recorded_at = rows.data[0]["recorded_at"]
    past_rows = c.table("pet_snapshots").select("pet_kind, quantity").eq("user_id", user_id).eq("recorded_at", recorded_at).execute()
    past = {r["pet_kind"]: r["quantity"] for r in past_rows.data}
    return {kind: max(0, data["quantity"] - past.get(kind, 0)) for kind, data in current_pets.items()}


# ─── ZeroPoint keys & jobs ───────────────────────────────────────────────────

def get_zp_key(user_id: int) -> str | None:
    c = _get_client()
    result = c.table("zp_keys").select("api_key").eq("user_id", user_id).execute()
    return result.data[0]["api_key"] if result.data else None


def save_zp_key(user_id: int, api_key: str):
    c = _get_client()
    c.table("zp_keys").upsert({"user_id": user_id, "api_key": api_key}).execute()


def get_zp_job(user_id: int) -> str | None:
    c = _get_client()
    result = c.table("zp_jobs").select("job_id").eq("user_id", user_id).execute()
    return result.data[0]["job_id"] if result.data else None


def save_zp_job(user_id: int, job_id: str):
    c = _get_client()
    c.table("zp_jobs").upsert({"user_id": user_id, "job_id": job_id, "notified": 0}).execute()


def clear_zp_job(user_id: int):
    c = _get_client()
    c.table("zp_jobs").delete().eq("user_id", user_id).execute()


def get_all_users_with_zp_jobs() -> list[tuple[int, str, str]]:
    c = _get_client()
    jobs = c.table("zp_jobs").select("user_id, job_id").eq("notified", 0).execute().data
    if not jobs:
        return []
    user_ids = [j["user_id"] for j in jobs]
    keys = c.table("zp_keys").select("user_id, api_key").in_("user_id", user_ids).execute().data
    keys_map = {k["user_id"]: k["api_key"] for k in keys}
    return [(j["user_id"], keys_map[j["user_id"]], j["job_id"]) for j in jobs if j["user_id"] in keys_map]


# ─── Auto-unlock ─────────────────────────────────────────────────────────────

def get_auto_unlock(user_id: int) -> bool:
    c = _get_client()
    result = c.table("auto_unlock").select("enabled").eq("user_id", user_id).execute()
    return bool(result.data[0]["enabled"]) if result.data else False


def set_auto_unlock(user_id: int, enabled: bool):
    c = _get_client()
    result = c.table("auto_unlock").update({"enabled": int(enabled)}).eq("user_id", user_id).execute()
    if not result.data:
        c.table("auto_unlock").insert({"user_id": user_id, "enabled": int(enabled)}).execute()


def get_users_due_for_auto_unlock() -> list[tuple[int, str, str | None]]:
    c = _get_client()
    records = c.table("auto_unlock").select("user_id, interval_hours, last_run_at").eq("enabled", 1).execute().data
    if not records:
        return []
    now = datetime.utcnow()
    due = []
    for r in records:
        if r["last_run_at"] is None:
            due.append(r)
        else:
            interval = r.get("interval_hours") or 3.0
            last_run_str = r["last_run_at"].replace("Z", "").split("+")[0]
            last_run = datetime.fromisoformat(last_run_str)
            if now >= last_run + timedelta(hours=interval):
                due.append(r)
    if not due:
        return []
    user_ids = [r["user_id"] for r in due]
    panels = c.table("panels").select("user_id, api_key").in_("user_id", user_ids).execute().data
    zp_keys = c.table("zp_keys").select("user_id, api_key").in_("user_id", user_ids).execute().data
    panels_map = {p["user_id"]: p["api_key"] for p in panels}
    zp_map = {z["user_id"]: z["api_key"] for z in zp_keys}
    return [(r["user_id"], panels_map[r["user_id"]], zp_map.get(r["user_id"])) for r in due if r["user_id"] in panels_map]


def update_auto_unlock_last_run(user_id: int):
    c = _get_client()
    c.table("auto_unlock").update({"last_run_at": _now_iso()}).eq("user_id", user_id).execute()


def get_auto_unlock_interval(user_id: int) -> float:
    c = _get_client()
    result = c.table("auto_unlock").select("interval_hours").eq("user_id", user_id).execute()
    return (result.data[0]["interval_hours"] or 3.0) if result.data else 3.0


def set_auto_unlock_interval(user_id: int, hours: float):
    c = _get_client()
    c.table("auto_unlock").update({"interval_hours": hours}).eq("user_id", user_id).execute()


# ─── Totals snapshots (money + potions) ──────────────────────────────────────

def save_totals_snapshot(user_id: int, money: int, potions: int):
    c = _get_client()
    now = datetime.utcnow().replace(second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    cutoff = (datetime.utcnow() - timedelta(days=8)).strftime("%Y-%m-%dT%H:%M:%SZ")
    c.table("totals_snapshots").delete().eq("user_id", user_id).lt("recorded_at", cutoff).execute()
    c.table("totals_snapshots").delete().eq("user_id", user_id).eq("recorded_at", now).execute()
    c.table("totals_snapshots").insert({
        "user_id": user_id, "money": money, "potions": potions, "recorded_at": now,
    }).execute()


def get_period_baseline(user_id: int, period_hours: int) -> dict | None:
    c = _get_client()
    rows = c.table("period_baselines").select("money, potions, started_at") \
        .eq("user_id", user_id).eq("period_hours", period_hours).execute()
    return rows.data[0] if rows.data else None


def save_period_baseline(user_id: int, period_hours: int, money: int, potions: int):
    c = _get_client()
    c.table("period_baselines").upsert({
        "user_id": user_id,
        "period_hours": period_hours,
        "money": money,
        "potions": potions,
        "started_at": _now_iso(),
    }).execute()


# ─── Watched pets ────────────────────────────────────────────────────────────

def get_watched_pets(user_id: int) -> list[str]:
    c = _get_client()
    result = c.table("watched_pets").select("filter_text").eq("user_id", user_id).order("filter_text").execute()
    return [r["filter_text"] for r in result.data]


def add_watched_pet(user_id: int, filter_text: str):
    c = _get_client()
    c.table("watched_pets").upsert({"user_id": user_id, "filter_text": filter_text.strip()}, ignore_duplicates=True).execute()


def clear_watched_pets(user_id: int):
    c = _get_client()
    c.table("watched_pets").delete().eq("user_id", user_id).execute()


def remove_watched_pet(user_id: int, filter_text: str):
    c = _get_client()
    c.table("watched_pets").delete().eq("user_id", user_id).eq("filter_text", filter_text).execute()


# ─── Autopilot ────────────────────────────────────────────────────────────────

def get_autopilot_config(user_id: int) -> dict | None:
    c = _get_client()
    result = c.table("autopilot_config").select("*").eq("user_id", user_id).execute()
    if not result.data:
        return None
    row = result.data[0]
    return {
        "main_account":           row.get("main_account"),
        "pet_id":                 row.get("pet_id"),
        "config_id":              row.get("config_id"),
        "running":                bool(row.get("running", 0)),
        "started_at":             row.get("started_at"),
        "batch_size":             row.get("batch_size") or 10,
        "check_interval":         row.get("check_interval") or 30,
        "last_checked_at":        row.get("last_checked_at"),
        "stuck_timeout":          row.get("stuck_timeout") or 10,
        "farm_config_id":         row.get("farm_config_id"),
        "trades_done":            row.get("trades_done") or 0,
        "max_traders_per_server": row.get("max_traders_per_server") or 10,
        "ready_count":            row.get("ready_count") or 0,
    }


def save_autopilot_ready_count(user_id: int, count: int):
    _upsert_config(user_id, {"ready_count": count})


def save_autopilot_main(user_id: int, main_account: str):
    _upsert_config(user_id, {"main_account": main_account})


def save_autopilot_config_id(user_id: int, config_id: int):
    _upsert_config(user_id, {"config_id": config_id})


def save_autopilot_pet(user_id: int, pet_id: str):
    _upsert_config(user_id, {"pet_id": pet_id})


def save_autopilot_check_interval(user_id: int, seconds: int):
    _upsert_config(user_id, {"check_interval": seconds})


def set_autopilot_last_checked(user_id: int):
    c = _get_client()
    c.table("autopilot_config").update({"last_checked_at": _now_iso()}).eq("user_id", user_id).execute()


def save_autopilot_batch_size(user_id: int, batch_size: int):
    _upsert_config(user_id, {"batch_size": batch_size})


def save_autopilot_max_traders_per_server(user_id: int, value: int):
    _upsert_config(user_id, {"max_traders_per_server": value})


def set_autopilot_started_at(user_id: int):
    _upsert_config(user_id, {"started_at": _now_iso()})


def get_autopilot_pets(user_id: int) -> list[tuple[int, str, int]]:
    c = _get_client()
    result = c.table("autopilot_pets").select("id, pet_id, min_count").eq("user_id", user_id).order("id").execute()
    return [(r["id"], r["pet_id"], r.get("min_count") or 1) for r in result.data]


def add_autopilot_pet(user_id: int, pet_id: str, min_count: int = 1) -> bool:
    c = _get_client()
    existing = c.table("autopilot_pets").select("id").eq("user_id", user_id).eq("pet_id", pet_id).execute()
    if existing.data:
        return False
    c.table("autopilot_pets").insert({"user_id": user_id, "pet_id": pet_id, "min_count": min_count}).execute()
    return True


def add_autopilot_pets_bulk(user_id: int, pet_ids: list[str]) -> tuple[int, int]:
    """Returns (added, skipped)."""
    c = _get_client()
    existing_rows = c.table("autopilot_pets").select("pet_id").eq("user_id", user_id).execute()
    existing_set = {r["pet_id"] for r in existing_rows.data}
    new_ids = [pid for pid in pet_ids if pid and pid not in existing_set]
    if new_ids:
        records = [{"user_id": user_id, "pet_id": pid, "min_count": 1} for pid in new_ids]
        c.table("autopilot_pets").insert(records).execute()
    return len(new_ids), len(pet_ids) - len(new_ids)


def update_autopilot_pet_min_count(row_id: int, min_count: int):
    c = _get_client()
    c.table("autopilot_pets").update({"min_count": min_count}).eq("id", row_id).execute()


def remove_autopilot_pet(row_id: int):
    c = _get_client()
    c.table("autopilot_pets").delete().eq("id", row_id).execute()


def set_autopilot_running(user_id: int, running: bool):
    _upsert_config(user_id, {"running": int(running)})


def get_users_with_autopilot_running() -> list[tuple]:
    c = _get_client()
    running = c.table("autopilot_config").select("user_id").eq("running", 1).execute().data
    if not running:
        return []
    user_ids = [r["user_id"] for r in running]
    panels = c.table("panels").select("user_id, api_key").in_("user_id", user_ids).execute().data
    panels_map = {p["user_id"]: p["api_key"] for p in panels}
    return [(r["user_id"], panels_map[r["user_id"]]) for r in running if r["user_id"] in panels_map]


def add_autopilot_queue(user_id: int, entries: list[tuple], status: str = 'farming'):
    c = _get_client()
    records = [{"user_id": user_id, "account_id": acc_id, "username": username, "status": status}
               for acc_id, username in entries]
    if records:
        c.table("autopilot_queue").insert(records).execute()


def clear_autopilot_queue(user_id: int):
    c = _get_client()
    c.table("autopilot_queue").delete().eq("user_id", user_id).execute()


def get_autopilot_pending_entries(user_id: int) -> list[tuple]:
    c = _get_client()
    result = c.table("autopilot_queue").select("id, account_id, username").eq("user_id", user_id).eq("status", "pending").order("id").execute()
    return [(r["id"], r["account_id"], r["username"]) for r in result.data]


def get_autopilot_active_entries(user_id: int) -> list[tuple]:
    c = _get_client()
    result = c.table("autopilot_queue").select("id, account_id, username").eq("user_id", user_id).eq("status", "active").order("id").execute()
    return [(r["id"], r["account_id"], r["username"]) for r in result.data]


def get_autopilot_active_count(user_id: int) -> int:
    c = _get_client()
    result = c.table("autopilot_queue").select("id").eq("user_id", user_id).eq("status", "active").execute()
    return len(result.data)


def get_autopilot_done_count(user_id: int) -> int:
    c = _get_client()
    result = c.table("autopilot_queue").select("id").eq("user_id", user_id).eq("status", "done").execute()
    return len(result.data)


def save_autopilot_stuck_timeout(user_id: int, minutes: int):
    _upsert_config(user_id, {"stuck_timeout": minutes})


def save_autopilot_farm_config_id(user_id: int, config_id: int):
    _upsert_config(user_id, {"farm_config_id": config_id})


def get_autopilot_queue_usernames(user_id: int) -> set[str]:
    c = _get_client()
    result = c.table("autopilot_queue").select("username").eq("user_id", user_id).execute()
    return {r["username"].lower() for r in result.data if r.get("username")}


def get_autopilot_farming_entries(user_id: int) -> list[tuple]:
    c = _get_client()
    result = c.table("autopilot_queue").select("id, account_id, username").eq("user_id", user_id).eq("status", "farming").order("id").execute()
    return [(r["id"], r["account_id"], r["username"]) for r in result.data]


def get_autopilot_trading_entries(user_id: int) -> list[tuple]:
    c = _get_client()
    result = c.table("autopilot_queue").select("id, account_id, username").eq("user_id", user_id).eq("status", "trading").order("id").execute()
    return [(r["id"], r["account_id"], r["username"]) for r in result.data]


def get_autopilot_farming_count(user_id: int) -> int:
    c = _get_client()
    result = c.table("autopilot_queue").select("id").eq("user_id", user_id).eq("status", "farming").execute()
    return len(result.data)


def get_autopilot_trading_count(user_id: int) -> int:
    c = _get_client()
    result = c.table("autopilot_queue").select("id").eq("user_id", user_id).eq("status", "trading").execute()
    return len(result.data)


def increment_autopilot_trades_done(user_id: int):
    c = _get_client()
    current = get_autopilot_trades_done(user_id)
    c.table("autopilot_config").update({"trades_done": current + 1}).eq("user_id", user_id).execute()


def get_autopilot_trades_done(user_id: int) -> int:
    c = _get_client()
    result = c.table("autopilot_config").select("trades_done").eq("user_id", user_id).execute()
    return (result.data[0]["trades_done"] or 0) if result.data else 0


def get_autopilot_stuck_entries(user_id: int, timeout_seconds: int) -> list[tuple]:
    c = _get_client()
    cutoff = (datetime.utcnow() - timedelta(seconds=timeout_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = c.table("autopilot_queue")\
        .select("id, account_id, username")\
        .eq("user_id", user_id)\
        .eq("status", "trading")\
        .lte("activated_at", cutoff)\
        .execute()
    return [(r["id"], r["account_id"], r["username"]) for r in result.data]


def set_autopilot_entry_status(entry_id: int, status: str):
    c = _get_client()
    if status in ("active", "trading"):
        c.table("autopilot_queue").update({"status": status, "activated_at": _now_iso()}).eq("id", entry_id).execute()
    else:
        c.table("autopilot_queue").update({"status": status}).eq("id", entry_id).execute()


# ─── AutoSwap ────────────────────────────────────────────────────────────────

def get_autoswap_config(user_id: int) -> dict | None:
    c = _get_client()
    result = c.table("autoswap_config").select("*").eq("user_id", user_id).execute()
    if not result.data:
        return None
    row = result.data[0]
    return {
        "auto_enabled":   bool(row.get("auto_enabled", 0)),
        "interval_hours": float(row.get("interval_hours") or 1.0),
        "last_run_at":    row.get("last_run_at"),
    }


def _upsert_autoswap(user_id: int, fields: dict):
    c = _get_client()
    result = c.table("autoswap_config").update(fields).eq("user_id", user_id).execute()
    if not result.data:
        try:
            c.table("autoswap_config").insert({"user_id": user_id, **fields}).execute()
        except Exception:
            pass


def toggle_autoswap_auto(user_id: int) -> bool:
    c = _get_client()
    result = c.table("autoswap_config").select("auto_enabled").eq("user_id", user_id).execute()
    current = bool(result.data[0]["auto_enabled"]) if result.data else False
    new_val = not current
    _upsert_autoswap(user_id, {"auto_enabled": int(new_val)})
    return new_val


def save_autoswap_interval(user_id: int, hours: float):
    _upsert_autoswap(user_id, {"interval_hours": hours})


def set_autoswap_last_run(user_id: int):
    _upsert_autoswap(user_id, {"last_run_at": _now_iso()})


def get_users_due_for_autoswap() -> list[tuple[int, str]]:
    c = _get_client()
    records = c.table("autoswap_config").select("user_id, interval_hours, last_run_at") \
        .eq("auto_enabled", 1).execute().data
    if not records:
        return []
    now = datetime.utcnow()
    due = []
    for r in records:
        if r["last_run_at"] is None:
            due.append(r)
        else:
            interval = r.get("interval_hours") or 1.0
            last_run_str = r["last_run_at"].replace("Z", "").split("+")[0]
            last_run = datetime.fromisoformat(last_run_str)
            if now >= last_run + timedelta(hours=interval):
                due.append(r)
    if not due:
        return []
    user_ids = [r["user_id"] for r in due]
    panels = c.table("panels").select("user_id, api_key").in_("user_id", user_ids).execute().data
    panels_map = {p["user_id"]: p["api_key"] for p in panels}
    return [(r["user_id"], panels_map[r["user_id"]]) for r in due if r["user_id"] in panels_map]


# ─── Autopilot events ─────────────────────────────────────────────────────────

def add_autopilot_event(user_id: int, event_type: str, username: str | None = None):
    c = _get_client()
    c.table("autopilot_events").insert({
        "user_id": user_id,
        "event_type": event_type,
        "username": username,
        "created_at": _now_iso(),
    }).execute()
