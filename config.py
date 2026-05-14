import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_API_TOKEN") or os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_API_TOKEN не найден! Проверь переменные окружения")

OWNER_ID = 6101243914

DB_PATH = os.getenv("DB_PATH", "oxysync.db")
ACCOUNTSOPS_URL = "https://accountops.org"
ZEROPOINT_URL   = "https://zeropoint.to"
