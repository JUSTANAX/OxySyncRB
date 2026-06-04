import asyncio
from datetime import datetime
from aiogram import Router, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest

from api.accountsops import (
    get_accounts_with_pet_details, set_accounts_enabled, set_accounts_config,
    get_trackstats_accounts, get_all_accounts, get_configs, get_usernames_by_tag,
    get_account_inventory_by_username, _pet_tier, _pet_display_name,
    restart_accounts, get_pets_batch, get_events,
)
from database import (
    get_panel,
    get_autopilot_config,
    save_autopilot_main, save_autopilot_config_id, save_autopilot_farm_config_id,
    save_autopilot_check_interval, save_autopilot_batch_size,
    save_autopilot_main_config_id, save_autopilot_potion_threshold,
    set_autopilot_running, set_autopilot_started_at,
    get_autopilot_farming_entries, get_autopilot_trading_entries,
    get_autopilot_farming_count,
    increment_autopilot_trades_done, get_autopilot_trades_done,
    add_autopilot_queue, clear_autopilot_queue,
    set_autopilot_entry_status,
    get_autopilot_pets, add_autopilot_pet, add_autopilot_pets_bulk,
    update_autopilot_pet_min_count, remove_autopilot_pet,
    update_autopilot_pet_filters,
    remove_autopilot_queue_entries,
    add_autopilot_event,
    get_autopilot_inactive_count,
)
from keyboards import autopilot_kb, ap_pets_kb, ap_inventory_kb, cancel_to_ap_kb, configs_kb, farm_configs_kb, main_configs_kb, type_mask_label, _TYPE_MASKS

router = Router()


class APStates(StatesGroup):
    waiting_main_account   = State()
    waiting_pet_id         = State()
    waiting_pet_bulk       = State()
    waiting_pet_threshold  = State()
    waiting_check_interval    = State()
    waiting_batch_size        = State()
    waiting_potion_threshold  = State()


def _runtime_str(started_at: str | None) -> str:
    if not started_at:
        return ""
    try:
        started = datetime.strptime(started_at.replace("Z", "").split("+")[0], "%Y-%m-%dT%H:%M:%S")
        delta   = datetime.utcnow() - started
        h = int(delta.total_seconds() // 3600)
        m = int((delta.total_seconds() % 3600) // 60)
        return f"{h}ч {m}м" if h else f"{m}м"
    except Exception:
        return ""


def _build_autopilot_page(user_id: int) -> tuple[str, any]:
    cfg            = get_autopilot_config(user_id)
    running        = cfg["running"]        if cfg else False
    main_account   = cfg["main_account"]   if cfg else None
    config_id      = cfg["config_id"]      if cfg else None
    farm_config_id = cfg["farm_config_id"] if cfg else None
    check_interval    = cfg["check_interval"]    if cfg else 30
    batch_size        = cfg["batch_size"]        if cfg else 10
    main_config_id    = cfg.get("main_config_id") if cfg else None
    potion_threshold  = cfg.get("potion_threshold") or 8 if cfg else 8
    trades_done    = cfg["trades_done"]    if cfg else 0
    ready_count    = cfg.get("ready_count", 0) if cfg else 0
    started_at     = cfg["started_at"]     if cfg else None
    pets           = get_autopilot_pets(user_id)
    pet_count      = len(pets)

    lines = ["🤖 <b>Авто-пилот</b>", ""]

    # ── Статус ────────────────────────────────────────────────────────────────
    if running:
        rt = _runtime_str(started_at)
        farming_count   = get_autopilot_farming_count(user_id)
        trading_entries = get_autopilot_trading_entries(user_id)
        trading_count   = len(trading_entries)
        inactive_count  = get_autopilot_inactive_count(user_id)

        rt_part = f"  ·  🕐 {rt}" if rt else ""
        lines.append(f"▶️ <b>Работает</b>{rt_part}")
        lines.append("")
        if inactive_count:
            lines.append(f"  ⏳ Ждут мейна     <b>{inactive_count}</b>")
        lines.append(f"  🌾 Фармит         <b>{farming_count}</b>")
        lines.append(f"  🦆 Нашли пета     <b>{ready_count}</b>")
        lines.append(f"  🔄 Трейдит         <b>{trading_count}</b>")
        for _, _, u in trading_entries:
            lines.append(f"      · <code>{u}</code>")
        lines.append(f"  ✅ Сделок всего    <b>{trades_done}</b>")
    else:
        lines.append("⏹ <b>Остановлен</b>")

    # ── Разделитель ───────────────────────────────────────────────────────────
    lines.append("")
    lines.append("──────────────────────")
    lines.append("")

    # ── Конфигурация ──────────────────────────────────────────────────────────
    main_str = f"<code>{main_account}</code>" if main_account else "<i>не задан</i>"
    lines.append(f"👤 Основной аккаунт: {main_str}")

    if pets:
        for _, pid, min_count, *_ in pets:
            short   = pid if len(pid) <= 28 else pid[:25] + "…"
            min_str = f"  <i>(мин: {min_count})</i>" if min_count > 1 else ""
            lines.append(f"  🦆 <code>{short}</code>{min_str}")
    else:
        lines.append("  🦆 <i>Петы не заданы</i>")

    lines.append("")
    trade_str = f"<code>{config_id}</code>"      if config_id      else "<i>не задан</i>"
    farm_str  = f"<code>{farm_config_id}</code>" if farm_config_id else "<i>не задан</i>"
    lines.append(f"🔄 Трейд конфиг: {trade_str}")
    lines.append(f"🌾 Фарм конфиг:  {farm_str}")
    lines.append("")
    main_cfg_str = f"<code>{main_config_id}</code>" if main_config_id else "<i>не задан</i>"
    lines.append(f"👑 Конфиг мейна: {main_cfg_str}  ·  🧪 Порог зелий: <b>{potion_threshold}</b>")
    lines.append(f"⏱ Проверка: <b>{check_interval}с</b>  ·  📊 Лимит: <b>{batch_size}</b>")

    return "\n".join(lines), autopilot_kb(main_account, pet_count, config_id, farm_config_id, running, check_interval, batch_size, main_config_id, potion_threshold)


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
    await state.update_data(prompt_msg_id=callback.message.message_id)
    await callback.message.edit_text(
        "👤 Введи <b>username</b> основного аккаунта:\n\n"
        "<i>Этот аккаунт включится первым и будет принимать питомцев.</i>",
        parse_mode="HTML",
        reply_markup=cancel_to_ap_kb(),
    )
    await callback.answer()


@router.message(APStates.waiting_main_account)
async def ap_receive_main(message: Message, state: FSMContext, bot: Bot):
    if not message.text:
        return
    username = message.text.strip()
    await message.delete()
    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")
    if not username:
        await message.answer("❌ Введи корректный username:", reply_markup=cancel_to_ap_kb())
        return
    save_autopilot_main(message.from_user.id, username)
    await state.clear()
    text, kb = _build_autopilot_page(message.from_user.id)
    if prompt_msg_id:
        try:
            await bot.edit_message_text(
                text, chat_id=message.chat.id, message_id=prompt_msg_id,
                parse_mode="HTML", reply_markup=kb,
            )
            return
        except TelegramBadRequest:
            pass
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


# ─── Задать конфиг ───────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "ap_set_config")
async def ap_set_config(callback: CallbackQuery):
    user_id = callback.from_user.id
    ao_key  = get_panel(user_id)
    if not ao_key:
        await callback.answer("❌ AccountsOps не подключён.", show_alert=True)
        return
    await callback.answer()
    ok, configs, err = await get_configs(ao_key)
    if not ok or not configs:
        await callback.message.edit_text(
            f"⚙️ <b>Конфиги</b>\n\n❌ Не удалось загрузить: {err or 'список пуст'}",
            parse_mode="HTML",
            reply_markup=cancel_to_ap_kb(),
        )
        return
    await callback.message.edit_text(
        "⚙️ <b>Выбери конфиг</b>:",
        parse_mode="HTML",
        reply_markup=configs_kb(configs),
    )


@router.callback_query(lambda c: c.data.startswith("ap_cfg:"))
async def ap_pick_config(callback: CallbackQuery):
    try:
        config_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("❌ Ошибка", show_alert=True)
        return
    save_autopilot_config_id(callback.from_user.id, config_id)
    await callback.answer(f"✅ Конфиг {config_id} сохранён")
    await _show_autopilot(callback.message, callback.from_user.id, edit=True)


# ─── Задать фарм конфиг ───────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "ap_set_farm_config")
async def ap_set_farm_config(callback: CallbackQuery):
    user_id = callback.from_user.id
    ao_key  = get_panel(user_id)
    if not ao_key:
        await callback.answer("❌ AccountsOps не подключён.", show_alert=True)
        return
    await callback.answer()
    ok, configs, err = await get_configs(ao_key)
    if not ok or not configs:
        await callback.message.edit_text(
            f"🌾 <b>Фарм конфиг</b>\n\n❌ Не удалось загрузить: {err or 'список пуст'}",
            parse_mode="HTML",
            reply_markup=cancel_to_ap_kb(),
        )
        return
    await callback.message.edit_text(
        "🌾 <b>Выбери фарм конфиг</b>:",
        parse_mode="HTML",
        reply_markup=farm_configs_kb(configs),
    )


@router.callback_query(lambda c: c.data.startswith("ap_farm_cfg:"))
async def ap_pick_farm_config(callback: CallbackQuery):
    try:
        config_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("❌ Ошибка", show_alert=True)
        return
    save_autopilot_farm_config_id(callback.from_user.id, config_id)
    await callback.answer(f"✅ Фарм конфиг {config_id} сохранён")
    await _show_autopilot(callback.message, callback.from_user.id, edit=True)


# ─── Конфиг мейна ────────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "ap_set_main_config")
async def ap_set_main_config(callback: CallbackQuery):
    user_id = callback.from_user.id
    ao_key  = get_panel(user_id)
    if not ao_key:
        await callback.answer("❌ AccountsOps не подключён.", show_alert=True)
        return
    await callback.answer()
    ok, configs, err = await get_configs(ao_key)
    if not ok or not configs:
        await callback.message.edit_text(
            f"👑 <b>Конфиг мейна</b>\n\n❌ Не удалось загрузить: {err or 'список пуст'}",
            parse_mode="HTML",
            reply_markup=cancel_to_ap_kb(),
        )
        return
    await callback.message.edit_text(
        "👑 <b>Выбери конфиг для основного аккаунта</b>:",
        parse_mode="HTML",
        reply_markup=main_configs_kb(configs),
    )


@router.callback_query(lambda c: c.data.startswith("ap_main_cfg:"))
async def ap_pick_main_config(callback: CallbackQuery):
    try:
        config_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("❌ Ошибка", show_alert=True)
        return
    save_autopilot_main_config_id(callback.from_user.id, config_id)
    await callback.answer(f"✅ Конфиг мейна {config_id} сохранён")
    await _show_autopilot(callback.message, callback.from_user.id, edit=True)


# ─── Порог зелий ─────────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "ap_set_potion_threshold")
async def ap_set_potion_threshold(callback: CallbackQuery, state: FSMContext):
    await state.set_state(APStates.waiting_potion_threshold)
    await state.update_data(prompt_msg_id=callback.message.message_id)
    await callback.message.edit_text(
        "🧪 Введи порог зелий для смены основного аккаунта:\n\n"
        "<i>Если у текущего мейна станет меньше этого кол-ва зелий — "
        "бот автоматически сменит его на аккаунт с наибольшим запасом.\n"
        "Допустимо от 1 до 9999.</i>",
        parse_mode="HTML",
        reply_markup=cancel_to_ap_kb(),
    )
    await callback.answer()


@router.message(APStates.waiting_potion_threshold)
async def ap_receive_potion_threshold(message: Message, state: FSMContext, bot: Bot):
    if not message.text:
        return
    text = message.text.strip()
    await message.delete()
    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")
    if not text.isdigit() or not (1 <= int(text) <= 9999):
        await message.answer("❌ Введи число от 1 до 9999:", reply_markup=cancel_to_ap_kb())
        return
    save_autopilot_potion_threshold(message.from_user.id, int(text))
    await state.clear()
    page_text, kb = _build_autopilot_page(message.from_user.id)
    if prompt_msg_id:
        try:
            await bot.edit_message_text(
                page_text, chat_id=message.chat.id, message_id=prompt_msg_id,
                parse_mode="HTML", reply_markup=kb,
            )
            return
        except TelegramBadRequest:
            pass
    await message.answer(page_text, parse_mode="HTML", reply_markup=kb)


# ─── Задать интервал проверки ─────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "ap_set_interval")
async def ap_set_interval(callback: CallbackQuery, state: FSMContext):
    await state.set_state(APStates.waiting_check_interval)
    await state.update_data(prompt_msg_id=callback.message.message_id)
    await callback.message.edit_text(
        "⏱ Введи интервал проверки инвентарей (в секундах):\n\n"
        "<i>Например: <code>30</code> — проверять каждые 30 секунд.\n"
        "Допустимо от 10 до 300 секунд.</i>",
        parse_mode="HTML",
        reply_markup=cancel_to_ap_kb(),
    )
    await callback.answer()


@router.message(APStates.waiting_check_interval)
async def ap_receive_interval(message: Message, state: FSMContext, bot: Bot):
    if not message.text:
        return
    text = message.text.strip()
    await message.delete()
    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")
    if not text.isdigit() or not (10 <= int(text) <= 300):
        await message.answer("❌ Введи число от 10 до 300 секунд:", reply_markup=cancel_to_ap_kb())
        return
    save_autopilot_check_interval(message.from_user.id, int(text))
    await state.clear()
    page_text, kb = _build_autopilot_page(message.from_user.id)
    if prompt_msg_id:
        try:
            await bot.edit_message_text(
                page_text, chat_id=message.chat.id, message_id=prompt_msg_id,
                parse_mode="HTML", reply_markup=kb,
            )
            return
        except TelegramBadRequest:
            pass
    await message.answer(page_text, parse_mode="HTML", reply_markup=kb)


# ─── Задать лимит торгующих ──────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "ap_set_batch")
async def ap_set_batch(callback: CallbackQuery, state: FSMContext):
    await state.set_state(APStates.waiting_batch_size)
    await state.update_data(prompt_msg_id=callback.message.message_id)
    await callback.message.edit_text(
        "📊 Введи максимальное кол-во аккаунтов одновременно в трейде:\n\n"
        "<i>Допустимо от 1 до 50. Рекомендуется: <code>10</code>.</i>",
        parse_mode="HTML",
        reply_markup=cancel_to_ap_kb(),
    )
    await callback.answer()


@router.message(APStates.waiting_batch_size)
async def ap_receive_batch(message: Message, state: FSMContext, bot: Bot):
    if not message.text:
        return
    text = message.text.strip()
    await message.delete()
    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")
    if not text.isdigit() or not (1 <= int(text) <= 50):
        await message.answer("❌ Введи число от 1 до 50:", reply_markup=cancel_to_ap_kb())
        return
    save_autopilot_batch_size(message.from_user.id, int(text))
    await state.clear()
    page_text, kb = _build_autopilot_page(message.from_user.id)
    if prompt_msg_id:
        try:
            await bot.edit_message_text(
                page_text, chat_id=message.chat.id, message_id=prompt_msg_id,
                parse_mode="HTML", reply_markup=kb,
            )
            return
        except TelegramBadRequest:
            pass
    await message.answer(page_text, parse_mode="HTML", reply_markup=kb)


# ─── Управление петами ───────────────────────────────────────────────────────

def _pets_page_text(user_id: int) -> tuple[str, any]:
    pets = get_autopilot_pets(user_id)
    lines = ["🦆 <b>Петы авто-пилота</b>", ""]
    if pets:
        for _, pid, min_count, age_min, age_max, type_mask in pets:
            min_str  = f"  📊 <b>{min_count}</b>" if min_count > 1 else ""
            age_str  = f"  🎂 <b>{age_min}–{age_max}</b>" if not (age_min == 1 and age_max == 6) else ""
            type_str = f"  🐾 <b>{type_mask_label(type_mask)}</b>" if type_mask != 7 else ""
            lines.append(f"• <code>{pid}</code>{min_str}{age_str}{type_str}")
    else:
        lines.append("<i>Нет добавленных петов</i>")
    lines.append("")
    lines.append("<i>📊 мин. акк.  ·  🎂 возраст  ·  🐾 тип пета</i>")
    return "\n".join(lines), ap_pets_kb(pets)


@router.callback_query(lambda c: c.data == "ap_set_pet")
async def ap_set_pet(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    text, kb = _pets_page_text(callback.from_user.id)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(lambda c: c.data == "ap_add_pet")
async def ap_add_pet(callback: CallbackQuery, state: FSMContext):
    await state.set_state(APStates.waiting_pet_id)
    await state.update_data(prompt_msg_id=callback.message.message_id)
    await callback.message.edit_text(
        "🦆 Введи <b>ID пета</b>:\n\n"
        "<i>Например: <code>soggy_spring_2026_strawberry_shortcake_ducky</code></i>",
        parse_mode="HTML",
        reply_markup=cancel_to_ap_kb(),
    )
    await callback.answer()


@router.message(APStates.waiting_pet_id)
async def ap_receive_pet(message: Message, state: FSMContext, bot: Bot):
    if not message.text:
        return
    pet_id = message.text.strip()
    await message.delete()
    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")
    if not pet_id:
        await message.answer("❌ Введи корректный ID пета:", reply_markup=cancel_to_ap_kb())
        return
    added = add_autopilot_pet(message.from_user.id, pet_id)
    await state.clear()
    notice = "✅ Пет добавлен" if added else "ℹ️ Такой пет уже есть"
    await message.answer(notice)
    text, kb = _pets_page_text(message.from_user.id)
    if prompt_msg_id:
        try:
            await bot.edit_message_text(
                text, chat_id=message.chat.id, message_id=prompt_msg_id,
                parse_mode="HTML", reply_markup=kb,
            )
            return
        except TelegramBadRequest:
            pass
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(lambda c: c.data == "ap_bulk_pet")
async def ap_bulk_pet(callback: CallbackQuery, state: FSMContext):
    await state.set_state(APStates.waiting_pet_bulk)
    await state.update_data(prompt_msg_id=callback.message.message_id)
    await callback.message.edit_text(
        "📋 <b>Bulk-добавление петов</b>\n\n"
        "Отправь несколько ID — каждый с новой строки:\n\n"
        "<code>soggy_spring_2026_unicorn\n"
        "dragon_fire_2025\n"
        "cat_rainbow_2026</code>",
        parse_mode="HTML",
        reply_markup=cancel_to_ap_kb(),
    )
    await callback.answer()


@router.message(APStates.waiting_pet_bulk)
async def ap_receive_bulk(message: Message, state: FSMContext, bot: Bot):
    if not message.text:
        return
    await message.delete()
    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")
    raw_ids = [line.strip() for line in message.text.splitlines()]
    pet_ids = [pid for pid in raw_ids if pid]
    if not pet_ids:
        await message.answer("❌ Не найдено ни одного ID:", reply_markup=cancel_to_ap_kb())
        return
    added, skipped = add_autopilot_pets_bulk(message.from_user.id, pet_ids)
    await state.clear()
    notice = f"✅ Добавлено: <b>{added}</b>"
    if skipped:
        notice += f"  ·  ⚠️ Уже было: <b>{skipped}</b>"
    await message.answer(notice, parse_mode="HTML")
    text, kb = _pets_page_text(message.from_user.id)
    if prompt_msg_id:
        try:
            await bot.edit_message_text(
                text, chat_id=message.chat.id, message_id=prompt_msg_id,
                parse_mode="HTML", reply_markup=kb,
            )
            return
        except TelegramBadRequest:
            pass
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(lambda c: c.data.startswith("ap_pet_threshold:"))
async def ap_pet_threshold(callback: CallbackQuery, state: FSMContext):
    try:
        row_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("❌ Ошибка", show_alert=True)
        return
    await state.set_state(APStates.waiting_pet_threshold)
    await state.update_data(prompt_msg_id=callback.message.message_id, pet_row_id=row_id)
    await callback.message.edit_text(
        "📊 <b>Минимальный порог для трейда</b>\n\n"
        "Введи число аккаунтов, у которых должен быть этот пет, "
        "прежде чем они перейдут в трейд:\n\n"
        "<i>• <code>1</code> — трейдить сразу как появится\n"
        "• <code>5</code> — ждать пока 5 аккаунтов накопят пета</i>",
        parse_mode="HTML",
        reply_markup=cancel_to_ap_kb(),
    )
    await callback.answer()


@router.message(APStates.waiting_pet_threshold)
async def ap_receive_threshold(message: Message, state: FSMContext, bot: Bot):
    if not message.text:
        return
    text = message.text.strip()
    await message.delete()
    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")
    pet_row_id = data.get("pet_row_id")
    if not text.isdigit() or not (1 <= int(text) <= 500):
        await message.answer("❌ Введи число от 1 до 500:", reply_markup=cancel_to_ap_kb())
        return
    update_autopilot_pet_min_count(pet_row_id, int(text))
    await state.clear()
    page_text, kb = _pets_page_text(message.from_user.id)
    if prompt_msg_id:
        try:
            await bot.edit_message_text(
                page_text, chat_id=message.chat.id, message_id=prompt_msg_id,
                parse_mode="HTML", reply_markup=kb,
            )
            return
        except TelegramBadRequest:
            pass
    await message.answer(page_text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(lambda c: c.data.startswith("ap_del_pet:"))
async def ap_del_pet(callback: CallbackQuery):
    try:
        row_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("❌ Ошибка", show_alert=True)
        return
    remove_autopilot_pet(row_id)
    await callback.answer("🗑 Удалено")
    text, kb = _pets_page_text(callback.from_user.id)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest:
        pass


@router.callback_query(lambda c: c.data.startswith("ap_pet_amin:"))
async def ap_pet_amin(callback: CallbackQuery):
    try:
        row_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("❌ Ошибка", show_alert=True)
        return
    pets = get_autopilot_pets(callback.from_user.id)
    pet  = next((p for p in pets if p[0] == row_id), None)
    if not pet:
        await callback.answer("❌ Ошибка", show_alert=True)
        return
    _, _, _, age_min, age_max, _ = pet
    new_min = (age_min % 6) + 1
    update_autopilot_pet_filters(row_id, age_min=new_min)
    await callback.answer()
    text, kb = _pets_page_text(callback.from_user.id)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest:
        pass


@router.callback_query(lambda c: c.data.startswith("ap_pet_amax:"))
async def ap_pet_amax(callback: CallbackQuery):
    try:
        row_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("❌ Ошибка", show_alert=True)
        return
    pets = get_autopilot_pets(callback.from_user.id)
    pet  = next((p for p in pets if p[0] == row_id), None)
    if not pet:
        await callback.answer("❌ Ошибка", show_alert=True)
        return
    _, _, _, age_min, age_max, _ = pet
    new_max = (age_max % 6) + 1
    update_autopilot_pet_filters(row_id, age_max=new_max)
    await callback.answer()
    text, kb = _pets_page_text(callback.from_user.id)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest:
        pass


@router.callback_query(lambda c: c.data.startswith("ap_pet_type:"))
async def ap_pet_type(callback: CallbackQuery):
    try:
        row_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("❌ Ошибка", show_alert=True)
        return
    pets = get_autopilot_pets(callback.from_user.id)
    pet  = next((p for p in pets if p[0] == row_id), None)
    if not pet:
        await callback.answer("❌ Ошибка", show_alert=True)
        return
    _, _, _, _, _, type_mask = pet
    try:
        idx      = _TYPE_MASKS.index(type_mask)
        new_mask = _TYPE_MASKS[(idx + 1) % len(_TYPE_MASKS)]
    except ValueError:
        new_mask = 7
    update_autopilot_pet_filters(row_id, type_mask=new_mask)
    await callback.answer()
    text, kb = _pets_page_text(callback.from_user.id)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest:
        pass


# ─── Запуск ───────────────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "ap_start")
async def ap_start(callback: CallbackQuery):
    user_id = callback.from_user.id
    ao_key  = get_panel(user_id)
    if not ao_key:
        await callback.answer("❌ AccountsOps не подключён.", show_alert=True)
        return

    cfg  = get_autopilot_config(user_id)
    pets = get_autopilot_pets(user_id)
    if not cfg or not cfg["main_account"] or not pets:
        await callback.answer("❌ Задай основной аккаунт и хотя бы один пет.", show_alert=True)
        return

    await callback.answer("⏳ Запускаю...")
    pet_count      = len(pets)
    config_id      = cfg.get("config_id")
    farm_config_id = cfg.get("farm_config_id")

    await callback.message.edit_text(
        "🤖 <b>Авто-пилот</b>\n\n⏳ Собираю список аккаунтов...",
        parse_mode="HTML",
    )

    (ok_all, ts_accounts, _), (_, raw_accounts, _), face_set, dead_set, (ok_ev, events, _) = await asyncio.gather(
        get_trackstats_accounts(ao_key),
        get_all_accounts(ao_key),
        get_usernames_by_tag(ao_key, "status:face"),
        get_usernames_by_tag(ao_key, "status:dead"),
        get_events(ao_key, limit=200),
    )
    if not ok_all or not ts_accounts:
        await callback.message.edit_text(
            "🤖 <b>Авто-пилот</b>\n\n❌ Не удалось получить список аккаунтов.",
            parse_mode="HTML",
            reply_markup=autopilot_kb(cfg["main_account"], pet_count, config_id, farm_config_id, False, cfg.get("check_interval", 30), cfg.get("batch_size", 10), cfg.get("main_config_id"), cfg.get("potion_threshold") or 8),
        )
        return

    # Detect which accounts are already in game via most recent account_launch event
    active_now: set[str] = set()
    if ok_ev and events:
        seen_ev: set[str] = set()
        for event in events:  # newest-first from API
            uname = (event.get("username") or "").strip().lower()
            if not uname or uname in seen_ev:
                continue
            seen_ev.add(uname)
            if event.get("kind") == "account_launch":
                active_now.add(uname)

    device_assigned: set[str] = {
        (acc.get("username") or acc.get("name") or "").strip().lower()
        for acc in raw_accounts
        if (acc.get("device_id") or "").strip()
    }

    main_lower    = cfg["main_account"].lower()
    farm_accounts = []
    for acc in ts_accounts:
        username = acc.get("username") or acc.get("name", "")
        if not username:
            continue
        u = username.lower()
        if u == main_lower or u in face_set or u in dead_set:
            continue
        if u not in device_assigned:
            continue
        acc_id = str(acc.get("id") or acc.get("account_id", ""))
        farm_accounts.append((acc_id, username))

    if not farm_accounts:
        await callback.message.edit_text(
            "🤖 <b>Авто-пилот</b>\n\nℹ️ Нет доступных аккаунтов для фарма.",
            parse_mode="HTML",
            reply_markup=autopilot_kb(cfg["main_account"], pet_count, config_id, farm_config_id, False, cfg.get("check_interval", 30), cfg.get("batch_size", 10), cfg.get("main_config_id"), cfg.get("potion_threshold") or 8),
        )
        return

    # Split: already in game vs not yet started
    main_already_active = main_lower in active_now
    already_active = [(aid, u) for aid, u in farm_accounts if u.lower() in active_now]
    to_start_later = [(aid, u) for aid, u in farm_accounts if u.lower() not in active_now]

    # Disable accounts not yet in game
    if to_start_later:
        await callback.message.edit_text(
            f"🤖 <b>Авто-пилот</b>\n\n⏳ Выключаю {len(to_start_later)} неактивных аккаунтов...",
            parse_mode="HTML",
        )
        await set_accounts_enabled(ao_key, [u for _, u in to_start_later], False)

    # Apply farm config to already-active accounts
    if farm_config_id and already_active:
        await set_accounts_config(ao_key, [u for _, u in already_active], farm_config_id)

    # Enable main + already-active farm accounts
    await callback.message.edit_text(
        "🤖 <b>Авто-пилот</b>\n\n⏳ Включаю мейн и активных...",
        parse_mode="HTML",
    )
    await set_accounts_enabled(ao_key, [cfg["main_account"]], True)
    if already_active:
        await set_accounts_enabled(ao_key, [u for _, u in already_active], True)

    clear_autopilot_queue(user_id)

    if main_already_active and to_start_later:
        # Main already in game — enable everyone immediately, no waiting needed
        if farm_config_id:
            await set_accounts_config(ao_key, [u for _, u in to_start_later], farm_config_id)
        await set_accounts_enabled(ao_key, [u for _, u in to_start_later], True)
        add_autopilot_queue(user_id, farm_accounts, status='farming')
    else:
        if already_active:
            add_autopilot_queue(user_id, already_active, status='farming')
        if to_start_later:
            # Will be activated once main fires account_launch event
            add_autopilot_queue(user_id, to_start_later, status='inactive')

    set_autopilot_started_at(user_id)
    set_autopilot_running(user_id, True)
    add_autopilot_event(user_id, "started")

    await _show_autopilot(callback.message, user_id, edit=True)


# ─── Рестарт трейдеров ───────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "ap_restart_trading")
async def ap_restart_trading(callback: CallbackQuery):
    user_id = callback.from_user.id
    ao_key  = get_panel(user_id)
    if not ao_key:
        await callback.answer("❌ AccountsOps не подключён.", show_alert=True)
        return

    trading = get_autopilot_trading_entries(user_id)
    if not trading:
        await callback.answer("ℹ️ Нет аккаунтов в трейде.", show_alert=True)
        return

    usernames = [u for _, _, u in trading]
    await callback.answer(f"⚡️ Рестарт {len(usernames)} трейдеров...")

    ok, _, err = await restart_accounts(ao_key, usernames)
    if not ok:
        await callback.message.answer(f"❌ Ошибка рестарта: {err}")
        return

    await _show_autopilot(callback.message, user_id, edit=True)


# ─── Инвентарь основного аккаунта ────────────────────────────────────────────

def _pet_age(pet: dict) -> int | None:
    raw = pet.get("age") or pet.get("level") or pet.get("age_stage")
    if raw is None:
        return None
    try:
        v = int(raw)
        return max(1, min(6, v if v >= 1 else v + 1))
    except Exception:
        return None


async def _build_inventory_text(user_id: int) -> str:
    ao_key = get_panel(user_id)
    cfg    = get_autopilot_config(user_id)
    if not ao_key or not cfg or not cfg.get("main_account"):
        return "❌ Основной аккаунт не задан."

    main = cfg["main_account"]
    ok, pets, err = await get_account_inventory_by_username(ao_key, main)
    if not ok:
        return f"📦 <b>Инвентарь: {main}</b>\n\n❌ {err}"
    if not pets:
        return f"📦 <b>Инвентарь: {main}</b>\n\n<i>Инвентарь пуст</i>"

    # Aggregate by (tier, name, age) — different ages are separate lines
    from collections import defaultdict
    agg: dict[tuple, int] = defaultdict(int)
    agg_kind: dict[tuple, str] = {}
    for pet in pets:
        tier = _pet_tier(pet)
        name = _pet_display_name(pet)
        qty  = pet.get("quantity", 1) or 1
        age  = _pet_age(pet)
        key  = (tier, name, age)
        agg[key] += qty
        if key not in agg_kind:
            agg_kind[key] = pet.get("pet_kind", "")

    TIER_EMOJI = {"mega": "🌟", "neon": "✨", "normal": "🦆"}

    def _render_group(tier: str, label: str) -> list[str]:
        emoji = TIER_EMOJI[tier]
        items = [(name, age, cnt) for (t, name, age), cnt in agg.items() if t == tier]
        if not items:
            return []
        items.sort(key=lambda x: (-x[2], x[0], x[1] or 0))
        result = [f"{emoji} <b>{label}</b>"]
        for name, age, cnt in items:
            qty_str  = f" ×{cnt}" if cnt > 1 else ""
            age_str  = f"  {age}" if age is not None else ""
            raw_kind = agg_kind.get((tier, name, age), "")
            kind_str = f"\n     <code>{raw_kind}</code>" if raw_kind else ""
            result.append(f"  {emoji} {name}{qty_str}{age_str}{kind_str}")
        return result

    lines = [f"📦 <b>Инвентарь: <code>{main}</code></b>", ""]

    for part in (_render_group("mega",   "Мега-Неон"),
                 _render_group("neon",   "Неон"),
                 _render_group("normal", "Обычные")):
        if part:
            lines.extend(part)
            lines.append("")

    total = sum(agg.values())
    lines.append(f"<i>Всего: {total} питомцев</i>")
    return "\n".join(lines)


@router.callback_query(lambda c: c.data == "ap_inventory")
async def ap_inventory(callback: CallbackQuery):
    await callback.answer("⏳ Загружаю...")
    await callback.message.edit_text("⏳ Загружаю инвентарь...", parse_mode="HTML")
    text = await _build_inventory_text(callback.from_user.id)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=ap_inventory_kb())
    except TelegramBadRequest:
        pass


@router.callback_query(lambda c: c.data == "ap_inventory_refresh")
async def ap_inventory_refresh(callback: CallbackQuery):
    await callback.answer("🔄")
    await callback.message.edit_text("⏳ Обновляю...", parse_mode="HTML")
    text = await _build_inventory_text(callback.from_user.id)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=ap_inventory_kb())
    except TelegramBadRequest:
        pass


# ─── Перезапуск всех аккаунтов ───────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "ap_restart_all")
async def ap_restart_all(callback: CallbackQuery):
    user_id = callback.from_user.id
    ao_key  = get_panel(user_id)
    if not ao_key:
        await callback.answer("❌ AccountsOps не подключён.", show_alert=True)
        return

    cfg = get_autopilot_config(user_id)
    if not cfg or not cfg["main_account"]:
        await callback.answer("❌ Задай основной аккаунт.", show_alert=True)
        return

    await callback.answer("⏳ Перезапускаю...")
    farm_config_id = cfg.get("farm_config_id")

    await callback.message.edit_text(
        "🤖 <b>Авто-пилот</b>\n\n⏳ Получаю список аккаунтов...",
        parse_mode="HTML",
    )

    ok_all, all_accounts, _ = await get_trackstats_accounts(ao_key)
    if not ok_all or not all_accounts:
        await _show_autopilot(callback.message, user_id, edit=True)
        return

    face_set, dead_set = await asyncio.gather(
        get_usernames_by_tag(ao_key, "status:face"),
        get_usernames_by_tag(ao_key, "status:dead"),
    )

    main_lower    = cfg["main_account"].lower()
    farm_usernames = []
    all_usernames  = []
    for acc in all_accounts:
        username = acc.get("username") or acc.get("name", "")
        if not username:
            continue
        all_usernames.append(username)
        u = username.lower()
        if u == main_lower or u in face_set or u in dead_set:
            continue
        farm_usernames.append(username)

    if farm_config_id and farm_usernames:
        await callback.message.edit_text(
            "🤖 <b>Авто-пилот</b>\n\n⏳ Применяю фарм конфиг...",
            parse_mode="HTML",
        )
        await set_accounts_config(ao_key, farm_usernames, farm_config_id)

    await callback.message.edit_text(
        f"🤖 <b>Авто-пилот</b>\n\n⏳ Выключаю {len(all_usernames)} аккаунтов...",
        parse_mode="HTML",
    )
    await set_accounts_enabled(ao_key, all_usernames, False)

    await callback.message.edit_text(
        "🤖 <b>Авто-пилот</b>\n\n⏳ Включаю основной + фарм аккаунты...",
        parse_mode="HTML",
    )
    await set_accounts_enabled(ao_key, [cfg["main_account"]], True)
    if farm_usernames:
        await set_accounts_enabled(ao_key, farm_usernames, True)

    await _show_autopilot(callback.message, user_id, edit=True)


# ─── Очистка очереди ─────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "ap_cleanup_queue")
async def ap_cleanup_queue(callback: CallbackQuery):
    user_id = callback.from_user.id
    ao_key  = get_panel(user_id)
    if not ao_key:
        await callback.answer("❌ AccountsOps не подключён.", show_alert=True)
        return

    farming_entries = get_autopilot_farming_entries(user_id)
    if not farming_entries:
        await callback.answer("ℹ️ Очередь фармеров пуста.", show_alert=True)
        await _show_autopilot(callback.message, user_id, edit=True)
        return

    await callback.answer("⏳ Проверяю...")
    await callback.message.edit_text(
        "🔧 <b>Очистка очереди</b>\n\n⏳ Получаю список девайсов...",
        parse_mode="HTML",
    )

    _, raw_accounts, _ = await get_all_accounts(ao_key)
    device_assigned: set[str] = {
        (acc.get("username") or acc.get("name") or "").strip().lower()
        for acc in raw_accounts
        if (acc.get("device_id") or "").strip()
    }

    to_remove = [
        entry_id
        for entry_id, _, username in farming_entries
        if username.lower() not in device_assigned
    ]

    if to_remove:
        remove_autopilot_queue_entries(to_remove)

    kept    = len(farming_entries) - len(to_remove)
    removed = len(to_remove)

    lines = ["🔧 <b>Очистка очереди — готово</b>", ""]
    lines.append(f"✅ Осталось в очереди: <b>{kept}</b>")
    lines.append(f"🗑 Убрано (без девайса): <b>{removed}</b>")
    lines.append("")
    lines.append("<i>Trading-аккаунты не затронуты.</i>")

    try:
        await callback.message.edit_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=ap_inventory_kb(),
        )
    except TelegramBadRequest:
        pass


# ─── Debug петов ──────────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "ap_debug")
async def ap_debug(callback: CallbackQuery):
    user_id = callback.from_user.id
    ao_key  = get_panel(user_id)
    if not ao_key:
        await callback.answer("❌ AccountsOps не подключён.", show_alert=True)
        return

    await callback.answer("⏳ Анализирую...")
    await callback.message.edit_text("🔍 <b>Debug</b>\n\n⏳ Собираю данные...", parse_mode="HTML")

    pet_rows       = get_autopilot_pets(user_id)
    farming_entries = get_autopilot_farming_entries(user_id)

    lines = ["🔍 <b>Debug авто-пилота</b>", ""]

    # — Конфиг петов
    lines.append(f"<b>Петы ({len(pet_rows)}):</b>")
    if pet_rows:
        for _, pid, min_count, age_min, age_max, type_mask in pet_rows:
            lines.append(f"  • <code>{pid}</code>  min={min_count}  age={age_min}–{age_max}  mask={type_mask}")
    else:
        lines.append("  ❌ Петы не настроены!")

    lines.append("")
    lines.append(f"<b>Очередь фармеров:</b> {len(farming_entries)}")

    if not farming_entries:
        lines.append("  ❌ Нет аккаунтов в статусе farming!")
        try:
            await callback.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=ap_inventory_kb())
        except TelegramBadRequest:
            pass
        return

    # — Резолвим fresh_ids из trackstats
    _, ts_accounts, _ = await get_trackstats_accounts(ao_key)
    username_to_id = {
        (acc.get("username") or acc.get("name", "")).lower(): str(acc.get("id") or "")
        for acc in ts_accounts if acc.get("id")
    }
    lines.append(f"<b>Trackstats аккаунтов:</b> {len(username_to_id)}")

    resolved = sum(1 for _, _, u in farming_entries if u.lower() in username_to_id)
    lines.append(f"<b>Резолвилось из trackstats:</b> {resolved}/{len(farming_entries)}")

    fresh_ids = [username_to_id.get(u.lower(), acc_id) for _, acc_id, u in farming_entries]

    # — Тянем петов для первых 20 аккаунтов
    sample_ids = [fid for fid in fresh_ids[:20] if fid]
    lines.append(f"<b>Сэмпл: запрашиваем петов у {len(sample_ids)} акк.</b>")
    pets_map = await get_pets_batch(ao_key, sample_ids)

    non_empty = sum(1 for pets in pets_map.values() if pets)
    lines.append(f"<b>Аккаунтов с непустым инвентарём:</b> {non_empty}/{len(sample_ids)}")

    # — Собираем уникальные pet_kind из сэмпла
    all_kinds: set[str] = set()
    for pets in pets_map.values():
        for p in pets:
            k = p.get("pet_kind")
            if k:
                all_kinds.add(k)

    lines.append(f"<b>Уникальных pet_kind в сэмпле:</b> {len(all_kinds)}")
    if all_kinds:
        sample_kinds = sorted(all_kinds)[:5]
        for k in sample_kinds:
            lines.append(f"  <code>{k}</code>")
        if len(all_kinds) > 5:
            lines.append(f"  ... и ещё {len(all_kinds) - 5}")

    # — Проверяем матч
    if pet_rows and all_kinds:
        pet_ids_set = {pid for _, pid, *_ in pet_rows}
        lines.append("")
        lines.append("<b>Матч (suffix):</b>")
        matched_any = False
        for kind in sorted(all_kinds):
            for pid in pet_ids_set:
                if kind == pid or kind.endswith(f"_{pid}"):
                    lines.append(f"  ✅ <code>{kind}</code> → <code>{pid}</code>")
                    matched_any = True
        if not matched_any:
            lines.append("  ❌ Ни один pet_kind не матчится с настроенными петами")
            lines.append("")
            lines.append("<i>Совет: попробуй ввести полный pet_kind как ID пета</i>")

    try:
        await callback.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=ap_inventory_kb())
    except TelegramBadRequest:
        pass


# ─── Остановка ────────────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "ap_stop")
async def ap_stop(callback: CallbackQuery):
    user_id = callback.from_user.id
    ao_key  = get_panel(user_id)

    await callback.answer("⏹ Останавливаю...")

    if ao_key:
        cfg      = get_autopilot_config(user_id)
        farming  = get_autopilot_farming_entries(user_id)
        trading  = get_autopilot_trading_entries(user_id)
        all_entries = farming + trading
        if all_entries:
            await set_accounts_enabled(ao_key, [u for _, _, u in all_entries], False)
        if cfg and cfg["main_account"]:
            await set_accounts_enabled(ao_key, [cfg["main_account"]], False)

    clear_autopilot_queue(user_id)
    set_autopilot_running(user_id, False)
    add_autopilot_event(user_id, "stopped")
    await _show_autopilot(callback.message, user_id, edit=True)

