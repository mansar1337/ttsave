import os
import re
import asyncio
import tempfile
from pathlib import Path
from typing import Optional

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import Message, ContentType
from aiogram.enums import ParseMode
import yt_dlp
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not found in environment variables")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

TIKTOK_REGEX = re.compile(
    r'(https?://)?(www\.)?(tiktok\.com/@[^/]+/video/\d+|vt\.tiktok\.com/[A-Za-z0-9]+|vm\.tiktok\.com/[A-Za-z0-9]+|tiktok\.com/t/[A-Za-z0-9]+|www\.tiktok\.com/@[^/]+/video/\d+)'
)

class TikTokDownloader:
    def __init__(self):
        self.ydl_opts = {
            'format': 'best[height<=1080]/best/worst',  # Более гибкий формат
            'outtmpl': os.path.join(tempfile.gettempdir(), 'tiktok_%(id)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'ignoreerrors': True,  # Игнорировать ошибки формата
            'no_check_certificate': True,  # Отключить проверку сертификатов
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
    
    async def download_tiktok(self, url: str) -> Optional[str]:
        try:
            # Сначала пробуем получить информацию без скачивания
            info_opts = self.ydl_opts.copy()
            info_opts['listformats'] = True
            info_opts['simulate'] = True
            
            with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                
                if info:
                    filename = ydl.prepare_filename(info)
                    if os.path.exists(filename):
                        return filename
                    else:
                        # Ищем файл с разными расширениями
                        for ext in ['mp4', 'jpg', 'webp', 'png']:
                            test_file = os.path.join(tempfile.gettempdir(), f"tiktok_{info.get('id', 'unknown')}.{ext}")
                            if os.path.exists(test_file):
                                return test_file
                                
                        # Если файл не найден по шаблону, ищем в временной директории
                        temp_dir = tempfile.gettempdir()
                        for file in os.listdir(temp_dir):
                            if file.startswith('tiktok_') and info.get('id') in file:
                                full_path = os.path.join(temp_dir, file)
                                if os.path.isfile(full_path):
                                    return full_path
        except Exception as e:
            print(f"Error downloading TikTok: {e}")
            return None
        
        return None

tiktok_downloader = TikTokDownloader()

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я бот для скачивания видео и картинок из TikTok.\n\n"
        "Просто отправь мне ссылку на TikTok видео и я скачаю его для тебя!\n\n"
        "Поддерживаемые форматы ссылок:\n"
        "• tiktok.com/@username/video/1234567890\n"
        "• vt.tiktok.com/...\n"
        "• vm.tiktok.com/...\n"
        "• tiktok.com/t/...",
        parse_mode=ParseMode.HTML
    )

@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "📖 <b>Помощь</b>\n\n"
        "🔗 <b>Как использовать:</b>\n"
        "1. Отправь ссылку на TikTok видео\n"
        "2. Я скачаю его и отправлю тебе\n\n"
        "⚠️ <b>Важно:</b>\n"
        "• Бот работает только с публичными видео\n"
        "• Максимальное качество видео - 720p\n"
        "• Время скачивания зависит от размера видео\n\n"
        "❓ <b>Проблемы?</b>\n"
        "Если видео не скачивается, попробуй:\n"
        "• Проверить, что ссылка правильная\n"
        "• Убедиться, что видео доступно публично\n"
        "• Попробовать другую ссылку",
        parse_mode=ParseMode.HTML
    )

@dp.message(F.text)
async def handle_text_message(message: Message):
    text = message.text
    
    tiktok_match = TIKTOK_REGEX.search(text)
    
    if not tiktok_match:
        return
    
    url = tiktok_match.group(0)
    if not url.startswith('http'):
        url = 'https://' + url
    
    print(f"Найдена TikTok ссылка: {url}")
    
    processing_msg = await message.reply("⏳ Скачиваю видео... Пожалуйста, подожди.")
    
    try:
        file_path = await tiktok_downloader.download_tiktok(url)
        
        if file_path and os.path.exists(file_path):
            file_size = os.path.getsize(file_path)
            
            if file_size > 50 * 1024 * 1024:  # 50MB limit
                await processing_msg.edit_text(
                    "❌ Видео слишком большое для Telegram (больше 50МБ)\n"
                    "Попробуй другую ссылку или скачай видео напрямую."
                )
                if os.path.exists(file_path):
                    os.remove(file_path)
                return
            
            await processing_msg.edit_text("✅ Загружаю видео в Telegram...")
            
            file_extension = Path(file_path).suffix.lower()
            
            if file_extension in ['.jpg', '.jpeg', '.png', '.webp']:
                await bot.send_photo(
                    chat_id=message.chat.id,
                    photo=types.FSInputFile(file_path),
                    caption="📸 Вот твое изображение из TikTok!"
                )
            else:
                await bot.send_video(
                    chat_id=message.chat.id,
                    video=types.FSInputFile(file_path),
                    caption="🎬 Вот твое видео из TikTok!"
                )
            
            await processing_msg.delete()
            
            if os.path.exists(file_path):
                os.remove(file_path)
        else:
            await processing_msg.edit_text(
                "❌ Не удалось скачать видео.\n"
                "Возможные причины:\n"
                "• Видео удалено или недоступно\n"
                "• Проблемы с доступом к TikTok\n"
                "• Неправильная ссылка\n\n"
                "Попробуй другую ссылку."
            )
    
    except Exception as e:
        print(f"Error in handle_text_message: {e}")
        await processing_msg.edit_text(
            "❌ Произошла ошибка при скачивании видео.\n"
            "Попробуй еще раз или другую ссылку."
        )

@dp.message()
async def handle_other_messages(message: Message):
    if message.content_type == ContentType.TEXT:
        return
    
    await message.reply(
        "🤔 Я понимаю только текстовые сообщения со ссылками на TikTok.\n\n"
        "Отправь мне ссылку на TikTok видео и я скачаю его для тебя!",
        parse_mode=ParseMode.HTML
    )

async def main():
    print("🚀 TikTok Bot запускается...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("👋 Бот остановлен")
