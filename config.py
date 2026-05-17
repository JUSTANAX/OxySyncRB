import os
from dotenv import load_dotenv

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_BASE_DIR, ".env"))

BOT_TOKEN = os.getenv("BOT_API_TOKEN") or os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_API_TOKEN не найден! Проверь переменные окружения")

OWNER_ID = 6101243914

DB_PATH          = os.getenv("DB_PATH", os.path.join(_BASE_DIR, "oxysync.db"))
ACCOUNTSOPS_KEY  = os.getenv("ACCOUNTSOPS_KEY", "")
ZP_KEY           = os.getenv("ZP_KEY", "")
ACCOUNTSOPS_URL  = "https://accountops.org"
ZEROPOINT_URL    = "https://zeropoint.to"

DEFAULT_WATCHED_PETS: list[str] = [
    "basic_egg_2022_alicorn",
    "basic_egg_2022_ancient_dragon",
    "basic_egg_2022_dragonfly",
    "pet_progression_2026_purrowl",
    "unicorn",
    "dragon",
    "admin_abuse_egg_2026_egg",
    "diamond_griffin",
    "food_pets_2026_dragonfruit_fox",
    "golden_unicorn",
    "pet_recycler_2025_emberlight",
    "golden_dragon",
    "penguins_2025_dango_penguins",
    "admin_abuse_2025_sushi_penguin",
    "diamond_dragon",
]
