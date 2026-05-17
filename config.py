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
    "Alicorn",
    "Ancient Dragon",
    "Blue Whale",
    "Dango Penguins",
    "DiamondDragon Pet",
    "DiamondGriffin Pet",
    "Diamond Mahi Mahi",
    "DiamondUnicorn Pet",
    "Dragon Fruit Fox",
    "Dragonfly",
    "Emberlight",
    "GoldenDragon Pet",
    "GoldenGriffin Pet",
    "GoldenUnicorn Pet",
    "Sea Turtle",
    "Silverback Gorilla",
]
