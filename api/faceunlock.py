import asyncio
import aiohttp
from config import ZEROPOINT_URL

_BASE = f"{ZEROPOINT_URL}/api/faceunlock-api"
_RETRY_EXC = (aiohttp.ClientConnectorError, aiohttp.ServerDisconnectedError, asyncio.TimeoutError)


async def _req(
    method: str,
    api_key: str,
    path: str,
    body: dict | None = None,
    raw: bool = False,
) -> tuple[bool, any, str]:
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
    last_err = "Не удалось подключиться к ZeroPoint."
    for attempt in range(3):
        try:
            async with aiohttp.ClientSession() as s:
                fn = getattr(s, method)
                kw: dict = {"headers": headers, "timeout": aiohttp.ClientTimeout(total=30)}
                if body is not None:
                    kw["json"] = body
                async with fn(f"{_BASE}{path}", **kw) as r:
                    if r.status == 401:
                        return False, None, "Неверный API ключ ZeroPoint."
                    if r.status == 403:
                        return False, None, "API ключ ZeroPoint отключён."
                    if r.status == 404:
                        return False, None, "not_found"
                    if r.status == 409:
                        d = await r.json()
                        return False, d, "Уже есть активная задача."
                    if r.status == 429:
                        d = await r.json()
                        return False, None, f"Rate limit. Повторить через {d.get('retry_after', '?')} с."
                    if r.status == 400:
                        d = await r.json()
                        return False, d, d.get("error", "Неверный запрос (400).")
                    if r.status == 503:
                        return False, None, "Сервис Face Unlock временно недоступен."
                    if r.status not in (200, 201):
                        return False, None, f"Ошибка сервера (код {r.status})."
                    if raw:
                        return True, await r.read(), ""
                    return True, await r.json(), ""
        except asyncio.TimeoutError:
            last_err = "Превышен таймаут ZeroPoint."
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
        except _RETRY_EXC:
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
        except Exception as e:
            return False, None, f"Ошибка: {e}"
    return False, None, last_err


async def get_balance(api_key: str) -> tuple[bool, dict, str]:
    ok, d, err = await _req("get", api_key, "/balance")
    return ok, d or {}, err


async def submit_job(api_key: str, accounts: str) -> tuple[bool, dict, str]:
    ok, d, err = await _req("post", api_key, "/submit", body={"accounts": accounts})
    return ok, d or {}, err


async def get_status(api_key: str, job_id: str) -> tuple[bool, dict, str]:
    ok, d, err = await _req("get", api_key, f"/status/{job_id}")
    return ok, d or {}, err


async def cancel_job(api_key: str, job_id: str) -> tuple[bool, dict, str]:
    ok, d, err = await _req("post", api_key, f"/cancel/{job_id}")
    return ok, d or {}, err


async def download_file(api_key: str, job_id: str, filename: str) -> tuple[bool, bytes, str]:
    ok, d, err = await _req("get", api_key, f"/download/{job_id}/{filename}", raw=True)
    return ok, d or b"", err
