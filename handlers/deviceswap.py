import asyncio
from aiogram import Router
from aiogram.types import CallbackQuery
from aiogram.exceptions import TelegramBadRequest

from api.accountsops import (
    get_all_accounts,
    get_account_folders,
    get_folder_accounts,
    get_usernames_by_tag,
    assign_accounts_to_device,
    unassign_accounts_from_device,
)
from database import (
    get_panel,
    get_deviceswap_config,
    toggle_deviceswap_auto,
    save_deviceswap_interval,
    set_deviceswap_last_run,
)
from keyboards import deviceswap_kb

router = Router()

_INTERVALS = [0.5, 1.0, 2.0, 3.0, 6.0, 12.0, 24.0]


def _build_page(user_id: int) -> tuple[str, any]:
    cfg            = get_deviceswap_config(user_id)
    auto_enabled   = cfg["auto_enabled"]   if cfg else False
    interval_hours = cfg["interval_hours"] if cfg else 1.0
    last_run_at    = cfg["last_run_at"]    if cfg else None

    lines = ["🔄 <b>AutoSwap</b>", ""]
    lines.append("Заменяет мёртвые и face-lock аккаунты на рабочие на каждом девайсе.")
    lines.append("")
    lines.append("Для каждого девайса:")
    lines.append("  — находит аккаунты с тегом <code>status:dead</code> или <code>status:face</code>")
    lines.append("  — отвязывает их от девайса")
    lines.append(f"  — берёт замену из папки <code>{NO_DEVICE_FOLDER}</code> (Sorting)")
    lines.append("  — привязывает их к девайсу")
    lines.append("")
    lines.append("──────────────────────")
    lines.append("")

    last_str = last_run_at[:19].replace("T", " ") if last_run_at else "никогда"
    lines.append(f"🕐 Последний запуск: <code>{last_str}</code>")

    auto_str  = "✅" if auto_enabled else "❌"
    hours_str = f"{int(interval_hours)}ч" if interval_hours == int(interval_hours) else f"{interval_hours}ч"
    lines.append(f"🔁 Авто: {auto_str}  ·  ⏱ Интервал: {hours_str}")

    return "\n".join(lines), deviceswap_kb(auto_enabled, interval_hours)


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


@router.callback_query(lambda c: c.data == "deviceswap")
async def open_deviceswap(callback: CallbackQuery):
    await callback.answer()
    await _show(callback.message, callback.from_user.id, edit=True)


@router.callback_query(lambda c: c.data == "ds_refresh")
async def ds_refresh(callback: CallbackQuery):
    await callback.answer("🔄")
    await _show(callback.message, callback.from_user.id, edit=True)


@router.callback_query(lambda c: c.data == "ds_auto_toggle")
async def ds_auto_toggle(callback: CallbackQuery):
    new_val = toggle_deviceswap_auto(callback.from_user.id)
    await callback.answer("✅ Авто включён" if new_val else "❌ Авто выключен")
    await _show(callback.message, callback.from_user.id, edit=True)


@router.callback_query(lambda c: c.data == "ds_interval_cycle")
async def ds_interval_cycle(callback: CallbackQuery):
    cfg     = get_deviceswap_config(callback.from_user.id)
    current = (cfg["interval_hours"] if cfg else 1.0) or 1.0
    try:
        idx      = _INTERVALS.index(current)
        next_val = _INTERVALS[(idx + 1) % len(_INTERVALS)]
    except ValueError:
        next_val = 1.0
    save_deviceswap_interval(callback.from_user.id, next_val)
    await callback.answer()
    await _show(callback.message, callback.from_user.id, edit=True)


NO_DEVICE_FOLDER = "No Device"


async def do_device_swap(ao_key: str, user_id: int) -> dict:
    """
    For each device: unassign dead/face accounts, assign replacements from
    the "No Device" folder (created by Sorting for accounts without a device).
    Returns {devices, replaced, no_reserve}.
    """
    (ok_acc, all_accounts, _), (ok_fld, all_folders, _), dead_set, face_set = await asyncio.gather(
        get_all_accounts(ao_key),
        get_account_folders(ao_key),
        get_usernames_by_tag(ao_key, "status:dead"),
        get_usernames_by_tag(ao_key, "status:face"),
    )

    if not ok_acc or not all_accounts:
        return {"devices": 0, "replaced": 0, "no_reserve": 0}

    bad_set = dead_set | face_set

    # Load reserve from "No Device" folder
    reserve: list[str] = []
    no_device_folder = next(
        (f for f in (all_folders or []) if f.get("name") == NO_DEVICE_FOLDER), None
    )
    if no_device_folder:
        ok_r, folder_accs, _ = await get_folder_accounts(ao_key, no_device_folder["id"])
        if ok_r:
            for acc in folder_accs:
                username = (acc.get("username") or acc.get("name") or "").strip()
                if username and username.lower() not in bad_set:
                    reserve.append(username)

    # Find bad accounts per device
    by_device: dict[str, list[str]] = {}
    for acc in all_accounts:
        username  = (acc.get("username") or acc.get("name") or "").strip()
        device_id = (acc.get("device_id") or "").strip()
        if not username or not device_id:
            continue
        if username.lower() in bad_set:
            by_device.setdefault(device_id, []).append(username)

    if not by_device:
        set_deviceswap_last_run(user_id)
        return {"devices": 0, "replaced": 0, "no_reserve": 0}

    total_replaced   = 0
    total_no_reserve = 0
    reserve_idx      = 0

    for device_id, bad_usernames in by_device.items():
        await unassign_accounts_from_device(ao_key, device_id, bad_usernames)

        slots     = len(bad_usernames)
        to_assign = reserve[reserve_idx:reserve_idx + slots]

        if to_assign:
            await assign_accounts_to_device(ao_key, device_id, to_assign)
            total_replaced += len(to_assign)
            reserve_idx    += len(to_assign)

        shortage = slots - len(to_assign)
        if shortage > 0:
            total_no_reserve += shortage

    set_deviceswap_last_run(user_id)
    return {
        "devices":    len(by_device),
        "replaced":   total_replaced,
        "no_reserve": total_no_reserve,
    }


@router.callback_query(lambda c: c.data == "ds_run")
async def ds_run(callback: CallbackQuery):
    user_id = callback.from_user.id
    ao_key  = get_panel(user_id)
    if not ao_key:
        await callback.answer("❌ AccountsOps не подключён.", show_alert=True)
        return

    await callback.answer("⏳ Запускаю...")
    await callback.message.edit_text(
        "🔄 <b>AutoSwap</b>\n\n⏳ Получаю аккаунты...",
        parse_mode="HTML",
    )

    stats = await do_device_swap(ao_key, user_id)

    cfg            = get_deviceswap_config(user_id)
    auto_enabled   = cfg["auto_enabled"]   if cfg else False
    interval_hours = cfg["interval_hours"] if cfg else 1.0

    lines = ["🔄 <b>AutoSwap — готово!</b>", ""]
    if stats["devices"] == 0:
        lines.append("ℹ️ Нет девайсов с мёртвыми или face-lock аккаунтами.")
    else:
        lines.append(f"📱 Девайсов обработано: <b>{stats['devices']}</b>")
        lines.append(f"✅ Заменено аккаунтов: <b>{stats['replaced']}</b>")
        if stats["no_reserve"]:
            lines.append(f"⚠️ Не хватило рабочих аккаунтов: <b>{stats['no_reserve']}</b>")

    try:
        await callback.message.edit_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=deviceswap_kb(auto_enabled, interval_hours),
        )
    except TelegramBadRequest:
        pass
