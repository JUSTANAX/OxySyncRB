import os
import json
import hmac
import hashlib
import urllib.parse
import requests
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, Response
from supabase import create_client, Client

app = Flask(__name__)

BOT_TOKEN    = os.environ["BOT_TOKEN"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
AO_URL       = "https://accountops.org"
ZP_URL       = "https://zeropoint.to/api/faceunlock-api"

_sb: Client | None = None


def _db() -> Client:
    global _sb
    if _sb is None:
        _sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _sb


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_iso(val) -> str | None:
    if not val:
        return None
    s = str(val).replace("+00:00", "Z")
    return s if s.endswith("Z") else s + "Z"


# ── Auth ──────────────────────────────────────────────────────────────────────

def _validate(raw: str) -> int | None:
    parsed = dict(urllib.parse.parse_qsl(raw, keep_blank_values=True))
    recv   = parsed.pop("hash", "")
    check  = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    exp    = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(recv, exp):
        return None
    try:
        return json.loads(parsed.get("user", "{}")).get("id")
    except Exception:
        return None


def _auth():
    raw = request.headers.get("X-Init-Data", "")
    if not raw:
        return None, ({"error": "Unauthorized"}, 401)
    uid = _validate(raw)
    if not uid:
        return None, ({"error": "Invalid initData"}, 401)
    return uid, None


# ── Supabase helpers ──────────────────────────────────────────────────────────

def _get_panel(user_id):
    r = _db().table("panels").select("api_key").eq("user_id", user_id).execute()
    return r.data[0]["api_key"] if r.data else None


def _get_zp_key(user_id):
    r = _db().table("zp_keys").select("api_key").eq("user_id", user_id).execute()
    return r.data[0]["api_key"] if r.data else None


def _upsert(table: str, user_id: int, fields: dict):
    r = _db().table(table).update(fields).eq("user_id", user_id).execute()
    if not r.data:
        try:
            _db().table(table).insert({"user_id": user_id, **fields}).execute()
        except Exception:
            pass


# ── AO helpers ────────────────────────────────────────────────────────────────

def _ao_get(ao_key, path):
    r = requests.get(AO_URL + path, headers={"X-Api-Key": ao_key}, timeout=20)
    return (r.json(), None) if r.status_code == 200 else (None, r.status_code)


def _ao_post(ao_key, path, body):
    h = {"X-Api-Key": ao_key, "Content-Type": "application/json"}
    r = requests.post(AO_URL + path, headers=h, json=body, timeout=20)
    return (r.json(), None) if r.status_code == 200 else (None, r.status_code)


def _ao_put(ao_key, path, body):
    h = {"X-Api-Key": ao_key, "Content-Type": "application/json"}
    r = requests.put(AO_URL + path, headers=h, json=body, timeout=20)
    return r.status_code in (200, 204)


def _set_enabled(ao_key, usernames, enabled):
    for i in range(0, max(len(usernames), 1), 50):
        _ao_put(ao_key, "/api/accounts/enable", {"usernames": usernames[i:i+50], "enabled": enabled})


def _set_config(ao_key, usernames, config_id):
    for i in range(0, max(len(usernames), 1), 50):
        _ao_put(ao_key, "/api/accounts/config", {"usernames": usernames[i:i+50], "config_id": config_id})


# ── ZP helpers ────────────────────────────────────────────────────────────────

def _zp(method, zp_key, path, body=None):
    h = {"X-API-Key": zp_key, "Content-Type": "application/json"}
    return getattr(requests, method)(ZP_URL + path, headers=h, json=body, timeout=30)


# ── Autopilot events ──────────────────────────────────────────────────────────

def _add_event(user_id, event_type, username=None):
    _db().table("autopilot_events").insert({
        "user_id": user_id, "event_type": event_type,
        "username": username, "created_at": _now_iso(),
    }).execute()


def _recent_events(user_id):
    r = _db().table("autopilot_events").select("event_type, username, created_at") \
        .eq("user_id", user_id).order("id", desc=True).limit(20).execute()
    return [{"time": _to_iso(e["created_at"]), "type": e["event_type"], "username": e["username"]}
            for e in r.data]


# ── Autopilot config ──────────────────────────────────────────────────────────

def _ap_cfg(user_id):
    r = _db().table("autopilot_config").select("*").eq("user_id", user_id).execute()
    if not r.data:
        return None
    row = r.data[0]
    return {
        "running":                bool(row.get("running", 0)),
        "main_account":           row.get("main_account"),
        "config_id":              row.get("config_id"),
        "farm_config_id":         row.get("farm_config_id"),
        "check_interval":         row.get("check_interval") or 30,
        "stuck_timeout":          row.get("stuck_timeout") or 10,
        "max_traders_per_server": row.get("max_traders_per_server") or 10,
        "started_at":             _to_iso(row.get("started_at")),
        "trades_done":            row.get("trades_done") or 0,
    }


def _queue_counts(user_id, stuck_timeout_min=10):
    r = _db().table("autopilot_queue").select("status, activated_at") \
        .eq("user_id", user_id).execute()
    counts = {"farming": 0, "trading": 0, "stuck": 0}
    cutoff = datetime.utcnow() - timedelta(minutes=stuck_timeout_min)
    for row in (r.data or []):
        s = row.get("status")
        if s == "farming":
            counts["farming"] += 1
        elif s == "trading":
            at = row.get("activated_at")
            is_stuck = False
            if at:
                try:
                    ts = datetime.fromisoformat(str(at).replace("Z", "").split("+")[0])
                    if ts <= cutoff:
                        is_stuck = True
                except Exception:
                    pass
            counts["stuck" if is_stuck else "trading"] += 1
    counts["total"] = sum(counts.values())
    return counts


# ─────────────────────────────────────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/dashboard")
def api_dashboard():
    uid, err = _auth()
    if err:
        return jsonify(err[0]), err[1]
    ao_key = _get_panel(uid)
    if not ao_key:
        return jsonify({"error": "АО ключ не подключён"}), 400
    data, code = _ao_get(ao_key, "/api/dashboard")
    if data is None:
        return jsonify({"error": f"АО вернул {code}"}), 502
    zp_balance = None
    zp_key = _get_zp_key(uid)
    if zp_key:
        try:
            resp = _zp("get", zp_key, "/balance")
            if resp.status_code == 200:
                b = resp.json()
                zp_balance = {"effective": b.get("effective", 0), "reserved": b.get("reserved", 0)}
        except Exception:
            pass
    return jsonify({
        "active_count":   data.get("active_count", 0),
        "passive_count":  data.get("passive_count", 0),
        "unstable_count": data.get("unstable_count", 0),
        "zp_balance":     zp_balance,
    })


@app.route("/api/alerts", methods=["GET"])
def get_alerts():
    uid, err = _auth()
    if err:
        return jsonify(err[0]), err[1]
    r = _db().table("alert_thresholds").select("threshold, enabled, triggered").eq("user_id", uid).execute()
    if not r.data:
        return jsonify({"threshold": None, "enabled": False, "triggered": False})
    row = r.data[0]
    return jsonify({"threshold": row.get("threshold"), "enabled": bool(row.get("enabled")), "triggered": bool(row.get("triggered"))})


@app.route("/api/alerts", methods=["PUT"])
def put_alerts():
    uid, err = _auth()
    if err:
        return jsonify(err[0]), err[1]
    body = request.get_json(silent=True) or {}
    fields = {}
    if "threshold" in body:
        t = body["threshold"]
        if not isinstance(t, int) or t < 1:
            return jsonify({"error": "threshold должен быть >= 1"}), 400
        fields["threshold"] = t
    if "enabled" in body:
        fields["enabled"] = int(bool(body["enabled"]))
    if fields:
        _upsert("alert_thresholds", uid, fields)
    r = _db().table("alert_thresholds").select("threshold, enabled, triggered").eq("user_id", uid).execute()
    if not r.data:
        return jsonify({"threshold": None, "enabled": False, "triggered": False})
    row = r.data[0]
    return jsonify({"threshold": row.get("threshold"), "enabled": bool(row.get("enabled")), "triggered": bool(row.get("triggered"))})


@app.route("/api/faceunlock", methods=["GET"])
def get_faceunlock():
    uid, err = _auth()
    if err:
        return jsonify(err[0]), err[1]
    zp_key = _get_zp_key(uid)
    row = {}
    au = _db().table("auto_unlock").select("enabled, interval_hours, last_run_at").eq("user_id", uid).execute()
    if au.data:
        row = au.data[0]
    auto_enabled   = bool(row.get("enabled"))
    interval_hours = float(row.get("interval_hours") or 3.0)
    last_run_at    = row.get("last_run_at")
    next_run_at = None
    if auto_enabled and last_run_at:
        try:
            ts = datetime.fromisoformat(str(last_run_at).replace("Z", "").split("+")[0])
            next_run_at = (ts + timedelta(hours=interval_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            pass
    job_data = None
    job_row = _db().table("zp_jobs").select("job_id").eq("user_id", uid).execute()
    if job_row.data and zp_key:
        job_id = job_row.data[0]["job_id"]
        try:
            resp = _zp("get", zp_key, f"/status/{job_id}")
            if resp.status_code == 200:
                st = resp.json()
                job_data = {
                    "job_id": job_id, "status": st.get("status"),
                    "total_accounts": st.get("total_accounts", 0),
                    "processed": st.get("processed", 0),
                    "successful": st.get("successful", 0),
                    "failed": st.get("failed", 0),
                    "other_failed": st.get("other_failed", 0),
                    "result_files": st.get("result_files", []),
                }
            elif resp.status_code == 404:
                _db().table("zp_jobs").delete().eq("user_id", uid).execute()
        except Exception:
            pass
    return jsonify({
        "zp_key": bool(zp_key), "auto_enabled": auto_enabled,
        "interval_hours": interval_hours, "next_run_at": next_run_at, "job": job_data,
    })


@app.route("/api/faceunlock", methods=["PUT"])
def put_faceunlock():
    uid, err = _auth()
    if err:
        return jsonify(err[0]), err[1]
    body = request.get_json(silent=True) or {}
    fields = {}
    if "auto_enabled" in body:
        fields["enabled"] = int(bool(body["auto_enabled"]))
    if "interval_hours" in body:
        h = float(body["interval_hours"])
        if h not in {1.0, 2.0, 3.0, 4.0, 6.0}:
            return jsonify({"error": "interval_hours должен быть одним из: 1, 2, 3, 4, 6"}), 400
        fields["interval_hours"] = h
    if fields:
        _upsert("auto_unlock", uid, fields)
    row = {}
    au = _db().table("auto_unlock").select("enabled, interval_hours, last_run_at").eq("user_id", uid).execute()
    if au.data:
        row = au.data[0]
    auto_enabled   = bool(row.get("enabled"))
    interval_hours = float(row.get("interval_hours") or 3.0)
    last_run_at    = row.get("last_run_at")
    next_run_at = None
    if auto_enabled and last_run_at:
        try:
            ts = datetime.fromisoformat(str(last_run_at).replace("Z", "").split("+")[0])
            next_run_at = (ts + timedelta(hours=interval_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            pass
    return jsonify({"auto_enabled": auto_enabled, "interval_hours": interval_hours, "next_run_at": next_run_at})


@app.route("/api/faceunlock/start", methods=["POST"])
def start_faceunlock():
    uid, err = _auth()
    if err:
        return jsonify(err[0]), err[1]
    ao_key = _get_panel(uid)
    zp_key = _get_zp_key(uid)
    if not ao_key:
        return jsonify({"error": "АО ключ не подключён"}), 400
    if not zp_key:
        return jsonify({"error": "ZeroPoint ключ не подключён"}), 400
    job_row = _db().table("zp_jobs").select("job_id").eq("user_id", uid).execute()
    if job_row.data:
        job_id = job_row.data[0]["job_id"]
        try:
            resp = _zp("get", zp_key, f"/status/{job_id}")
            if resp.status_code == 200 and resp.json().get("status") in ("pending", "processing"):
                return jsonify({"error": "Уже есть активная задача", "job_id": job_id}), 409
        except Exception:
            pass
    data, code = _ao_post(ao_key, "/api/devices/accounts", {"tag": "status:face"})
    if data is None:
        return jsonify({"error": f"АО вернул {code}"}), 502
    lines = []
    for device in (data.get("devices") or []):
        for acc in device.get("accounts", []):
            cookie = (acc.get("cookie") or "").strip()
            if cookie.startswith(".ROBLOSECURITY="):
                cookie = cookie[len(".ROBLOSECURITY="):]
            if "_|WARNING" not in cookie:
                continue
            username = (acc.get("username") or "").strip()
            password = (acc.get("password") or "").strip()
            lines.append(f"{username}:{password}:{cookie}" if username and password else cookie)
    if not lines:
        return jsonify({"error": "Нет аккаунтов с тегом status:face"}), 400
    resp = _zp("post", zp_key, "/submit", {"accounts": "\n".join(lines)})
    if resp.status_code == 409:
        d = resp.json()
        existing = d.get("existing_job_id")
        if existing:
            _db().table("zp_jobs").upsert({"user_id": uid, "job_id": existing, "notified": 0}).execute()
        return jsonify({"error": "Уже есть активная задача", "job_id": existing}), 409
    if resp.status_code not in (200, 201):
        return jsonify({"error": f"ZeroPoint ошибка (код {resp.status_code})"}), 502
    result = resp.json()
    job_id = result.get("job_id")
    if job_id:
        _db().table("zp_jobs").upsert({"user_id": uid, "job_id": job_id, "notified": 0}).execute()
    return jsonify({"job_id": job_id, "accounts": len(lines)})


@app.route("/api/faceunlock/cancel", methods=["POST"])
def cancel_faceunlock():
    uid, err = _auth()
    if err:
        return jsonify(err[0]), err[1]
    zp_key = _get_zp_key(uid)
    if not zp_key:
        return jsonify({"error": "ZeroPoint ключ не подключён"}), 400
    job_row = _db().table("zp_jobs").select("job_id").eq("user_id", uid).execute()
    if not job_row.data:
        return jsonify({"error": "Нет активной задачи"}), 400
    job_id = job_row.data[0]["job_id"]
    _zp("post", zp_key, f"/cancel/{job_id}")
    return jsonify({"ok": True})


@app.route("/api/faceunlock/download/<filename>")
def download_faceunlock(filename):
    uid, err = _auth()
    if err:
        return jsonify(err[0]), err[1]
    zp_key = _get_zp_key(uid)
    if not zp_key:
        return jsonify({"error": "ZeroPoint ключ не подключён"}), 400
    job_row = _db().table("zp_jobs").select("job_id").eq("user_id", uid).execute()
    if not job_row.data:
        return jsonify({"error": "Нет активной задачи"}), 400
    job_id = job_row.data[0]["job_id"]
    resp = _zp("get", zp_key, f"/download/{job_id}/{filename}")
    if resp.status_code == 404:
        return jsonify({"error": "Файл не найден"}), 404
    if resp.status_code != 200:
        return jsonify({"error": f"ZeroPoint ошибка (код {resp.status_code})"}), 502
    return Response(resp.content, mimetype="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.route("/api/autopilot", methods=["GET"])
def get_autopilot():
    uid, err = _auth()
    if err:
        return jsonify(err[0]), err[1]
    cfg = _ap_cfg(uid) or {
        "running": False, "main_account": None, "config_id": None, "farm_config_id": None,
        "check_interval": 30, "stuck_timeout": 10, "max_traders_per_server": 10,
        "started_at": None, "trades_done": 0,
    }
    pets  = _db().table("autopilot_pets").select("id, pet_id").eq("user_id", uid).order("id").execute()
    queue  = _queue_counts(uid, cfg.get("stuck_timeout") or 10)
    recent = _recent_events(uid)
    return jsonify({
        **cfg,
        "pets":   [{"id": p["id"], "pet_id": p["pet_id"]} for p in pets.data],
        "queue":  queue,
        "recent": recent,
    })


@app.route("/api/autopilot/config", methods=["PUT"])
def put_autopilot_config():
    uid, err = _auth()
    if err:
        return jsonify(err[0]), err[1]
    body = request.get_json(silent=True) or {}
    fields = {}
    if "main_account" in body:
        v = str(body["main_account"]).strip()
        fields["main_account"] = v or None
    if "config_id" in body:
        fields["config_id"] = int(body["config_id"]) if body["config_id"] is not None else None
    if "farm_config_id" in body:
        fields["farm_config_id"] = int(body["farm_config_id"]) if body["farm_config_id"] is not None else None
    if "check_interval" in body:
        v = int(body["check_interval"])
        if not (10 <= v <= 300):
            return jsonify({"error": "check_interval: 10–300"}), 400
        fields["check_interval"] = v
    if "stuck_timeout" in body:
        v = int(body["stuck_timeout"])
        if not (1 <= v <= 60):
            return jsonify({"error": "stuck_timeout: 1–60"}), 400
        fields["stuck_timeout"] = v
    if "max_traders_per_server" in body:
        v = int(body["max_traders_per_server"])
        if not (1 <= v <= 50):
            return jsonify({"error": "max_traders_per_server: 1–50"}), 400
        fields["max_traders_per_server"] = v
    if fields:
        _upsert("autopilot_config", uid, fields)
    cfg = _ap_cfg(uid) or {}
    return jsonify({k: cfg.get(k) for k in (
        "main_account", "config_id", "farm_config_id",
        "check_interval", "stuck_timeout", "max_traders_per_server",
    )})


@app.route("/api/autopilot/pets", methods=["GET"])
def get_ap_pets():
    uid, err = _auth()
    if err:
        return jsonify(err[0]), err[1]
    r = _db().table("autopilot_pets").select("id, pet_id").eq("user_id", uid).order("id").execute()
    return jsonify([{"id": p["id"], "pet_id": p["pet_id"]} for p in r.data])


@app.route("/api/autopilot/pets", methods=["POST"])
def add_ap_pet():
    uid, err = _auth()
    if err:
        return jsonify(err[0]), err[1]
    body = request.get_json(silent=True) or {}
    pet_id = (body.get("pet_id") or "").strip()
    if not pet_id:
        return jsonify({"error": "pet_id обязателен"}), 400
    existing = _db().table("autopilot_pets").select("id").eq("user_id", uid).eq("pet_id", pet_id).execute()
    if existing.data:
        return jsonify({"error": "Такой пет уже добавлен"}), 409
    r = _db().table("autopilot_pets").insert({"user_id": uid, "pet_id": pet_id}).execute()
    new_id = r.data[0]["id"] if r.data else None
    return jsonify({"id": new_id, "pet_id": pet_id})


@app.route("/api/autopilot/pets/<int:pet_row_id>", methods=["DELETE"])
def delete_ap_pet(pet_row_id):
    uid, err = _auth()
    if err:
        return jsonify(err[0]), err[1]
    _db().table("autopilot_pets").delete().eq("id", pet_row_id).eq("user_id", uid).execute()
    return jsonify({"ok": True})


@app.route("/api/autopilot/configs", methods=["GET"])
def get_ap_configs():
    uid, err = _auth()
    if err:
        return jsonify(err[0]), err[1]
    ao_key = _get_panel(uid)
    if not ao_key:
        return jsonify({"error": "АО ключ не подключён"}), 400
    data, code = _ao_get(ao_key, "/api/player-configs")
    if data is None:
        return jsonify({"error": f"АО вернул {code}"}), 502
    return jsonify(data if isinstance(data, list) else [])


@app.route("/api/autopilot/start", methods=["POST"])
def start_autopilot():
    uid, err = _auth()
    if err:
        return jsonify(err[0]), err[1]
    ao_key = _get_panel(uid)
    if not ao_key:
        return jsonify({"error": "АО ключ не подключён"}), 400
    cfg = _ap_cfg(uid)
    if not cfg or not cfg.get("main_account"):
        return jsonify({"error": "Не задан main_account"}), 400
    if not _db().table("autopilot_pets").select("id").eq("user_id", uid).execute().data:
        return jsonify({"error": "Не заданы pet_ids"}), 400
    if cfg.get("running"):
        return jsonify({"error": "Уже запущен"}), 409

    main_account   = cfg["main_account"]
    farm_config_id = cfg.get("farm_config_id")

    data, code = _ao_get(ao_key, "/api/trackstats/accounts")
    if data is None:
        return jsonify({"error": f"АО вернул {code}"}), 502
    accounts = data if isinstance(data, list) else (data.get("accounts") or [])

    face_d, _ = _ao_post(ao_key, "/api/devices/accounts", {"tag": "status:face"})
    dead_d, _ = _ao_post(ao_key, "/api/devices/accounts", {"tag": "status:dead"})
    excluded = set()
    for d in (face_d, dead_d):
        if d:
            for device in (d.get("devices") or []):
                for acc in device.get("accounts", []):
                    u = (acc.get("username") or "").strip().lower()
                    if u:
                        excluded.add(u)

    farm_entries  = []
    all_usernames = []
    for acc in accounts:
        u      = (acc.get("username") or acc.get("name") or "").strip()
        acc_id = acc.get("id")
        if not u or not acc_id:
            continue
        all_usernames.append(u)
        if u.lower() == main_account.lower() or u.lower() in excluded:
            continue
        farm_entries.append((acc_id, u))

    farm_usernames = [u for _, u in farm_entries]
    _set_enabled(ao_key, all_usernames, False)
    _set_enabled(ao_key, [main_account], True)
    if farm_config_id and farm_usernames:
        _set_config(ao_key, farm_usernames, farm_config_id)
    if farm_usernames:
        _set_enabled(ao_key, farm_usernames, True)

    _db().table("autopilot_queue").delete().eq("user_id", uid).execute()
    _db().table("autopilot_events").delete().eq("user_id", uid).execute()
    records = [{"user_id": uid, "account_id": str(acc_id), "username": u, "status": "farming"}
               for acc_id, u in farm_entries]
    for i in range(0, len(records), 200):
        if records[i:i+200]:
            _db().table("autopilot_queue").insert(records[i:i+200]).execute()

    _upsert("autopilot_config", uid, {"running": 1, "started_at": _now_iso(), "trades_done": 0})
    _add_event(uid, "started")
    return jsonify({"queued": len(farm_entries)})


@app.route("/api/autopilot/stop", methods=["POST"])
def stop_autopilot():
    uid, err = _auth()
    if err:
        return jsonify(err[0]), err[1]
    ao_key = _get_panel(uid)
    cfg    = _ap_cfg(uid)
    if ao_key:
        r = _db().table("autopilot_queue").select("username").eq("user_id", uid).execute()
        usernames = [row["username"] for row in (r.data or []) if row.get("username")]
        if usernames:
            _set_enabled(ao_key, usernames, False)
        if cfg and cfg.get("main_account"):
            _set_enabled(ao_key, [cfg["main_account"]], False)
    _db().table("autopilot_queue").delete().eq("user_id", uid).execute()
    _upsert("autopilot_config", uid, {"running": 0})
    _add_event(uid, "stopped")
    return jsonify({"ok": True})
