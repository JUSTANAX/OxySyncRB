import asyncio
from collections import defaultdict
from aiogram import Router
from aiogram.types import CallbackQuery
from aiogram.exceptions import TelegramBadRequest

from api.accountsops import (
    get_account_folders,
    get_folder_accounts,
    get_all_accounts,
    get_usernames_by_tag,
    move_accounts_to_folder,
    create_folder,
)
from database import (
    get_panel,
    get_autoswap_config,
    toggle_autoswap_auto,
    save_autoswap_interval,
    set_autoswap_last_run,
)
from keyboards import autoswap_kb

router = Router()

_INTERVALS = [0.5, 1.0, 2.0, 3.0, 6.0, 12.0, 24.0]


def _build_autoswap_page(user_id: int) -> tuple[str, any]:
    cfg            = get_autoswap_config(user_id)
    auto_enabled   = cfg["auto_enabled"]   if cfg else False
    interval_hours = cfg["interval_hours"] if cfg else 1.0
    last_run_at    = cfg["last_run_at"]    if cfg else None

    lines = ["📂 <b>Sorting</b>", ""]
    lines.append("Сортирует все аккаунты по девайсам.")
    lines.append("Для каждого девайса — своя папка:")
    lines.append("  <b>input</b>  → Живые (без status:dead)")
    lines.append("  <b>output</b> → Мёртвые (status:dead)")
    lines.append("")
    lines.append("──────────────────────")
    lines.append("")

    last_str = last_run_at[:19].replace("T", " ") if last_run_at else "никогда"
    lines.append(f"🕐 Последний запуск: <code>{last_str}</code>")

    auto_str  = "✅" if auto_enabled else "❌"
    hours_str = f"{int(interval_hours)}ч" if interval_hours == int(interval_hours) else f"{interval_hours}ч"
    lines.append(f"🔁 Авто: {auto_str}  ·  ⏱ Интервал: {hours_str}")

    return "\n".join(lines), autoswap_kb(auto_enabled, interval_hours)


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


# ─── Страница ────────────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "autoswap")
async def open_autoswap(callback: CallbackQuery):
    await callback.answer()
    await _show_autoswap(callback.message, callback.from_user.id, edit=True)


@router.callback_query(lambda c: c.data == "as_refresh")
async def as_refresh(callback: CallbackQuery):
    await callback.answer("🔄")
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


# ─── Сортировка ──────────────────────────────────────────────────────────────

async def do_sort(ao_key: str, user_id: int) -> dict:
    """
    Groups accounts by device_id, creates a folder per device if needed,
    moves live accounts to section=input and dead to section=output.
    Returns {devices, live, dead, created_folders}.
    """
    (ok_acc, unfoldered, _), (ok_fld, existing_folders, _), dead_set = await asyncio.gather(
        get_all_accounts(ao_key),
        get_account_folders(ao_key),
        get_usernames_by_tag(ao_key, "status:dead"),
    )

    existing_folders = existing_folders if ok_fld else []

    # collect accounts already in folders (parallel)
    foldered: list = []
    if existing_folders:
        results = await asyncio.gather(
            *[get_folder_accounts(ao_key, f["id"]) for f in existing_folders],
            return_exceptions=True,
        )
        for r in results:
            if not isinstance(r, BaseException):
                ok, accs, _ = r
                if ok:
                    foldered.extend(accs)

    # merge unfoldered + foldered, deduplicate by username
    seen: set[str] = set()
    all_accounts: list = []
    for acc in (unfoldered if ok_acc else []) + foldered:
        u = (acc.get("username") or acc.get("name") or "").strip().lower()
        if u and u not in seen:
            seen.add(u)
            all_accounts.append(acc)

    if not all_accounts:
        return {"devices": 0, "live": 0, "dead": 0, "created": 0}

    # existing folders: name → id
    folder_map: dict[str, int] = {f["name"]: f["id"] for f in existing_folders}

    # group by device_id; accounts without a device go to a dedicated bucket
    NO_DEVICE_KEY = "No Device"
    by_device: dict[str, list[str]] = defaultdict(list)
    for acc in all_accounts:
        device_id = (acc.get("device_id") or "").strip()
        username  = (acc.get("username") or acc.get("name") or "").strip()
        if not username:
            continue
        by_device[device_id or NO_DEVICE_KEY].append(username)

    total_live = total_dead = created = 0

    for device_id, usernames in by_device.items():
        folder_name = device_id

        if folder_name not in folder_map:
            ok_c, new_folder, _ = await create_folder(ao_key, folder_name)
            if not ok_c or not new_folder or not new_folder.get("id"):
                continue
            folder_map[folder_name] = new_folder["id"]
            created += 1

        folder_id = folder_map[folder_name]

        live_list = [u for u in usernames if u.lower() not in dead_set]
        dead_list = [u for u in usernames if u.lower() in dead_set]

        tasks = []
        if live_list:
            tasks.append(move_accounts_to_folder(ao_key, live_list, folder_id, section="input"))
        if dead_list:
            tasks.append(move_accounts_to_folder(ao_key, dead_list, folder_id, section="output"))
        if tasks:
            await asyncio.gather(*tasks)

        total_live += len(live_list)
        total_dead += len(dead_list)

    set_autoswap_last_run(user_id)
    return {
        "devices": len(by_device),
        "live":    total_live,
        "dead":    total_dead,
        "created": created,
    }


@router.callback_query(lambda c: c.data == "as_run")
async def as_run(callback: CallbackQuery):
    user_id = callback.from_user.id
    ao_key  = get_panel(user_id)
    if not ao_key:
        await callback.answer("❌ AccountsOps не подключён.", show_alert=True)
        return

    await callback.answer("⏳ Запускаю...")
    await callback.message.edit_text(
        "📂 <b>Sorting</b>\n\n⏳ Получаю аккаунты и папки...",
        parse_mode="HTML",
    )

    stats = await do_sort(ao_key, user_id)

    cfg            = get_autoswap_config(user_id)
    auto_enabled   = cfg["auto_enabled"]   if cfg else False
    interval_hours = cfg["interval_hours"] if cfg else 1.0

    lines = ["📂 <b>Sorting — готово!</b>", ""]
    lines.append(f"📱 Девайсов обработано: <b>{stats['devices']}</b>")
    if stats["created"]:
        lines.append(f"🆕 Папок создано: <b>{stats['created']}</b>")
    lines.append(f"✅ Живых (input): <b>{stats['live']}</b>")
    lines.append(f"💀 Мёртвых (output): <b>{stats['dead']}</b>")

    try:
        await callback.message.edit_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=autoswap_kb(auto_enabled, interval_hours),
        )
    except TelegramBadRequest:
        pass
