import asyncio
import logging
logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s")

from datetime import datetime
from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import TelegramObject

import os
from config import BOT_TOKEN, OWNER_ID, ACCOUNTSOPS_KEY, ZP_KEY
from database import (
    init_db,
    save_panel, save_zp_key,
    get_users_with_alerts, update_alert_notified, set_alert_triggered,
    get_users_due_for_auto_unlock, update_auto_unlock_last_run,
    get_all_users_with_zp_jobs,
    get_zp_job, save_zp_job, clear_zp_job,
    get_users_with_autopilot_running,
    get_autopilot_config, set_autopilot_running, set_autopilot_last_checked,
    get_autopilot_farming_entries, get_autopilot_trading_entries,
    get_autopilot_trading_count,
    increment_autopilot_trades_done,
    get_autopilot_stuck_entries,
    set_autopilot_entry_status,
    get_autopilot_pets,
    add_autopilot_event,
    save_autopilot_ready_count,
    get_autopilot_queue_usernames,
    add_autopilot_queue,
    get_users_due_for_autoswap,
    get_autoswap_config as get_autoswap_cfg,
    get_users_due_for_deviceswap,
)
from handlers import start
from handlers import faceunlock
from handlers import autopilot
from handlers import autoswap
from handlers import deviceswap
from handlers.start import build_stats_text
from keyboards import stats_kb
from state_cache import get_all_stats_msgs, clear_stats_msg
from api.accountsops import (
    get_dashboard, get_face_accounts,
    get_account_pets, get_pets_batch,
    get_trackstats_accounts,
    set_accounts_enabled, set_accounts_config,
    get_usernames_by_tag,
)
from api.faceunlock import submit_job, get_status


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

        update_auto_unlock_last_run(user_id)

        job_id = get_zp_job(user_id)
        if job_id:
            ok_s, st, _ = await get_status(zp_key, job_id)
            if ok_s and st.get("status") in ("pending", "processing"):
                continue

        ok, accounts, err = await get_face_accounts(ao_key)
        if not ok or not accounts:
            continue

        ok2, result, err2 = await submit_job(zp_key, "\n".join(accounts))
        if not ok2:
            if "активная задача" in err2 and isinstance(result, dict):
                existing = result.get("existing_job_id")
                if existing:
                    save_zp_job(user_id, existing)
            logging.warning("Auto-unlock submit user=%s: %s", user_id, err2)
            continue

        job_id = result.get("job_id")
        if job_id:
            save_zp_job(user_id, job_id)
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


async def poll_job_completion(bot: Bot):
    for user_id, zp_key, job_id in get_all_users_with_zp_jobs():
        ok_s, st, err_s = await get_status(zp_key, job_id)
        if not ok_s:
            if err_s == "not_found":
                clear_zp_job(user_id)
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


async def _process_one_autopilot(bot: Bot, user_id: int, ao_key: str):
    cfg      = get_autopilot_config(user_id)
    pet_rows = get_autopilot_pets(user_id)
    if not cfg or not cfg["main_account"] or not pet_rows:
        set_autopilot_running(user_id, False)
        return

    # pet_rows = (id, pet_id, min_count)
    pet_thresholds  = {pid: min_count for _, pid, min_count in pet_rows}
    pet_ids_set     = set(pet_thresholds.keys())
    trade_config_id = cfg.get("config_id")
    farm_config_id  = cfg.get("farm_config_id")
    max_traders_per_server = cfg.get("batch_size") or 10

    # Get live active accounts from dashboard — used to filter stuck detection
    ok_d, dash, _ = await get_dashboard(ao_key)
    active_usernames: set[str] = set()
    if ok_d:
        active_usernames = {
            a["username"].lower()
            for a in dash.get("active_accounts", [])
            if a.get("username")
        }

    # Check trading accounts — did they trade the pet?
    for entry_id, acc_id, username in get_autopilot_trading_entries(user_id):
        ok, pets, _ = await get_account_pets(ao_key, acc_id)
        if not ok:
            continue
        if not any(p.get("pet_kind") in pet_ids_set for p in pets):
            if farm_config_id:
                await set_accounts_config(ao_key, [username], farm_config_id)
            await set_accounts_enabled(ao_key, [username], False)
            await set_accounts_enabled(ao_key, [username], True)
            set_autopilot_entry_status(entry_id, "farming")
            increment_autopilot_trades_done(user_id)
            add_autopilot_event(user_id, "trade_complete", username)

    # Check stuck trading accounts — only flag if account is confirmed active in game
    # (filters out accounts still in queue/joining phase)
    stuck_timeout = cfg.get("stuck_timeout") or 10
    stuck_raw = get_autopilot_stuck_entries(user_id, stuck_timeout * 60)
    stuck = [
        (eid, aid, u) for eid, aid, u in stuck_raw
        if not active_usernames or u.lower() in active_usernames
    ]
    if stuck:
        stuck_usernames = [username for _, _, username in stuck]
        for entry_id, _, username in stuck:
            if farm_config_id:
                await set_accounts_config(ao_key, [username], farm_config_id)
            await set_accounts_enabled(ao_key, [username], False)
            await set_accounts_enabled(ao_key, [username], True)
            set_autopilot_entry_status(entry_id, "farming")
            add_autopilot_event(user_id, "stuck", username)
        try:
            lines = [f"⏰ <b>Авто-пилот</b> — зависшие аккаунты возвращены в фарм\n"]
            lines.append(f"Без передачи пета >{stuck_timeout} мин: <b>{len(stuck_usernames)}</b>")
            for u in stuck_usernames[:10]:
                lines.append(f"• <code>{u}</code>")
            if len(stuck_usernames) > 10:
                lines.append(f"... и ещё {len(stuck_usernames) - 10}")
            await bot.send_message(user_id, "\n".join(lines), parse_mode="HTML")
        except Exception as e:
            logging.error("Stuck notify user=%s: %s", user_id, e)

    # Build fresh username→acc_id map from trackstats (avoids stale stored IDs)
    _, ts_accounts, _ = await get_trackstats_accounts(ao_key)
    username_to_id = {
        (acc.get("username") or acc.get("name", "")).lower(): str(acc.get("id") or "")
        for acc in ts_accounts
        if acc.get("id")
    }

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

    ready_by_pet: dict[str, list] = {}
    for (entry_id, acc_id, username), fresh_id in zip(farming_entries, fresh_ids):
        for p in (pets_map.get(fresh_id) or []):
            kind = p.get("pet_kind")
            if kind in pet_ids_set:
                ready_by_pet.setdefault(kind, []).append((entry_id, acc_id, username))
                break

    save_autopilot_ready_count(user_id, sum(len(v) for v in ready_by_pet.values()))

    current_trading = get_autopilot_trading_count(user_id)
    if current_trading >= max_traders_per_server:
        return

    promoted: set[int] = set()
    for pet_kind, ready_accounts in ready_by_pet.items():
        threshold = pet_thresholds.get(pet_kind, 1)
        if len(ready_accounts) < threshold:
            continue
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
                elapsed = (now - datetime.strptime(last, "%Y-%m-%d %H:%M:%S")).total_seconds()
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
    dp.include_router(start.router)
    asyncio.create_task(alert_loop(bot))
    asyncio.create_task(auto_unlock_loop(bot))
    asyncio.create_task(job_poller_loop(bot))
    asyncio.create_task(stats_refresh_loop(bot))
    asyncio.create_task(autopilot_transfer_loop(bot))
    asyncio.create_task(autoswap_loop(bot))
    asyncio.create_task(deviceswap_loop(bot))
    print("OxySync Bot v2.0.1 запущен ✅")
    try:
        await bot.send_message(OWNER_ID, "✅ <b>OxySync Bot v2.0.1</b> запущен", parse_mode="HTML")
    except Exception:
        pass
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
