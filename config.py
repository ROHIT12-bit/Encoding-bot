"""
config.py - load environment variables for the bot
"""
import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("Error: BOT_TOKEN not set in .env")

API_ID = os.getenv("API_ID") or None
API_HASH = os.getenv("API_HASH") or None

CRF = os.getenv("CRF", "23")
PRESET = os.getenv("PRESET", "medium")
BOT_NAME = os.getenv("BOT_NAME", "Encode Bot")
TMP_DIR = os.path.join(os.getcwd(), "tmp")
os.makedirs(TMP_DIR, exist_ok=True)
"""

