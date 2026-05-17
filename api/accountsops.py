import re
import json
import asyncio
import logging
import aiohttp
from config import ACCOUNTSOPS_URL

_RETRY_EXC = (aiohttp.ClientConnectorError, aiohttp.ServerDisconnectedError, asyncio.TimeoutError)


def pet_kind_to_name(pet_kind: str) -> str:
    name = re.sub(r'^.*_\d{4}_', '', pet_kind)
    return name.replace('_', ' ').title()


async def _post(api_key: str, endpoint: str, body: dict) -> tuple[bool, any, str]:
    headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}
    url = f"{ACCOUNTSOPS_URL}{endpoint}"
    last_err = "Не удалось подключиться к AccountsOps."
    for attempt in range(3):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, headers=headers, json=body,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    raw = await resp.text()
                    if resp.status == 401:
                        return False, None, "Неверный API ключ."
                    if resp.status == 403:
                        return False, None, "Доступ запрещён."
                    if resp.status != 200:
                        return False, None, f"Ошибка сервера (код {resp.status})."
                    return True, json.loads(raw), ""
        except asyncio.TimeoutError:
            last_err = "Превышен таймаут AccountsOps."
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
        except _RETRY_EXC:
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
        except Exception as e:
            return False, None, f"Ошибка: {e}"
    return False, None, last_err


async def _get(api_key: str, endpoint: str) -> tuple[bool, any, str]:
    headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}
    url = f"{ACCOUNTSOPS_URL}{endpoint}"
    last_err = "Не удалось подключиться к AccountsOps."
    for attempt in range(3):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    body = await resp.text()
                    logging.debug("[AO] %s → %s", endpoint, resp.status)
                    if resp.status == 401:
                        return False, None, "Неверный API ключ."
                    if resp.status == 403:
                        return False, None, "Доступ запрещён."
                    if resp.status != 200:
                        return False, None, f"Ошибка сервера (код {resp.status})."
                    return True, json.loads(body), ""
        except asyncio.TimeoutError:
            last_err = "Превышен таймаут AccountsOps."
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
        except _RETRY_EXC:
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
        except Exception as e:
            logging.error("[AO] %s → %s", endpoint, e)
            return False, None, f"Ошибка: {e}"
    return False, None, last_err


async def get_dashboard(api_key: str) -> tuple[bool, dict, str]:
    ok, data, err = await _get(api_key, "/api/dashboard")
    return ok, data or {}, err


async def get_trackstats_accounts(api_key: str) -> tuple[bool, list, str]:
    ok, data, err = await _get(api_key, "/api/trackstats/accounts")
    if not ok:
        return False, [], err
    if isinstance(data, dict):
        return True, data.get("accounts") or [], ""
    if isinstance(data, list):
        return True, data, ""
    return True, [], ""


async def get_account_pets(api_key: str, account_id) -> tuple[bool, list, str]:
    ok, data, err = await _get(api_key, f"/api/trackstats/accounts/{account_id}/pets")
    return ok, data or [], err


async def get_all_pets(api_key: str) -> tuple[bool, dict, str]:
    """Aggregate pets across all accounts (concurrent per-account requests)."""
    ok, accounts, err = await get_trackstats_accounts(api_key)
    if not ok:
        return False, {}, err
    if not accounts:
        return True, {}, ""

    acc_ids = [acc["id"] for acc in accounts if acc.get("id")]
    if not acc_ids:
        return True, {}, ""

    results = await asyncio.gather(
        *[get_account_pets(api_key, aid) for aid in acc_ids],
        return_exceptions=True,
    )

    pets: dict = {}
    for result in results:
        if isinstance(result, BaseException):
            continue
        ok2, acc_pets, _ = result
        if not ok2:
            continue
        for pet in acc_pets:
            kind = pet.get("pet_kind")
            if not kind:
                continue
            if kind not in pets:
                pets[kind] = {
                    "quantity": 0,
                    "is_egg": pet.get("is_egg", False),
                    "name": pet_kind_to_name(kind),
                }
            pets[kind]["quantity"] += pet.get("quantity", 0)

    return True, pets, ""


def _format_for_zp(acc: dict) -> str | None:
    """Format one AccountsOps account for ZeroPoint submission.
    Returns None if cookie is missing or invalid."""
    cookie = (acc.get("cookie") or "").strip()
    if cookie.startswith(".ROBLOSECURITY="):
        cookie = cookie[len(".ROBLOSECURITY="):]
    if "_|WARNING" not in cookie:
        return None
    username = (acc.get("username") or "").strip()
    password = (acc.get("password") or "").strip()
    if username and password:
        return f"{username}:{password}:{cookie}"
    return cookie


async def get_face_accounts(api_key: str) -> tuple[bool, list[str], str]:
    """Fetch accounts tagged status:face and format them for ZeroPoint."""
    ok, data, err = await _post(api_key, "/api/devices/accounts", {"tag": "status:face"})
    if not ok:
        return False, [], err
    devices = data.get("devices", []) if isinstance(data, dict) else []
    formatted: list[str] = []
    for device in devices:
        for acc in device.get("accounts", []):
            line = _format_for_zp(acc)
            if line:
                formatted.append(line)
    return True, formatted, ""


def filter_pets(pets: dict, search: str, exclude: str | None = None) -> dict:
    """Return pets whose display name contains search (case-insensitive).
    Optionally exclude pets whose name contains the exclude string."""
    q = search.lower()
    result = {k: v for k, v in pets.items() if q in v["name"].lower()}
    if exclude:
        ex = exclude.lower()
        result = {k: v for k, v in result.items() if ex not in v["name"].lower()}
    return result
