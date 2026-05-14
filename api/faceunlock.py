import aiohttp
from config import ZEROPOINT_URL

_BASE = f"{ZEROPOINT_URL}/api/faceunlock-api"


async def _req(
    method: str,
    api_key: str,
    path: str,
    body: dict | None = None,
    raw: bool = False,
) -> tuple[bool, any, str]:
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
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
    except aiohttp.ClientConnectorError:
        return False, None, "Не удалось подключиться к ZeroPoint."
    except Exception as e:
        return False, None, f"Ошибка: {e}"


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
