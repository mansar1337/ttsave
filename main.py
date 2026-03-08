"""
TikTok Downloader Telegram Bot
Built with aiogram 3.x, yt-dlp, asyncio
Supports premium users, quality selection, real-time progress, and cancellation.
"""

import asyncio
import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
import yt_dlp

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BOT_TOKEN: str = os.environ["BOT_TOKEN"]  # Must be set via environment variable
PREMIUM_FILE = Path("premium_users.json")
TIKTOK_RE = re.compile(r"https?://(?:www\.|vm\.)?tiktok\.com/\S+", re.IGNORECASE)

# Quality presets (yt-dlp format strings)
QUALITY_FORMATS = {
    "best": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
    "medium": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best",
    "low": "bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/best[height<=360][ext=mp4]/best",
}

# ---------------------------------------------------------------------------
# Premium storage (simple JSON)
# ---------------------------------------------------------------------------

def load_premium() -> set[int]:
    """Load premium user IDs from JSON file."""
    if PREMIUM_FILE.exists():
        try:
            return set(json.loads(PREMIUM_FILE.read_text()))
        except Exception:
            return set()
    return set()


def save_premium(users: set[int]) -> None:
    """Persist premium user IDs to JSON file."""
    PREMIUM_FILE.write_text(json.dumps(list(users)))


premium_users: set[int] = load_premium()

# ---------------------------------------------------------------------------
# Active downloads registry  {user_id: asyncio.Task}
# ---------------------------------------------------------------------------
active_tasks: dict[int, asyncio.Task] = {}

# ---------------------------------------------------------------------------
# FSM States
# ---------------------------------------------------------------------------

class DownloadStates(StatesGroup):
    choosing_options = State()
    downloading = State()


# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------

def premium_options_keyboard(quality: str = "best", audio: str = "sound") -> InlineKeyboardMarkup:
    """Inline keyboard for premium users to pick quality and audio."""
    quality_buttons = [
        InlineKeyboardButton(
            text=("✅ " if quality == "best" else "") + "Best",
            callback_data="q_best",
        ),
        InlineKeyboardButton(
            text=("✅ " if quality == "medium" else "") + "Medium",
            callback_data="q_medium",
        ),
        InlineKeyboardButton(
            text=("✅ " if quality == "low" else "") + "Low",
            callback_data="q_low",
        ),
    ]
    audio_buttons = [
        InlineKeyboardButton(
            text=("✅ " if audio == "sound" else "") + "🔊 With sound",
            callback_data="a_sound",
        ),
        InlineKeyboardButton(
            text=("✅ " if audio == "silent" else "") + "🔇 Silent",
            callback_data="a_silent",
        ),
    ]
    confirm_button = InlineKeyboardButton(text="⬇️ Download", callback_data="confirm_download")
    return InlineKeyboardMarkup(
        inline_keyboard=[quality_buttons, audio_buttons, [confirm_button]]
    )


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_download")]]
    )


# ---------------------------------------------------------------------------
# Router & handlers
# ---------------------------------------------------------------------------
router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "👋 <b>TikTok Downloader Bot</b>\n\n"
        "Send me any TikTok link and I'll download it for you!\n\n"
        "🔑 Send <code>PREMIUM2026</code> to unlock premium features:\n"
        "  • Quality selection (Best / Medium / Low)\n"
        "  • Audio options (With sound / Silent)\n"
        "  • Detailed download progress (speed, ETA, size)",
        parse_mode="HTML",
    )


@router.message(Command("premium"))
async def cmd_premium_status(message: Message) -> None:
    uid = message.from_user.id
    if uid in premium_users:
        await message.answer("⭐ You already have <b>Premium</b> access!", parse_mode="HTML")
    else:
        await message.answer(
            "Send the activation code to get Premium access.",
            parse_mode="HTML",
        )


@router.message(F.text == "PREMIUM2026")
async def activate_premium(message: Message) -> None:
    uid = message.from_user.id
    if uid in premium_users:
        await message.answer("⭐ You already have <b>Premium</b>!", parse_mode="HTML")
        return
    premium_users.add(uid)
    save_premium(premium_users)
    logger.info("User %d activated premium.", uid)
    await message.answer(
        "🎉 <b>Premium activated!</b>\n\n"
        "You now have access to:\n"
        "• Quality selection\n"
        "• Audio options\n"
        "• Detailed progress info\n\n"
        "Send a TikTok link to try it out!",
        parse_mode="HTML",
    )


@router.message(F.text.regexp(TIKTOK_RE))
async def handle_tiktok_link(message: Message, state: FSMContext) -> None:
    """Detect TikTok link and either show premium options or start download."""
    uid = message.from_user.id
    url_match = TIKTOK_RE.search(message.text)
    if not url_match:
        return
    url = url_match.group(0)

    # Cancel any existing task for this user
    if uid in active_tasks and not active_tasks[uid].done():
        active_tasks[uid].cancel()

    if uid in premium_users:
        # Store URL and defaults in FSM, show options keyboard
        await state.set_state(DownloadStates.choosing_options)
        await state.update_data(url=url, quality="best", audio="sound")
        await message.answer(
            "⭐ <b>Premium options</b>\nChoose quality and audio, then press Download:",
            reply_markup=premium_options_keyboard("best", "sound"),
            parse_mode="HTML",
        )
    else:
        # Non-premium: download immediately with defaults
        status_msg = await message.answer("⏳ Preparing download…", reply_markup=cancel_keyboard())
        task = asyncio.create_task(
            download_and_send(
                bot=message.bot,
                chat_id=uid,
                url=url,
                status_msg_id=status_msg.message_id,
                quality="best",
                audio="sound",
                is_premium=False,
            )
        )
        active_tasks[uid] = task


# ---------------------------------------------------------------------------
# Premium option callbacks
# ---------------------------------------------------------------------------

@router.callback_query(DownloadStates.choosing_options, F.data.startswith("q_"))
async def cb_quality(callback: CallbackQuery, state: FSMContext) -> None:
    quality = callback.data.split("_", 1)[1]  # best / medium / low
    data = await state.get_data()
    await state.update_data(quality=quality)
    await callback.message.edit_reply_markup(
        reply_markup=premium_options_keyboard(quality, data.get("audio", "sound"))
    )
    await callback.answer()


@router.callback_query(DownloadStates.choosing_options, F.data.startswith("a_"))
async def cb_audio(callback: CallbackQuery, state: FSMContext) -> None:
    audio = callback.data.split("_", 1)[1]  # sound / silent
    data = await state.get_data()
    await state.update_data(audio=audio)
    await callback.message.edit_reply_markup(
        reply_markup=premium_options_keyboard(data.get("quality", "best"), audio)
    )
    await callback.answer()


@router.callback_query(DownloadStates.choosing_options, F.data == "confirm_download")
async def cb_confirm_download(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    uid = callback.from_user.id
    url = data.get("url")
    quality = data.get("quality", "best")
    audio = data.get("audio", "sound")

    await state.set_state(DownloadStates.downloading)
    await callback.message.edit_text("⏳ Preparing download…", reply_markup=cancel_keyboard())

    task = asyncio.create_task(
        download_and_send(
            bot=callback.bot,
            chat_id=uid,
            url=url,
            status_msg_id=callback.message.message_id,
            quality=quality,
            audio=audio,
            is_premium=True,
        )
    )
    active_tasks[uid] = task
    await callback.answer()


@router.callback_query(F.data == "cancel_download")
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    uid = callback.from_user.id
    task = active_tasks.get(uid)
    if task and not task.done():
        task.cancel()
        logger.info("User %d cancelled download.", uid)
        await callback.message.edit_text("❌ Download cancelled.")
    else:
        await callback.message.edit_text("ℹ️ No active download to cancel.")
    await state.clear()
    await callback.answer()


# ---------------------------------------------------------------------------
# Core download coroutine
# ---------------------------------------------------------------------------

async def download_and_send(
    bot: Bot,
    chat_id: int,
    url: str,
    status_msg_id: int,
    quality: str,
    audio: str,
    is_premium: bool,
) -> None:
    """Download the TikTok video and send it; update progress in real time."""

    tmpdir = tempfile.mkdtemp(prefix="tiktok_")
    output_path: Optional[Path] = None
    last_edit_time: float = 0.0

    # ✅ Capture the running loop HERE, in the async context, before any threads start.
    loop = asyncio.get_running_loop()

    async def safe_edit(text: str) -> None:
        nonlocal last_edit_time
        now = time.monotonic()
        if now - last_edit_time < 0.5:
            return
        last_edit_time = now
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg_id,
                text=text,
                reply_markup=cancel_keyboard(),
            )
        except Exception:
            pass

    def progress_hook(d: dict) -> None:
        """Called by yt-dlp from an executor thread — use the pre-captured loop."""
        if d.get("status") == "downloading":
            if is_premium:
                downloaded = d.get("downloaded_bytes", 0)
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                speed = d.get("speed") or 0
                eta = d.get("eta") or 0
                percent_str = d.get("_percent_str", "?%").strip()
                speed_str = _fmt_bytes(speed) + "/s" if speed else "—"
                size_str = f"{_fmt_bytes(downloaded)} / {_fmt_bytes(total)}" if total else _fmt_bytes(downloaded)
                eta_str = _fmt_time(eta) if eta else "—"
                text = (
                    f"⬇️ Downloading…\n"
                    f"📊 {percent_str}  {size_str}\n"
                    f"⚡ {speed_str}   ⏱ ETA: {eta_str}"
                )
            else:
                percent_str = d.get("_percent_str", "").strip()
                text = f"⬇️ Downloading… {percent_str}"

            # ✅ Use the pre-captured loop, not get_event_loop() (which fails in threads)
            asyncio.run_coroutine_threadsafe(safe_edit(text), loop)

    try:
        await safe_edit("🔍 Fetching video info…")

        fmt = QUALITY_FORMATS.get(quality, QUALITY_FORMATS["best"])
        ydl_opts: dict = {
            "format": fmt,
            "outtmpl": str(Path(tmpdir) / "%(id)s.%(ext)s"),
            "progress_hooks": [progress_hook],
            "quiet": True,
            "no_warnings": True,
            "merge_output_format": "mp4",
            "noplaylist": True,
        }

        if audio == "silent":
            if quality == "low":
                ydl_opts["format"] = "bestvideo[height<=360][ext=mp4]/bestvideo"
            elif quality == "medium":
                ydl_opts["format"] = "bestvideo[height<=720][ext=mp4]/bestvideo"
            else:
                ydl_opts["format"] = "bestvideo[height<=720][ext=mp4]/bestvideo[ext=mp4]/bestvideo"

        info: dict = await loop.run_in_executor(
            None, lambda: _run_ydl(ydl_opts, url)
        )

        if info is None:
            await safe_edit("❌ Could not retrieve video info.")
            return

        video_id = info.get("id", "")
        candidates = list(Path(tmpdir).glob(f"{video_id}.*"))
        if not candidates:
            candidates = list(Path(tmpdir).glob("*"))
        if not candidates:
            await safe_edit("❌ Download failed: file not found.")
            return

        output_path = candidates[0]
        file_size = output_path.stat().st_size

        if file_size > 50 * 1024 * 1024:
            await safe_edit(
                "❌ File is too large to send via Telegram (>50 MB).\n"
                "Try a lower quality setting."
            )
            return

        await safe_edit("📤 Uploading to Telegram…")

        title = info.get("title", "TikTok video")
        caption = f"🎬 {title[:900]}" if title else "🎬 TikTok video"

        with output_path.open("rb") as video_file:
            await bot.send_video(
                chat_id=chat_id,
                video=video_file,  # type: ignore[arg-type]
                caption=caption,
                supports_streaming=True,
            )

        try:
            await bot.delete_message(chat_id=chat_id, message_id=status_msg_id)
        except Exception:
            pass

        logger.info("Sent video to user %d (%s)", chat_id, url)

    except asyncio.CancelledError:
        logger.info("Download task cancelled for user %d.", chat_id)
        raise

    except yt_dlp.utils.DownloadError as exc:
        logger.warning("yt-dlp error for user %d: %s", chat_id, exc)
        msg = str(exc)
        if "private" in msg.lower():
            await safe_edit("❌ This video is private and cannot be downloaded.")
        elif "removed" in msg.lower():
            await safe_edit("❌ This video has been removed.")
        else:
            await safe_edit(f"❌ Download error:\n<code>{msg[:200]}</code>")

    except Exception as exc:
        logger.exception("Unexpected error for user %d: %s", chat_id, exc)
        await safe_edit(f"❌ Unexpected error: {exc}")

    finally:
        _cleanup(tmpdir)
        active_tasks.pop(chat_id, None)

def _run_ydl(opts: dict, url: str) -> Optional[dict]:
    """Synchronous yt-dlp extraction; runs in executor thread."""
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=True)


def _cleanup(tmpdir: str) -> None:
    """Delete temp directory and its contents."""
    try:
        for f in Path(tmpdir).iterdir():
            f.unlink(missing_ok=True)
        Path(tmpdir).rmdir()
    except Exception as exc:
        logger.warning("Cleanup failed: %s", exc)


def _fmt_bytes(n: float) -> str:
    """Human-readable byte size."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _fmt_time(seconds: int) -> str:
    """Format seconds as mm:ss or hh:mm:ss."""
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    bot = Bot(token=BOT_TOKEN)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    dp.include_router(router)

    logger.info("Bot starting (long polling)…")
    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        logger.info("Bot stopped.")
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
