import asyncio
import logging
logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s")

from datetime import datetime
from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import TelegramObject

from config import BOT_TOKEN, OWNER_ID, ACCOUNTSOPS_KEY, ZP_KEY
from database import (
    init_db,
    save_panel, save_zp_key,
    get_users_with_alerts, update_alert_notified, set_alert_triggered,
    get_users_due_for_auto_unlock, update_auto_unlock_last_run,
    get_all_users_with_zp_jobs,
    get_zp_job, save_zp_job, clear_zp_job,
    get_users_with_autopilot_running,
    get_autopilot_config, set_autopilot_running,
    get_autopilot_active_entries, get_autopilot_pending_entries,
    get_autopilot_active_count, get_autopilot_done_count,
    set_autopilot_entry_status,
)
from handlers import start
from handlers import faceunlock
from handlers import autopilot
from handlers.start import build_stats_text
from keyboards import stats_kb
from state_cache import get_all_stats_msgs, clear_stats_msg
from api.accountsops import (
    get_dashboard, get_face_accounts,
    get_account_pets, set_accounts_enabled,
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
    cfg = get_autopilot_config(user_id)
    if not cfg or not cfg["main_account"] or not cfg["pet_id"]:
        set_autopilot_running(user_id, False)
        return

    pet_id       = cfg["pet_id"]
    main_account = cfg["main_account"]
    active       = get_autopilot_active_entries(user_id)

    done_ids:       list[int] = []
    done_usernames: list[str] = []
    for entry_id, acc_id, username in active:
        ok, pets, _ = await get_account_pets(ao_key, acc_id)
        if ok:
            has_pet = any(p.get("pet_kind") == pet_id for p in pets)
            if not has_pet:
                done_ids.append(entry_id)
                done_usernames.append(username)

    if done_usernames:
        await set_accounts_enabled(ao_key, done_usernames, False)
        for eid in done_ids:
            set_autopilot_entry_status(eid, "done")

    slots = max(0, 10 - get_autopilot_active_count(user_id))
    if slots > 0:
        pending = get_autopilot_pending_entries(user_id)[:slots]
        if pending:
            new_users = [u for _, _, u in pending]
            ok2, _, _ = await set_accounts_enabled(ao_key, new_users, True)
            if ok2:
                for eid, _, _ in pending:
                    set_autopilot_entry_status(eid, "active")

    if get_autopilot_active_count(user_id) == 0 and not get_autopilot_pending_entries(user_id):
        await set_accounts_enabled(ao_key, [main_account], False)
        set_autopilot_running(user_id, False)
        done_count  = get_autopilot_done_count(user_id)
        started_at  = cfg.get("started_at")
        elapsed_str = ""
        if started_at:
            try:
                delta   = datetime.utcnow() - datetime.strptime(started_at, "%Y-%m-%d %H:%M:%S")
                seconds = int(delta.total_seconds())
                if seconds < 60:
                    elapsed_str = f"{seconds}с"
                elif seconds < 3600:
                    m, s = divmod(seconds, 60)
                    elapsed_str = f"{m} мин {s}с" if s else f"{m} мин"
                else:
                    h, rem = divmod(seconds, 3600)
                    m = rem // 60
                    elapsed_str = f"{h} ч {m} мин" if m else f"{h} ч"
            except Exception:
                pass
        try:
            await bot.send_message(
                user_id,
                f"🤖 <b>Авто-пилот</b> — завершён ✅\n\n"
                f"Питомцев передано: <b>{done_count}</b>\n"
                f"Основной аккаунт отключён."
                + (f"\n⏱ Время: <b>{elapsed_str}</b>" if elapsed_str else ""),
                parse_mode="HTML",
            )
        except Exception as e:
            logging.error("Autopilot done notify user=%s: %s", user_id, e)


async def run_autopilot_transfer(bot: Bot):
    for user_id, ao_key in get_users_with_autopilot_running():
        try:
            await _process_one_autopilot(bot, user_id, ao_key)
        except Exception as e:
            logging.error("Autopilot transfer user=%s: %s", user_id, e)


async def autopilot_transfer_loop(bot: Bot):
    while True:
        await asyncio.sleep(30)
        try:
            await run_autopilot_transfer(bot)
        except Exception as e:
            logging.error("Autopilot transfer loop error: %s", e)


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
    dp.include_router(start.router)
    asyncio.create_task(alert_loop(bot))
    asyncio.create_task(auto_unlock_loop(bot))
    asyncio.create_task(job_poller_loop(bot))
    asyncio.create_task(stats_refresh_loop(bot))
    asyncio.create_task(autopilot_transfer_loop(bot))
    print("OxySync Bot v1.4.5 запущен ✅")
    try:
        await bot.send_message(OWNER_ID, "✅ <b>OxySync Bot v1.4.5</b> запущен", parse_mode="HTML")
    except Exception:
        pass
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
