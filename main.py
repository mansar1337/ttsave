import os
import re
import asyncio
import tempfile
import time
import math
from pathlib import Path
from typing import Optional, Dict, List
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, ContentType, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.utils.keyboard import InlineKeyboardBuilder
import yt_dlp
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 6309839081

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not found in environment variables")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Оптимизация для GitHub Actions
GITHUB_ACTIONS_MODE = os.getenv("GITHUB_ACTIONS") == "true"
MAX_RUNTIME = 21600  # Максимальное время работы в GitHub Actions (6 часов = 21600 секунд)

# Премиум настройки
PREMIUM_SECRET_CODE = "PREMIUM2024"  # Секретный код для активации
premium_users = set()  # Множество премиум пользователей

# Языковые настройки
LANGUAGE = os.getenv("BOT_LANGUAGE", "ru")  # ru/en по умолчанию
AUTHOR_CHANNEL = "@project_eliminator"vfr

# Хранилище статистики
bot_stats = {
    'total_downloads': 0,
    'successful_downloads': 0,
    'failed_downloads': 0,
    'premium_users': 0,
    'start_time': datetime.now(),
    'last_download': None,
    'users': set(),  # Все пользователи которые заходили в бот
    'user_activity': {}  # Активность пользователей
}

# Список заблокированных пользователей
blocked_users = set()

# Временное хранилище для прогресса
download_progress = {}

# ThreadPoolExecutor для асинхронного скачивания
executor = ThreadPoolExecutor(max_workers=4)

# Тексты на разных языках
TEXTS = {
    'ru': {
        'welcome': "🎉 **Добро пожаловать в TikTok Downloader Bot!**",
        'help_text': "ℹ️ **Помощь - TikTok Downloader Bot**",
        'start_help': "Я помогу тебе скачивать видео и изображения из TikTok бесплатно и без watermark!",
        'features': "🚀 **Что я умею:**",
        'download_video': "📥 Скачать видео",
        'statistics': "📊 Статистика",
        'help': "ℹ️ Помощь",
        'my_id': "🆔 Мой ID",
        'premium': "👑 Премиум",
        'premium_settings': "⚙️ Премиум настройки",
        'cancel_download': "❌ Отменить скачивание",
        'downloading': "📥 **Скачивание видео**",
        'searching': "🔍 Поиск видео...",
        'completed': "✅ Скачивание завершено!",
        'error': "❌ Ошибка скачивания",
        'too_big': "❌ **Ошибка размера файла**",
        'channel': f"📢 **Канал автора:** {AUTHOR_CHANNEL}",
        'premium_activated': "🎉 **Премиум активирован!**",
        'premium_benefits': "🎉 Ваши преимущества:",
        'premium_code': "🔓 **Получите премиум доступ:**",
        'secret_code': "Отправьте секретный код в сообщении:\n\n`PREMIUM2024`",
        'premium_hint': "🤫 **Код известен только избранным...**"
    },
    'en': {
        'welcome': "🎉 **Welcome to TikTok Downloader Bot!**",
        'help_text': "ℹ️ **Help - TikTok Downloader Bot**",
        'start_help': "I help you download TikTok videos and images for free and without watermark!",
        'features': "🚀 **What I can do:**",
        'download_video': "📥 Download Video",
        'statistics': "📊 Statistics",
        'help': "ℹ️ Help",
        'my_id': "🆔 My ID",
        'premium': "👑 Premium",
        'premium_settings': "⚙️ Premium Settings",
        'cancel_download': "❌ Cancel Download",
        'downloading': "📥 **Downloading Video**",
        'searching': "🔍 Searching for video...",
        'completed': "✅ Download completed!",
        'error': "❌ Download error",
        'too_big': "❌ **File size error**",
        'channel': f"📢 **Author's Channel:** {AUTHOR_CHANNEL}",
        'premium_activated': "🎉 **Premium Activated!**",
        'premium_benefits': "🎉 Your benefits:",
        'premium_code': "🔓 **Get Premium Access:**",
        'secret_code': "Send the secret code in a message:\n\n`PREMIUM2024`",
        'premium_hint': "🤫 **Code known only to the chosen ones...**"
    }
}

def get_text(key: str) -> str:
    """Получить текст на текущем языке"""
    return TEXTS.get(LANGUAGE, TEXTS['ru']).get(key, key)

# Хранилище активных скачиваний для отмены
active_downloads = {}

# Хранилище последних сообщений для избежания дублирования
last_messages = {}

# Функция отмены скачивания
def cancel_download(user_id: int) -> bool:
    if user_id in download_progress and download_progress[user_id].get('active', False):
        download_progress[user_id]['active'] = False
        download_progress[user_id]['status_text'] = "❌ Скачивание отменено"
        if user_id in active_downloads:
            del active_downloads[user_id]
        
        # Принудительно останавливаем все процессы yt-dlp для этого пользователя
        import signal
        import threading
        
        # Ищем и останавливаем все потоки скачивания
        for thread in threading.enumerate():
            if thread.name == f"download_{user_id}" or f"tiktok_download_{user_id}" in str(thread.name):
                print(f"Force stopping thread {thread.name} for user {user_id}")
                # В Windows используем terminate, в Unix - SIGTERM
                if hasattr(os, 'kill'):
                    try:
                        os.kill(thread.ident, signal.SIGTERM)
                    except:
                        pass
        
        return True
    return False

# Функция проверки премиум статуса
def is_premium_user(user_id: int) -> bool:
    return user_id in premium_users

# Функция активации премиум
def activate_premium(user_id: int) -> bool:
    if user_id not in premium_users:
        premium_users.add(user_id)
        bot_stats['premium_users'] = len(premium_users)
        return True
    return False

# Функция проверки админа
def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

# Функция проверки заблокированного пользователя
def is_user_blocked(user_id: int) -> bool:
    return user_id in blocked_users

# Функция обновления статистики
def update_stats(success: bool, user_id: int):
    bot_stats['total_downloads'] += 1
    
    if success:
        bot_stats['successful_downloads'] += 1
        bot_stats['last_download'] = datetime.now()
    else:
        bot_stats['failed_downloads'] += 1
    
    # Добавляем пользователя в статистику
    bot_stats['users'].add(user_id)
    
    # Обновляем активность пользователя
    if user_id not in bot_stats['user_activity']:
        bot_stats['user_activity'][user_id] = {
            'first_visit': datetime.now(),
            'last_visit': datetime.now(),
            'visits': 0
        }
    
    bot_stats['user_activity'][user_id]['last_visit'] = datetime.now()
    bot_stats['user_activity'][user_id]['visits'] += 1

# Функция создания прогресс-бара
def create_progress_bar(current: int, total: int, length: int = 20) -> str:
    if total == 0:
        return "█" * length
    
    filled = int((current / total) * length)
    bar = "█" * filled + "░" * (length - filled)
    percentage = min(100, int((current / total) * 100))
    return f"{bar} {percentage}%"

# Функция создания основной клавиатуры
def create_main_keyboard(user_id: int = None) -> ReplyKeyboardMarkup:
    is_premium = is_premium_user(user_id) if user_id else False
    
    if is_premium:
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [
                    KeyboardButton(text=get_text('download_video')),
                    KeyboardButton(text=get_text('statistics'))
                ],
                [
                    KeyboardButton(text=get_text('premium_settings')),
                    KeyboardButton(text=get_text('help'))
                ],
                [
                    KeyboardButton(text=get_text('my_id')),
                    KeyboardButton(text=get_text('premium'))
                ],
                [
                    KeyboardButton(text=get_text('cancel_download'))
                ]
            ],
            resize_keyboard=True,
            input_field_placeholder="Отправьте ссылку на TikTok..." if LANGUAGE == 'ru' else "Send TikTok link..."
        )
    else:
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [
                    KeyboardButton(text=get_text('download_video')),
                    KeyboardButton(text=get_text('statistics'))
                ],
                [
                    KeyboardButton(text=get_text('help')),
                    KeyboardButton(text=get_text('my_id'))
                ],
                [
                    KeyboardButton(text=get_text('cancel_download'))
                ]
            ],
            resize_keyboard=True,
            input_field_placeholder="Отправьте ссылку на TikTok..." if LANGUAGE == 'ru' else "Send TikTok link..."
        )
    return keyboard

# Функция создания админ клавиатуры
def create_admin_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"),
        InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users")
    )
    builder.row(
        InlineKeyboardButton(text="🚫 Заблокировать", callback_data="admin_block"),
        InlineKeyboardButton(text="✅ Разблокировать", callback_data="admin_unblock")
    )
    builder.row(
        InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast"),
        InlineKeyboardButton(text="⚙️ Настройки", callback_data="admin_settings")
    )
    builder.row(
        InlineKeyboardButton(text="🔄 Перезагрузить", callback_data="admin_restart")
    )
    return builder.as_markup()

# Функция создания кнопок навигации
def create_navigation_keyboard(back_button: bool = True, admin_panel: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    
    if admin_panel:
        builder.row(InlineKeyboardButton(text="🔐 Админ панель", callback_data="admin_back"))
    
    if back_button:
        builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main"))
    
    return builder.as_markup()

TIKTOK_REGEX = re.compile(
    r'(https?://)?(www\.)?(tiktok\.com/@[^/]+/(?:video|photo)/\d+|vt\.tiktok\.com/[A-Za-z0-9]+|vm\.tiktok\.com/[A-Za-z0-9]+|tiktok\.com/t/[A-Za-z0-9]+|www\.tiktok\.com/@[^/]+/(?:video|photo)/\d+)'
)

class TikTokDownloader:
    def __init__(self, user_id: int = None):
        self.user_id = user_id
        self.is_premium = is_premium_user(user_id) if user_id else False
        
        # Базовые настройки
        self.ydl_opts = {
            'format': 'best[height<=1080]/best/worst',  # Более гибкий формат
            'outtmpl': os.path.join(tempfile.gettempdir(), 'tiktok_%(id)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'ignoreerrors': True,  # Игнорировать ошибки формата
            'no_check_certificate': True,  # Отключить проверку сертификатов
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'progress_hooks': [self.progress_hook],  # Добавляем хук для прогресса
        }
        
        # Премиум и админ настройки
        if self.is_premium or is_admin(user_id):
            self.ydl_opts.update({
                'format': 'best[height<=2160]/best/worst',  # 4K для премиум и админов
                'noplaylist': False,  # Поддержка плейлистов
                'writesubtitles': True,  # Субтитры
                'writeautomaticsub': True,  # Авто-субтитры
                'embedsubtitles': True,  # Встроенные субтитры
            })
    
    def progress_hook(self, d):
        """Хук для отслеживания прогресса скачивания"""
        if d['status'] == 'downloading':
            downloaded = d.get('downloaded_bytes', 0)
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            speed = d.get('speed', 0)
            
            # Обновляем прогресс для конкретного пользователя
            if self.user_id and self.user_id in download_progress:
                progress_data = {
                    'downloaded': downloaded,
                    'total': total,
                    'speed': speed,
                    'percentage': (downloaded / total * 100) if total > 0 else 0
                }
                download_progress[self.user_id].update(progress_data)
                print(f"Progress updated for user {self.user_id}: {progress_data['percentage']:.1f}%")
    
    def _download_sync(self, url: str, user_id: int) -> Optional[str]:
        """Синхронное скачивание для выполнения в ThreadPool"""
        import threading
        import signal
        
        # Устанавливаем имя потока для идентификации
        threading.current_thread().name = f"tiktok_download_{user_id}"
        
        try:
            # Проверяем не отменено ли скачивание перед началом
            if user_id not in active_downloads:
                print(f"Download cancelled before start for user {user_id}")
                return None
                
            with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                
                # Проверяем не отменено ли скачивание после извлечения информации
                if user_id not in active_downloads:
                    print(f"Download cancelled after info extraction for user {user_id}")
                    return None
                
                if info:
                    filename = ydl.prepare_filename(info)
                    if os.path.exists(filename):
                        return filename
                    else:
                        # Ищем файл с разными расширениями
                        for ext in ['mp4', 'jpg', 'webp', 'png', 'srt', 'vtt']:
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
            print(f"Error in _download_sync: {e}")
            return None
        
        return None
    
    async def download_tiktok(self, url: str, user_id: int, message: Message) -> Optional[str]:
        try:
            # Инициализируем прогресс для пользователя
            download_progress[user_id] = {
                'active': True,
                'downloaded': 0,
                'total': 0,
                'speed': 0,
                'percentage': 0,
                'message': message,
                'status_text': get_text('searching'),
                'start_time': time.time()
            }
            
            # Добавляем в активные скачивания
            active_downloads[user_id] = True
            
            print(f"Starting async download for user {user_id}")
            
            # Запускаем задачу обновления прогресса с частотой для премиум и админов
            update_interval = 0.3 if (self.is_premium or is_admin(user_id)) else 1.0  # 300мс для премиум/админов, 1с для обычных
            progress_task = asyncio.create_task(self.update_progress_message(user_id, update_interval))
            print(f"Progress task started for user {user_id}")
            
            # Выполняем скачивание в отдельном потоке, чтобы не блокировать event loop
            loop = asyncio.get_event_loop()
            file_path = await loop.run_in_executor(executor, self._download_sync, url, user_id)
            
            # Проверяем не отменено ли скачивание
            if user_id not in active_downloads:
                print(f"Download cancelled for user {user_id}")
                return None
            
            # Завершаем прогресс
            download_progress[user_id]['active'] = False
            download_progress[user_id]['status_text'] = get_text('completed')
            
            # Отменяем задачу прогресса
            progress_task.cancel()
            print(f"Progress task cancelled for user {user_id}")
            
            # Удаляем из активных скачиваний
            if user_id in active_downloads:
                del active_downloads[user_id]
            
            return file_path
            
        except Exception as e:
            print(f"Error downloading TikTok: {e}")
            download_progress[user_id]['active'] = False
            download_progress[user_id]['status_text'] = get_text('error')
            if user_id in active_downloads:
                del active_downloads[user_id]
            return None
        
        finally:
            # Очищаем прогресс через некоторое время
            await asyncio.sleep(5)
            download_progress.pop(user_id, None)
            last_messages.pop(user_id, None)
    
    async def update_progress_message(self, user_id: int, update_interval: float = 1.0):
        """Обновление сообщения с прогрессом"""
        count = 0
        while download_progress.get(user_id, {}).get('active', False):
            try:
                progress_data = download_progress.get(user_id, {})
                message = progress_data.get('message')
                
                count += 1
                print(f"Update #{count} for user {user_id}: {progress_data.get('percentage', 0):.1f}%")
                
                # Обновляем сообщение даже если total=0, чтобы показать статус
                if message:
                    # Создаем красивое сообщение с прогрессом
                    if progress_data.get('total', 0) > 0:
                        progress_bar = create_progress_bar(
                            progress_data.get('downloaded', 0), 
                            progress_data.get('total', 0)
                        )
                        
                        speed = progress_data.get('speed', 0)
                        speed_text = f"{speed/1024/1024:.1f} MB/s" if speed > 0 else "Вычисление..."
                        
                        status_text = progress_data.get('status_text', "Скачивание...")
                        
                        # Дополнительная информация для премиум
                        premium_info = ""
                        if self.is_premium:
                            elapsed_time = time.time() - progress_data.get('start_time', time.time())
                            eta = (progress_data.get('total', 0) - progress_data.get('downloaded', 0)) / speed if speed > 0 else 0
                            premium_info = f"""
⏱️ Время: {elapsed_time:.1f}s
⏳ Осталось: {eta:.1f}s
👑 Премиум режим
                            """.strip()
                        
                        text = f"""
📥 **Скачивание видео** {'👑' if self.is_premium else ''}
━━━━━━━━━━━━━━━━━━
{progress_bar}
📊 {progress_data.get('percentage', 0):.1f}%
⚡ Скорость: {speed_text}
📦 Скачано: {progress_data.get('downloaded', 0)/1024/1024:.1f} MB
📏 Всего: {progress_data.get('total', 0)/1024/1024:.1f} MB
{status_text}
{premium_info}
                        """.strip()
                    else:
                        # Показываем статус даже без прогресса
                        speed = progress_data.get('speed', 0)
                        speed_text = f"{speed/1024/1024:.1f} MB/s" if speed > 0 else "Вычисление..."
                        status_text = progress_data.get('status_text', "🔍 Поиск видео...")
                        
                        text = f"""
📥 **Скачивание видео** {'👑' if self.is_premium else ''}
━━━━━━━━━━━━━━━━━━
░░░░░░░░░░░░░░░░░░░░ 0%
📊 Вычисление...
⚡ Скорость: {speed_text}
📦 Скачано: 0.0 MB
📏 Всего: 0.0 MB
{status_text}{' 👑' if self.is_premium else ''}
                        """.strip()
                    
                    try:
                        # Проверяем не изменилось ли сообщение
                        current_message = text
                        last_message = last_messages.get(user_id, "")
                        
                        if current_message != last_message:
                            await message.edit_text(text, parse_mode=ParseMode.MARKDOWN)
                            last_messages[user_id] = current_message
                            print(f"Message updated successfully for user {user_id}")
                        else:
                            print(f"Message unchanged for user {user_id}, skipping update")
                    except Exception as e:
                        print(f"Failed to update message: {e}")
                        pass  # Сообщение могло быть удалено
                else:
                    print(f"No message for user {user_id}")
                
                await asyncio.sleep(update_interval)  # Динамическое обновление
                
            except Exception as e:
                print(f"Error updating progress: {e}")
                break

tiktok_downloader = TikTokDownloader()

# Обработка кнопки отмены скачивания
@dp.message(F.text == get_text('cancel_download'))
async def button_cancel_download(message: Message):
    if cancel_download(message.from_user.id):
        cancel_text = "❌ **Скачивание отменено**\n\nВы можете начать новое скачивание." if LANGUAGE == 'ru' else "❌ **Download Cancelled**\n\nYou can start a new download."
        await message.answer(cancel_text, reply_markup=create_main_keyboard(message.from_user.id), parse_mode=ParseMode.MARKDOWN)
    else:
        no_download_text = "ℹ️ **Нет активных скачиваний**\n\nОтправьте ссылку на TikTok видео." if LANGUAGE == 'ru' else "ℹ️ **No Active Downloads**\n\nSend a TikTok video link."
        await message.answer(no_download_text, reply_markup=create_main_keyboard(message.from_user.id), parse_mode=ParseMode.MARKDOWN)

# Обработка премиум кнопок
@dp.message(F.text == get_text('premium'))
async def button_premium(message: Message):
    if is_premium_user(message.from_user.id):
        premium_text = """
👑 **Премиум статус активирован!**
━━━━━━━━━━━━━━━━━━

🎉 Ваши преимущества:
• 📹 4K качество видео
• ⚡ Обновление прогресса каждые 300мс
• 🎬 Поддержка плейлистов
• 📝 Субтитры к видео
• 🚫 Приоритетная обработка
• 📊 Расширенная статистика

⚙️ **Настройки:**
• Макс. качество: 4K (2160p)
• Скорость обновления: 3x быстрее
• Дополнительные форматы: .srt, .vtt

🎮 **Используйте "⚙️ Премиум настройки"**
        """.strip()
        
        await message.answer(premium_text, reply_markup=create_main_keyboard(message.from_user.id), parse_mode=ParseMode.MARKDOWN)
    else:
        premium_text = """
👑 **Активация Премиум**
━━━━━━━━━━━━━━━━━━

🔓 **Получите премиум доступ:**

📋 **Что дает премиум:**
• 📹 4K качество видео (вместо 1080p)
• ⚡ Ультра-быстрый прогресс-бар (300мс)
• 🎬 Скачивание плейлистов
• 📝 Автоматические субтитры
• 🚫 Приоритет в очереди
• 📊 Детальная статистика

🔑 **Как активировать:**
Отправьте секретный код в сообщении:

`PREMIUM2024`

💡 **После активации:**
Новые кнопки и функции появятся автоматически!

🤫 **Код известен только избранным...**
        """.strip()
        
        await message.answer(premium_text, reply_markup=create_main_keyboard(message.from_user.id), parse_mode=ParseMode.MARKDOWN)

@dp.message(F.text == "⚙️ Премиум настройки")
async def button_premium_settings(message: Message):
    if not is_premium_user(message.from_user.id):
        await message.answer(
            "❌ **Доступ запрещен**\n\n"
            "Эта функция доступна только для премиум пользователей.\n\n"
            "👑 Активируйте премиум статус!",
            reply_markup=create_main_keyboard(message.from_user.id),
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    settings_text = """
⚙️ **Премиум настройки**
━━━━━━━━━━━━━━━━━━

🎬 **Качество видео:** 4K (2160p)
⚡ **Обновление прогресса:** 300мс
📝 **Субтитры:** Автоматически
🚀 **Приоритет:** Высокий

📋 **Доступные форматы:**
• MP4 (до 4K)
• SRT (субтитры)
• VTT (субтитры)
• WEBP, JPG, PNG

🔧 **Дополнительно:**
• Поддержка плейлистов
• Множественные скачивания
• Расширенная статистика

💎 **Ваш премиум статус активен!**
    """.strip()
    
    await message.answer(settings_text, reply_markup=create_main_keyboard(message.from_user.id), parse_mode=ParseMode.MARKDOWN)

# Обработка секретного кода
@dp.message(F.text == PREMIUM_SECRET_CODE)
async def activate_premium_command(message: Message):
    if is_premium_user(message.from_user.id):
        await message.answer(
            "👑 **Премиум уже активирован!**\n\n"
            "Вы уже используете премиум функции.",
            reply_markup=create_main_keyboard(message.from_user.id),
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    if activate_premium(message.from_user.id):
        success_text = f"""
🎉 **Премиум активирован!**
━━━━━━━━━━━━━━━━━━

👑 Поздравляю, {message.from_user.first_name}!

🚀 **Ваши новые возможности:**
• 📹 4K качество видео
• ⚡ Ультра-быстрый прогресс-бар
• 🎬 Поддержка плейлистов
• 📝 Автоматические субтитры
• 🚫 Приоритетная обработка
• 📊 Расширенная статистика

🎮 **Новые кнопки появились!**
Проверьте главную меню.

💎 **Наслаждайтесь премиум функциями!**
        """.strip()
        
        await message.answer(success_text, reply_markup=create_main_keyboard(message.from_user.id), parse_mode=ParseMode.MARKDOWN)
        
        # Уведомление админа о новой активации
        if is_admin(ADMIN_ID):
            await bot.send_message(
                ADMIN_ID,
                f"👑 **Новый премиум пользователь!**\n\n"
                f"👤 {message.from_user.full_name}\n"
                f"🆔 ID: `{message.from_user.id}`\n"
                f"🏷️ @{message.from_user.username if message.from_user.username else 'Нет'}",
                parse_mode=ParseMode.MARKDOWN
            )
    else:
        await message.answer(
            "❌ **Ошибка активации**\n\n"
            "Попробуйте снова или обратитесь к администратору.",
            reply_markup=create_main_keyboard(message.from_user.id),
            parse_mode=ParseMode.MARKDOWN
        )

# Админ панель
@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ У вас нет доступа к этой команде", reply_markup=create_main_keyboard())
        return
    
    await message.answer("🔐 **Админ панель**", reply_markup=create_admin_keyboard(), parse_mode=ParseMode.MARKDOWN)

# Обработка навигационных кнопок
@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery):
    await callback.message.edit_text(
        "🏠 **Главное меню**\n\n"
        "Выберите действие из кнопок ниже или отправьте ссылку на TikTok видео",
        reply_markup=create_navigation_keyboard(back_button=False),
        parse_mode=ParseMode.MARKDOWN
    )
    await callback.answer()

@dp.callback_query(F.data == "admin_back")
async def admin_back(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Доступ запрещен", show_alert=True)
        return
    
    await callback.message.edit_text(
        "🔐 **Админ панель**", 
        reply_markup=create_admin_keyboard(), 
        parse_mode=ParseMode.MARKDOWN
    )
    await callback.answer()

# Обработка текстовых кнопок
@dp.message(F.text == "📥 Скачать видео")
async def button_download(message: Message):
    await message.answer(
        "� **Скачивание видео**\n\n"
        "Отправьте ссылку на TikTok видео в любом формате:\n\n"
        "• `tiktok.com/@username/video/1234567890`\n"
        "• `vt.tiktok.com/...`\n"
        "• `vm.tiktok.com/...`\n"
        "• `tiktok.com/t/...`\n\n"
        "🔗 Просто отправьте ссылку и я начну скачивание!",
        reply_markup=create_navigation_keyboard(),
        parse_mode=ParseMode.MARKDOWN
    )

@dp.message(F.text == "📊 Статистика")
async def button_stats(message: Message):
    if is_admin(message.from_user.id):
        uptime = datetime.now() - bot_stats['start_time']
        success_rate = (bot_stats['successful_downloads'] / bot_stats['total_downloads'] * 100) if bot_stats['total_downloads'] > 0 else 0
        
        stats_text = f"""
� **Статистика бота**
━━━━━━━━━━━━━━━━━━
🔥 Всего скачиваний: {bot_stats['total_downloads']}
✅ Успешных: {bot_stats['successful_downloads']}
❌ Неудачных: {bot_stats['failed_downloads']}
📈 Успешность: {success_rate:.1f}%
👥 Пользователей: {bot_stats['users_count']}
⏰ Время работы: {str(uptime).split('.')[0]}
🕐 Последнее скачивание: {bot_stats['last_download'].strftime('%H:%M %d.%m.%Y') if bot_stats['last_download'] else 'Нет'}
        """.strip()
        
        await message.answer(stats_text, reply_markup=create_navigation_keyboard(admin_panel=True), parse_mode=ParseMode.MARKDOWN)
    else:
        # Общая статистика для пользователей
        user_stats = f"""
📊 **Статистика бота**
━━━━━━━━━━━━━━━━━━
🔥 Всего скачиваний: {bot_stats['total_downloads']}
✅ Успешных: {bot_stats['successful_downloads']}
👥 Активных пользователей: {bot_stats['users_count']}
⏰ Бот работает: {(datetime.now() - bot_stats['start_time']).days} дней
        """.strip()
        
        await message.answer(user_stats, reply_markup=create_navigation_keyboard(), parse_mode=ParseMode.MARKDOWN)

@dp.message(F.text == "ℹ️ Помощь")
async def button_help(message: Message):
    help_text = """
ℹ️ **Помощь - TikTok Downloader Bot**
━━━━━━━━━━━━━━━━━━

🔗 **Как использовать:**
1. Нажмите "� Скачать видео"
2. Отправьте ссылку на TikTok
3. Дождитесь скачивания

📋 **Поддерживаемые форматы:**
• `tiktok.com/@username/video/1234567890`
• `vt.tiktok.com/...`
• `vm.tiktok.com/...`
• `tiktok.com/t/...`

⚠️ **Важно:**
• Работает только с публичными видео
• Макс. качество: 1080p
• Макс. размер: 50MB

🚀 **Быстрый старт:**
Просто отправьте любую TikTok ссылку!

📞 **Поддержка:**
Если возникли проблемы - попробуйте другую ссылку.
    """.strip()
    
    await message.answer(help_text, reply_markup=create_navigation_keyboard(), parse_mode=ParseMode.MARKDOWN)

@dp.message(F.text == "🆔 Мой ID")
async def button_id(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username or "Нет"
    
    id_text = f"""
🆔 **Ваша информация**
━━━━━━━━━━━━━━━━━━
👤 ID: `{user_id}`
🏷️ Username: @{username}
📝 Имя: {message.from_user.full_name}
    """.strip()
    
    await message.answer(id_text, reply_markup=create_navigation_keyboard(), parse_mode=ParseMode.MARKDOWN)

# Обработка админ колбэков
@dp.callback_query(F.data.startswith("admin_"))
async def admin_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Доступ запрещен", show_alert=True)
        return
    
    action = callback.data.split("_")[1]
    
    if action == "stats":
        uptime = datetime.now() - bot_stats['start_time']
        success_rate = (bot_stats['successful_downloads'] / bot_stats['total_downloads'] * 100) if bot_stats['total_downloads'] > 0 else 0
        
        stats_text = f"""
📊 **Статистика бота**
━━━━━━━━━━━━━━━━━━
🔥 Всего скачиваний: {bot_stats['total_downloads']}
✅ Успешных: {bot_stats['successful_downloads']}
❌ Неудачных: {bot_stats['failed_downloads']}
📈 Успешность: {success_rate:.1f}%
👥 Пользователей: {bot_stats['users_count']}
⏰ Время работы: {str(uptime).split('.')[0]}
🕐 Последнее скачивание: {bot_stats['last_download'].strftime('%H:%M %d.%m.%Y') if bot_stats['last_download'] else 'Нет'}
        """
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_stats")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back")]
        ])
        
        await callback.message.edit_text(stats_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
    
    elif action == "users":
        users_list = bot_stats.get('users', set())
        user_activity = bot_stats.get('user_activity', {})
        
        # Создаем детальный список пользователей
        users_text = f"""
👥 **Пользователи бота**
━━━━━━━━━━━━━━━━━━
📊 Всего уникальных пользователей: {len(users_list)}
🚫 Заблокировано: {len(blocked_users)}
📈 Активных за последние 24ч: {len([u for u in user_activity.values() if (datetime.now() - u['last_visit']).days < 1])}
        """
        
        # Добавляем топ-10 самых активных
        if user_activity:
            users_text += "\n🔥 **Топ-10 самых активных:**\n"
            sorted_users = sorted(user_activity.items(), key=lambda x: x[1]['visits'], reverse=True)[:10]
            for i, (user_id, activity) in enumerate(sorted_users, 1):
                last_visit = activity['last_visit'].strftime("%d.%m %H:%M")
                users_text += f"{i}. ID: `{user_id}` - {activity['visits']} визитов (посл. {last_visit})\n"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_users")],
            [InlineKeyboardButton(text="📊 Активность", callback_data="admin_activity")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back")]
        ])
        
        try:
            await callback.message.edit_text(users_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            print(f"Failed to edit admin users message: {e}")
            # Если сообщение не изменилось, просто отвечаем
            pass
    
    elif action == "block":
        await callback.message.edit_text(
            "🚫 **Заблокировать пользователя**\n\n"
            "Отправьте ID пользователя которого нужно заблокировать\n"
            "Или переслайте сообщение от этого пользователя",
            parse_mode=ParseMode.MARKDOWN
        )
        # Устанавливаем состояние ожидания ID
        dp.message.register(block_user_handler, F.text)
    
    elif action == "unblock":
        if not blocked_users:
            await callback.message.edit_text("✅ Список заблокированных пользователей пуст", parse_mode=ParseMode.MARKDOWN)
            return
        
        users_text = "🚫 **Заблокированные пользователи**\n\n"
        for user_id in blocked_users:
            users_text += f"🔹 `{user_id}`\n"
        
        users_text += "\nОтправьте ID пользователя для разблокировки"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back")]
        ])
        
        await callback.message.edit_text(users_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
        # Устанавливаем состояние ожидания ID
        dp.message.register(unblock_user_handler, F.text)
    
    elif action == "broadcast":
        await callback.message.edit_text(
            "📢 **Рассылка сообщений**\n\n"
            "Отправьте текст сообщения для рассылки всем пользователям",
            parse_mode=ParseMode.MARKDOWN
        )
        # Устанавливаем состояние ожидания сообщения
        dp.message.register(broadcast_handler, F.text)
    
    elif action == "restart":
        await callback.message.edit_text("🔄 Перезагрузка бота...")
        # Перезапуск через исключение
        os._exit(0)
    
    elif action == "activity":
        user_activity = bot_stats.get('user_activity', {})
        
        if not user_activity:
            activity_text = "📊 **Активность пользователей**\n\nПока нет данных об активности пользователей."
        else:
            activity_text = "📊 **Детальная активность пользователей**\n\n"
            
            # Сортируем по последнему визиту
            sorted_activity = sorted(user_activity.items(), key=lambda x: x[1]['last_visit'], reverse=True)
            
            for user_id, activity in sorted_activity[:20]:  # Показываем последние 20
                first_visit = activity['first_visit'].strftime("%d.%m.%Y %H:%M")
                last_visit = activity['last_visit'].strftime("%d.%m.%Y %H:%M")
                visits = activity['visits']
                
                activity_text += f"👤 ID: `{user_id}`\n"
                activity_text += f"📅 Первый визит: {first_visit}\n"
                activity_text += f"🕐 Последний визит: {last_visit}\n"
                activity_text += f"🔢 Всего визитов: {visits}\n"
                activity_text += "━━━━━━━━━━━━━━━━━━\n"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_activity")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back")]
        ])
        
        try:
            await callback.message.edit_text(activity_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            print(f"Failed to edit activity message: {e}")
            pass
    
    elif action == "back":
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users")],
            [InlineKeyboardButton(text="� Активность", callback_data="admin_activity")],
            [InlineKeyboardButton(text="�� Заблокировать", callback_data="admin_block")],
            [InlineKeyboardButton(text="✅ Разблокировать", callback_data="admin_unblock")],
            [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
            [InlineKeyboardButton(text="⚙️ Настройки", callback_data="admin_settings")],
            [InlineKeyboardButton(text="🔄 Перезагрузить бота", callback_data="admin_restart")]
        ])
        await callback.message.edit_text("🔐 **Админ панель**", reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
    
    await callback.answer()

# Обработчики для админ функций
async def block_user_handler(message: Message):
    if not is_admin(message.from_user.id):
        return
    
    try:
        user_id = int(message.text)
        blocked_users.add(user_id)
        await message.answer(f"✅ Пользователь `{user_id}` заблокирован", parse_mode=ParseMode.MARKDOWN)
        dp.message.unregister(block_user_handler)
    except ValueError:
        await message.answer("❌ Неверный формат ID пользователя. Введите числовой ID.")

async def unblock_user_handler(message: Message):
    if not is_admin(message.from_user.id):
        return
    
    try:
        user_id = int(message.text)
        if user_id in blocked_users:
            blocked_users.remove(user_id)
            await message.answer(f"✅ Пользователь `{user_id}` разблокирован", parse_mode=ParseMode.MARKDOWN)
        else:
            await message.answer(f"❌ Пользователь `{user_id}` не найден в списке заблокированных", parse_mode=ParseMode.MARKDOWN)
        dp.message.unregister(unblock_user_handler)
    except ValueError:
        await message.answer("❌ Неверный формат ID пользователя. Введите числовой ID.")

async def broadcast_handler(message: Message):
    if not is_admin(message.from_user.id):
        return
    
    text = message.text
    users = bot_stats.get('users', set())
    success_count = 0
    
    for user_id in users:
        if user_id not in blocked_users:
            try:
                await bot.send_message(user_id, f"📢 **Сообщение от администратора:**\n\n{text}", parse_mode=ParseMode.MARKDOWN)
                success_count += 1
                await asyncio.sleep(0.1)  # Задержка чтобы не превысить лимиты
            except Exception as e:
                print(f"Failed to send broadcast to {user_id}: {e}")
    
    await message.answer(f"✅ Рассылка завершена\n\n📊 Отправлено: {success_count}/{len(users)} пользователей", parse_mode=ParseMode.MARKDOWN)
    dp.message.unregister(broadcast_handler)

@dp.message(Command("start"))
async def cmd_start(message: Message):
    try:
        # Отслеживаем активность пользователя
        bot_stats['users'].add(message.from_user.id)
        if message.from_user.id not in bot_stats['user_activity']:
            bot_stats['user_activity'][message.from_user.id] = {
                'first_visit': datetime.now(),
                'last_visit': datetime.now(),
                'visits': 0
            }
        
        bot_stats['user_activity'][message.from_user.id]['last_visit'] = datetime.now()
        bot_stats['user_activity'][message.from_user.id]['visits'] += 1
        
        is_premium = is_premium_user(message.from_user.id)
        is_admin_user = is_admin(message.from_user.id)
        
        if LANGUAGE == 'ru':
            welcome_text = f"""
{get_text('welcome')} {'👑' if is_premium else ''} {'🔧' if is_admin_user else ''}
━━━━━━━━━━━━━━━━━━

👋 Привет, {message.from_user.first_name}!

{get_text('start_help')}

{get_text('features')}
• 📥 Скачивать видео и фото
• 🖼️ Сохранять изображения и фото
• ⚡ Быстрая обработка ссылок
• 📊 Красивый прогресс-бар
{'• 👑 Премиум: 4K качество, субтитры и быстрые обновления' if is_premium else ''}
{'• 🔧 Админ: 4K качество, субтитры, быстрые обновления и админ-панель' if is_admin_user else ''}

🔗 **Просто отправьте ссылку:**
`tiktok.com/@username/video/1234567890`
`vt.tiktok.com/...`
`vm.tiktok.com/...`

{'👑 **Премиум статус активирован!**' if is_premium else ''}
{'🔧 **Админ права активированы!**' if is_admin_user else ''}
{'🔓 **Хотите больше функций? Отправьте `PREMIUM2024`**' if not is_premium and not is_admin_user else ''}

{get_text('channel')}

🎮 **Используйте кнопки ниже для навигации!**
            """.strip()
        else:
            welcome_text = f"""
{get_text('welcome')} {'👑' if is_premium else ''} {'🔧' if is_admin_user else ''}
━━━━━━━━━━━━━━━━━━

👋 Hello, {message.from_user.first_name}!

{get_text('start_help')}

{get_text('features')}
• 📥 Download videos and photos up to 50MB
• 🖼️ Save images and photos
• ⚡ Fast link processing
• 📊 Beautiful progress bar
{'• 👑 Premium: 4K quality, subtitles and fast updates' if is_premium else ''}
{'• 🔧 Admin: 4K quality, subtitles, fast updates and admin panel' if is_admin_user else ''}

🔗 **Just send a link:**
`tiktok.com/@username/video/1234567890`
`vt.tiktok.com/...`
`vm.tiktok.com/...`

{'👑 **Premium status activated!**' if is_premium else ''}
{'🔧 **Admin rights activated!**' if is_admin_user else ''}
{'🔓 **Want more features? Send `PREMIUM2024`**' if not is_premium and not is_admin_user else ''}

{get_text('channel')}

🎮 **Use the buttons below for navigation!**
            """.strip()
        
        await message.answer(welcome_text, reply_markup=create_main_keyboard(message.from_user.id), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        print(f"Error in cmd_start: {e}")
        # Отправляем упрощенное сообщение без форматирования
        simple_text = f"Привет, {message.from_user.first_name}! Отправьте ссылку на TikTok видео для скачивания."
        await message.answer(simple_text, reply_markup=create_main_keyboard(message.from_user.id))

@dp.message(Command("help"))
async def cmd_help(message: Message):
    try:
        help_text = """
ℹ️ **Подощь - TikTok Downloader Bot**
━━━━━━━━━━━━━━━━━━

🔗 **Как использовать:**
1. Нажмите "📥 Скачать видео" или отправьте ссылку
2. Отправьте ссылку на TikTok видео
3. Наблюдайте за прогресс-баром
4. Получите готовое видео!

📋 **Поддерживаемые форматы:**
• `tiktok.com/@username/video/1234567890`
• `vt.tiktok.com/...` (короткие ссылки)
• `vm.tiktok.com/...` (мобильные ссылки)
• `tiktok.com/t/...` (новые ссылки)

⚡ **Возможности:**
• 🎬 Видео до 1080p качества
• 🖼️ Изображения и фото
• 📊 Прогресс скачивания в реальном времени
• 🚀 Автоматическое определение типа контента
• 📱 Работает на всех устройствах

⚠️ **Ограничения:**
• Только публичные видео
• Макс. размер файла: 50MB
• Автоматическое удаление временных файлов

🐛 **Проблемы?**
Если видео не скачивается:
• Проверьте правильность ссылки
• Убедитесь что видео доступно
• Попробуйте другую ссылку

💡 **Совет:**
Используйте кнопки для быстрой навигации!
        """.strip()
        
        await message.answer(help_text, reply_markup=create_main_keyboard(), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        print(f"Error in cmd_help: {e}")
        simple_help = "Отправьте ссылку на TikTok видео для скачивания. Поддерживаются все форматы ссылок."
        await message.answer(simple_help, reply_markup=create_main_keyboard(message.from_user.id))

@dp.message(Command("id"))
async def cmd_id(message: Message):
    try:
        user_id = message.from_user.id
        username = message.from_user.username or "Нет"
        
        id_text = f"""
🆔 **Ваша информация**
━━━━━━━━━━━━━━━━━━
👤 ID: `{user_id}`
🏷️ Username: @{username}
📝 Имя: {message.from_user.full_name}
        """.strip()
        
        await message.answer(id_text, reply_markup=create_main_keyboard(), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        print(f"Error in cmd_id: {e}")
        simple_id = f"Ваш ID: {message.from_user.id}"
        await message.answer(simple_id, reply_markup=create_main_keyboard(message.from_user.id))

@dp.message(F.text)
async def handle_text_message(message: Message):
    # Проверяем не заблокирован ли пользователь
    if is_user_blocked(message.from_user.id):
        return
    
    text = message.text
    
    # Проверяем секретный код премиум
    if text == PREMIUM_SECRET_CODE:
        await activate_premium_command(message)
        return
    
    tiktok_match = TIKTOK_REGEX.search(text)
    
    if not tiktok_match:
        # Если это не ссылка, показываем подсказку
        if not any(button.text in text for button in create_main_keyboard(message.from_user.id).keyboard[0] + 
                  create_main_keyboard(message.from_user.id).keyboard[1] +
                  (create_main_keyboard(message.from_user.id).keyboard[2] if is_premium_user(message.from_user.id) else [])):
            await message.answer(
                "🔍 **Не найдено TikTok ссылки**\n\n"
                "Отправьте ссылку на TikTok видео или используйте кнопки ниже:",
                reply_markup=create_main_keyboard(message.from_user.id),
                parse_mode=ParseMode.MARKDOWN
            )
        return
    
    url = tiktok_match.group(0)
    if not url.startswith('http'):
        url = 'https://' + url
    
    print(f"Найдена TikTok ссылка: {url}")
    
    # Создаем начальное сообщение с прогрессом
    is_premium = is_premium_user(message.from_user.id)
    progress_text = f"""
📥 **Скачивание видео** {'👑' if is_premium else ''}
━━━━━━━━━━━━━━━━━━
░░░░░░░░░░░░░░░░░░░░ 0%
📊 0.0%
⚡ Скорость: Вычисление...
📦 Скачано: 0.0 MB
📏 Всего: 0.0 MB
🔍 Поиск видео{' 👑' if is_premium else ''}...
    """.strip()
    
    processing_msg = await message.answer(progress_text, parse_mode=ParseMode.MARKDOWN)
    
    try:
        # Создаем экземпляр TikTokDownloader с премиум настройками
        downloader = TikTokDownloader(message.from_user.id)
        
        # Запускаем скачивание с прогресс-баром
        file_path = await downloader.download_tiktok(url, message.from_user.id, processing_msg)
        
        if file_path and os.path.exists(file_path):
            file_size = os.path.getsize(file_path)
            
            # Проверяем лимит размера (только лимит Telegram для всех)
            telegram_limit = 50 * 1024 * 1024  # Telegram лимит ~50MB для всех
            
            if file_size > telegram_limit:
                size_mb = file_size / 1024 / 1024
                
                error_text = f"""
❌ **Ошибка размера файла**

Видео слишком большое для Telegram ({size_mb:.1f}МБ > 50МБ)

Лимит Telegram: ~50МБ для всех пользователей

Попробуйте другую ссылку или скачайте видео напрямую через браузер.
                """.strip()
                
                await processing_msg.edit_text(error_text, parse_mode=ParseMode.MARKDOWN)
                if os.path.exists(file_path):
                    os.remove(file_path)
                update_stats(False, message.from_user.id)
                return
            
            # Показываем финальный прогресс
            is_premium_user = is_premium_user(message.from_user.id)
            is_admin_user = is_admin(message.from_user.id)
            
            final_progress = f"""
📥 **Скачивание видео** {'👑' if is_premium_user else ''} {'🔧' if is_admin_user else ''}
━━━━━━━━━━━━━━━━━━
████████████████████ 100%
📊 100.0%
⚡ Скорость: Готово!
📦 Скачано: {file_size/1024/1024:.1f} MB
📏 Всего: {file_size/1024/1024:.1f} MB
✅ Скачивание завершено{' 👑' if is_premium_user else ''}{' 🔧' if is_admin_user else ''}!
            """.strip()
            
            await processing_msg.edit_text(final_progress, parse_mode=ParseMode.MARKDOWN)
            
            await asyncio.sleep(1)  # Небольшая пауза
            
            await processing_msg.edit_text("📤 **Загрузка в Telegram...**", parse_mode=ParseMode.MARKDOWN)
            
            file_extension = Path(file_path).suffix.lower()
            
            if file_extension in ['.jpg', '.jpeg', '.png', '.webp']:
                await bot.send_photo(
                    chat_id=message.chat.id,
                    photo=types.FSInputFile(file_path),
                    caption=f"🖼️ **Вот ваше изображение из TikTok!**{' 👑' if is_premium_user else ''}{' 🔧' if is_admin_user else ''}\n\n"
                           f"🎉 Скачано успешно! Используйте кнопки для навигации.",
                    reply_markup=create_main_keyboard(message.from_user.id),
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await bot.send_video(
                    chat_id=message.chat.id,
                    video=types.FSInputFile(file_path),
                    caption=f"🎬 **Вот ваше видео из TikTok!**{' 👑' if is_premium_user else ''}{' 🔧' if is_admin_user else ''}\n\n"
                           f"🎉 Скачано успешно! Используйте кнопки для навигации.",
                    reply_markup=create_main_keyboard(message.from_user.id),
                    parse_mode=ParseMode.MARKDOWN
                )
            
            await processing_msg.delete()
            
            if os.path.exists(file_path):
                os.remove(file_path)
            
            # Обновляем статистику
            update_stats(True, message.from_user.id)
        else:
            await processing_msg.edit_text(
                "❌ **Ошибка скачивания**\n\n"
                "Не удалось скачать видео.\n\n"
                "**Возможные причины:**\n"
                "• Видео удалено или недоступно\n"
                "• Проблемы с доступом к TikTok\n"
                "• Неправильная ссылка\n\n"
                "🔄 Попробуйте другую ссылку.",
                parse_mode=ParseMode.MARKDOWN
            )
            
            # Обновляем статистику
            update_stats(False, message.from_user.id)
    
    except Exception as e:
        print(f"Error in handle_text_message: {e}")
        await processing_msg.edit_text(
            "❌ **Критическая ошибка**\n\n"
            "Произошла ошибка при скачивании видео.\n\n"
            "🔄 Попробуйте еще раз или другую ссылку.",
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Обновляем статистику
        update_stats(False, message.from_user.id)

@dp.message()
async def handle_other_messages(message: Message):
    if message.content_type == ContentType.TEXT:
        return
    
    await message.reply(
        "🤔 **Я понимаю только текстовые сообщения**\n\n"
        "Отправьте ссылку на TikTok видео или используйте кнопки ниже:",
        reply_markup=create_main_keyboard(),
        parse_mode=ParseMode.MARKDOWN
    )

async def main():
    print("🚀 TikTok Bot запускается...")
    
    # Оптимизация для GitHub Actions
    if GITHUB_ACTIONS_MODE:
        print("🔄 Режим GitHub Actions активирован")
        print(f"⏰ Максимальное время работы: {MAX_RUNTIME} секунд")
        
        # Запускаем таймер для автоматического завершения
        asyncio.create_task(github_actions_timer())
    
    await dp.start_polling(bot)

async def github_actions_timer():
    """Таймер для автоматического завершения в GitHub Actions с логированием"""
    start_time = time.time()
    
    while time.time() - start_time < MAX_RUNTIME:
        elapsed = int(time.time() - start_time)
        hours = elapsed // 3600
        minutes = (elapsed % 3600) // 60
        seconds = elapsed % 60
        
        print(f"🤖 Бот работает: {hours:02d}:{minutes:02d}:{seconds:02d} | Осталось: {MAX_RUNTIME - elapsed}с")
        await asyncio.sleep(60)  # Логируем каждую минуту
    
    print("⏰ Время работы истекло (6 часов), завершаем...")
    os._exit(0)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("👋 Бот остановлен")
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        os._exit(1)
