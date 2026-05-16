from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError

from api.accountsops import get_dashboard, get_all_pets, filter_pets
from database import (
    get_panel, save_panel,
    get_alert, set_alert, toggle_alert,
    save_pet_snapshot, get_pets_farmed,
    get_watched_pets, add_watched_pet, remove_watched_pet,
)
from keyboards import (
    stats_kb, settings_kb, alerts_kb, cancel_kb, back_kb,
    pets_mgmt_kb, pets_add_kb,
)
from state_cache import save_stats_msg, clear_stats_msg
from charts import build_pets_image

PERIODS = [
    (1,   "1ч"),
    (12,  "12ч"),
    (24,  "24ч"),
    (72,  "3д"),
    (168, "7д"),
]

router = Router()


class States(StatesGroup):
    waiting_key       = State()
    waiting_threshold = State()


# ─── /start ──────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    if not get_panel(message.from_user.id):
        await message.answer(
            "👋 Добро пожаловать в <b>OxySync</b>!\n\n"
            "Отправь свой <b>API ключ</b> AccountsOps:",
            parse_mode="HTML",
        )
        await state.set_state(States.waiting_key)
        return
    await show_stats(message, message.from_user.id)


# ─── Ввод API ключа ───────────────────────────────────────────────────────────

@router.message(States.waiting_key)
async def receive_key(message: Message, state: FSMContext):
    api_key = message.text.strip()
    await message.delete()
    data = await state.get_data()
    from_settings = data.get("from_settings", False)

    msg = await message.answer("🔄 Проверяю ключ...")
    ok, _, err = await get_dashboard(api_key)
    if not ok:
        await msg.edit_text(
            f"❌ <b>Ошибка:</b> {err}\n\nПопробуй ещё раз:",
            parse_mode="HTML",
            reply_markup=cancel_kb("settings" if from_settings else "noop"),
        )
        return

    save_panel(message.from_user.id, api_key)
    await state.clear()
    await msg.delete()
    await show_stats(message, message.from_user.id)


# ─── Построение статистики ────────────────────────────────────────────────────

async def build_stats_text(user_id: int) -> str:
    api_key = get_panel(user_id)
    if not api_key:
        return "❌ API ключ не настроен.\n\nОтправь /start чтобы подключить."

    ok_d, dash, err_d = await get_dashboard(api_key)
    ok_p, all_pets, _ = await get_all_pets(api_key)

    status = "🟢 AccountsOps" if ok_d else "🔴 AccountsOps"
    lines  = [f"📊 <b>OxySync</b>\n{status}"]

    if ok_d:
        active        = dash.get("active_count",   0)
        total_passive = (dash.get("queue_count",    0)
                       + dash.get("joining_count",  0)
                       + dash.get("connected_count", 0))
        unstable      = dash.get("unstable_count", 0)
        lines.append("")
        lines.append(f"  👥  ✅ {active}   💤 {total_passive}   ⚠️ {unstable}")
    else:
        lines.append(f"\n❌ {err_d}")

    if ok_p and all_pets:
        save_pet_snapshot(user_id, all_pets)

        # Filter to watched pets if user has set any
        watched_kinds = {pk for pk, _ in get_watched_pets(user_id)}
        display = (
            {k: v for k, v in all_pets.items() if k in watched_kinds}
            if watched_kinds else all_pets
        )

        period_diffs = {
            label: get_pets_farmed(user_id, display, hours)
            for hours, label in PERIODS
        }

        unicorns = filter_pets(display, "unicorn")
        dragons  = filter_pets(display, "dragon", exclude="dragonfly")

        pet_lines = []
        for emoji, category in [("🦄", unicorns), ("🐉", dragons)]:
            for kind, data in sorted(category.items(), key=lambda x: -x[1]["quantity"]):
                egg = " 🥚" if data["is_egg"] else ""
                pet_lines.append(f"  {emoji} {data['name']}{egg} × {data['quantity']}")

                stat_parts = []
                for _, label in PERIODS:
                    diffs = period_diffs.get(label)
                    if diffs is None:
                        stat_parts.append(f"{label}: —")
                    else:
                        stat_parts.append(f"{label}: +{diffs.get(kind, 0)}")
                pet_lines.append("    " + "  ·  ".join(stat_parts))

        if pet_lines:
            suffix = f" (фильтр: {len(watched_kinds)})" if watched_kinds else ""
            lines.append("")
            lines.append(f"  🐾 <b>Петы</b>{suffix}")
            lines.extend(pet_lines)

    return "\n".join(lines)


async def show_stats(msg_or_obj, user_id: int, edit: bool = False):
    kb = stats_kb()
    try:
        if edit and hasattr(msg_or_obj, "edit_text"):
            try:
                await msg_or_obj.edit_text("🔄 Загружаю...", parse_mode="HTML")
            except (TelegramBadRequest, TelegramNetworkError):
                pass
            text = await build_stats_text(user_id)
            try:
                await msg_or_obj.edit_text(text, parse_mode="HTML", reply_markup=kb)
            except TelegramBadRequest as e:
                if "message is not modified" not in str(e):
                    raise
            save_stats_msg(user_id, msg_or_obj.chat.id, msg_or_obj.message_id)
        else:
            loading = await msg_or_obj.answer("🔄 Загружаю...")
            text = await build_stats_text(user_id)
            try:
                await loading.edit_text(text, parse_mode="HTML", reply_markup=kb)
            except (TelegramBadRequest, TelegramNetworkError):
                await msg_or_obj.answer(text, parse_mode="HTML", reply_markup=kb)
            save_stats_msg(user_id, loading.chat.id, loading.message_id)
    except (TelegramBadRequest, TelegramNetworkError):
        pass


# ─── Кнопки главного экрана ───────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "refresh")
async def on_refresh(callback: CallbackQuery):
    await callback.answer("🔄 Обновляю...")
    await show_stats(callback.message, callback.from_user.id, edit=True)


@router.callback_query(lambda c: c.data == "back")
async def on_back(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await show_stats(callback.message, callback.from_user.id, edit=True)


@router.callback_query(lambda c: c.data == "noop")
async def on_noop(callback: CallbackQuery):
    await callback.answer()


# ─── Настройки ───────────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "settings")
async def open_settings(callback: CallbackQuery):
    clear_stats_msg(callback.from_user.id)
    has_key = get_panel(callback.from_user.id) is not None
    await callback.message.edit_text(
        "🔧 <b>Настройки</b>",
        parse_mode="HTML",
        reply_markup=settings_kb(has_key),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data == "set_key")
async def set_key_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(States.waiting_key)
    await state.update_data(from_settings=True)
    await callback.message.edit_text(
        "🔑 Отправь новый <b>API ключ</b> AccountsOps:",
        parse_mode="HTML",
        reply_markup=cancel_kb("settings"),
    )
    await callback.answer()


# ─── Уведомления ─────────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "alerts")
async def open_alerts(callback: CallbackQuery):
    clear_stats_msg(callback.from_user.id)
    row = get_alert(callback.from_user.id)
    threshold = row[0] if row else None
    enabled   = bool(row[1]) if row else False
    await callback.message.edit_text(
        "🔔 <b>Уведомления</b>\n\n"
        "Бот пришлёт сообщение когда активных аккаунтов станет меньше порога.",
        parse_mode="HTML",
        reply_markup=alerts_kb(threshold, enabled),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data == "alert_toggle")
async def on_alert_toggle(callback: CallbackQuery):
    enabled = toggle_alert(callback.from_user.id)
    row = get_alert(callback.from_user.id)
    threshold = row[0] if row else None
    await callback.message.edit_reply_markup(
        reply_markup=alerts_kb(threshold, enabled)
    )
    await callback.answer("✅ Включено" if enabled else "❌ Выключено")


@router.callback_query(lambda c: c.data == "alert_set")
async def alert_set_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(States.waiting_threshold)
    await callback.message.edit_text(
        "🔔 Введи минимальное количество активных аккаунтов.\n"
        "Если станет меньше — придёт уведомление:",
        parse_mode="HTML",
        reply_markup=cancel_kb("alerts"),
    )
    await callback.answer()


@router.message(States.waiting_threshold)
async def receive_threshold(message: Message, state: FSMContext):
    text = message.text.strip()
    await message.delete()
    if not text.isdigit() or int(text) <= 0:
        await message.answer(
            "❌ Введи целое положительное число:",
            reply_markup=cancel_kb("alerts"),
        )
        return
    set_alert(message.from_user.id, int(text))
    await state.clear()
    row = get_alert(message.from_user.id)
    await message.answer(
        f"✅ Порог установлен: <b>{row[0]}</b> аккаунтов",
        parse_mode="HTML",
        reply_markup=alerts_kb(row[0], bool(row[1])),
    )


# ─── Карточка петов ───────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "pets_card")
async def on_pets_card(callback: CallbackQuery):
    await callback.answer("📊 Генерирую...")
    user_id = callback.from_user.id
    api_key = get_panel(user_id)
    if not api_key:
        await callback.answer("❌ API ключ не настроен.", show_alert=True)
        return

    ok, all_pets, _ = await get_all_pets(api_key)
    if not ok or not all_pets:
        await callback.answer("❌ Нет данных о петах.", show_alert=True)
        return

    watched_kinds = {pk for pk, _ in get_watched_pets(user_id)}
    display = (
        {k: v for k, v in all_pets.items() if k in watched_kinds}
        if watched_kinds else all_pets
    )

    period_diffs = {
        label: get_pets_farmed(user_id, display, hours)
        for hours, label in PERIODS
    }

    png = build_pets_image(display, period_diffs)
    await callback.message.answer_photo(
        BufferedInputFile(png, filename="pets.png"),
        caption="🐾 <b>Pet Stats</b>",
        parse_mode="HTML",
    )


# ─── Трекинг петов — управление ──────────────────────────────────────────────

def _pets_mgmt_text(watched: list[tuple[str, str]]) -> str:
    if not watched:
        return (
            "🐾 <b>Трекинг петов</b>\n\n"
            "Список пуст — в статистике показываются все петы.\n\n"
            "Нажми <b>➕ Добавить пета</b> чтобы выбрать конкретных."
        )
    names = "\n".join(f"  • {label}" for _, label in watched)
    return (
        f"🐾 <b>Трекинг петов</b>\n\n"
        f"В статистике и карточке отображаются только:\n{names}\n\n"
        "Нажми на пета чтобы убрать его из списка."
    )


@router.callback_query(lambda c: c.data == "pets_mgmt")
async def open_pets_mgmt(callback: CallbackQuery, state: FSMContext):
    clear_stats_msg(callback.from_user.id)
    await state.update_data(pet_add_cache=None)
    watched = get_watched_pets(callback.from_user.id)
    await callback.message.edit_text(
        _pets_mgmt_text(watched),
        parse_mode="HTML",
        reply_markup=pets_mgmt_kb(watched),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("pet_rm:"))
async def pet_rm(callback: CallbackQuery):
    user_id  = callback.from_user.id
    pet_kind = callback.data[len("pet_rm:"):]
    remove_watched_pet(user_id, pet_kind)
    await callback.answer("❌ Убран из трекинга")
    watched = get_watched_pets(user_id)
    await callback.message.edit_text(
        _pets_mgmt_text(watched),
        parse_mode="HTML",
        reply_markup=pets_mgmt_kb(watched),
    )


# ─── Трекинг петов — экран добавления ────────────────────────────────────────

@router.callback_query(lambda c: c.data == "pet_add")
async def pet_add(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    api_key = get_panel(user_id)
    if not api_key:
        await callback.answer("❌ API не настроен.", show_alert=True)
        return

    await callback.answer("⏳")
    ok, all_pets, _ = await get_all_pets(api_key)
    if not ok or not all_pets:
        await callback.answer("❌ Нет данных о петах.", show_alert=True)
        return

    # Cache so pet_toggle doesn't need to re-fetch
    cache = {k: {"name": d["name"], "quantity": d["quantity"]} for k, d in all_pets.items()}
    await state.update_data(pet_add_cache=cache)

    watched_set = {pk for pk, _ in get_watched_pets(user_id)}
    available = [
        (kind, info["name"], kind in watched_set)
        for kind, info in sorted(cache.items(), key=lambda x: -x[1]["quantity"])
    ]

    await callback.message.edit_text(
        "🐾 <b>Выбери петов для трекинга</b>\n\n"
        "✅ = уже в списке  |  Нажми чтобы добавить / убрать",
        parse_mode="HTML",
        reply_markup=pets_add_kb(available),
    )


@router.callback_query(lambda c: c.data.startswith("pet_toggle:"))
async def pet_toggle(callback: CallbackQuery, state: FSMContext):
    user_id  = callback.from_user.id
    pet_kind = callback.data[len("pet_toggle:"):]

    watched_set = {pk for pk, _ in get_watched_pets(user_id)}
    fsmdata     = await state.get_data()
    cache: dict = fsmdata.get("pet_add_cache") or {}

    if pet_kind in watched_set:
        remove_watched_pet(user_id, pet_kind)
        await callback.answer("❌ Убран")
    else:
        label = (cache.get(pet_kind) or {}).get("name") or pet_kind
        add_watched_pet(user_id, pet_kind, label)
        await callback.answer("✅ Добавлен")

    # Refresh keyboard from cache (no extra API call)
    new_watched = {pk for pk, _ in get_watched_pets(user_id)}
    available = [
        (kind, info["name"], kind in new_watched)
        for kind, info in sorted(cache.items(), key=lambda x: -x[1]["quantity"])
    ]
    try:
        await callback.message.edit_reply_markup(reply_markup=pets_add_kb(available))
    except TelegramBadRequest:
        pass
