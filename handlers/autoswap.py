import asyncio
from aiogram import Router
from aiogram.types import CallbackQuery
from aiogram.exceptions import TelegramBadRequest

from api.accountsops import (
    get_account_folders,
    get_trackstats_accounts,
    get_usernames_by_tag,
    move_accounts_to_folder,
)
from database import (
    get_panel,
    get_autoswap_config,
    save_autoswap_live_folder,
    save_autoswap_dead_folder,
    toggle_autoswap_auto,
    save_autoswap_interval,
    set_autoswap_last_run,
)
from keyboards import autoswap_kb, as_live_folders_kb, as_dead_folders_kb

router = Router()

_INTERVALS = [0.5, 1.0, 2.0, 3.0, 6.0, 12.0, 24.0]


def _build_autoswap_page(user_id: int) -> tuple[str, any]:
    cfg = get_autoswap_config(user_id)
    live_folder_id   = cfg["live_folder_id"]   if cfg else None
    live_folder_name = cfg["live_folder_name"] if cfg else None
    dead_folder_id   = cfg["dead_folder_id"]   if cfg else None
    dead_folder_name = cfg["dead_folder_name"] if cfg else None
    auto_enabled     = cfg["auto_enabled"]     if cfg else False
    interval_hours   = cfg["interval_hours"]   if cfg else 1.0
    last_run_at      = cfg["last_run_at"]      if cfg else None

    lines = ["📂 <b>Sorting</b>", ""]
    lines.append("Раскидывает аккаунты по папкам:")
    lines.append("живые → папка живых,  мёртвые → папка мёртвых.")
    lines.append("")
    lines.append("──────────────────────")
    lines.append("")

    if live_folder_id and live_folder_name:
        lines.append(f"✅ Живые: <b>{live_folder_name}</b>  <i>(ID: {live_folder_id})</i>")
    elif live_folder_id:
        lines.append(f"✅ Живые: <i>папка {live_folder_id}</i>")
    else:
        lines.append("✅ Живые: <i>не выбрана</i>")

    if dead_folder_id and dead_folder_name:
        lines.append(f"💀 Мёртвые: <b>{dead_folder_name}</b>  <i>(ID: {dead_folder_id})</i>")
    elif dead_folder_id:
        lines.append(f"💀 Мёртвые: <i>папка {dead_folder_id}</i>")
    else:
        lines.append("💀 Мёртвые: <i>не выбрана</i>")

    lines.append("")
    last_str = last_run_at[:19].replace("T", " ") if last_run_at else "никогда"
    lines.append(f"🕐 Последний запуск: <code>{last_str}</code>")

    auto_str = "✅" if auto_enabled else "❌"
    hours_str = f"{int(interval_hours)}ч" if interval_hours == int(interval_hours) else f"{interval_hours}ч"
    lines.append(f"🔁 Авто: {auto_str}  ·  ⏱ Интервал: {hours_str}")

    return "\n".join(lines), autoswap_kb(live_folder_id, dead_folder_id, auto_enabled, interval_hours)


async def _show_autoswap(target, user_id: int, edit: bool = False):
    text, kb = _build_autoswap_page(user_id)
    try:
        if edit and hasattr(target, "edit_text"):
            await target.edit_text(text, parse_mode="HTML", reply_markup=kb)
        else:
            await target.answer(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


# ─── Открыть страницу ─────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "autoswap")
async def open_autoswap(callback: CallbackQuery):
    await callback.answer()
    await _show_autoswap(callback.message, callback.from_user.id, edit=True)


@router.callback_query(lambda c: c.data == "as_refresh")
async def as_refresh(callback: CallbackQuery):
    await callback.answer("🔄")
    await _show_autoswap(callback.message, callback.from_user.id, edit=True)


# ─── Выбор папки живых ───────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "as_set_live_folder")
async def as_set_live_folder(callback: CallbackQuery):
    user_id = callback.from_user.id
    ao_key  = get_panel(user_id)
    if not ao_key:
        await callback.answer("❌ AccountsOps не подключён.", show_alert=True)
        return
    await callback.answer()
    ok, folders, err = await get_account_folders(ao_key)
    if not ok or not folders:
        await callback.message.edit_text(
            f"📁 <b>Папки</b>\n\n❌ Не удалось загрузить: {err or 'список пуст'}",
            parse_mode="HTML",
            reply_markup=as_live_folders_kb([]),
        )
        return
    await callback.message.edit_text(
        "✅ <b>Выбери папку для живых аккаунтов:</b>",
        parse_mode="HTML",
        reply_markup=as_live_folders_kb(folders),
    )


@router.callback_query(lambda c: c.data.startswith("as_live:"))
async def as_pick_live_folder(callback: CallbackQuery):
    parts = callback.data.split(":", 2)
    try:
        folder_id   = int(parts[1])
        folder_name = parts[2] if len(parts) > 2 else str(folder_id)
    except (IndexError, ValueError):
        await callback.answer("❌ Ошибка", show_alert=True)
        return
    save_autoswap_live_folder(callback.from_user.id, folder_id, folder_name)
    await callback.answer("✅ Папка живых сохранена")
    await _show_autoswap(callback.message, callback.from_user.id, edit=True)


# ─── Выбор папки мёртвых ─────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "as_set_dead_folder")
async def as_set_dead_folder(callback: CallbackQuery):
    user_id = callback.from_user.id
    ao_key  = get_panel(user_id)
    if not ao_key:
        await callback.answer("❌ AccountsOps не подключён.", show_alert=True)
        return
    await callback.answer()
    ok, folders, err = await get_account_folders(ao_key)
    if not ok or not folders:
        await callback.message.edit_text(
            f"📁 <b>Папки</b>\n\n❌ Не удалось загрузить: {err or 'список пуст'}",
            parse_mode="HTML",
            reply_markup=as_dead_folders_kb([]),
        )
        return
    await callback.message.edit_text(
        "💀 <b>Выбери папку для мёртвых аккаунтов:</b>",
        parse_mode="HTML",
        reply_markup=as_dead_folders_kb(folders),
    )


@router.callback_query(lambda c: c.data.startswith("as_dead:"))
async def as_pick_dead_folder(callback: CallbackQuery):
    parts = callback.data.split(":", 2)
    try:
        folder_id   = int(parts[1])
        folder_name = parts[2] if len(parts) > 2 else str(folder_id)
    except (IndexError, ValueError):
        await callback.answer("❌ Ошибка", show_alert=True)
        return
    save_autoswap_dead_folder(callback.from_user.id, folder_id, folder_name)
    await callback.answer("✅ Папка мёртвых сохранена")
    await _show_autoswap(callback.message, callback.from_user.id, edit=True)


# ─── Авто-настройки ──────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "as_auto_toggle")
async def as_auto_toggle(callback: CallbackQuery):
    new_val = toggle_autoswap_auto(callback.from_user.id)
    await callback.answer("✅ Авто включён" if new_val else "❌ Авто выключен")
    await _show_autoswap(callback.message, callback.from_user.id, edit=True)


@router.callback_query(lambda c: c.data == "as_interval_cycle")
async def as_interval_cycle(callback: CallbackQuery):
    cfg     = get_autoswap_config(callback.from_user.id)
    current = (cfg["interval_hours"] if cfg else 1.0) or 1.0
    try:
        idx      = _INTERVALS.index(current)
        next_val = _INTERVALS[(idx + 1) % len(_INTERVALS)]
    except ValueError:
        next_val = 1.0
    save_autoswap_interval(callback.from_user.id, next_val)
    await callback.answer()
    await _show_autoswap(callback.message, callback.from_user.id, edit=True)


# ─── Запуск сортировки ───────────────────────────────────────────────────────

async def do_sort(ao_key: str, user_id: int) -> tuple[int, int]:
    """Sort all accounts into live/dead folders. Returns (live_count, dead_count)."""
    cfg = get_autoswap_config(user_id)
    if not cfg or not cfg["live_folder_id"] or not cfg["dead_folder_id"]:
        return 0, 0

    live_folder_id = cfg["live_folder_id"]
    dead_folder_id = cfg["dead_folder_id"]

    ok_ts, all_accounts, _ = await get_trackstats_accounts(ao_key)
    if not ok_ts or not all_accounts:
        return 0, 0

    dead_set = await get_usernames_by_tag(ao_key, "status:dead")

    live_usernames: list[str] = []
    dead_usernames: list[str] = []
    for acc in all_accounts:
        username = acc.get("username") or acc.get("name", "")
        if not username:
            continue
        if username.lower() in dead_set:
            dead_usernames.append(username)
        else:
            live_usernames.append(username)

    await asyncio.gather(
        move_accounts_to_folder(ao_key, live_usernames, live_folder_id) if live_usernames else asyncio.sleep(0),
        move_accounts_to_folder(ao_key, dead_usernames, dead_folder_id) if dead_usernames else asyncio.sleep(0),
    )

    set_autoswap_last_run(user_id)
    return len(live_usernames), len(dead_usernames)


@router.callback_query(lambda c: c.data == "as_run")
async def as_run(callback: CallbackQuery):
    user_id = callback.from_user.id
    ao_key  = get_panel(user_id)
    if not ao_key:
        await callback.answer("❌ AccountsOps не подключён.", show_alert=True)
        return

    cfg = get_autoswap_config(user_id)
    if not cfg or not cfg["live_folder_id"] or not cfg["dead_folder_id"]:
        await callback.answer("❌ Сначала задай обе папки.", show_alert=True)
        return

    await callback.answer("⏳ Запускаю...")
    await callback.message.edit_text(
        "📂 <b>Sorting</b>\n\n⏳ Получаю список аккаунтов...",
        parse_mode="HTML",
    )

    live_count, dead_count = await do_sort(ao_key, user_id)

    cfg2 = get_autoswap_config(user_id)
    lines = ["📂 <b>Sorting — готово!</b>", ""]
    lines.append(f"✅ Живых → «{cfg2['live_folder_name'] or cfg2['live_folder_id']}»: <b>{live_count}</b>")
    lines.append(f"💀 Мёртвых → «{cfg2['dead_folder_name'] or cfg2['dead_folder_id']}»: <b>{dead_count}</b>")

    kb = autoswap_kb(
        cfg2["live_folder_id"], cfg2["dead_folder_id"],
        cfg2["auto_enabled"],   cfg2["interval_hours"],
    )
    try:
        await callback.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest:
        pass
