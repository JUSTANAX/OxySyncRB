_stats_msgs:      dict[int, tuple[int, int]] = {}
_zp_pending:      dict[str, list[str]]       = {}
_trade_debug_log: list[dict]                 = []
_trade_seen:      set[str]                   = set()

_TRADE_DEBUG_MAX = 150


def tdlog(username: str, event: str, detail: str = ""):
    import time
    _trade_debug_log.append({
        "ts":       time.time(),
        "username": username,
        "event":    event,
        "detail":   detail,
    })
    if len(_trade_debug_log) > _TRADE_DEBUG_MAX:
        _trade_debug_log.pop(0)


def get_trade_debug_log() -> list[dict]:
    return list(_trade_debug_log)


def is_trade_seen(username_lower: str) -> bool:
    return username_lower in _trade_seen


def mark_trade_seen(username_lower: str):
    _trade_seen.add(username_lower)


def unmark_trade_seen(username_lower: str):
    _trade_seen.discard(username_lower)


def save_stats_msg(user_id: int, chat_id: int, message_id: int):
    _stats_msgs[user_id] = (chat_id, message_id)


def clear_stats_msg(user_id: int):
    _stats_msgs.pop(user_id, None)


def get_all_stats_msgs() -> list[tuple[int, int, int]]:
    return [(uid, c, m) for uid, (c, m) in list(_stats_msgs.items())]


def save_zp_pending(job_id: str, usernames: list[str]):
    _zp_pending[job_id] = usernames


def pop_zp_pending(job_id: str) -> list[str]:
    return _zp_pending.pop(job_id, [])
