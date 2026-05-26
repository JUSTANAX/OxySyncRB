import asyncio
from collections import defaultdict
from aiogram import Router
from aiogram.types import CallbackQuery
from aiogram.exceptions import TelegramBadRequest

from api.accountsops import (
    get_account_folders,
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
    lines.append("Сортирует все аккаунты по папкам.")
    lines.append("")
    lines.append("📁 <b>Dead &amp; Face</b> — одна общая папка:")
    lines.append("  <b>input</b>  → Мёртвые (status:dead)")
    lines.append("  <b>output</b> → Face-lock (status:face)")
    lines.append("")
    lines.append("📁 <b>Папки девайсов</b>:")
    lines.append("  <b>input</b>  → Только живые аккаунты")
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

SPECIAL_FOLDER = "Dead & Face"


async def do_sort(ao_key: str, user_id: int) -> dict:
    """
    Special folder "Dead & Face":
      input  → all dead accounts (status:dead)
      output → all face-lock accounts (status:face)
    Device folders:
      input  → live accounts only (not dead, not face)
    /api/accounts returns ALL accounts with tags + folder_section, so no
    per-folder fetches are needed.
    Returns {devices, live, dead, face, created}.
    """
    (ok_acc, all_accounts, _), (ok_fld, existing_folders, _), dead_set, face_set = await asyncio.gather(
        get_all_accounts(ao_key),
        get_account_folders(ao_key),
        get_usernames_by_tag(ao_key, "status:dead"),
        get_usernames_by_tag(ao_key, "status:face"),
    )

    existing_folders = existing_folders if ok_fld else []
    folder_map: dict[str, int] = {f["name"]: f["id"] for f in existing_folders}

    if not ok_acc or not all_accounts:
        return {"devices": 0, "live": 0, "dead": 0, "face": 0, "created": 0}

    # Primary source: tags array on every account object returned by /api/accounts
    for acc in all_accounts:
        u = (acc.get("username") or acc.get("name") or "").strip().lower()
        if not u:
            continue
        raw_tags = acc.get("tags") or []
        if isinstance(raw_tags, list):
            tag_strs = {str(t).lower() for t in raw_tags}
            if "status:dead" in tag_strs:
                dead_set.add(u)
            elif "status:face" in tag_strs:
                face_set.add(u)

    # Fallback: accounts in Dead & Face folder keep their section classification
    # (covers accounts whose tags were cleared but are still in the special folder)
    for acc in all_accounts:
        u = (acc.get("username") or acc.get("name") or "").strip().lower()
        if not u or u in dead_set or u in face_set:
            continue
        if (acc.get("folder_name") or "").strip() == SPECIAL_FOLDER:
            section = (acc.get("folder_section") or acc.get("section") or "").lower()
            if section == "input":
                dead_set.add(u)
            elif section == "output":
                face_set.add(u)

    # dead takes priority
    face_set -= dead_set

    # Deduplicate by username
    seen: set[str] = set()
    deduped: list = []
    for acc in all_accounts:
        u = (acc.get("username") or acc.get("name") or "").strip().lower()
        if u and u not in seen:
            seen.add(u)
            deduped.append(acc)

    # Ensure Dead & Face folder exists
    if SPECIAL_FOLDER not in folder_map:
        ok_c, new_folder, _ = await create_folder(ao_key, SPECIAL_FOLDER)
        if ok_c and new_folder and new_folder.get("id"):
            folder_map[SPECIAL_FOLDER] = new_folder["id"]

    # Classify
    dead_list: list[str] = []
    face_list: list[str] = []
    NO_DEVICE_KEY = "No Device"
    by_device: dict[str, list[str]] = defaultdict(list)

    for acc in deduped:
        username  = (acc.get("username") or acc.get("name") or "").strip()
        device_id = (acc.get("device_id") or "").strip()
        if not username:
            continue
        u = username.lower()
        if u in dead_set:
            dead_list.append(username)
        elif u in face_set:
            face_list.append(username)
        else:
            by_device[device_id or NO_DEVICE_KEY].append(username)

    created = 0

    # Move dead + face into special folder
    if SPECIAL_FOLDER in folder_map:
        special_id = folder_map[SPECIAL_FOLDER]
        tasks = []
        if dead_list:
            tasks.append(move_accounts_to_folder(ao_key, dead_list, special_id, section="input"))
        if face_list:
            tasks.append(move_accounts_to_folder(ao_key, face_list, special_id, section="output"))
        if tasks:
            await asyncio.gather(*tasks)

    # Move live accounts into per-device folders
    total_live = 0
    for device_id, usernames in by_device.items():
        folder_name = device_id
        if folder_name not in folder_map:
            ok_c, new_folder, _ = await create_folder(ao_key, folder_name)
            if not ok_c or not new_folder or not new_folder.get("id"):
                continue
            folder_map[folder_name] = new_folder["id"]
            created += 1
        folder_id = folder_map[folder_name]
        await move_accounts_to_folder(ao_key, usernames, folder_id, section="input")
        total_live += len(usernames)

    set_autoswap_last_run(user_id)
    return {
        "devices": len(by_device),
        "live":    total_live,
        "dead":    len(dead_list),
        "face":    len(face_list),
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
    lines.append(f"✅ Живых (девайсы → input): <b>{stats['live']}</b>")
    lines.append(f"💀 Мёртвых (Dead & Face → input): <b>{stats['dead']}</b>")
    lines.append(f"🔒 Face-lock (Dead & Face → output): <b>{stats['face']}</b>")

    try:
        await callback.message.edit_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=autoswap_kb(auto_enabled, interval_hours),
        )
    except TelegramBadRequest:
        pass
