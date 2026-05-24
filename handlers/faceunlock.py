from aiogram import Router, Bot
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest

from api.faceunlock import get_balance, submit_job, get_status, cancel_job, download_file
from api.accountsops import get_face_accounts
from database import (
    get_panel,
    get_zp_key, save_zp_key,
    get_zp_job, save_zp_job, clear_zp_job,
    get_auto_unlock, set_auto_unlock,
    get_auto_unlock_interval, set_auto_unlock_interval,
    get_auto_enable_pet, set_auto_enable_pet,
)
from keyboards import automation_kb, fu_no_key_kb, fu_kb, fu_confirm_kb, cancel_to_fu_kb, auto_enable_pet_kb
from state_cache import clear_stats_msg

_INTERVAL_PRESETS = [1.0, 2.0, 3.0, 4.0, 6.0]

router = Router()


class FUStates(StatesGroup):
    waiting_key    = State()
    confirming_run = State()


# ─── Построение страницы ──────────────────────────────────────────────────────

async def _build_fu_page(user_id: int) -> tuple[str, any]:
    zp_key = get_zp_key(user_id)
    if not zp_key:
        return (
            "🔓 <b>Auto-Unlock-Face</b>\n\n🔑 API ключ ZeroPoint не подключён.",
            fu_no_key_kb(),
        )

    lines = ["🔓 <b>Auto-Unlock-Face</b>"]

    ok_b, bal, err_b = await get_balance(zp_key)
    if ok_b:
        eff = bal.get("effective", 0)
        res = bal.get("reserved", 0)
        line = f"💰 Баланс: <b>${eff:.2f}</b>"
        if res > 0:
            line += f"  (резерв: ${res:.2f})"
        lines.append("")
        lines.append(line)
    else:
        lines.append(f"\n⚠️ Баланс: {err_b}")

    job_id       = get_zp_job(user_id)
    job_status   = None
    result_files: list = []

    if job_id:
        ok_s, st, err_s = await get_status(zp_key, job_id)
        if ok_s:
            job_status   = st.get("status", "unknown")
            total        = st.get("total_accounts", 0)
            processed    = st.get("processed", 0)
            successful   = st.get("successful", 0)
            failed       = st.get("failed", 0)
            other_failed = st.get("other_failed", 0)
            result_files = st.get("result_files") or []

            pct = int(processed / total * 100) if total > 0 else 0
            icon_map = {
                "pending":    "⏳",
                "processing": "⚙️",
                "completed":  "✅",
                "failed":     "❌",
                "cancelled":  "🚫",
            }
            label_map = {
                "pending":    "в очереди",
                "processing": "обрабатывается",
                "completed":  "завершена",
                "failed":     "ошибка",
                "cancelled":  "отменена",
            }
            icon  = icon_map.get(job_status, "❓")
            label = label_map.get(job_status, job_status)
            short = job_id[:8] + "..."

            lines.append("")
            lines.append(f"{icon} Задача <code>{short}</code> — {label}")
            lines.append(f"   {processed}/{total} ({pct}%)")
            lines.append(f"   ✅ Разблокировано: {successful}")
            if failed:
                lines.append(f"   ❌ Face ID: {failed}")
            if other_failed:
                lines.append(f"   ⚠️ Прочие: {other_failed}")
        elif err_s == "not_found":
            clear_zp_job(user_id)
            lines.append("\n📭 Нет активных задач")
        else:
            lines.append(f"\n⚠️ Статус: {err_s}")
    else:
        lines.append("\n📭 Нет активных задач")

    auto_enabled = get_auto_unlock(user_id)
    interval = get_auto_unlock_interval(user_id)
    return "\n".join(lines), fu_kb(job_status, result_files, auto_enabled, interval)


async def _show_fu(target, user_id: int, edit: bool = False):
    text, kb = await _build_fu_page(user_id)
    try:
        if edit and hasattr(target, "edit_text"):
            await target.edit_text(text, parse_mode="HTML", reply_markup=kb)
        else:
            await target.answer(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


# ─── Автоматизация — меню ─────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "automation")
async def open_automation(callback: CallbackQuery, state: FSMContext):
    clear_stats_msg(callback.from_user.id)
    await state.clear()
    await callback.message.edit_text(
        "🤖 <b>Автоматизация</b>\n\nВыбери инструмент:",
        parse_mode="HTML",
        reply_markup=automation_kb(),
    )
    await callback.answer()


# ─── Face Unlock — открыть страницу ──────────────────────────────────────────

@router.callback_query(lambda c: c.data == "face_unlock")
async def open_face_unlock(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer("⏳")
    await _show_fu(callback.message, callback.from_user.id, edit=True)


@router.callback_query(lambda c: c.data == "fu_refresh")
async def fu_refresh(callback: CallbackQuery):
    await callback.answer("🔄 Обновляю...")
    await _show_fu(callback.message, callback.from_user.id, edit=True)


# ─── Face Unlock — ключ ZeroPoint ────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "fu_set_key")
async def fu_set_key_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(FUStates.waiting_key)
    await callback.message.edit_text(
        "🔑 Отправь API ключ <b>ZeroPoint Face Unlock</b>:\n\n"
        "<i>Начинается с <code>ZP_FaceUnlock_</code></i>",
        parse_mode="HTML",
        reply_markup=cancel_to_fu_kb(),
    )
    await callback.answer()


@router.message(FUStates.waiting_key)
async def fu_receive_key(message: Message, state: FSMContext):
    api_key = message.text.strip()
    await message.delete()
    msg = await message.answer("🔄 Проверяю ключ...")

    ok, _, err = await get_balance(api_key)
    if not ok:
        await msg.edit_text(
            f"❌ <b>Ошибка:</b> {err}\n\nПопробуй ещё раз:",
            parse_mode="HTML",
            reply_markup=cancel_to_fu_kb(),
        )
        return

    save_zp_key(message.from_user.id, api_key)
    await state.clear()
    await msg.delete()
    await _show_fu(message, message.from_user.id)


# ─── Face Unlock — запуск авто-разблокировки ─────────────────────────────────

@router.callback_query(lambda c: c.data == "fu_run")
async def fu_run(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback.from_user.id

    ao_key = get_panel(user_id)
    if not ao_key:
        await callback.answer(
            "❌ AccountsOps не подключён.\nПерейди в Настройки → API-ключи.",
            show_alert=True,
        )
        return

    zp_key = get_zp_key(user_id)
    if not zp_key:
        await callback.answer(
            "❌ ZeroPoint API ключ не подключён.\nНажми 🔑 Ключ ZP.",
            show_alert=True,
        )
        return

    job_id = get_zp_job(user_id)
    if job_id:
        ok_s, st, _ = await get_status(zp_key, job_id)
        if ok_s and st.get("status") in ("pending", "processing"):
            await callback.answer("Уже есть активная задача!", show_alert=True)
            return

    await callback.answer("⏳")
    await callback.message.edit_text(
        "🔓 <b>Auto-Unlock-Face</b>\n\n"
        "⏳ Ищу аккаунты с тегом <code>status:face</code>...",
        parse_mode="HTML",
    )

    ok, accounts, err = await get_face_accounts(ao_key)
    if not ok:
        await callback.message.edit_text(
            f"🔓 <b>Auto-Unlock-Face</b>\n\n❌ Ошибка AccountsOps: {err}",
            parse_mode="HTML",
            reply_markup=fu_kb(None, []),
        )
        return

    if not accounts:
        await callback.message.edit_text(
            "🔓 <b>Auto-Unlock-Face</b>\n\n"
            "✅ Аккаунтов с тегом <code>status:face</code> не найдено.",
            parse_mode="HTML",
            reply_markup=fu_kb(None, []),
        )
        return

    await state.set_state(FUStates.confirming_run)
    await state.update_data(accounts_text="\n".join(accounts))

    await callback.message.edit_text(
        f"🔓 <b>Auto-Unlock-Face</b>\n\n"
        f"Найдено <b>{len(accounts)}</b> аккаунтов с тегом <code>status:face</code>.\n\n"
        f"Отправить на разблокировку через ZeroPoint?",
        parse_mode="HTML",
        reply_markup=fu_confirm_kb(len(accounts)),
    )


@router.callback_query(lambda c: c.data == "fu_confirm")
async def fu_confirm(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    accounts_text = data.get("accounts_text", "")
    await state.clear()

    if not accounts_text:
        await callback.answer("Данные устарели. Попробуй ещё раз.", show_alert=True)
        await _show_fu(callback.message, callback.from_user.id, edit=True)
        return

    zp_key = get_zp_key(callback.from_user.id)
    if not zp_key:
        await callback.answer("❌ ZeroPoint API ключ не настроен.", show_alert=True)
        return

    await callback.answer("⏳ Отправляю...")
    await callback.message.edit_text(
        "🔓 <b>Auto-Unlock-Face</b>\n\n⏳ Отправляю на ZeroPoint...",
        parse_mode="HTML",
    )

    ok, result, err = await submit_job(zp_key, accounts_text)

    if not ok:
        if "активная задача" in err and isinstance(result, dict):
            existing = result.get("existing_job_id")
            if existing:
                save_zp_job(callback.from_user.id, existing)
        await callback.message.edit_text(
            f"🔓 <b>Auto-Unlock-Face</b>\n\n❌ Ошибка: {err}",
            parse_mode="HTML",
            reply_markup=fu_kb(None, []),
        )
        return

    job_id = result.get("job_id")
    if job_id:
        save_zp_job(callback.from_user.id, job_id)

    total    = result.get("total_accounts", 0)
    paid     = result.get("paid_accounts_count", 0)
    est_cost = result.get("estimated_cost", 0.0)

    await callback.answer(f"✅ Задача запущена! Платных: {paid} (~${est_cost:.2f})")
    await callback.message.answer(
        f"🔓 <b>Auto-Unlock-Face</b> — цикл запущен 🚀\n\n"
        f"📋 Аккаунтов: <b>{total}</b>  |  💰 Платных: {paid} (~${est_cost:.2f})\n\n"
        "Уведомлю когда задача завершится.",
        parse_mode="HTML",
    )
    await _show_fu(callback.message, callback.from_user.id, edit=True)


# ─── Face Unlock — отмена задачи ─────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "fu_cancel")
async def fu_cancel_job(callback: CallbackQuery):
    user_id = callback.from_user.id
    job_id  = get_zp_job(user_id)
    zp_key  = get_zp_key(user_id)

    if not job_id or not zp_key:
        await callback.answer("Нет активной задачи.", show_alert=True)
        return

    ok, _, err = await cancel_job(zp_key, job_id)
    if not ok:
        await callback.answer(f"❌ {err}", show_alert=True)
        return

    await callback.answer("🚫 Задача отменена")
    await _show_fu(callback.message, user_id, edit=True)


# ─── Face Unlock — скачать результаты ────────────────────────────────────────

@router.callback_query(lambda c: c.data.startswith("fu_dl:"))
async def fu_download(callback: CallbackQuery, bot: Bot):
    user_id  = callback.from_user.id
    filename = callback.data[len("fu_dl:"):]
    job_id   = get_zp_job(user_id)
    zp_key   = get_zp_key(user_id)

    if not job_id or not zp_key:
        await callback.answer("Задача не найдена.", show_alert=True)
        return

    await callback.answer("⏳ Загружаю файл...")
    ok, raw_bytes, err = await download_file(zp_key, job_id, filename)

    if not ok:
        await callback.message.answer(f"❌ Ошибка скачивания: {err}")
        return

    await bot.send_document(
        callback.message.chat.id,
        BufferedInputFile(raw_bytes, filename=filename),
    )


# ─── Face Unlock — авто-цикл toggle ──────────────────────────────────────────

@router.callback_query(lambda c: c.data == "fu_auto_toggle")
async def fu_auto_toggle(callback: CallbackQuery):
    user_id = callback.from_user.id
    new_val = not get_auto_unlock(user_id)
    set_auto_unlock(user_id, new_val)
    status = "включён ✅" if new_val else "выключен ❌"
    await callback.answer(f"🔁 Авто-цикл {status}", show_alert=False)
    await _show_fu(callback.message, user_id, edit=True)


# ─── /unlock — шорткат ───────────────────────────────────────────────────────

@router.message(Command("unlock"))
async def cmd_unlock(message: Message, state: FSMContext):
    user_id = message.from_user.id
    ao_key  = get_panel(user_id)
    zp_key  = get_zp_key(user_id)

    if not ao_key:
        await message.answer(
            "❌ AccountsOps не подключён.\n"
            "Настрой через /start → Настройки."
        )
        return
    if not zp_key:
        await message.answer(
            "❌ ZeroPoint API ключ не подключён.\n"
            "Настрой через /start → Автоматизация → Auto-Unlock-Face."
        )
        return

    job_id = get_zp_job(user_id)
    if job_id:
        ok_s, st, _ = await get_status(zp_key, job_id)
        if ok_s and st.get("status") in ("pending", "processing"):
            await message.answer("⚠️ Уже есть активная задача разблокировки.")
            return

    msg = await message.answer("⏳ Ищу аккаунты с тегом <code>status:face</code>...", parse_mode="HTML")
    ok, accounts, err = await get_face_accounts(ao_key)
    if not ok:
        await msg.edit_text(f"❌ Ошибка AccountsOps: {err}")
        return
    if not accounts:
        await msg.edit_text("✅ Аккаунтов с тегом <code>status:face</code> не найдено.", parse_mode="HTML")
        return

    await state.set_state(FUStates.confirming_run)
    await state.update_data(accounts_text="\n".join(accounts))
    await msg.edit_text(
        f"🔓 <b>/unlock</b>\n\n"
        f"Найдено <b>{len(accounts)}</b> аккаунтов с тегом <code>status:face</code>.\n\n"
        "Отправить на разблокировку через ZeroPoint?",
        parse_mode="HTML",
        reply_markup=fu_confirm_kb(len(accounts)),
    )


# ─── Face Unlock — интервал авто-цикла ───────────────────────────────────────

@router.callback_query(lambda c: c.data == "fu_interval_cycle")
async def fu_interval_cycle(callback: CallbackQuery):
    user_id = callback.from_user.id
    current = get_auto_unlock_interval(user_id)
    try:
        idx = _INTERVAL_PRESETS.index(current)
        next_val = _INTERVAL_PRESETS[(idx + 1) % len(_INTERVAL_PRESETS)]
    except ValueError:
        next_val = _INTERVAL_PRESETS[0]
    set_auto_unlock_interval(user_id, next_val)
    hours_str = f"{int(next_val)}ч"
    await callback.answer(f"⏱ Интервал: {hours_str}")
    await _show_fu(callback.message, user_id, edit=True)


# ─── Auto-Enable-Pet ──────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "auto_enable_pet")
async def open_auto_enable_pet(callback: CallbackQuery):
    enabled = get_auto_enable_pet(callback.from_user.id)
    await callback.message.edit_text(
        "🦆 <b>Auto-Enable-Pet</b>\n\n"
        "Бот каждые 10 минут проверяет аккаунты. "
        "Если у аккаунта есть пет "
        "<code>soggy_spring_2026_strawberry_shortcake_ducky</code> — "
        "автоматически включает его.",
        parse_mode="HTML",
        reply_markup=auto_enable_pet_kb(enabled),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data == "aep_toggle")
async def aep_toggle(callback: CallbackQuery):
    user_id = callback.from_user.id
    new_val = not get_auto_enable_pet(user_id)
    set_auto_enable_pet(user_id, new_val)
    status = "включён ✅" if new_val else "выключен ❌"
    await callback.answer(f"🦆 Auto-Enable-Pet {status}")
    await callback.message.edit_reply_markup(reply_markup=auto_enable_pet_kb(new_val))
