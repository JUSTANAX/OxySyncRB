import re
import asyncio
import logging
import time
logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s")

from datetime import datetime

_last_event_id:    dict[int, int]   = {}  # user_id → последний обработанный event id
_account_launch_ts: dict[str, float] = {}  # username.lower() → время account_launch
from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import TelegramObject

import os
from config import BOT_TOKEN, OWNER_ID, ACCOUNTSOPS_KEY, ZP_KEY
from database import (
    init_db,
    get_panel,
    save_panel, save_zp_key,
    save_autopilot_main,
    get_users_with_alerts, update_alert_notified, set_alert_triggered,
    get_users_due_for_auto_unlock, update_auto_unlock_last_run,
    get_all_users_with_zp_jobs,
    get_zp_job, save_zp_job, clear_zp_job,
    get_users_with_autopilot_running,
    get_autopilot_config, set_autopilot_running, set_autopilot_last_checked,
    get_autopilot_farming_entries, get_autopilot_trading_entries,
    get_autopilot_trading_count,
    increment_autopilot_trades_done,
    set_autopilot_entry_status,
    get_autopilot_pets,
    add_autopilot_event,
    save_autopilot_ready_count,
    get_autopilot_queue_usernames,
    add_autopilot_queue,
    get_users_due_for_autoswap,
    get_autoswap_config as get_autoswap_cfg,
    get_users_due_for_deviceswap,
    get_users_due_for_devicetrim,
)
from handlers import start
from handlers import faceunlock
from handlers import autopilot
from handlers import autoswap
from handlers import deviceswap
from handlers import devicetrim
from handlers.start import build_stats_text
from keyboards import stats_kb
from state_cache import get_all_stats_msgs, clear_stats_msg, save_zp_pending, pop_zp_pending
from api.accountsops import (
    get_dashboard, get_face_accounts,
    get_account_pets, get_pets_batch,
    get_trackstats_accounts, get_all_accounts,
    set_accounts_enabled, set_accounts_config,
    get_usernames_by_tag, get_events,
    get_config_by_id, update_config,
    get_account_folders, create_folder, move_accounts_to_folder,
    clear_accounts_status_tags,
)
from api.faceunlock import submit_job, get_status, download_file


class OwnerOnly(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: dict):
        user = data.get("event_from_user")
        if user is None or user.id != OWNER_ID:
            return
        return await handler(event, data)


async def check_alerts(bot: Bot):
    for user_id, api_key, threshold, last_notified, triggered in get_users_with_alerts():
        ok, data, _ = await get_dashboard(api_key)
        if not ok:
            continue

        count = data.get("active_count", 0)

        if count < threshold and not triggered:
            try:
                await bot.send_message(
                    user_id,
                    f"⚠️ <b>OxySync — Уведомление</b>\n\n"
                    f"Активных аккаунтов: <b>{count}</b>\n"
                    f"Порог: {threshold}\n\n"
                    "Проверь ферму!",
                    parse_mode="HTML",
                )
                set_alert_triggered(user_id, True)
                update_alert_notified(user_id)
            except Exception as e:
                logging.error("Alert send user=%s: %s", user_id, e)
        elif count >= threshold and triggered:
            try:
                await bot.send_message(
                    user_id,
                    f"✅ <b>OxySync — Восстановление</b>\n\n"
                    f"Активных аккаунтов: <b>{count}</b>\n"
                    "Ферма работает нормально.",
                    parse_mode="HTML",
                )
                set_alert_triggered(user_id, False)
            except Exception as e:
                logging.error("Alert recovery user=%s: %s", user_id, e)


async def alert_loop(bot: Bot):
    while True:
        await asyncio.sleep(300)
        try:
            await check_alerts(bot)
        except Exception as e:
            logging.error("Alert loop error: %s", e)


async def run_auto_unlock(bot: Bot):
    for user_id, ao_key, zp_key in get_users_due_for_auto_unlock():
        if not zp_key:
            continue

        job_id = get_zp_job(user_id)
        if job_id:
            ok_s, st, _ = await get_status(zp_key, job_id)
            if ok_s and st.get("status") in ("pending", "processing"):
                update_auto_unlock_last_run(user_id)
                continue

        ok, accounts, err = await get_face_accounts(ao_key)
        if not ok or not accounts:
            update_auto_unlock_last_run(user_id)
            continue

        ok2, result, err2 = await submit_job(zp_key, "\n".join(accounts))
        if not ok2:
            if "активная задача" in err2 and isinstance(result, dict):
                existing = result.get("existing_job_id")
                if existing:
                    save_zp_job(user_id, existing)
            logging.warning("Auto-unlock submit user=%s: %s", user_id, err2)
            update_auto_unlock_last_run(user_id)
            continue

        update_auto_unlock_last_run(user_id)
        job_id = result.get("job_id")
        if job_id:
            save_zp_job(user_id, job_id)
            submitted = [
                line.split(":")[0].strip()
                for line in accounts
                if ":" in line and not line.startswith("_|WARNING") and line.split(":")[0].strip()
            ]
            if submitted:
                save_zp_pending(job_id, submitted)
            paid = result.get("paid_accounts_count", 0)
            est  = result.get("estimated_cost", 0.0)
            try:
                await bot.send_message(
                    user_id,
                    f"🔓 <b>Auto-Unlock-Face</b> — цикл запущен\n\n"
                    f"Аккаунтов: <b>{len(accounts)}</b>  |  "
                    f"Платных: {paid} (~${est:.2f})",
                    parse_mode="HTML",
                )
            except Exception as e:
                logging.error("Auto-unlock notify user=%s: %s", user_id, e)


async def auto_unlock_loop(bot: Bot):
    while True:
        await asyncio.sleep(1800)
        try:
            await run_auto_unlock(bot)
        except Exception as e:
            logging.error("Auto-unlock loop error: %s", e)


_NO_DEVICE_FOLDER = "No Device"
_ZP_SUCCESS_KEYWORDS = ("success", "unlock", "valid", "good", "working")


async def _get_or_create_no_device_folder(ao_key: str) -> int | None:
    ok, folders, _ = await get_account_folders(ao_key)
    if ok:
        folder = next((f for f in folders if f.get("name") == _NO_DEVICE_FOLDER), None)
        if folder:
            return folder["id"]
    ok_cr, data, _ = await create_folder(ao_key, _NO_DEVICE_FOLDER)
    return data.get("id") if ok_cr else None


async def _parse_zp_success_usernames(zp_key: str, job_id: str, result_files: list[str]) -> list[str]:
    """Download ZeroPoint success result file and extract usernames."""
    success_file = next(
        (f for f in result_files if any(kw in f.lower() for kw in _ZP_SUCCESS_KEYWORDS)),
        None,
    )
    if not success_file:
        return []
    ok, raw, _ = await download_file(zp_key, job_id, success_file)
    if not ok or not raw:
        return []
    usernames: list[str] = []
    for line in raw.decode("utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(":")
        if len(parts) >= 3:
            username = parts[0].strip()
            if username and not username.startswith("_|"):
                usernames.append(username)
    return usernames


async def poll_job_completion(bot: Bot):
    for user_id, zp_key, job_id in get_all_users_with_zp_jobs():
        ok_s, st, err_s = await get_status(zp_key, job_id)
        if not ok_s:
            if err_s == "not_found":
                clear_zp_job(user_id)
                pop_zp_pending(job_id)
            continue

        status = st.get("status")
        if status not in ("completed", "failed", "cancelled"):
            continue

        icon_map  = {"completed": "✅", "failed": "❌", "cancelled": "🚫"}
        label_map = {"completed": "завершена", "failed": "ошибка", "cancelled": "отменена"}
        icon  = icon_map.get(status, "❓")
        label = label_map.get(status, status)

        total      = st.get("total_accounts", 0)
        successful = st.get("successful", 0)
        failed     = st.get("failed", 0)
        other      = st.get("other_failed", 0)

        lines = [f"🔓 <b>Auto-Unlock-Face</b> — {label} {icon}", ""]
        lines.append(f"📊 Всего аккаунтов: <b>{total}</b>")
        lines.append(f"✅ Разблокировано: <b>{successful}</b>")
        lines.append(f"❌ Face ID не снят: <b>{failed}</b>")
        if other:
            lines.append(f"⚠️ Прочие ошибки: <b>{other}</b>")

        pending_usernames = pop_zp_pending(job_id)

        if status == "completed" and successful > 0:
            ao_key = get_panel(user_id)
            if ao_key:
                result_files = st.get("result_files") or []
                usernames_to_move = await _parse_zp_success_usernames(zp_key, job_id, result_files)
                if not usernames_to_move:
                    usernames_to_move = pending_usernames
                if usernames_to_move:
                    folder_id = await _get_or_create_no_device_folder(ao_key)
                    if folder_id:
                        ok_mv, _, err_mv = await move_accounts_to_folder(ao_key, usernames_to_move, folder_id)
                        if ok_mv:
                            lines.append(f"📂 Перенесено в No Device: <b>{len(usernames_to_move)}</b>")
                            await clear_accounts_status_tags(ao_key, usernames_to_move)
                        else:
                            logging.error("Move to No Device user=%s: %s", user_id, err_mv)

        try:
            await bot.send_message(user_id, "\n".join(lines), parse_mode="HTML")
        except Exception as e:
            logging.error("Job poller notify user=%s: %s", user_id, e)

        clear_zp_job(user_id)


async def job_poller_loop(bot: Bot):
    while True:
        await asyncio.sleep(30)
        try:
            await poll_job_completion(bot)
        except Exception as e:
            logging.error("Job poller loop error: %s", e)


async def stats_refresh_loop(bot: Bot):
    while True:
        await asyncio.sleep(300)
        for user_id, chat_id, message_id in get_all_stats_msgs():
            try:
                text = await asyncio.wait_for(build_stats_text(user_id), timeout=40.0)
                await bot.edit_message_text(
                    text,
                    chat_id=chat_id,
                    message_id=message_id,
                    parse_mode="HTML",
                    reply_markup=stats_kb(),
                )
            except TelegramBadRequest as e:
                if "message is not modified" not in str(e):
                    clear_stats_msg(user_id)
            except Exception:
                clear_stats_msg(user_id)


def _pet_kind_matches(kind: str, pet_ids_set: set[str]) -> bool:
    if not kind:
        return False
    if kind in pet_ids_set:
        return True
    return any(kind.endswith(f"_{pid}") for pid in pet_ids_set)


def _find_matching_pid(kind: str, pet_ids_set: set[str]) -> str | None:
    """Return the configured pet_id that kind matches, or None."""
    if not kind:
        return None
    if kind in pet_ids_set:
        return kind
    for pid in pet_ids_set:
        if kind.endswith(f"_{pid}"):
            return pid
    return None


def _patch_usernames(script: str, usernames: list[str]) -> str:
    lua_list = "{" + ", ".join(f'"{u}"' for u in usernames) + "}"
    return re.sub(r'(Usernames\s*=\s*)\{[^}]*\}', lambda m: m.group(1) + lua_list, script)


async def check_and_swap_main(bot: Bot, user_id: int, ao_key: str):
    cfg = get_autopilot_config(user_id)
    if not cfg or not cfg.get("main_account") or not cfg.get("config_id"):
        return
    if not cfg.get("main_config_id"):
        return

    main_lower      = cfg["main_account"].lower()
    threshold       = cfg.get("potion_threshold") or 8
    trade_config_id = cfg["config_id"]
    main_config_id  = cfg["main_config_id"]
    farm_config_id  = cfg.get("farm_config_id")

    ok, accounts, _ = await get_trackstats_accounts(ao_key)
    if not ok:
        return

    main_potions = next(
        (acc.get("potions") or 0 for acc in accounts
         if (acc.get("username") or "").lower() == main_lower),
        None,
    )
    if main_potions is None or main_potions >= threshold:
        return

    candidates = [
        (acc.get("potions") or 0, acc.get("username"))
        for acc in accounts
        if (acc.get("username") or "").lower() != main_lower
        and "status:valid" in (acc.get("tags") or [])
        and acc.get("device_id")
        and acc.get("folder_section") == "input"
    ]
    if not candidates:
        return

    candidates.sort(reverse=True)
    new_main_potions, new_main = candidates[0]

    ok_cfg, config_data, err_cfg = await get_config_by_id(ao_key, trade_config_id)
    if not ok_cfg:
        logging.error("Main swap get config user=%s: %s", user_id, err_cfg)
        return

    scripts = config_data.get("scripts") or []
    config_data["scripts"] = [_patch_usernames(s, [new_main]) for s in scripts]

    ok_put, _, err_put = await update_config(ao_key, trade_config_id, config_data)
    if not ok_put:
        logging.error("Main swap put config user=%s: %s", user_id, err_put)
        return

    old_main = cfg["main_account"]

    if farm_config_id:
        await set_accounts_config(ao_key, [old_main], farm_config_id)
    await set_accounts_enabled(ao_key, [old_main], True)

    await set_accounts_config(ao_key, [new_main], main_config_id)
    await set_accounts_enabled(ao_key, [new_main], True)

    save_autopilot_main(user_id, new_main)

    try:
        await bot.send_message(
            user_id,
            f"🔄 <b>Авто-пилот</b> — смена основного аккаунта\n\n"
            f"Старый: <code>{old_main}</code> — {main_potions} зелий\n"
            f"Новый: <code>{new_main}</code> — {new_main_potions} зелий",
            parse_mode="HTML",
        )
    except Exception as e:
        logging.error("Main swap notify user=%s: %s", user_id, e)


async def main_swap_loop(bot: Bot):
    while True:
        await asyncio.sleep(300)
        for user_id, ao_key in get_users_with_autopilot_running():
            try:
                await check_and_swap_main(bot, user_id, ao_key)
            except Exception as e:
                logging.error("Main swap loop user=%s: %s", user_id, e)


async def _process_one_autopilot(bot: Bot, user_id: int, ao_key: str):
    cfg      = get_autopilot_config(user_id)
    pet_rows = get_autopilot_pets(user_id)
    if not cfg or not cfg["main_account"] or not pet_rows:
        set_autopilot_running(user_id, False)
        return

    # pet_rows = (id, pet_id, min_count, age_min, age_max, type_mask)
    pet_configs: dict[str, dict] = {
        pid: {"min_count": min_count, "age_min": age_min, "age_max": age_max, "type_mask": type_mask}
        for _, pid, min_count, age_min, age_max, type_mask in pet_rows
    }
    pet_ids_set     = set(pet_configs.keys())
    trade_config_id = cfg.get("config_id")
    farm_config_id  = cfg.get("farm_config_id")
    max_traders_per_server = cfg.get("batch_size") or 10

    # Process new events — primary trade detection + launch tracking
    ok_ev, events, _ = await get_events(ao_key, limit=50)
    if ok_ev and events:
        new_events = [e for e in events if e.get("id", 0) > _last_event_id.get(user_id, 0)]
        if new_events:
            _last_event_id[user_id] = max(e["id"] for e in new_events)
            trading_map = {u.lower(): (eid, aid, u) for eid, aid, u in get_autopilot_trading_entries(user_id)}
            farming_set = {u.lower() for _, _, u in get_autopilot_farming_entries(user_id)}
            for event in reversed(new_events):
                uname = (event.get("username") or "").lower()
                kind  = event.get("kind", "")
                msg   = event.get("message", "")
                if kind == "kick" and "All trades completed" in msg and uname in trading_map:
                    eid, aid, orig_u = trading_map.pop(uname)
                    if farm_config_id:
                        await set_accounts_config(ao_key, [orig_u], farm_config_id)
                    await set_accounts_enabled(ao_key, [orig_u], True)
                    set_autopilot_entry_status(eid, "farming")
                    increment_autopilot_trades_done(user_id)
                    add_autopilot_event(user_id, "trade_complete", orig_u)
                elif kind == "account_launch" and uname in farming_set:
                    _account_launch_ts[uname] = time.time()

    # Build fresh username→acc_id map from trackstats (avoids stale stored IDs)
    (_, ts_accounts, _), (_, raw_accounts, _) = await asyncio.gather(
        get_trackstats_accounts(ao_key),
        get_all_accounts(ao_key),
    )
    username_to_id = {
        (acc.get("username") or acc.get("name", "")).lower(): str(acc.get("id") or "")
        for acc in ts_accounts
        if acc.get("id")
    }
    # Only auto-enroll accounts that are assigned to a device
    device_assigned: set[str] = {
        (acc.get("username") or acc.get("name") or "").strip().lower()
        for acc in raw_accounts
        if (acc.get("device_id") or "").strip()
    }
    # Accounts that were disabled by the game script after trading
    disabled_set: set[str] = {
        (acc.get("username") or acc.get("name") or "").strip().lower()
        for acc in raw_accounts
        if not acc.get("enabled", True)
    }

    # Fallback: trade detection for accounts missed by events
    # Primary signal: account was disabled by the game script after trading
    # Secondary signal: inventory no longer contains the target pet
    for entry_id, acc_id, username in get_autopilot_trading_entries(user_id):
        u = username.lower()
        trade_done = False
        if u in disabled_set:
            # Game script disabled the account — trade is complete regardless of stale inventory
            trade_done = True
        else:
            fresh_id = username_to_id.get(u, acc_id)
            ok, pets, _ = await get_account_pets(ao_key, fresh_id)
            if ok and not any(_pet_kind_matches(p.get("pet_kind", ""), pet_ids_set) for p in pets):
                trade_done = True
        if trade_done:
            if farm_config_id:
                await set_accounts_config(ao_key, [username], farm_config_id)
            await set_accounts_enabled(ao_key, [username], True)
            set_autopilot_entry_status(entry_id, "farming")
            increment_autopilot_trades_done(user_id)
            add_autopilot_event(user_id, "trade_complete", username)

    # Auto-enroll newly unblocked accounts not yet in queue
    main_lower = cfg["main_account"].lower()
    queue_usernames = get_autopilot_queue_usernames(user_id)
    dead_set = await get_usernames_by_tag(ao_key, "status:dead")
    new_accounts = []
    for acc in ts_accounts:
        username = acc.get("username") or acc.get("name", "")
        if not username:
            continue
        u = username.lower()
        if u == main_lower or u in dead_set or u in queue_usernames:
            continue
        if u not in device_assigned:
            continue
        acc_id = str(acc.get("id") or "")
        new_accounts.append((acc_id, username))

    if new_accounts:
        new_usernames = [u for _, u in new_accounts]
        if farm_config_id:
            await set_accounts_config(ao_key, new_usernames, farm_config_id)
        await set_accounts_enabled(ao_key, new_usernames, True)
        add_autopilot_queue(user_id, new_accounts, status='farming')
        add_autopilot_event(user_id, "accounts_added", f"+{len(new_accounts)}")
        try:
            await bot.send_message(
                user_id,
                f"🔓 <b>Авто-пилот</b> — добавлено <b>{len(new_accounts)}</b> новых аккаунтов\n\n"
                f"<i>Обнаружены аккаунты не из очереди (разблокированы или новые)</i>",
                parse_mode="HTML",
            )
        except Exception as e:
            logging.error("New accounts notify user=%s: %s", user_id, e)

    # Check farming accounts — fetch all pet inventories in parallel
    farming_entries = get_autopilot_farming_entries(user_id)
    fresh_ids = [username_to_id.get(username.lower(), acc_id)
                 for _, acc_id, username in farming_entries]
    pets_map = await get_pets_batch(ao_key, [fid for fid in fresh_ids if fid])

    now_ts = time.time()
    ready_by_pet: dict[str, list] = {}
    for (entry_id, acc_id, username), fresh_id in zip(farming_entries, fresh_ids):
        launch_ts = _account_launch_ts.get(username.lower(), 0)
        if launch_ts > 0 and now_ts - launch_ts < 60:
            continue
        pet_counts: dict[str, int] = {}
        for p in (pets_map.get(fresh_id) or []):
            kind        = p.get("pet_kind") or ""
            matched_pid = _find_matching_pid(kind, pet_ids_set)
            if matched_pid is None:
                continue
            pcfg    = pet_configs[matched_pid]
            pet_age = p.get("age") or 1
            age_lo  = min(pcfg["age_min"], pcfg["age_max"])
            age_hi  = max(pcfg["age_min"], pcfg["age_max"])
            if not (age_lo <= pet_age <= age_hi):
                continue
            type_bit = 4 if p.get("is_mega") else (2 if p.get("is_neon") else 1)
            if not (pcfg["type_mask"] & type_bit):
                continue
            qty = p.get("quantity") or 1
            pet_counts[matched_pid] = pet_counts.get(matched_pid, 0) + qty
        for pid, count in pet_counts.items():
            if count >= pet_configs[pid]["min_count"]:
                ready_by_pet.setdefault(pid, []).append((entry_id, acc_id, username))

    save_autopilot_ready_count(user_id, sum(len(v) for v in ready_by_pet.values()))

    current_trading = get_autopilot_trading_count(user_id)
    if current_trading >= max_traders_per_server:
        return

    promoted: set[int] = set()
    for pid, ready_accounts in ready_by_pet.items():
        for entry_id, acc_id, username in ready_accounts:
            if entry_id in promoted or current_trading >= max_traders_per_server:
                break
            if trade_config_id:
                await set_accounts_config(ao_key, [username], trade_config_id)
            await set_accounts_enabled(ao_key, [username], False)
            await set_accounts_enabled(ao_key, [username], True)
            set_autopilot_entry_status(entry_id, "trading")
            add_autopilot_event(user_id, "got_pet", username)
            promoted.add(entry_id)
            current_trading += 1


async def run_autopilot_transfer(bot: Bot):
    now = datetime.utcnow()
    for user_id, ao_key in get_users_with_autopilot_running():
        try:
            cfg = get_autopilot_config(user_id)
            interval = (cfg.get("check_interval") or 30) if cfg else 30
            last = (cfg.get("last_checked_at") or "") if cfg else ""
            if last:
                last_clean = last.replace("Z", "").split("+")[0]
                elapsed = (now - datetime.fromisoformat(last_clean)).total_seconds()
                if elapsed < interval:
                    continue
            set_autopilot_last_checked(user_id)
            await _process_one_autopilot(bot, user_id, ao_key)
        except Exception as e:
            logging.error("Autopilot transfer user=%s: %s", user_id, e)


async def autopilot_transfer_loop(bot: Bot):
    while True:
        await asyncio.sleep(5)
        try:
            await run_autopilot_transfer(bot)
        except Exception as e:
            logging.error("Autopilot transfer loop error: %s", e)


async def run_autoswap(bot: Bot):
    from handlers.autoswap import do_sort
    for user_id, ao_key in get_users_due_for_autoswap():
        try:
            stats = await do_sort(ao_key, user_id)
            lines = ["📂 <b>Sorting</b> — авто-сортировка выполнена", ""]
            lines.append(f"📱 Девайсов: <b>{stats['devices']}</b>")
            if stats["created"]:
                lines.append(f"🆕 Папок создано: <b>{stats['created']}</b>")
            lines.append(f"✅ Живых (девайсы → input): <b>{stats['live']}</b>")
            lines.append(f"💀 Мёртвых (Dead & Face → input): <b>{stats['dead']}</b>")
            lines.append(f"🔒 Face-lock (Dead & Face → output): <b>{stats['face']}</b>")
            await bot.send_message(user_id, "\n".join(lines), parse_mode="HTML")
        except Exception as e:
            logging.error("AutoSwap run user=%s: %s", user_id, e)


async def autoswap_loop(bot: Bot):
    while True:
        await asyncio.sleep(1800)
        try:
            await run_autoswap(bot)
        except Exception as e:
            logging.error("AutoSwap loop error: %s", e)


async def run_deviceswap(bot: Bot):
    from handlers.deviceswap import do_device_swap
    for user_id, ao_key in get_users_due_for_deviceswap():
        try:
            stats = await do_device_swap(ao_key, user_id)
            if stats["devices"] == 0:
                continue
            lines = ["🔄 <b>AutoSwap</b> — авто-замена выполнена", ""]
            lines.append(f"📱 Девайсов обработано: <b>{stats['devices']}</b>")
            lines.append(f"✅ Заменено аккаунтов: <b>{stats['replaced']}</b>")
            if stats["no_reserve"]:
                lines.append(f"⚠️ Не хватило рабочих аккаунтов: <b>{stats['no_reserve']}</b>")
            await bot.send_message(user_id, "\n".join(lines), parse_mode="HTML")
        except Exception as e:
            logging.error("DeviceSwap run user=%s: %s", user_id, e)


async def deviceswap_loop(bot: Bot):
    while True:
        await asyncio.sleep(1800)
        try:
            await run_deviceswap(bot)
        except Exception as e:
            logging.error("DeviceSwap loop error: %s", e)


async def run_devicetrim(bot: Bot):
    from handlers.devicetrim import do_trim
    for user_id, ao_key, max_per_device in get_users_due_for_devicetrim():
        try:
            stats = await do_trim(ao_key, user_id, max_per_device)
            if stats["devices"] == 0:
                continue
            lines = ["✂️ <b>Trim</b> — авто-запуск выполнен", ""]
            lines.append(f"📱 Девайсов обработано: <b>{stats['devices']}</b>")
            if stats["trimmed"]:
                lines.append(f"📤 Убрано в No Device: <b>{stats['trimmed']}</b>")
            if stats["filled"]:
                lines.append(f"📥 Добавлено из No Device: <b>{stats['filled']}</b>")
            await bot.send_message(user_id, "\n".join(lines), parse_mode="HTML")
        except Exception as e:
            logging.error("DeviceTrim run user=%s: %s", user_id, e)


async def devicetrim_loop(bot: Bot):
    while True:
        await asyncio.sleep(1800)
        try:
            await run_devicetrim(bot)
        except Exception as e:
            logging.error("DeviceTrim loop error: %s", e)


async def main():
    init_db()
    if ACCOUNTSOPS_KEY:
        save_panel(OWNER_ID, ACCOUNTSOPS_KEY)
    if ZP_KEY:
        save_zp_key(OWNER_ID, ZP_KEY)
    bot = Bot(token=BOT_TOKEN)
    dp  = Dispatcher(storage=MemoryStorage())

    @dp.error()
    async def error_handler(event):
        exc = event.exception
        if isinstance(exc, TelegramBadRequest) and (
            "query is too old"        in str(exc) or
            "message is not modified" in str(exc)
        ):
            return
        logging.error("Unhandled: %s", exc, exc_info=exc)

    dp.update.middleware(OwnerOnly())
    dp.include_router(faceunlock.router)
    dp.include_router(autopilot.router)
    dp.include_router(autoswap.router)
    dp.include_router(deviceswap.router)
    dp.include_router(devicetrim.router)
    dp.include_router(start.router)
    asyncio.create_task(alert_loop(bot))
    asyncio.create_task(auto_unlock_loop(bot))
    asyncio.create_task(job_poller_loop(bot))
    asyncio.create_task(stats_refresh_loop(bot))
    asyncio.create_task(autopilot_transfer_loop(bot))
    asyncio.create_task(main_swap_loop(bot))
    asyncio.create_task(autoswap_loop(bot))
    asyncio.create_task(deviceswap_loop(bot))
    asyncio.create_task(devicetrim_loop(bot))
    print("OxySync Bot v2.3.13 запущен ✅")
    try:
        await bot.send_message(OWNER_ID, "✅ <b>OxySync Bot v2.3.13</b> запущен", parse_mode="HTML")
    except Exception:
        pass
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
