"""
config.py
Loads configuration from .env and exposes variables for the bot.
"""
import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("Error: BOT_TOKEN not set in .env")

# Encoding defaults
CRF = os.getenv("CRF", "23")
PRESET = os.getenv("PRESET", "medium")

# Bot name / welcome message
BOT_NAME = os.getenv("BOT_NAME", "Encode Bot")
TMP_DIR = os.path.join(os.getcwd(), "tmp")
