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

# ─── Вспомогательные ─────────────────────────────────────────────────────────

# ─── Автоматизация ───────────────────────────────────────────────────────────

def automation_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔓 Auto-Unlock-Face", callback_data="face_unlock")],
        [InlineKeyboardButton(text="🔙 Назад",             callback_data="back")],
    ])


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

