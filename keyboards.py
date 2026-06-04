from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

_TYPE_MASKS  = [7, 1, 2, 4, 3, 5, 6]
_TYPE_LABELS = {7: "Все", 1: "Норм", 2: "Неон", 4: "Мега", 3: "Норм+Неон", 5: "Норм+Мега", 6: "Неон+Мега"}


def type_mask_label(mask: int) -> str:
    return _TYPE_LABELS.get(mask, f"М:{mask}")


# ─── Главный экран ────────────────────────────────────────────────────────────

def stats_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh")],
        [
            InlineKeyboardButton(text="🔔 Уведомления", callback_data="alerts"),
            InlineKeyboardButton(text="🔧 Настройки",   callback_data="settings"),
        ],
        [InlineKeyboardButton(text="🤖 Автоматизация", callback_data="automation")],
    ])

# ─── Настройки ────────────────────────────────────────────────────────────────

def settings_kb(has_key: bool) -> InlineKeyboardMarkup:
    label = "🔑 Сменить API ключ" if has_key else "🔑 Подключить API ключ"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data="set_key")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back")],
    ])

# ─── Уведомления ─────────────────────────────────────────────────────────────

def alerts_kb(threshold: int | None, enabled: bool) -> InlineKeyboardMarkup:
    if threshold is not None:
        edit_label   = f"✏️ Порог: < {threshold} аккаунтов"
        toggle_label = "✅ Включено" if enabled else "❌ Выключено"
    else:
        edit_label   = "✏️ Задать порог"
        toggle_label = "❌ Не задано"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=edit_label,   callback_data="alert_set")],
        [InlineKeyboardButton(text=toggle_label, callback_data="alert_toggle")],
        [InlineKeyboardButton(text="🔙 Назад",    callback_data="back")],
    ])

# ─── Автоматизация ───────────────────────────────────────────────────────────

def automation_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔓 Auto-Unlock-Face", callback_data="face_unlock")],
        [InlineKeyboardButton(text="📂 Sorting",          callback_data="autoswap")],
        [InlineKeyboardButton(text="🔄 AutoSwap",         callback_data="deviceswap")],
        [InlineKeyboardButton(text="✂️ Trim",             callback_data="devicetrim")],
        [InlineKeyboardButton(text="🤖 Авто-пилот",       callback_data="autopilot")],
        [InlineKeyboardButton(text="🔙 Назад",            callback_data="back")],
    ])



def ap_pets_kb(pets: list[tuple]) -> InlineKeyboardMarkup:
    rows = []
    for row_id, pet_id, min_count, age_min, age_max, type_mask in pets:
        short = pet_id if len(pet_id) <= 22 else pet_id[:19] + "..."
        rows.append([
            InlineKeyboardButton(text=f"🦆 {short}", callback_data="noop"),
            InlineKeyboardButton(text=f"📊 {min_count}", callback_data=f"ap_pet_threshold:{row_id}"),
            InlineKeyboardButton(text="❌", callback_data=f"ap_del_pet:{row_id}"),
        ])
        rows.append([
            InlineKeyboardButton(text=f"🎂 Мин:{age_min}", callback_data=f"ap_pet_amin:{row_id}"),
            InlineKeyboardButton(text=f"🎂 Макс:{age_max}", callback_data=f"ap_pet_amax:{row_id}"),
            InlineKeyboardButton(text=f"🐾 {type_mask_label(type_mask)}", callback_data=f"ap_pet_type:{row_id}"),
        ])
    rows.append([
        InlineKeyboardButton(text="➕ Добавить", callback_data="ap_add_pet"),
        InlineKeyboardButton(text="📋 Bulk", callback_data="ap_bulk_pet"),
    ])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="autopilot")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def autopilot_kb(
    main_account: str | None,
    pet_count: int,
    config_id: int | None,
    farm_config_id: int | None,
    running: bool,
    check_interval: int = 30,
    batch_size: int = 10,
    main_config_id: int | None = None,
    potion_threshold: int = 8,
    trade_detect_mode: str = "events",
) -> InlineKeyboardMarkup:
    rows = []

    main_label  = f"👤 {main_account}" if main_account else "👤 Задать основной аккаунт"
    pet_label   = f"🦆 Петы: {pet_count}" if pet_count > 0 else "🦆 Добавить петы"

    trade_cfg_label = f"🔄 Трейд конфиг: {config_id}" if config_id else "🔄 Трейд конфиг: не задан"
    farm_cfg_label  = f"🌾 Фарм конфиг: {farm_config_id}" if farm_config_id else "🌾 Фарм конфиг: не задан"
    main_cfg_label  = f"👑 Конфиг мейна: {main_config_id}" if main_config_id else "👑 Конфиг мейна: не задан"
    interval_label  = f"⏱ Проверка: {check_interval}с"
    batch_label     = f"📊 Трейдеров: {batch_size}"
    potion_label    = f"🧪 Порог зелий: {potion_threshold}"
    detect_label    = "🎯 Детект: События" if trade_detect_mode == "events" else "📦 Детект: Инвентарь"

    rows.append([InlineKeyboardButton(text=main_label,       callback_data="ap_set_main")])
    rows.append([InlineKeyboardButton(text=pet_label,        callback_data="ap_set_pet")])
    rows.append([InlineKeyboardButton(text=trade_cfg_label,  callback_data="ap_set_config")])
    rows.append([InlineKeyboardButton(text=farm_cfg_label,   callback_data="ap_set_farm_config")])
    rows.append([InlineKeyboardButton(text=main_cfg_label,   callback_data="ap_set_main_config")])
    rows.append([
        InlineKeyboardButton(text=interval_label, callback_data="ap_set_interval"),
        InlineKeyboardButton(text=batch_label,    callback_data="ap_set_batch"),
        InlineKeyboardButton(text=potion_label,   callback_data="ap_set_potion_threshold"),
    ])
    rows.append([InlineKeyboardButton(text=detect_label, callback_data="ap_toggle_detect")])

    if running:
        rows.append([
            InlineKeyboardButton(text="🔄 Обновить",   callback_data="ap_refresh"),
            InlineKeyboardButton(text="⏹ Остановить", callback_data="ap_stop"),
        ])
        rows.append([InlineKeyboardButton(text="⚡️ Рестарт трейдеров", callback_data="ap_restart_trading")])
    else:
        rows.append([InlineKeyboardButton(text="▶️ Запустить", callback_data="ap_start")])

    if main_account:
        rows.append([InlineKeyboardButton(text="📦 Инвентарь осн. аккаунта", callback_data="ap_inventory")])
    rows.append([InlineKeyboardButton(text="♻️ Перезапустить все аккаунты", callback_data="ap_restart_all")])
    rows.append([InlineKeyboardButton(text="🔍 Debug петов", callback_data="ap_debug")])
    rows.append([InlineKeyboardButton(text="⏱ Тайминг трейдов", callback_data="ap_timing")])
    rows.append([InlineKeyboardButton(text="🔧 Очистить очередь", callback_data="ap_cleanup_queue")])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="automation")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def main_configs_kb(configs: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for cfg in configs:
        cfg_id   = cfg.get("id")
        cfg_name = cfg.get("name") or str(cfg_id)
        rows.append([InlineKeyboardButton(
            text=f"👑 {cfg_name}",
            callback_data=f"ap_main_cfg:{cfg_id}",
        )])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="autopilot")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def farm_configs_kb(configs: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for cfg in configs:
        cfg_id   = cfg.get("id")
        cfg_name = cfg.get("name") or str(cfg_id)
        rows.append([InlineKeyboardButton(
            text=f"🌾 {cfg_name}",
            callback_data=f"ap_farm_cfg:{cfg_id}",
        )])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="autopilot")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ap_inventory_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="ap_inventory_refresh")],
        [InlineKeyboardButton(text="🔙 Назад",    callback_data="autopilot")],
    ])


def cancel_to_ap_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="autopilot")]
    ])


def configs_kb(configs: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for cfg in configs:
        cfg_id   = cfg.get("id")
        cfg_name = cfg.get("name") or str(cfg_id)
        rows.append([InlineKeyboardButton(
            text=f"⚙️ {cfg_name}",
            callback_data=f"ap_cfg:{cfg_id}",
        )])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="autopilot")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def autoswap_kb(auto_enabled: bool, interval_hours: float) -> InlineKeyboardMarkup:
    rows = []

    rows.append([InlineKeyboardButton(text="▶️ Запустить сортировку", callback_data="as_run")])

    auto_label = "🔁 Авто: ✅" if auto_enabled else "🔁 Авто: ❌"
    if auto_enabled:
        hours_str = f"{int(interval_hours)}ч" if interval_hours == int(interval_hours) else f"{interval_hours}ч"
        rows.append([
            InlineKeyboardButton(text=auto_label,        callback_data="as_auto_toggle"),
            InlineKeyboardButton(text=f"⏱ {hours_str}", callback_data="as_interval_cycle"),
        ])
    else:
        rows.append([InlineKeyboardButton(text=auto_label, callback_data="as_auto_toggle")])

    rows.append([
        InlineKeyboardButton(text="🔄 Обновить", callback_data="as_refresh"),
        InlineKeyboardButton(text="🔙 Назад",    callback_data="automation"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def deviceswap_kb(auto_enabled: bool, interval_hours: float) -> InlineKeyboardMarkup:
    rows = []
    rows.append([InlineKeyboardButton(text="▶️ Запустить AutoSwap", callback_data="ds_run")])

    auto_label = "🔁 Авто: ✅" if auto_enabled else "🔁 Авто: ❌"
    if auto_enabled:
        hours_str = f"{int(interval_hours)}ч" if interval_hours == int(interval_hours) else f"{interval_hours}ч"
        rows.append([
            InlineKeyboardButton(text=auto_label,        callback_data="ds_auto_toggle"),
            InlineKeyboardButton(text=f"⏱ {hours_str}", callback_data="ds_interval_cycle"),
        ])
    else:
        rows.append([InlineKeyboardButton(text=auto_label, callback_data="ds_auto_toggle")])

    rows.append([
        InlineKeyboardButton(text="🔄 Обновить", callback_data="ds_refresh"),
        InlineKeyboardButton(text="🔙 Назад",    callback_data="automation"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def devicetrim_kb(auto_enabled: bool, interval_hours: float, max_per_device: int) -> InlineKeyboardMarkup:
    rows = []
    rows.append([InlineKeyboardButton(text="▶️ Запустить Trim", callback_data="dt_run")])
    rows.append([InlineKeyboardButton(text=f"📊 Лимит: {max_per_device} акк. ✏️", callback_data="dt_max_set")])

    auto_label = "🔁 Авто: ✅" if auto_enabled else "🔁 Авто: ❌"
    if auto_enabled:
        hours_str = f"{int(interval_hours)}ч" if interval_hours == int(interval_hours) else f"{interval_hours}ч"
        rows.append([
            InlineKeyboardButton(text=auto_label,        callback_data="dt_auto_toggle"),
            InlineKeyboardButton(text=f"⏱ {hours_str}", callback_data="dt_interval_cycle"),
        ])
    else:
        rows.append([InlineKeyboardButton(text=auto_label, callback_data="dt_auto_toggle")])

    rows.append([
        InlineKeyboardButton(text="🔄 Обновить", callback_data="dt_refresh"),
        InlineKeyboardButton(text="🔙 Назад",    callback_data="automation"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def fu_no_key_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔑 Подключить ZeroPoint", callback_data="fu_set_key")],
        [InlineKeyboardButton(text="🔙 Назад",                 callback_data="automation")],
    ])


def fu_kb(
    job_status: str | None,
    result_files: list,
    auto_enabled: bool = False,
    interval: float = 3.0,
) -> InlineKeyboardMarkup:
    rows = []
    if job_status in ("pending", "processing"):
        rows.append([
            InlineKeyboardButton(text="🔄 Обновить", callback_data="fu_refresh"),
            InlineKeyboardButton(text="❌ Отменить", callback_data="fu_cancel"),
        ])
    elif job_status == "completed":
        for f_info in result_files:
            fname = f_info.get("filename") if isinstance(f_info, dict) else str(f_info)
            if fname:
                rows.append([InlineKeyboardButton(
                    text=f"📥 {fname}", callback_data=f"fu_dl:{fname}"
                )])
        rows.append([
            InlineKeyboardButton(text="🔄 Обновить",     callback_data="fu_refresh"),
            InlineKeyboardButton(text="🔓 Новый запуск",  callback_data="fu_run"),
        ])
    elif job_status in ("failed", "cancelled"):
        rows.append([
            InlineKeyboardButton(text="🔄 Обновить",     callback_data="fu_refresh"),
            InlineKeyboardButton(text="🔓 Новый запуск",  callback_data="fu_run"),
        ])
    else:
        rows.append([InlineKeyboardButton(
            text="🔓 Запустить разблокировку", callback_data="fu_run"
        )])
    auto_label = "🔁 Авто-цикл: ✅" if auto_enabled else "🔁 Авто-цикл: ❌"
    rows.append([InlineKeyboardButton(text=auto_label, callback_data="fu_auto_toggle")])
    if auto_enabled:
        hours_str = f"{int(interval)}ч" if interval == int(interval) else f"{interval}ч"
        rows.append([InlineKeyboardButton(
            text=f"⏱ Интервал: {hours_str}", callback_data="fu_interval_cycle"
        )])
    rows.append([
        InlineKeyboardButton(text="🔑 Ключ ZP", callback_data="fu_set_key"),
        InlineKeyboardButton(text="🔙 Назад",    callback_data="automation"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def fu_confirm_kb(count: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=f"✅ Запустить ({count} акк.)", callback_data="fu_confirm"
            ),
            InlineKeyboardButton(text="❌ Отмена", callback_data="face_unlock"),
        ]
    ])


def cancel_to_fu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="face_unlock")]
    ])


def cancel_kb(back_cb: str = "back") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data=back_cb)]
    ])

def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back")]
    ])


