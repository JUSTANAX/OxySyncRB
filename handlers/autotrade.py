from datetime import datetime
from aiogram import Router, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest

from api.accountsops import set_accounts_enabled, set_accounts_config, get_configs
from database import (
    get_panel,
    get_autotrade_config,
    save_autotrade_config_id,
    set_autotrade_running,
    set_autotrade_started_at,
    get_autotrade_accounts,
    add_autotrade_account,
    add_autotrade_accounts_bulk,
    clear_autotrade_accounts,
)
from keyboards import autotrade_kb, at_configs_kb, cancel_to_at_kb

router = Router()


class ATStates(StatesGroup):
    waiting_account = State()
    waiting_bulk    = State()


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


def _build_autotrade_page(user_id: int) -> tuple[str, any]:
    cfg        = get_autotrade_config(user_id)
    running    = cfg["running"]    if cfg else False
    config_id  = cfg["config_id"] if cfg else None
    started_at = cfg["started_at"] if cfg else None
    accounts   = get_autotrade_accounts(user_id)

    lines = ["💰 <b>Авто-трейд</b>", ""]

    if running:
        rt = _runtime_str(started_at)
        rt_part = f"  ·  🕐 {rt}" if rt else ""
        lines.append(f"▶️ <b>Работает</b>{rt_part}")
        lines.append(f"  🔄 Аккаунтов в трейде: <b>{len(accounts)}</b>")
    else:
        lines.append("⏹ <b>Остановлен</b>")

    lines.append("")
    lines.append("──────────────────────")
    lines.append("")

    cfg_str = f"<code>{config_id}</code>" if config_id else "<i>не задан</i>"
    lines.append(f"⚙️ Конфиг: {cfg_str}")
    lines.append("")

    if accounts:
        lines.append(f"👥 Аккаунтов: <b>{len(accounts)}</b>")
        for u in accounts[:20]:
            lines.append(f"  · <code>{u}</code>")
        if len(accounts) > 20:
            lines.append(f"  <i>... и ещё {len(accounts) - 20}</i>")
    else:
        lines.append("👥 <i>Аккаунты не добавлены</i>")

    return "\n".join(lines), autotrade_kb(config_id, len(accounts), running)


async def _show_autotrade(target, user_id: int, edit: bool = False):
    text, kb = _build_autotrade_page(user_id)
    try:
        if edit and hasattr(target, "edit_text"):
            await target.edit_text(text, parse_mode="HTML", reply_markup=kb)
        else:
            await target.answer(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


# ─── Открыть страницу ─────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "autotrade")
async def open_autotrade(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    await _show_autotrade(callback.message, callback.from_user.id, edit=True)


@router.callback_query(lambda c: c.data == "at_refresh")
async def at_refresh(callback: CallbackQuery):
    await callback.answer("🔄")
    await _show_autotrade(callback.message, callback.from_user.id, edit=True)


# ─── Конфиг ───────────────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "at_set_config")
async def at_set_config(callback: CallbackQuery):
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
            reply_markup=cancel_to_at_kb(),
        )
        return
    await callback.message.edit_text(
        "⚙️ <b>Выбери конфиг для Авто-трейда</b>:",
        parse_mode="HTML",
        reply_markup=at_configs_kb(configs),
    )


@router.callback_query(lambda c: c.data.startswith("at_cfg:"))
async def at_pick_config(callback: CallbackQuery):
    try:
        config_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("❌ Ошибка", show_alert=True)
        return
    save_autotrade_config_id(callback.from_user.id, config_id)
    await callback.answer(f"✅ Конфиг {config_id} сохранён")
    await _show_autotrade(callback.message, callback.from_user.id, edit=True)


# ─── Добавить аккаунт ─────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "at_add_account")
async def at_add_account(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ATStates.waiting_account)
    await state.update_data(prompt_msg_id=callback.message.message_id)
    await callback.message.edit_text(
        "👤 Введи <b>username</b> аккаунта:\n\n"
        "<i>Например: <code>myaccount123</code></i>",
        parse_mode="HTML",
        reply_markup=cancel_to_at_kb(),
    )
    await callback.answer()


@router.message(ATStates.waiting_account)
async def at_receive_account(message: Message, state: FSMContext, bot: Bot):
    if not message.text:
        return
    username = message.text.strip()
    await message.delete()
    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")
    if not username:
        await message.answer("❌ Введи корректный username:", reply_markup=cancel_to_at_kb())
        return
    added = add_autotrade_account(message.from_user.id, username)
    await state.clear()
    await message.answer("✅ Аккаунт добавлен" if added else "ℹ️ Такой аккаунт уже есть")
    text, kb = _build_autotrade_page(message.from_user.id)
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


# ─── Bulk-добавление ──────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "at_bulk_accounts")
async def at_bulk_accounts(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ATStates.waiting_bulk)
    await state.update_data(prompt_msg_id=callback.message.message_id)
    await callback.message.edit_text(
        "📋 <b>Bulk-добавление аккаунтов</b>\n\n"
        "Отправь usernames — каждый с новой строки:\n\n"
        "<code>account1\naccount2\naccount3</code>",
        parse_mode="HTML",
        reply_markup=cancel_to_at_kb(),
    )
    await callback.answer()


@router.message(ATStates.waiting_bulk)
async def at_receive_bulk(message: Message, state: FSMContext, bot: Bot):
    if not message.text:
        return
    await message.delete()
    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")
    raw       = [line.strip() for line in message.text.splitlines()]
    usernames = [u for u in raw if u]
    if not usernames:
        await message.answer("❌ Не найдено ни одного аккаунта:", reply_markup=cancel_to_at_kb())
        return
    added, skipped = add_autotrade_accounts_bulk(message.from_user.id, usernames)
    await state.clear()
    notice = f"✅ Добавлено: <b>{added}</b>"
    if skipped:
        notice += f"  ·  ⚠️ Уже было: <b>{skipped}</b>"
    await message.answer(notice, parse_mode="HTML")
    text, kb = _build_autotrade_page(message.from_user.id)
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


# ─── Очистить список ──────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "at_clear")
async def at_clear(callback: CallbackQuery):
    clear_autotrade_accounts(callback.from_user.id)
    await callback.answer("🗑 Список очищен")
    await _show_autotrade(callback.message, callback.from_user.id, edit=True)


# ─── Запуск ───────────────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "at_start")
async def at_start(callback: CallbackQuery):
    user_id  = callback.from_user.id
    ao_key   = get_panel(user_id)
    if not ao_key:
        await callback.answer("❌ AccountsOps не подключён.", show_alert=True)
        return

    cfg      = get_autotrade_config(user_id)
    accounts = get_autotrade_accounts(user_id)

    if not accounts:
        await callback.answer("❌ Добавь хотя бы один аккаунт.", show_alert=True)
        return
    if not cfg or not cfg.get("config_id"):
        await callback.answer("❌ Задай конфиг.", show_alert=True)
        return

    await callback.answer("⏳ Запускаю...")
    config_id = cfg["config_id"]

    await callback.message.edit_text(
        "💰 <b>Авто-трейд</b>\n\n⏳ Применяю конфиг...",
        parse_mode="HTML",
    )
    await set_accounts_config(ao_key, accounts, config_id)

    await callback.message.edit_text(
        "💰 <b>Авто-трейд</b>\n\n⏳ Включаю аккаунты...",
        parse_mode="HTML",
    )
    await set_accounts_enabled(ao_key, accounts, True)

    set_autotrade_started_at(user_id)
    set_autotrade_running(user_id, True)
    await _show_autotrade(callback.message, user_id, edit=True)


# ─── Остановка ────────────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "at_stop")
async def at_stop(callback: CallbackQuery):
    user_id = callback.from_user.id
    ao_key  = get_panel(user_id)

    await callback.answer("⏹ Останавливаю...")

    if ao_key:
        accounts = get_autotrade_accounts(user_id)
        if accounts:
            await set_accounts_enabled(ao_key, accounts, False)

    set_autotrade_running(user_id, False)
    await _show_autotrade(callback.message, user_id, edit=True)
