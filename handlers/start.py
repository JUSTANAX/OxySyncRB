import asyncio

from aiogram import Router
from aiogram.filters import CommandStart, StateFilter
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError

from api.accountsops import get_dashboard, get_all_pets, filter_pets
from api.faceunlock import get_balance
from config import DEFAULT_WATCHED_PETS
from database import (
    get_panel, save_panel,
    get_alert, set_alert, toggle_alert,
    save_pet_snapshot, get_pets_farmed,
    get_zp_key,
)
from keyboards import stats_kb, settings_kb, alerts_kb, cancel_kb
from state_cache import save_stats_msg, clear_stats_msg

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

def _append_pets(lines: list, user_id: int, ok_p: bool, all_pets: dict, watched_filters: list[str]):
    if not watched_filters:
        return
    lines.append("")
    if not ok_p:
        lines.append("  🐾 <b>Петы</b>: ❌ ошибка загрузки")
        return
    if not all_pets:
        lines.append("  🐾 <b>Петы</b>: нет данных (trackstats пуст)")
        return
    save_pet_snapshot(user_id, all_pets)
    watched_set = {f.lower() for f in watched_filters}
    display = {k: v for k, v in all_pets.items() if k.lower() in watched_set}
    if not display:
        lines.append(f"  🐾 <b>Петы</b>: 0/{len(all_pets)} совпадений по ID")
        return
    period_diffs = {label: get_pets_farmed(user_id, display, hours) for hours, label in PERIODS}
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
            parts = []
            for _, label in PERIODS:
                diffs = period_diffs.get(label)
                parts.append(f"{label}: {'—' if diffs is None else f'+{diffs.get(kind, 0)}'}")
            pet_lines.append("    " + "  ·  ".join(parts))
    lines.append(f"  🐾 <b>Петы</b> ({len(display)}/{len(all_pets)} аккаунт.)")
    if pet_lines:
        lines.extend(pet_lines)
    else:
        lines.append("    (нет питомцев в аккаунтах)")


def _build_lines(ok_d, dash, err_d, ok_zp, zp_bal) -> list[str]:
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
        eff = zp_bal.get("effective", 0)
        res = zp_bal.get("reserved",  0)
        zp_line = f"  💰 ZP: <b>${eff:.2f}</b>"
        if res > 0:
            zp_line += f"  (резерв: ${res:.2f})"
        lines.append(zp_line)
    return lines


async def build_stats_text(user_id: int) -> str:
    api_key = get_panel(user_id)
    if not api_key:
        return "❌ API ключ не настроен.\n\nОтправь /start чтобы подключить."
    zp_key = get_zp_key(user_id)
    results = await asyncio.gather(
        get_dashboard(api_key),
        get_balance(zp_key) if zp_key else _skip(),
        get_all_pets(api_key),
        return_exceptions=True,
    )
    ok_d,  dash,     err_d = results[0] if not isinstance(results[0], BaseException) else (False, {}, str(results[0]))
    ok_zp, zp_bal,   _     = results[1] if not isinstance(results[1], BaseException) else (False, {}, "")
    ok_p,  all_pets, _     = results[2] if not isinstance(results[2], BaseException) else (False, {}, "")
    lines = _build_lines(ok_d, dash, err_d, ok_zp, zp_bal)
    _append_pets(lines, user_id, ok_p, all_pets, DEFAULT_WATCHED_PETS)
    return "\n".join(lines)


async def show_stats(msg_or_obj, user_id: int, edit: bool = False):
    kb = stats_kb()

    # ── edit path (refresh / back) ─────────────────────────────────────────────
    if edit and hasattr(msg_or_obj, "edit_text"):
        try:
            await msg_or_obj.edit_text("🔄 Загружаю...", parse_mode="HTML")
        except (TelegramBadRequest, TelegramNetworkError):
            pass
        try:
            text = await asyncio.wait_for(build_stats_text(user_id), timeout=30.0)
        except Exception as e:
            text = f"❌ Ошибка загрузки: {type(e).__name__}. Нажми обновить."
        try:
            await msg_or_obj.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                pass
        except Exception:
            pass
        save_stats_msg(user_id, msg_or_obj.chat.id, msg_or_obj.message_id)
        return

    # ── initial load ───────────────────────────────────────────────────────────
    loading = await msg_or_obj.answer("⏳ <b>[1/4]</b> Читаю настройки...", parse_mode="HTML")

    async def upd(text: str):
        try:
            await loading.edit_text(text, parse_mode="HTML")
        except Exception:
            pass

    try:
        api_key = get_panel(user_id)
        if not api_key:
            await upd("❌ API ключ не найден в БД. Отправь /start заново.")
            return
        zp_key = get_zp_key(user_id)

        await upd("⏳ <b>[2/4]</b> Запрос дашборда AccountsOps...")
        try:
            ok_d, dash, err_d = await asyncio.wait_for(get_dashboard(api_key), timeout=20.0)
        except asyncio.TimeoutError:
            ok_d, dash, err_d = False, {}, "таймаут (20 с)"

        zp_label = "баланс ZP..." if zp_key else "ZP ключ не задан, пропускаю..."
        await upd(f"⏳ <b>[3/4]</b> {zp_label}")
        ok_zp, zp_bal = False, {}
        if zp_key:
            try:
                ok_zp, zp_bal, _ = await asyncio.wait_for(get_balance(zp_key), timeout=20.0)
            except asyncio.TimeoutError:
                pass

        await upd("⏳ <b>[4/4]</b> Загружаю петов...")
        ok_p, all_pets = False, {}
        try:
            ok_p, all_pets, _ = await asyncio.wait_for(get_all_pets(api_key), timeout=40.0)
        except asyncio.TimeoutError:
            pass

        lines = _build_lines(ok_d, dash, err_d, ok_zp, zp_bal)
        _append_pets(lines, user_id, ok_p, all_pets, DEFAULT_WATCHED_PETS)
        text = "\n".join(lines)

        try:
            await loading.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except (TelegramBadRequest, TelegramNetworkError):
            await msg_or_obj.answer(text, parse_mode="HTML", reply_markup=kb)
        save_stats_msg(user_id, loading.chat.id, loading.message_id)

    except Exception as e:
        try:
            await loading.edit_text(
                f"❌ Неожиданная ошибка: <code>{type(e).__name__}: {e}</code>\n\nПопробуй /start",
                parse_mode="HTML",
            )
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

