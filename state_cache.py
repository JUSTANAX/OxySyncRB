_stats_msgs: dict[int, tuple[int, int]] = {}  # user_id -> (chat_id, message_id)
_zp_pending: dict[str, list[str]] = {}       # job_id  -> list of submitted usernames


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
