from aiogram import Router
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest

from api.accountsops import get_accounts_with_pet_details, set_accounts_enabled, get_trackstats_accounts
from database import (
    get_panel,
    get_autopilot_config,
    save_autopilot_main, save_autopilot_pet,
    set_autopilot_running,
    get_autopilot_active_count, get_autopilot_done_count,
    get_autopilot_active_entries, get_autopilot_pending_entries,
    add_autopilot_queue, clear_autopilot_queue,
    set_autopilot_entry_status,
    get_auto_enable_pet, set_auto_enable_pet,
)
from keyboards import autopilot_kb, cancel_to_ap_kb, auto_enable_pet_kb

router = Router()


class APStates(StatesGroup):
    waiting_main_account = State()
    waiting_pet_id       = State()


def _build_autopilot_page(user_id: int) -> tuple[str, any]:
    cfg          = get_autopilot_config(user_id)
    running      = cfg["running"]      if cfg else False
    main_account = cfg["main_account"] if cfg else None
    pet_id       = cfg["pet_id"]       if cfg else None
    auto_enabled = get_auto_enable_pet(user_id)

    lines = ["🤖 <b>Авто-пилот</b>", ""]
    main_str = f"<code>{main_account}</code>" if main_account else "<i>не задан</i>"
    pet_str  = f"<code>{pet_id}</code>"       if pet_id       else "<i>не задан</i>"
    lines.append(f"👤 Аккаунт: {main_str}")
    lines.append(f"🦆 Пет: {pet_str}")
    lines.append("")

    if running:
        active_count  = get_autopilot_active_count(user_id)
        pending_count = len(get_autopilot_pending_entries(user_id))
        done_count    = get_autopilot_done_count(user_id)
        lines.append(
            f"▶️ <b>Запущен</b>  ·  "
            f"Активных: {active_count}/10  ·  "
            f"Ожидает: {pending_count}  ·  "
            f"Готово: {done_count}"
        )
    else:
        lines.append("⏹ <b>Остановлен</b>")

    return "\n".join(lines), autopilot_kb(main_account, pet_id, running, auto_enabled)


async def _show_autopilot(target, user_id: int, edit: bool = False):
    text, kb = _build_autopilot_page(user_id)
    try:
        if edit and hasattr(target, "edit_text"):
            await target.edit_text(text, parse_mode="HTML", reply_markup=kb)
        else:
            await target.answer(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


# ─── Открыть страницу ─────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "autopilot")
async def open_autopilot(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    await _show_autopilot(callback.message, callback.from_user.id, edit=True)


@router.callback_query(lambda c: c.data == "ap_refresh")
async def ap_refresh(callback: CallbackQuery):
    await callback.answer("🔄")
    await _show_autopilot(callback.message, callback.from_user.id, edit=True)


# ─── Задать основной аккаунт ──────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "ap_set_main")
async def ap_set_main(callback: CallbackQuery, state: FSMContext):
    await state.set_state(APStates.waiting_main_account)
    await callback.message.edit_text(
        "👤 Введи <b>username</b> основного аккаунта:\n\n"
        "<i>Этот аккаунт включится первым и будет принимать питомцев.</i>",
        parse_mode="HTML",
        reply_markup=cancel_to_ap_kb(),
    )
    await callback.answer()


@router.message(APStates.waiting_main_account)
async def ap_receive_main(message: Message, state: FSMContext):
    username = message.text.strip()
    await message.delete()
    if not username:
        await message.answer("❌ Введи корректный username:", reply_markup=cancel_to_ap_kb())
        return
    save_autopilot_main(message.from_user.id, username)
    await state.clear()
    await _show_autopilot(message, message.from_user.id)


# ─── Задать ID пета ───────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "ap_set_pet")
async def ap_set_pet(callback: CallbackQuery, state: FSMContext):
    await state.set_state(APStates.waiting_pet_id)
    await callback.message.edit_text(
        "🦆 Введи <b>ID пета</b>:\n\n"
        "<i>Например: <code>soggy_spring_2026_strawberry_shortcake_ducky</code></i>",
        parse_mode="HTML",
        reply_markup=cancel_to_ap_kb(),
    )
    await callback.answer()


@router.message(APStates.waiting_pet_id)
async def ap_receive_pet(message: Message, state: FSMContext):
    pet_id = message.text.strip()
    await message.delete()
    if not pet_id:
        await message.answer("❌ Введи корректный ID пета:", reply_markup=cancel_to_ap_kb())
        return
    save_autopilot_pet(message.from_user.id, pet_id)
    await state.clear()
    await _show_autopilot(message, message.from_user.id)


# ─── Запуск ───────────────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "ap_start")
async def ap_start(callback: CallbackQuery):
    user_id = callback.from_user.id
    ao_key  = get_panel(user_id)
    if not ao_key:
        await callback.answer("❌ AccountsOps не подключён.", show_alert=True)
        return

    cfg = get_autopilot_config(user_id)
    if not cfg or not cfg["main_account"] or not cfg["pet_id"]:
        await callback.answer("❌ Задай основной аккаунт и ID пета.", show_alert=True)
        return

    await callback.answer("⏳ Запускаю...")
    await callback.message.edit_text(
        "🤖 <b>Авто-пилот</b>\n\n⏳ Отключаю все аккаунты...",
        parse_mode="HTML",
    )

    ok_all, all_accounts, _ = await get_trackstats_accounts(ao_key)
    if ok_all and all_accounts:
        all_usernames = [
            acc.get("username") or acc.get("name", "")
            for acc in all_accounts
            if acc.get("username") or acc.get("name")
        ]
        all_usernames = [u for u in all_usernames if u]
        if all_usernames:
            await set_accounts_enabled(ao_key, all_usernames, False)

    await callback.message.edit_text(
        "🤖 <b>Авто-пилот</b>\n\n⏳ Включаю основной аккаунт...",
        parse_mode="HTML",
    )

    ok_main, _, err_main = await set_accounts_enabled(ao_key, [cfg["main_account"]], True)
    if not ok_main:
        await callback.message.edit_text(
            f"🤖 <b>Авто-пилот</b>\n\n❌ Ошибка включения основного аккаунта: {err_main}",
            parse_mode="HTML",
            reply_markup=autopilot_kb(cfg["main_account"], cfg["pet_id"], False,
                                      get_auto_enable_pet(user_id)),
        )
        return

    await callback.message.edit_text(
        "🤖 <b>Авто-пилот</b>\n\n⏳ Сканирую аккаунты с петом...",
        parse_mode="HTML",
    )

    ok, accounts, err = await get_accounts_with_pet_details(ao_key, cfg["pet_id"])
    if not ok:
        await set_accounts_enabled(ao_key, [cfg["main_account"]], False)
        await callback.message.edit_text(
            f"🤖 <b>Авто-пилот</b>\n\n❌ Ошибка сканирования: {err}",
            parse_mode="HTML",
            reply_markup=autopilot_kb(cfg["main_account"], cfg["pet_id"], False,
                                      get_auto_enable_pet(user_id)),
        )
        return

    queue_accounts = [
        (acc_id, username) for acc_id, username in accounts
        if username.lower() != cfg["main_account"].lower()
    ]

    if not queue_accounts:
        await set_accounts_enabled(ao_key, [cfg["main_account"]], False)
        await callback.message.edit_text(
            "🤖 <b>Авто-пилот</b>\n\n"
            "ℹ️ Аккаунтов с этим петом не найдено.\n"
            "Основной аккаунт отключён.",
            parse_mode="HTML",
            reply_markup=autopilot_kb(cfg["main_account"], cfg["pet_id"], False,
                                      get_auto_enable_pet(user_id)),
        )
        return

    clear_autopilot_queue(user_id)
    add_autopilot_queue(user_id, queue_accounts)
    set_autopilot_running(user_id, True)

    first_batch = get_autopilot_pending_entries(user_id)[:10]
    if first_batch:
        await set_accounts_enabled(ao_key, [u for _, _, u in first_batch], True)
        for entry_id, _, _ in first_batch:
            set_autopilot_entry_status(entry_id, "active")

    await _show_autopilot(callback.message, user_id, edit=True)


# ─── Остановка ────────────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "ap_stop")
async def ap_stop(callback: CallbackQuery):
    user_id = callback.from_user.id
    ao_key  = get_panel(user_id)

    await callback.answer("⏹ Останавливаю...")

    if ao_key:
        active = get_autopilot_active_entries(user_id)
        if active:
            await set_accounts_enabled(ao_key, [u for _, _, u in active], False)
        cfg = get_autopilot_config(user_id)
        if cfg and cfg["main_account"]:
            await set_accounts_enabled(ao_key, [cfg["main_account"]], False)

    clear_autopilot_queue(user_id)
    set_autopilot_running(user_id, False)
    await _show_autopilot(callback.message, user_id, edit=True)


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
