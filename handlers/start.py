import asyncio

from aiogram import Router, F
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError

from api.accountsops import get_dashboard, get_all_pets, filter_pets
from api.faceunlock import get_balance
from database import (
    get_panel, save_panel,
    get_alert, set_alert, toggle_alert,
    save_pet_snapshot, get_pets_farmed,
    get_watched_pets, add_watched_pet, remove_watched_pet,
    get_zp_key,
)
from keyboards import stats_kb, settings_kb, alerts_kb, cancel_kb, back_kb, pets_mgmt_kb
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
    waiting_key        = State()
    waiting_threshold  = State()
    waiting_pet_filter = State()


async def _skip():
    return False, {}, ""


# ─── /start ──────────────────────────────────────────────────────────────────

@router.message(CommandStart(), StateFilter("*"))
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
    try:
        ok, _, err = await asyncio.wait_for(get_dashboard(api_key), timeout=20.0)
    except asyncio.TimeoutError:
        ok, err = False, "Сервер не ответил. Попробуй ещё раз."
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

    zp_key          = get_zp_key(user_id)
    watched_filters = get_watched_pets(user_id)

    # Fetch concurrently; skip get_all_pets entirely when no filters are set
    ok_d, dash, err_d = (False, {}, "")
    ok_zp, zp_bal, _  = (False, {}, "")
    ok_p, all_pets, _ = (False, {}, "")

    ok_d, dash, err_d, ok_zp, zp_bal, ok_p, all_pets = (
        await _gather_stats(api_key, zp_key, watched_filters)
    )

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

    if ok_zp:
        eff     = zp_bal.get("effective", 0)
        res     = zp_bal.get("reserved",  0)
        zp_line = f"  💰 ZP: <b>${eff:.2f}</b>"
        if res > 0:
            zp_line += f"  (резерв: ${res:.2f})"
        lines.append(zp_line)

    if ok_p and all_pets and watched_filters:
        save_pet_snapshot(user_id, all_pets)

        watched_set = {f.lower() for f in watched_filters}
        display = {k: v for k, v in all_pets.items() if k.lower() in watched_set}

        period_diffs = {
            label: get_pets_farmed(user_id, display, hours)
            for hours, label in PERIODS
        }

        unicorns = {**filter_pets(display, "unicorn"), **filter_pets(display, "alicorn")}
        dragons  = filter_pets(display, "dragon", exclude="dragonfly")
        shown    = set(unicorns) | set(dragons)
        others   = {k: v for k, v in display.items() if k not in shown}

        pet_lines = []
        for emoji, category in [("🦄", unicorns), ("🐉", dragons), ("🐾", others)]:
            if not category:
                continue
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
            lines.append("")
            lines.append(f"  🐾 <b>Петы</b> (фильтр: {len(watched_filters)})")
            lines.extend(pet_lines)

    return "\n".join(lines)


async def _gather_stats(api_key: str, zp_key: str | None, watched_filters: list[str]):
    results = await asyncio.gather(
        get_dashboard(api_key),
        get_balance(zp_key) if zp_key else _skip(),
        get_all_pets(api_key) if watched_filters else _skip(),
        return_exceptions=True,
    )
    ok_d,  dash,     err_d = results[0] if not isinstance(results[0], BaseException) else (False, {}, str(results[0]))
    ok_zp, zp_bal,   _     = results[1] if not isinstance(results[1], BaseException) else (False, {}, "")
    ok_p,  all_pets, _     = results[2] if not isinstance(results[2], BaseException) else (False, {}, "")
    return ok_d, dash, err_d, ok_zp, zp_bal, ok_p, all_pets


async def show_stats(msg_or_obj, user_id: int, edit: bool = False):
    kb = stats_kb()
    loading = None
    try:
        if edit and hasattr(msg_or_obj, "edit_text"):
            try:
                await msg_or_obj.edit_text("🔄 Загружаю...", parse_mode="HTML")
            except (TelegramBadRequest, TelegramNetworkError):
                pass
            text = await asyncio.wait_for(build_stats_text(user_id), timeout=40.0)
            try:
                await msg_or_obj.edit_text(text, parse_mode="HTML", reply_markup=kb)
            except TelegramBadRequest as e:
                if "message is not modified" not in str(e):
                    raise
            save_stats_msg(user_id, msg_or_obj.chat.id, msg_or_obj.message_id)
        else:
            loading = await msg_or_obj.answer("🔄 Загружаю...")
            text = await asyncio.wait_for(build_stats_text(user_id), timeout=40.0)
            try:
                await loading.edit_text(text, parse_mode="HTML", reply_markup=kb)
            except (TelegramBadRequest, TelegramNetworkError):
                await msg_or_obj.answer(text, parse_mode="HTML", reply_markup=kb)
            save_stats_msg(user_id, loading.chat.id, loading.message_id)
    except Exception:
        if loading is not None:
            try:
                await loading.edit_text("❌ Ошибка загрузки. Попробуй /start")
            except Exception:
                pass


# ─── Кнопки главного экрана ───────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "refresh")
async def on_refresh(callback: CallbackQuery):
    await callback.answer("🔄 Обновляю...")
    await show_stats(callback.message, callback.from_user.id, edit=True)


@router.callback_query(lambda c: c.data == "back")
async def on_back(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
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
    await callback.message.edit_reply_markup(reply_markup=alerts_kb(threshold, enabled))
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
        await message.answer("❌ Введи целое положительное число:",
                             reply_markup=cancel_kb("alerts"))
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

async def _send_pets_card(target, user_id: int):
    api_key = get_panel(user_id)
    if not api_key:
        await target.answer("❌ API ключ не настроен.")
        return

    ok, all_pets, _ = await get_all_pets(api_key)
    if not ok or not all_pets:
        await target.answer("❌ Нет данных о петах.")
        return

    watched_filters = get_watched_pets(user_id)
    if watched_filters:
        watched_set = {f.lower() for f in watched_filters}
        display = {k: v for k, v in all_pets.items() if k.lower() in watched_set}
    else:
        display = all_pets

    period_diffs = {
        label: get_pets_farmed(user_id, display, hours)
        for hours, label in PERIODS
    }

    png = build_pets_image(display, period_diffs)
    await target.answer_document(
        BufferedInputFile(png, filename="pets.png"),
        caption="🐾 <b>Pet Stats</b>",
        parse_mode="HTML",
    )


@router.callback_query(lambda c: c.data == "pets_card")
async def on_pets_card(callback: CallbackQuery):
    await callback.answer("📊 Генерирую...")
    await _send_pets_card(callback.message, callback.from_user.id)


@router.message(Command("card"))
async def cmd_card(message: Message):
    msg = await message.answer("📊 Генерирую...")
    await _send_pets_card(message, message.from_user.id)
    await msg.delete()


# ─── Трекинг петов — управление ──────────────────────────────────────────────

def _pets_mgmt_text(filters: list[str]) -> str:
    if not filters:
        return (
            "🐾 <b>Трекинг петов</b>\n\n"
            "Список пуст — петы на главном экране не показываются.\n\n"
            "Нажми <b>➕ Добавить</b> и введи ID пета (pet_kind)."
        )
    names = "\n".join(f"  • {f}" for f in filters)
    return (
        f"🐾 <b>Трекинг петов</b>\n\n"
        f"Отслеживаемые ID:\n{names}\n\n"
        "Нажми на ID чтобы удалить его из списка."
    )


@router.callback_query(lambda c: c.data == "pets_mgmt")
async def open_pets_mgmt(callback: CallbackQuery, state: FSMContext):
    clear_stats_msg(callback.from_user.id)
    await state.clear()
    filters = get_watched_pets(callback.from_user.id)
    await callback.message.edit_text(
        _pets_mgmt_text(filters),
        parse_mode="HTML",
        reply_markup=pets_mgmt_kb(filters),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("pet_rm:"))
async def pet_rm(callback: CallbackQuery):
    user_id     = callback.from_user.id
    filter_text = callback.data[len("pet_rm:"):]
    remove_watched_pet(user_id, filter_text)
    await callback.answer(f'❌ Фильтр "{filter_text}" удалён')
    filters = get_watched_pets(user_id)
    await callback.message.edit_text(
        _pets_mgmt_text(filters),
        parse_mode="HTML",
        reply_markup=pets_mgmt_kb(filters),
    )


# ─── Трекинг петов — добавление через текст ──────────────────────────────────

@router.callback_query(lambda c: c.data == "pet_add")
async def pet_add_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(States.waiting_pet_filter)
    await callback.message.edit_text(
        "🐾 <b>Добавить пета</b>\n\n"
        "Введи <b>ID пета</b> (pet_kind), регистр не важен.\n\n"
        "Пример: <code>golden_dragon</code>",
        parse_mode="HTML",
        reply_markup=cancel_kb("pets_mgmt"),
    )
    await callback.answer()


@router.message(States.waiting_pet_filter, F.text)
async def receive_pet_filter(message: Message, state: FSMContext):
    text = message.text.strip()
    await message.delete()
    if not text or len(text) < 2:
        await message.answer(
            "❌ Слишком короткое название. Введи хотя бы 2 символа:",
            reply_markup=cancel_kb("pets_mgmt"),
        )
        return

    add_watched_pet(message.from_user.id, text)
    await state.clear()
    filters = get_watched_pets(message.from_user.id)
    await message.answer(
        f'✅ Фильтр <b>"{text}"</b> добавлен.\n\n' + _pets_mgmt_text(filters),
        parse_mode="HTML",
        reply_markup=pets_mgmt_kb(filters),
    )
