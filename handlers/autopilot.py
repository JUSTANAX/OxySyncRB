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
    save_autopilot_main, save_autopilot_config_id,
    save_autopilot_batch_size, save_autopilot_check_interval, save_autopilot_stuck_timeout,
    set_autopilot_running, set_autopilot_started_at,
    get_autopilot_active_count, get_autopilot_done_count,
    get_autopilot_active_entries, get_autopilot_pending_entries,
    add_autopilot_queue, clear_autopilot_queue,
    set_autopilot_entry_status,
    get_autopilot_pets, add_autopilot_pet, remove_autopilot_pet,
)
from keyboards import autopilot_kb, ap_pets_kb, cancel_to_ap_kb, configs_kb

router = Router()


class APStates(StatesGroup):
    waiting_main_account   = State()
    waiting_pet_id         = State()
    waiting_batch_size     = State()
    waiting_check_interval = State()
    waiting_stuck_timeout  = State()


def _build_autopilot_page(user_id: int) -> tuple[str, any]:
    cfg          = get_autopilot_config(user_id)
    running      = cfg["running"]      if cfg else False
    main_account = cfg["main_account"] if cfg else None
    config_id    = cfg["config_id"]    if cfg else None
    batch_size     = cfg["batch_size"]     if cfg else 10
    check_interval = cfg["check_interval"] if cfg else 30
    stuck_timeout  = cfg["stuck_timeout"]  if cfg else 10
    pets           = get_autopilot_pets(user_id)
    pet_count      = len(pets)

    lines = ["🤖 <b>Авто-пилот</b>", ""]
    main_str   = f"<code>{main_account}</code>" if main_account else "<i>не задан</i>"
    config_str = f"<code>{config_id}</code>"     if config_id    else "<i>не задан</i>"
    lines.append(f"👤 Аккаунт: {main_str}")
    if pets:
        for _, pid in pets:
            lines.append(f"🦆 <code>{pid}</code>")
    else:
        lines.append("🦆 Петы: <i>не заданы</i>")
    lines.append(f"⚙️ Конфиг: {config_str}")
    lines.append(f"👥 Одновременно: <b>{batch_size}</b>  ·  ⏱ Проверка: <b>{check_interval}с</b>  ·  ⏰ Стак: <b>{stuck_timeout}м</b>")
    lines.append("")

    if running:
        active_count  = get_autopilot_active_count(user_id)
        pending_count = len(get_autopilot_pending_entries(user_id))
        done_count    = get_autopilot_done_count(user_id)
        lines.append(
            f"▶️ <b>Запущен</b>  ·  "
            f"Активных: {active_count}/{batch_size}  ·  "
            f"Ожидает: {pending_count}  ·  "
            f"Готово: {done_count}"
        )
    else:
        lines.append("⏹ <b>Остановлен</b>")

    return "\n".join(lines), autopilot_kb(main_account, pet_count, config_id, running, batch_size, check_interval, stuck_timeout)


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


# ─── Задать размер батча ─────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "ap_set_batch")
async def ap_set_batch(callback: CallbackQuery, state: FSMContext):
    await state.set_state(APStates.waiting_batch_size)
    await state.update_data(prompt_msg_id=callback.message.message_id)
    await callback.message.edit_text(
        "👥 Введи количество аккаунтов, которые будут работать одновременно:\n\n"
        "<i>Например: <code>5</code> — бот будет держать 5 активных аккаунтов.\n"
        "Допустимо от 1 до 50.</i>",
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


# ─── Управление петами ───────────────────────────────────────────────────────

def _pets_page_text(user_id: int) -> tuple[str, any]:
    pets = get_autopilot_pets(user_id)
    lines = ["🦆 <b>Петы авто-пилота</b>", ""]
    if pets:
        for _, pid in pets:
            lines.append(f"• <code>{pid}</code>")
    else:
        lines.append("<i>Нет добавленных петов</i>")
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

    cfg = get_autopilot_config(user_id)
    pets = get_autopilot_pets(user_id)
    if not cfg or not cfg["main_account"] or not pets:
        await callback.answer("❌ Задай основной аккаунт и хотя бы один пет.", show_alert=True)
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

    config_id  = cfg.get("config_id")
    pet_count  = len(pets)
    pet_ids    = [pid for _, pid in pets]

    ok_main, _, err_main = await set_accounts_enabled(ao_key, [cfg["main_account"]], True)
    if not ok_main:
        await callback.message.edit_text(
            f"🤖 <b>Авто-пилот</b>\n\n❌ Ошибка включения основного аккаунта: {err_main}",
            parse_mode="HTML",
            reply_markup=autopilot_kb(cfg["main_account"], pet_count, config_id, False),
        )
        return

    await callback.message.edit_text(
        "🤖 <b>Авто-пилот</b>\n\n⏳ Сканирую аккаунты с петами...",
        parse_mode="HTML",
    )

    # Scan all pets in parallel, deduplicate by account_id
    scan_results = await asyncio.gather(
        *[get_accounts_with_pet_details(ao_key, pid) for pid in pet_ids],
        return_exceptions=True,
    )
    seen_ids: set = set()
    accounts: list[tuple] = []
    scan_errors = []
    for res in scan_results:
        if isinstance(res, BaseException):
            continue
        ok, accs, err = res
        if not ok:
            scan_errors.append(err)
            continue
        for acc_id, username in accs:
            if acc_id not in seen_ids:
                seen_ids.add(acc_id)
                accounts.append((acc_id, username))

    if not accounts and scan_errors:
        await set_accounts_enabled(ao_key, [cfg["main_account"]], False)
        await callback.message.edit_text(
            f"🤖 <b>Авто-пилот</b>\n\n❌ Ошибка сканирования: {scan_errors[0]}",
            parse_mode="HTML",
            reply_markup=autopilot_kb(cfg["main_account"], pet_count, config_id, False, auto_en),
        )
        return

    # Fetch face and dead account sets in parallel
    face_set, dead_set = await asyncio.gather(
        get_usernames_by_tag(ao_key, "status:face"),
        get_usernames_by_tag(ao_key, "status:dead"),
    )

    main_lower = cfg["main_account"].lower()
    queue_accounts  = []
    skipped_face    = []
    skipped_dead    = []
    for acc_id, username in accounts:
        u = username.lower()
        if u == main_lower:
            continue
        if u in face_set:
            skipped_face.append(username)
        elif u in dead_set:
            skipped_dead.append(username)
        else:
            queue_accounts.append((acc_id, username))

    if not queue_accounts:
        await set_accounts_enabled(ao_key, [cfg["main_account"]], False)
        skip_lines = []
        if skipped_face:
            skip_lines.append(f"⚠️ С петом, но статус face: <b>{len(skipped_face)}</b>")
        if skipped_dead:
            skip_lines.append(f"💀 С петом, но мёртвые: <b>{len(skipped_dead)}</b>")
        msg = "🤖 <b>Авто-пилот</b>\n\nℹ️ Рабочих аккаунтов с петами не найдено.\nОсновной аккаунт отключён."
        if skip_lines:
            msg += "\n\n" + "\n".join(skip_lines)
        await callback.message.edit_text(
            msg,
            parse_mode="HTML",
            reply_markup=autopilot_kb(cfg["main_account"], pet_count, config_id, False),
        )
        return

    clear_autopilot_queue(user_id)
    add_autopilot_queue(user_id, queue_accounts)
    set_autopilot_started_at(user_id)
    set_autopilot_running(user_id, True)

    if config_id:
        all_usernames = [u for _, u in queue_accounts]
        await set_accounts_config(ao_key, all_usernames, config_id)

    batch_size  = cfg.get("batch_size") or 10
    first_batch = get_autopilot_pending_entries(user_id)[:batch_size]
    if first_batch:
        await set_accounts_enabled(ao_key, [u for _, _, u in first_batch], True)
        for entry_id, _, _ in first_batch:
            set_autopilot_entry_status(entry_id, "active")

    # Build skip notice if any accounts were filtered out
    skip_parts = []
    if skipped_face:
        skip_parts.append(f"⚠️ Пропущено (face): <b>{len(skipped_face)}</b>")
    if skipped_dead:
        skip_parts.append(f"💀 Пропущено (мёртвые): <b>{len(skipped_dead)}</b>")

    if skip_parts:
        await callback.message.answer(
            "🤖 <b>Авто-пилот</b> — найдены пропущенные аккаунты\n\n"
            + "\n".join(skip_parts)
            + f"\n✅ В очереди: <b>{len(queue_accounts)}</b>",
            parse_mode="HTML",
        )

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

