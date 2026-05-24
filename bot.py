import asyncio
import logging
import random
logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s")

from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import TelegramObject

from config import BOT_TOKEN, OWNER_ID
from database import (
    init_db,
    get_users_with_alerts, update_alert_notified,
    get_users_with_auto_unlock,
    get_zp_job, save_zp_job, clear_zp_job,
    is_zp_job_notified, set_zp_job_notified,
    get_users_with_auto_enable_pet,
    get_auto_enable_pet_notified, set_auto_enable_pet_notified,
)
from handlers import start
from handlers import faceunlock
from api.accountsops import get_dashboard, get_face_accounts, get_accounts_with_pet, enable_accounts
from api.faceunlock import submit_job, get_status


class OwnerOnly(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: dict):
        user = data.get("event_from_user")
        if user is None or user.id != OWNER_ID:
            return
        return await handler(event, data)


async def check_alerts(bot: Bot):
    for user_id, api_key, threshold, last_notified in get_users_with_alerts():
        if last_notified:
            try:
                last = datetime.fromisoformat(last_notified)
                if datetime.utcnow() - last < timedelta(minutes=30):
                    continue
            except Exception:
                pass

        ok, data, _ = await get_dashboard(api_key)
        if not ok:
            continue

        count = data.get("active_count", 0)
        if count < threshold:
            try:
                await bot.send_message(
                    user_id,
                    f"⚠️ <b>OxySync — Уведомление</b>\n\n"
                    f"Активных аккаунтов: <b>{count}</b>\n"
                    f"Порог: {threshold}\n\n"
                    "Проверь ферму!",
                    parse_mode="HTML",
                )
                update_alert_notified(user_id)
            except Exception as e:
                logging.error("Alert send user=%s: %s", user_id, e)


async def alert_loop(bot: Bot):
    while True:
        await asyncio.sleep(300)
        try:
            await check_alerts(bot)
        except Exception as e:
            logging.error("Alert loop error: %s", e)


async def run_auto_unlock(bot: Bot):
    for user_id, ao_key, zp_key in get_users_with_auto_unlock():
        if not zp_key:
            continue
        # Skip if there's already an active job
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
        delay = random.uniform(2 * 3600, 4 * 3600)
        await asyncio.sleep(delay)
        try:
            await run_auto_unlock(bot)
        except Exception as e:
            logging.error("Auto-unlock loop error: %s", e)


async def poll_job_completion(bot: Bot):
    for user_id, ao_key, zp_key in get_users_with_auto_unlock():
        if not zp_key:
            continue
        job_id = get_zp_job(user_id)
        if not job_id:
            continue
        if is_zp_job_notified(user_id):
            continue

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

        try:
            await bot.send_message(
                user_id,
                f"🔓 <b>Auto-Unlock-Face</b> — задача {label} {icon}\n\n"
                f"Всего: {total}  |  ✅ {successful}  |  ❌ {failed}",
                parse_mode="HTML",
            )
            set_zp_job_notified(user_id)
        except Exception as e:
            logging.error("Job poller notify user=%s: %s", user_id, e)


async def job_poller_loop(bot: Bot):
    while True:
        await asyncio.sleep(30)
        try:
            await poll_job_completion(bot)
        except Exception as e:
            logging.error("Job poller loop error: %s", e)


DUCKY_PET = "soggy_spring_2026_strawberry_shortcake_ducky"
DUCKY_NOTIFY_COOLDOWN = 30 * 60  # seconds


async def run_auto_enable_pet(bot: Bot):
    for user_id, ao_key in get_users_with_auto_enable_pet():
        ok, usernames, err = await get_accounts_with_pet(ao_key, DUCKY_PET)
        if not ok:
            logging.warning("Auto-enable-pet fetch user=%s: %s", user_id, err)
            continue
        if not usernames:
            continue

        ok2, _, err2 = await enable_accounts(ao_key, usernames)
        if not ok2:
            logging.warning("Auto-enable-pet enable user=%s: %s", user_id, err2)
            continue

        last = get_auto_enable_pet_notified(user_id)
        if last:
            try:
                elapsed = (datetime.utcnow() - datetime.fromisoformat(last)).total_seconds()
                if elapsed < DUCKY_NOTIFY_COOLDOWN:
                    continue
            except Exception:
                pass

        try:
            await bot.send_message(
                user_id,
                f"🦆 <b>Auto-Enable-Pet</b>\n\n"
                f"Найдено аккаунтов с питомцем: <b>{len(usernames)}</b>\n"
                f"Статус включён ✅",
                parse_mode="HTML",
            )
            set_auto_enable_pet_notified(user_id)
        except Exception as e:
            logging.error("Auto-enable-pet notify user=%s: %s", user_id, e)


async def auto_enable_pet_loop(bot: Bot):
    while True:
        await asyncio.sleep(600)  # каждые 10 минут
        try:
            await run_auto_enable_pet(bot)
        except Exception as e:
            logging.error("Auto-enable-pet loop error: %s", e)


async def main():
    init_db()
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
    dp.include_router(start.router)
    asyncio.create_task(alert_loop(bot))
    asyncio.create_task(auto_unlock_loop(bot))
    asyncio.create_task(job_poller_loop(bot))
    asyncio.create_task(auto_enable_pet_loop(bot))
    print("OxySync Bot запущен ✅")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
