#!/usr/bin/env python3
"""
Encode Bot using Pyrogram (async) suitable for Render deployment.
- Receives a video via /encode flow, encodes with ffmpeg (libx265), and returns the file.
Notes:
- Ensure ffmpeg is installed on the host (Render does not include ffmpeg by default).
- Telegram bot upload limit applies (~50MB for bot accounts). For larger files consider using a user session (not covered here) or external upload.
"""
import os
import asyncio
import shlex
import uuid
import logging
import subprocess
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.types import Message
import config

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

TMP_DIR = config.TMP_DIR

# Build Pyrogram Client. If API_ID/API_HASH provided, use them, else use bot-only
client_kwargs = {}
if config.API_ID and config.API_HASH:
    # If provided, run a named session (safe on Render if envs set)
    session_name = "encode_bot_session"
    client = Client(session_name, api_id=int(config.API_ID), api_hash=config.API_HASH, bot_token=config.BOT_TOKEN)
else:
    # Bot-only mode
    client = Client("encode-bot", bot_token=config.BOT_TOKEN)

def safe_filename(original_name: str) -> str:
    base = os.path.basename(original_name)
    return f"{uuid.uuid4().hex}_{base}"

def fname_looks_like_video(name: str) -> bool:
    name = (name or "").lower()
    return any(name.endswith(ext) for ext in (".mp4", ".mkv", ".mov", ".avi", ".webm", ".ts", ".m4v"))

async def run_ffmpeg(in_path: str, out_path: str, crf: str, preset: str) -> (int, str):
    """
    Run ffmpeg in a thread to avoid blocking the event loop.
    Returns (returncode, stderr_output)
    """
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", in_path,
        "-c:v", "libx265", "-crf", str(crf), "-preset", str(preset),
        "-c:a", "copy", "-c:s", "copy",
        out_path
    ]
    logger.info("Running ffmpeg: %s", " ".join(shlex.quote(x) for x in cmd))
    # Run in executor to avoid blocking
    proc = await asyncio.get_event_loop().run_in_executor(None, lambda: subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE))
    stderr = proc.stderr.decode(errors="ignore")
    return proc.returncode, stderr

@client.on_message(filters.command("start") & filters.private)
async def start_handler(client: Client, message: Message):
    welcome = f"👋 Hi! {config.BOT_NAME}\nI can encode videos to a smaller size using HEVC (H.265).\n\nUse /encode and then send the video file (mp4 / mkv)."
    await message.reply_text(welcome)

@client.on_message(filters.command("help") & filters.private)
async def help_handler(client: Client, message: Message):
    await message.reply_text("Usage:\n/encode - start encoding flow\n/cancel - cancel current operation")

# We'll use a simple approach: user sends /encode and then replies with a video/document
user_waiting = set()

@client.on_message(filters.command("encode") & filters.private)
async def encode_cmd(client: Client, message: Message):
    await message.reply_text("📥 Please send the video file you want to encode (reply to this message with the file).")
    user_waiting.add(message.from_user.id)

@client.on_message(filters.command("cancel") & filters.private)
async def cancel_cmd(client: Client, message: Message):
    user_waiting.discard(message.from_user.id)
    await message.reply_text("Cancelled. Use /encode to start again.")

@client.on_message((filters.video | filters.document) & filters.private)
async def handle_media(client: Client, message: Message):
    uid = message.from_user.id
    if uid not in user_waiting:
        # Ignore unsolicited uploads
        return

    # Determine file object and filename
    file = None
    fname = None
    if message.video:
        file = await message.video.get_file()
        fname = message.video.file_name or f"video_{uuid.uuid4().hex}.mp4"
    elif message.document:
        mime = message.document.mime_type or ""
        if "video" not in mime and not fname_looks_like_video(message.document.file_name or ""):
            await message.reply_text("❌ That doesn't look like a video file. Please send mp4/mkv video.")
            user_waiting.discard(uid)
            return
        file = await message.document.get_file()
        fname = message.document.file_name or f"video_{uuid.uuid4().hex}.mkv"

    in_path = os.path.join(TMP_DIR, safe_filename(fname))
    out_name = os.path.splitext(fname)[0] + "_encoded.mp4"
    out_path = os.path.join(TMP_DIR, safe_filename(out_name))

    status = await message.reply_text("⬇️ Downloading your file... (this may take a while)")
    try:
        await file.download(in_path)
    except Exception as e:
        logger.exception("Download failed")
        await status.edit_text(f"❌ Failed to download file: {e}")
        user_waiting.discard(uid)
        if os.path.exists(in_path):
            os.remove(in_path)
        return

    await status.edit_text("🔹 Encoding started... (this can be slow on Render if ffmpeg is CPU-limited)")
    try:
        retcode, stderr = await run_ffmpeg(in_path, out_path, config.CRF, config.PRESET)
        if retcode != 0:
            logger.error("FFmpeg error: %s", stderr[:2000])
            await status.edit_text("❌ Encoding failed. FFmpeg returned an error.")
            await message.reply_text(f"FFmpeg error (short):\n{stderr[:1000] or 'No details'}")
            if os.path.exists(in_path):
                os.remove(in_path)
            user_waiting.discard(uid)
            return
    except asyncio.TimeoutError:
        await status.edit_text("❌ Encoding timed out.")
        if os.path.exists(in_path):
            os.remove(in_path)
        user_waiting.discard(uid)
        return
    except Exception as e:
        logger.exception("Unexpected error")
        await status.edit_text(f"❌ Unexpected error: {e}")
        if os.path.exists(in_path):
            os.remove(in_path)
        user_waiting.discard(uid)
        return

    if not os.path.exists(out_path):
        await status.edit_text("❌ Encoding finished but output file not found.")
        if os.path.exists(in_path):
            os.remove(in_path)
        user_waiting.discard(uid)
        return

    size_mb = os.path.getsize(out_path) / (1024*1024)
    if size_mb > 49.5:
        await status.edit_text(f"⚠️ Encoding finished but file is {size_mb:.1f} MB. Bots may not be able to upload >50MB. Sending attempt anyway...")
    else:
        await status.edit_text("⚙️ Uploading encoded file...")

    try:
        await message.reply_document(out_path, caption="✅ Here is your encoded video.")
        await status.edit_text("✅ Done! Encoded file sent. Cleaning up...")
    except Exception as e:
        logger.exception("Upload failed")
        await status.edit_text(f"❌ Failed to upload encoded file: {e}")
    finally:
        for p in (in_path, out_path):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                logger.exception("Cleanup error for %s", p)

    user_waiting.discard(uid)

if __name__ == "__main__":
    # Run the client
    client.run()
