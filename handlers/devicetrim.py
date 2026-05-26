import asyncio
from collections import defaultdict
from aiogram import Router
from aiogram.types import CallbackQuery
from aiogram.exceptions import TelegramBadRequest

from api.accountsops import (
    get_all_accounts,
    get_usernames_by_tag,
    unassign_accounts_from_device,
)
from database import (
    get_panel,
    get_devicetrim_config,
    toggle_devicetrim_auto,
    save_devicetrim_interval,
    save_devicetrim_max,
    set_devicetrim_last_run,
)
from keyboards import devicetrim_kb

router = Router()

_INTERVALS  = [0.5, 1.0, 2.0, 3.0, 6.0, 12.0, 24.0]
_MAX_VALUES = [50, 100, 150, 200, 250, 300, 350, 400, 500]


def _build_page(user_id: int) -> tuple[str, any]:
    cfg            = get_devicetrim_config(user_id)
    auto_enabled   = cfg["auto_enabled"]   if cfg else False
    interval_hours = cfg["interval_hours"] if cfg else 1.0
    max_per_device = cfg["max_per_device"] if cfg else 300
    last_run_at    = cfg["last_run_at"]    if cfg else None

    lines = ["✂️ <b>Trim</b>", ""]
    lines.append("Обрезает лишние аккаунты с каждого девайса до заданного лимита.")
    lines.append("Лишние аккаунты уходят в <b>No Device</b>.")
    lines.append("Приоритет на удаление: мёртвые и face-lock первыми.")
    lines.append("")
    lines.append(f"📊 Лимит: <b>{max_per_device}</b> аккаунтов на девайс")
    lines.append("")
    lines.append("──────────────────────")
    lines.append("")

    last_str = last_run_at[:19].replace("T", " ") if last_run_at else "никогда"
    lines.append(f"🕐 Последний запуск: <code>{last_str}</code>")

    auto_str  = "✅" if auto_enabled else "❌"
    hours_str = f"{int(interval_hours)}ч" if interval_hours == int(interval_hours) else f"{interval_hours}ч"
    lines.append(f"🔁 Авто: {auto_str}  ·  ⏱ Интервал: {hours_str}")

    return "\n".join(lines), devicetrim_kb(auto_enabled, interval_hours, max_per_device)


async def _show(target, user_id: int, edit: bool = False):
    text, kb = _build_page(user_id)
    try:
        if edit and hasattr(target, "edit_text"):
            await target.edit_text(text, parse_mode="HTML", reply_markup=kb)
        else:
            await target.answer(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


@router.callback_query(lambda c: c.data == "devicetrim")
async def open_devicetrim(callback: CallbackQuery):
    await callback.answer()
    await _show(callback.message, callback.from_user.id, edit=True)


@router.callback_query(lambda c: c.data == "dt_refresh")
async def dt_refresh(callback: CallbackQuery):
    await callback.answer("🔄")
    await _show(callback.message, callback.from_user.id, edit=True)


@router.callback_query(lambda c: c.data == "dt_auto_toggle")
async def dt_auto_toggle(callback: CallbackQuery):
    new_val = toggle_devicetrim_auto(callback.from_user.id)
    await callback.answer("✅ Авто включён" if new_val else "❌ Авто выключен")
    await _show(callback.message, callback.from_user.id, edit=True)


@router.callback_query(lambda c: c.data == "dt_interval_cycle")
async def dt_interval_cycle(callback: CallbackQuery):
    cfg     = get_devicetrim_config(callback.from_user.id)
    current = (cfg["interval_hours"] if cfg else 1.0) or 1.0
    try:
        idx      = _INTERVALS.index(current)
        next_val = _INTERVALS[(idx + 1) % len(_INTERVALS)]
    except ValueError:
        next_val = 1.0
    save_devicetrim_interval(callback.from_user.id, next_val)
    await callback.answer()
    await _show(callback.message, callback.from_user.id, edit=True)


@router.callback_query(lambda c: c.data == "dt_max_cycle")
async def dt_max_cycle(callback: CallbackQuery):
    cfg     = get_devicetrim_config(callback.from_user.id)
    current = (cfg["max_per_device"] if cfg else 300) or 300
    try:
        idx      = _MAX_VALUES.index(current)
        next_val = _MAX_VALUES[(idx + 1) % len(_MAX_VALUES)]
    except ValueError:
        next_val = 300
    save_devicetrim_max(callback.from_user.id, next_val)
    await callback.answer()
    await _show(callback.message, callback.from_user.id, edit=True)


async def do_trim(ao_key: str, user_id: int, max_per_device: int) -> dict:
    """
    For each device with more than max_per_device accounts:
    unassign the excess, removing dead/face accounts first.
    Unassigned accounts naturally become No Device.
    Returns {devices, trimmed}.
    """
    (ok_acc, all_accounts, _), dead_set, face_set = await asyncio.gather(
        get_all_accounts(ao_key),
        get_usernames_by_tag(ao_key, "status:dead"),
        get_usernames_by_tag(ao_key, "status:face"),
    )

    if not ok_acc or not all_accounts:
        return {"devices": 0, "trimmed": 0}

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

    bad_set = dead_set | face_set

    by_device: dict[str, list[tuple[str, bool]]] = defaultdict(list)
    for acc in all_accounts:
        username  = (acc.get("username") or acc.get("name") or "").strip()
        device_id = (acc.get("device_id") or "").strip()
        if not username or not device_id:
            continue
        by_device[device_id].append((username, username.lower() in bad_set))

    total_trimmed    = 0
    affected_devices = 0

    for device_id, acc_list in by_device.items():
        if len(acc_list) <= max_per_device:
            continue
        # bad accounts (dead/face) removed first
        acc_list.sort(key=lambda x: (0 if x[1] else 1))
        excess    = len(acc_list) - max_per_device
        to_remove = [username for username, _ in acc_list[:excess]]
        await unassign_accounts_from_device(ao_key, device_id, to_remove)
        total_trimmed    += len(to_remove)
        affected_devices += 1

    set_devicetrim_last_run(user_id)
    return {"devices": affected_devices, "trimmed": total_trimmed}


@router.callback_query(lambda c: c.data == "dt_run")
async def dt_run(callback: CallbackQuery):
    user_id = callback.from_user.id
    ao_key  = get_panel(user_id)
    if not ao_key:
        await callback.answer("❌ AccountsOps не подключён.", show_alert=True)
        return

    cfg            = get_devicetrim_config(user_id)
    max_per_device = (cfg["max_per_device"] if cfg else 300) or 300

    await callback.answer("⏳ Запускаю...")
    await callback.message.edit_text(
        "✂️ <b>Trim</b>\n\n⏳ Получаю аккаунты...",
        parse_mode="HTML",
    )

    stats = await do_trim(ao_key, user_id, max_per_device)

    cfg            = get_devicetrim_config(user_id)
    auto_enabled   = cfg["auto_enabled"]   if cfg else False
    interval_hours = cfg["interval_hours"] if cfg else 1.0

    lines = ["✂️ <b>Trim — готово!</b>", ""]
    if stats["devices"] == 0:
        lines.append("ℹ️ Все девайсы в пределах лимита.")
    else:
        lines.append(f"📱 Девайсов обрезано: <b>{stats['devices']}</b>")
        lines.append(f"📤 Перемещено в No Device: <b>{stats['trimmed']}</b>")

    try:
        await callback.message.edit_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=devicetrim_kb(auto_enabled, interval_hours, max_per_device),
        )
    except TelegramBadRequest:
        pass
