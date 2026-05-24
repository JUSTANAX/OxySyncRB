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
    """Aggregate pets across all accounts using a shared session + semaphore."""
    ok, accounts, err = await get_trackstats_accounts(api_key)
    if not ok:
        return False, {}, err
    if not accounts:
        return True, {}, ""

    acc_ids = [acc["id"] for acc in accounts
               if acc.get("id") and acc.get("total_pets", 1) > 0]
    if not acc_ids:
        return True, {}, ""

    headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}
    sem = asyncio.Semaphore(20)

    async def _do_get(session: aiohttp.ClientSession, url: str) -> list:
        async with session.get(url, headers=headers,
                               timeout=aiohttp.ClientTimeout(total=4)) as resp:
            if resp.status != 200:
                return []
            return await resp.json()

    async def fetch_one(session: aiohttp.ClientSession, aid) -> list:
        async with sem:
            url = f"{ACCOUNTSOPS_URL}/api/trackstats/accounts/{aid}/pets"
            try:
                return await asyncio.wait_for(_do_get(session, url), timeout=5.0)
            except asyncio.CancelledError:
                raise
            except Exception:
                return []

    connector = aiohttp.TCPConnector(limit=25, force_close=True)
    async with aiohttp.ClientSession(connector=connector) as session:
        try:
            raw = await asyncio.wait_for(
                asyncio.gather(*[fetch_one(session, aid) for aid in acc_ids],
                               return_exceptions=True),
                timeout=15.0,
            )
        except asyncio.TimeoutError:
            raw = []

    pets: dict = {}
    for entry in raw:
        if isinstance(entry, BaseException) or not isinstance(entry, list):
            continue
        for pet in entry:
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


async def enable_accounts(api_key: str, usernames: list[str]) -> tuple[bool, any, str]:
    return await _post(api_key, "/api/accounts/enable", {"usernames": usernames, "enabled": True})


async def get_accounts_with_pet(api_key: str, pet_kind: str) -> tuple[bool, list[str], str]:
    """Returns usernames of accounts that have the specified pet_kind."""
    ok, accounts, err = await get_trackstats_accounts(api_key)
    if not ok:
        return False, [], err
    if not accounts:
        return True, [], ""

    accs = [(acc.get("id"), acc.get("username") or acc.get("name", ""))
            for acc in accounts
            if acc.get("id") and acc.get("total_pets", 1) > 0]
    if not accs:
        return True, [], ""

    headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}
    sem = asyncio.Semaphore(20)

    async def _do_get(session: aiohttp.ClientSession, url: str) -> list:
        async with session.get(url, headers=headers,
                               timeout=aiohttp.ClientTimeout(total=4)) as resp:
            if resp.status != 200:
                return []
            return await resp.json()

    async def fetch_one(session: aiohttp.ClientSession, aid) -> list:
        async with sem:
            url = f"{ACCOUNTSOPS_URL}/api/trackstats/accounts/{aid}/pets"
            try:
                return await asyncio.wait_for(_do_get(session, url), timeout=5.0)
            except asyncio.CancelledError:
                raise
            except Exception:
                return []

    connector = aiohttp.TCPConnector(limit=25, force_close=True)
    async with aiohttp.ClientSession(connector=connector) as session:
        try:
            raw = await asyncio.wait_for(
                asyncio.gather(*[fetch_one(session, aid) for aid, _ in accs],
                               return_exceptions=True),
                timeout=15.0,
            )
        except asyncio.TimeoutError:
            raw = []

    usernames: list[str] = []
    for (acc_id, username), pets in zip(accs, raw):
        if isinstance(pets, BaseException) or not isinstance(pets, list):
            continue
        for pet in pets:
            if pet.get("pet_kind") == pet_kind:
                if username:
                    usernames.append(username)
                break

    return True, usernames, ""


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
