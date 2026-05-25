import asyncio
from aiogram import Router, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest

from api.accountsops import get_accounts_with_pet_details, set_accounts_enabled, set_accounts_config, get_trackstats_accounts, get_configs, get_usernames_by_tag
from database import (
    get_panel,
    get_autopilot_config,
    save_autopilot_main, save_autopilot_config_id, save_autopilot_farm_config_id,
    save_autopilot_check_interval, save_autopilot_stuck_timeout, save_autopilot_batch_size,
    set_autopilot_running, set_autopilot_started_at,
    get_autopilot_farming_entries, get_autopilot_trading_entries,
    get_autopilot_farming_count, get_autopilot_trading_count,
    increment_autopilot_trades_done, get_autopilot_trades_done,
    add_autopilot_queue, clear_autopilot_queue,
    set_autopilot_entry_status,
    get_autopilot_pets, add_autopilot_pet, add_autopilot_pets_bulk,
    update_autopilot_pet_min_count, remove_autopilot_pet,
    add_autopilot_event,
)
from keyboards import autopilot_kb, ap_pets_kb, cancel_to_ap_kb, configs_kb, farm_configs_kb

router = Router()


class APStates(StatesGroup):
    waiting_main_account   = State()
    waiting_pet_id         = State()
    waiting_pet_bulk       = State()
    waiting_pet_threshold  = State()
    waiting_check_interval = State()
    waiting_stuck_timeout  = State()
    waiting_batch_size     = State()


def _build_autopilot_page(user_id: int) -> tuple[str, any]:
    cfg            = get_autopilot_config(user_id)
    running        = cfg["running"]        if cfg else False
    main_account   = cfg["main_account"]   if cfg else None
    config_id      = cfg["config_id"]      if cfg else None
    farm_config_id = cfg["farm_config_id"] if cfg else None
    check_interval = cfg["check_interval"] if cfg else 30
    stuck_timeout  = cfg["stuck_timeout"]  if cfg else 10
    batch_size     = cfg["batch_size"]     if cfg else 10
    pets           = get_autopilot_pets(user_id)
    pet_count      = len(pets)

    lines = ["🤖 <b>Авто-пилот</b>", ""]
    main_str       = f"<code>{main_account}</code>"   if main_account   else "<i>не задан</i>"
    trade_cfg_str  = f"<code>{config_id}</code>"      if config_id      else "<i>не задан</i>"
    farm_cfg_str   = f"<code>{farm_config_id}</code>" if farm_config_id else "<i>не задан</i>"
    lines.append(f"👤 Аккаунт: {main_str}")
    if pets:
        for _, pid, min_count in pets:
            min_str = f" (мин: {min_count})" if min_count > 1 else ""
            lines.append(f"🦆 <code>{pid}</code>{min_str}")
    else:
        lines.append("🦆 Петы: <i>не заданы</i>")
    lines.append(f"🔄 Трейд конфиг: {trade_cfg_str}")
    lines.append(f"🌾 Фарм конфиг: {farm_cfg_str}")
    lines.append(f"⏱ Проверка: <b>{check_interval}с</b>  ·  ⏰ Стак: <b>{stuck_timeout}м</b>  ·  📊 Трейдеров: <b>{batch_size}</b>")
    lines.append("")

    if running:
        farming_count = get_autopilot_farming_count(user_id)
        trading_count = get_autopilot_trading_count(user_id)
        trades_done   = get_autopilot_trades_done(user_id)
        lines.append(
            f"▶️ <b>Запущен</b>  ·  "
            f"Фармит: {farming_count}  ·  "
            f"Торгует: {trading_count}  ·  "
            f"Сделок: {trades_done}"
        )
    else:
        lines.append("⏹ <b>Остановлен</b>")

    return "\n".join(lines), autopilot_kb(main_account, pet_count, config_id, farm_config_id, running, check_interval, stuck_timeout, batch_size)


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


# ─── Задать стак-таймаут ─────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "ap_set_stuck")
async def ap_set_stuck(callback: CallbackQuery, state: FSMContext):
    await state.set_state(APStates.waiting_stuck_timeout)
    await state.update_data(prompt_msg_id=callback.message.message_id)
    await callback.message.edit_text(
        "⏰ Введи стак-таймаут (в минутах):\n\n"
        "<i>Если аккаунт активен дольше этого времени без передачи пета — "
        "он будет заменён следующим из очереди.\n"
        "Допустимо от 1 до 60 минут. Рекомендуется: <code>10</code>.</i>",
        parse_mode="HTML",
        reply_markup=cancel_to_ap_kb(),
    )
    await callback.answer()


@router.message(APStates.waiting_stuck_timeout)
async def ap_receive_stuck(message: Message, state: FSMContext, bot: Bot):
    if not message.text:
        return
    text = message.text.strip()
    await message.delete()
    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")
    if not text.isdigit() or not (1 <= int(text) <= 60):
        await message.answer("❌ Введи число от 1 до 60 минут:", reply_markup=cancel_to_ap_kb())
        return
    save_autopilot_stuck_timeout(message.from_user.id, int(text))
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
        for _, pid, min_count in pets:
            min_str = f"  📊 мин: <b>{min_count}</b>" if min_count > 1 else ""
            lines.append(f"• <code>{pid}</code>{min_str}")
    else:
        lines.append("<i>Нет добавленных петов</i>")
    lines.append("")
    lines.append("<i>📊 — минимум аккаунтов с этим петом для перевода в трейд</i>")
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

    ok_all, all_accounts, _ = await get_trackstats_accounts(ao_key)
    if not ok_all or not all_accounts:
        await callback.message.edit_text(
            "🤖 <b>Авто-пилот</b>\n\n❌ Не удалось получить список аккаунтов.",
            parse_mode="HTML",
            reply_markup=autopilot_kb(cfg["main_account"], pet_count, config_id, farm_config_id, False, cfg.get("check_interval", 30), cfg.get("stuck_timeout", 10), cfg.get("batch_size", 10)),
        )
        return

    face_set, dead_set = await asyncio.gather(
        get_usernames_by_tag(ao_key, "status:face"),
        get_usernames_by_tag(ao_key, "status:dead"),
    )

    main_lower    = cfg["main_account"].lower()
    farm_accounts = []
    all_usernames = []
    for acc in all_accounts:
        username = acc.get("username") or acc.get("name", "")
        if not username:
            continue
        all_usernames.append(username)
        u = username.lower()
        if u == main_lower or u in face_set or u in dead_set:
            continue
        acc_id = str(acc.get("id") or acc.get("account_id", ""))
        farm_accounts.append((acc_id, username))

    if not farm_accounts:
        await callback.message.edit_text(
            "🤖 <b>Авто-пилот</b>\n\nℹ️ Нет доступных аккаунтов для фарма.",
            parse_mode="HTML",
            reply_markup=autopilot_kb(cfg["main_account"], pet_count, config_id, farm_config_id, False, cfg.get("check_interval", 30), cfg.get("stuck_timeout", 10), cfg.get("batch_size", 10)),
        )
        return

    ok_main, _, err_main = await set_accounts_enabled(ao_key, [cfg["main_account"]], True)
    if not ok_main:
        await callback.message.edit_text(
            f"🤖 <b>Авто-пилот</b>\n\n❌ Ошибка включения основного аккаунта: {err_main}",
            parse_mode="HTML",
            reply_markup=autopilot_kb(cfg["main_account"], pet_count, config_id, farm_config_id, False, cfg.get("check_interval", 30), cfg.get("stuck_timeout", 10), cfg.get("batch_size", 10)),
        )
        return

    clear_autopilot_queue(user_id)
    add_autopilot_queue(user_id, farm_accounts, status='farming')
    set_autopilot_started_at(user_id)
    set_autopilot_running(user_id, True)
    add_autopilot_event(user_id, "started")

    await _show_autopilot(callback.message, user_id, edit=True)


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


# ─── Остановка ────────────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "ap_stop")
async def ap_stop(callback: CallbackQuery):
    user_id = callback.from_user.id
    ao_key  = get_panel(user_id)

    await callback.answer("⏹ Останавливаю...")

    if ao_key:
        farming = get_autopilot_farming_entries(user_id)
        trading = get_autopilot_trading_entries(user_id)
        all_active = farming + trading
        if all_active:
            await set_accounts_enabled(ao_key, [u for _, _, u in all_active], False)
        cfg = get_autopilot_config(user_id)
        if cfg and cfg["main_account"]:
            await set_accounts_enabled(ao_key, [cfg["main_account"]], False)

    clear_autopilot_queue(user_id)
    set_autopilot_running(user_id, False)
    add_autopilot_event(user_id, "stopped")
    await _show_autopilot(callback.message, user_id, edit=True)

