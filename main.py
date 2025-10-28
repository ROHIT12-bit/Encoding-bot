#!/usr/bin/env python3
"""
Telegram video encoder bot (FFmpeg) for Termux / Pydroid 3 / low-end devices.
Features:
- Accept a video (or mkv/mp4) from user after /encode command.
- Encode it with libx265 (HEVC) using CRF and preset from config.
- Keep audio and subtitles streams (copies audio & subtitle if possible).
- Show simple progress messages and clean up temporary files.
- Uses python-telegram-bot v13
"""
import os
import logging
import uuid
import subprocess
import shlex
from functools import wraps
from pathlib import Path

from telegram import Update, Bot
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    ConversationHandler,
    CallbackContext,
)

import config

# Ensure tmp dir exists
TMP_DIR = getattr(config, "TMP_DIR", os.path.join(os.getcwd(), "tmp"))
Path(TMP_DIR).mkdir(exist_ok=True)

# Conversation states
WAITING_FILE = 1

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

def send_try_except(func):
    """Decorator to catch exceptions and inform user."""
    @wraps(func)
    def wrapper(update: Update, context: CallbackContext, *args, **kwargs):
        try:
            return func(update, context, *args, **kwargs)
        except Exception as e:
            logger.exception("Handler error:")
            text = f"❌ An error occurred: {e}"
            try:
                update.message.reply_text(text)
            except Exception:
                logger.exception("Failed to notify user about error.")
    return wrapper

@send_try_except
def start(update: Update, context: CallbackContext):
    welcome = f"👋 Hi! {config.BOT_NAME}\nI can encode videos to a smaller size using HEVC (H.265).\n\nUse /encode and then send the video file (mp4 / mkv).\nMax bot upload ~50MB. If your file is bigger, bot may fail to upload."
    update.message.reply_text(welcome)

@send_try_except
def help_cmd(update: Update, context: CallbackContext):
    update.message.reply_text("Usage:\n/encode - start encoding flow\n/cancel - cancel")

@send_try_except
def encode_command(update: Update, context: CallbackContext):
    update.message.reply_text(
        "📥 Please send the video file you want to encode (reply to this message with the file).\nSupported: mp4, mkv, other video documents."
    )
    return WAITING_FILE

def safe_filename(original_name: str):
    base = os.path.basename(original_name)
    # ensure unique
    return f"{uuid.uuid4().hex}_{base}"

@send_try_except
def handle_video(update: Update, context: CallbackContext):
    """Download the video, run ffmpeg to encode, and upload result."""
    msg = update.message
    bot: Bot = context.bot

    # Accept video or document (videos sometimes come as document)
    file_obj = None
    fname = None
    if msg.video:
        file_obj = msg.video.get_file()
        fname = msg.video.file_name or f"video_{uuid.uuid4().hex}.mp4"
    elif msg.document:
        # check mime type - accept common video/matroska
        mime = msg.document.mime_type or ''
        if not ('video' in mime or fname_looks_like_video(msg.document.file_name or '')):
            msg.reply_text("❌ That doesn't look like a video file. Please send a mp4/mkv video.")
            return ConversationHandler.END
        file_obj = msg.document.get_file()
        fname = msg.document.file_name or f"video_{uuid.uuid4().hex}.mkv"
    else:
        msg.reply_text("❌ No video detected. Send an actual video file.")
        return ConversationHandler.END

    in_path = os.path.join(TMP_DIR, safe_filename(fname))
    out_name = os.path.splitext(fname)[0] + "_encoded.mp4"
    out_path = os.path.join(TMP_DIR, safe_filename(out_name))

    # Downloading
    status_msg = msg.reply_text("⬇️ Downloading your file... (this may take a while)")
    try:
        file_obj.download(custom_path=in_path)
    except Exception as e:
        logger.exception("Download failed")
        try:
            status_msg.edit_text(f"❌ Failed to download file: {e}")
        except Exception:
            pass
        # cleanup
        if os.path.exists(in_path):
            os.remove(in_path)
        return ConversationHandler.END

    # Encode command
    ffmpeg_cmd = (
        f'ffmpeg -y -hide_banner -loglevel error -i {shlex.quote(in_path)} '
        f'-c:v libx265 -crf {shlex.quote(str(config.CRF))} -preset {shlex.quote(str(config.PRESET))} '
        f'-c:a copy -c:s copy {shlex.quote(out_path)}'
    )

    status_msg.edit_text("🔹 Encoding started... (this can be slow on mobile)")
    try:
        proc = subprocess.run(
            ffmpeg_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60*60
        )
        if proc.returncode != 0:
            stderr = proc.stderr.decode(errors='ignore')
            logger.error("FFmpeg error: %s", stderr)
            try:
                status_msg.edit_text("❌ Encoding failed. FFmpeg returned an error.")
                shortened = stderr[:1000] or "No error details"
                msg.reply_text(f"FFmpeg error (short):\n{shortened}")
            except Exception:
                pass
            # cleanup
            if os.path.exists(in_path):
                os.remove(in_path)
            return ConversationHandler.END

    except subprocess.TimeoutExpired:
        try:
            status_msg.edit_text("❌ Encoding timed out (took too long). Consider increasing timeout or using a faster preset.")
        except Exception:
            pass
        if os.path.exists(in_path):
            os.remove(in_path)
        return ConversationHandler.END
    except Exception as e:
        logger.exception("Unexpected error during encoding")
        try:
            status_msg.edit_text(f"❌ Unexpected error: {e}")
        except Exception:
            pass
        if os.path.exists(in_path):
            os.remove(in_path)
        return ConversationHandler.END

    # Check output exists
    if not os.path.exists(out_path):
        try:
            status_msg.edit_text("❌ Encoding finished but output file not found.")
        except Exception:
            pass
        if os.path.exists(in_path):
            os.remove(in_path)
        return ConversationHandler.END

    # Send the encoded file (as document). Telegram bots have file size limits (~50MB).
    try:
        size_mb = os.path.getsize(out_path) / (1024*1024)
        if size_mb > 49.5:
            try:
                status_msg.edit_text(
                    f"⚠️ Encoding finished but file is {size_mb:.1f} MB. Bots may not be able to upload >50MB.\n"
                    "You can download the file from device storage or use a user account bot."
                )
            except Exception:
                pass
        else:
            status_msg.edit_text("⚙️ Uploading encoded file...")
        with open(out_path, "rb") as f:
            msg.reply_document(f, filename=os.path.basename(out_path), caption="✅ Here is your encoded video.")
        status_msg.edit_text("✅ Done! Encoded file sent. Cleaning up...")
    except Exception as e:
        logger.exception("Failed to send encoded file")
        try:
            status_msg.edit_text(f"❌ Failed to upload encoded file: {e}")
        except Exception:
            pass
    finally:
        # Cleanup files
        for p in (in_path, out_path):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                logger.exception("Cleanup error for %s", p)

    return ConversationHandler.END

def fname_looks_like_video(name: str) -> bool:
    name = (name or '').lower()
    return any(name.endswith(ext) for ext in ('.mp4', '.mkv', '.mov', '.avi', '.webm', '.ts', '.m4v'))

@send_try_except
def cancel(update: Update, context: CallbackContext):
    update.message.reply_text("Cancelled. Use /encode to start again.")
    return ConversationHandler.END

def main():
    updater = Updater(config.BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    conv = ConversationHandler(
        entry_points=[CommandHandler('encode', encode_command)],
        states={
            WAITING_FILE: [MessageHandler(Filters.video | Filters.document, handle_video)]
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        allow_reentry=True,
    )

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help_cmd))
    dp.add_handler(conv)

    logger.info("Bot starting...")
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
