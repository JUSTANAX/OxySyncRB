from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

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
        [InlineKeyboardButton(text="🤖 Авто-пилот",        callback_data="autopilot")],
        [InlineKeyboardButton(text="🔙 Назад",              callback_data="back")],
    ])



def ap_pets_kb(pets: list[tuple]) -> InlineKeyboardMarkup:
    rows = []
    for row_id, pet_id in pets:
        short = pet_id if len(pet_id) <= 30 else pet_id[:27] + "..."
        rows.append([
            InlineKeyboardButton(text=f"🦆 {short}", callback_data="noop"),
            InlineKeyboardButton(text="❌", callback_data=f"ap_del_pet:{row_id}"),
        ])
    rows.append([InlineKeyboardButton(text="➕ Добавить пет", callback_data="ap_add_pet")])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="autopilot")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def autopilot_kb(
    main_account: str | None,
    pet_count: int,
    config_id: int | None,
    farm_config_id: int | None,
    running: bool,
    check_interval: int = 30,
    stuck_timeout: int = 10,
    batch_size: int = 10,
) -> InlineKeyboardMarkup:
    rows = []

    main_label  = f"👤 {main_account}" if main_account else "👤 Задать основной аккаунт"
    pet_label   = f"🦆 Петы: {pet_count}" if pet_count > 0 else "🦆 Добавить петы"

    trade_cfg_label = f"🔄 Трейд конфиг: {config_id}" if config_id else "🔄 Трейд конфиг: не задан"
    farm_cfg_label  = f"🌾 Фарм конфиг: {farm_config_id}" if farm_config_id else "🌾 Фарм конфиг: не задан"
    interval_label  = f"⏱ Проверка: {check_interval}с"
    stuck_label     = f"⏰ Стак: {stuck_timeout}м"
    batch_label     = f"📊 Трейдеров: {batch_size}"

    rows.append([InlineKeyboardButton(text=main_label,       callback_data="ap_set_main")])
    rows.append([InlineKeyboardButton(text=pet_label,        callback_data="ap_set_pet")])
    rows.append([InlineKeyboardButton(text=trade_cfg_label,  callback_data="ap_set_config")])
    rows.append([InlineKeyboardButton(text=farm_cfg_label,   callback_data="ap_set_farm_config")])
    rows.append([
        InlineKeyboardButton(text=interval_label, callback_data="ap_set_interval"),
        InlineKeyboardButton(text=stuck_label,    callback_data="ap_set_stuck"),
        InlineKeyboardButton(text=batch_label,    callback_data="ap_set_batch"),
    ])

    if running:
        rows.append([
            InlineKeyboardButton(text="🔄 Обновить",   callback_data="ap_refresh"),
            InlineKeyboardButton(text="⏹ Остановить", callback_data="ap_stop"),
        ])
    else:
        rows.append([InlineKeyboardButton(text="▶️ Запустить", callback_data="ap_start")])

    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="automation")])
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
