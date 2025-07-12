import logging
import os
import re
import tempfile
import asyncio
from pathlib import Path
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import yt_dlp

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация
BOT_TOKEN = os.getenv("BOT_TOKEN")
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
MAX_DURATION = 30 * 60  # 30 минут

def is_youtube_url(url):
    """Проверяет, является ли URL ссылкой на YouTube"""
    youtube_regex = re.compile(
        r'(https?://)?(www\.)?(youtube|youtu|youtube-nocookie)\.(com|be)/'
        r'(watch\?v=|embed/|v/|.+\?v=)?([^&=%\?]{11})'
    )
    return youtube_regex.match(url) is not None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    welcome_text = """
🎵 **YouTube Audio Bot**

Я помогу скачать аудио с YouTube для транскрипции!

**Как использовать:**
1. Отправьте мне ссылку на YouTube видео
2. Дождитесь скачивания аудио
3. Перешлите полученный файл основному боту для транскрипции

**Ограничения:**
• Максимум 30 минут
• Максимум 50MB
• Только публичные видео

Отправьте /help для получения помощи.
    """
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /help"""
    help_text = """
📋 **Помощь**

**Команды:**
• /start - Начать работу
• /help - Показать это сообщение
• /stats - Статистика бота

**Поддерживаемые форматы YouTube:**
• youtube.com/watch?v=...
• youtu.be/...
• youtube.com/embed/...

**Процесс работы:**
1. Отправьте YouTube ссылку
2. Бот скачает аудио в формате MP3
3. Перешлите файл основному боту

**Проблемы?**
• Проверьте, что видео публичное
• Убедитесь, что длительность < 30 минут
• Попробуйте другое видео, если не работает
    """
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /stats"""
    stats_text = f"""
📊 **Статистика бота**

🤖 Бот работает нормально
🎵 Поддерживаемые форматы: MP3, M4A
📏 Максимальный размер: {MAX_FILE_SIZE // (1024*1024)}MB
⏱️ Максимальная длительность: {MAX_DURATION // 60} минут
    """
    await update.message.reply_text(stats_text, parse_mode='Markdown')

def get_ydl_opts(temp_dir):
    """Настройки для yt-dlp"""
    return {
        'format': 'bestaudio/best',
        'outtmpl': f'{temp_dir}/%(title)s.%(ext)s',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'quiet': True,
        'no_warnings': True,
        'max_filesize': MAX_FILE_SIZE,
        'socket_timeout': 30,
        # Настройки для обхода блокировок
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
            'Accept-Encoding': 'gzip,deflate',
            'Connection': 'keep-alive',
        },
        'extractor_args': {
            'youtube': {
                'skip': ['hls', 'dash'],
                'player_client': ['android', 'web']
            }
        }
    }

def download_audio_sync(url, temp_dir):
    """Синхронная функция для скачивания аудио"""
    ydl_opts = get_ydl_opts(temp_dir)
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        # Сначала получаем информацию
        info = ydl.extract_info(url, download=False)
        
        title = info.get('title', 'Unknown')
        duration = info.get('duration', 0)
        uploader = info.get('uploader', 'Unknown')
        
        # Проверяем длительность
        if duration > MAX_DURATION:
            raise Exception(f"Видео слишком длинное ({duration//60} мин). Максимум: {MAX_DURATION//60} мин")
        
        # Скачиваем
        ydl.download([url])
        
        # Находим скачанный файл
        audio_files = []
        for file in os.listdir(temp_dir):
            if file.endswith(('.mp3', '.m4a', '.webm', '.wav')):
                audio_files.append(file)
        
        if not audio_files:
            raise Exception("Аудио файл не найден после скачивания")
        
        audio_file = os.path.join(temp_dir, audio_files[0])
        
        return {
            'file_path': audio_file,
            'title': title,
            'duration': duration,
            'uploader': uploader,
            'file_size': os.path.getsize(audio_file)
        }

async def download_youtube_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Основная функция скачивания аудио"""
    url = update.message.text.strip()
    
    if not is_youtube_url(url):
        await update.message.reply_text(
            "❌ Это не ссылка на YouTube.\n\n"
            "Отправьте корректную ссылку, например:\n"
            "• https://youtube.com/watch?v=...\n"
            "• https://youtu.be/..."
        )
        return
    
    # Уведомляем о начале скачивания
    status_message = await update.message.reply_text("⏳ Анализирую видео...")
    
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            # Обновляем статус
            await status_message.edit_text("📡 Скачиваю аудио... Это может занять время.")
            
            # Выполняем скачивание в отдельном потоке
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, download_audio_sync, url, temp_dir
            )
            
            # Проверяем размер файла
            if result['file_size'] > MAX_FILE_SIZE:
                await status_message.edit_text(
                    f"❌ Файл слишком большой ({result['file_size']//1024//1024}MB)\n"
                    f"Максимум: {MAX_FILE_SIZE//1024//1024}MB"
                )
                return
            
            await status_message.edit_text("📤 Отправляю файл...")
            
            # Формируем описание
            duration_str = f"{result['duration']//60}:{result['duration']%60:02d}"
            size_str = f"{result['file_size']//1024} KB"
            
            caption = f"""
🎵 **Аудио готово!**

📄 {result['title'][:80]}{'...' if len(result['title']) > 80 else ''}
👤 {result['uploader']}
⏱️ Длительность: {duration_str}
📏 Размер: {size_str}

➡️ **Теперь перешлите этот файл основному боту для транскрипции**
            """
            
            # Отправляем файл
            with open(result['file_path'], 'rb') as audio_file:
                await update.message.reply_document(
                    document=audio_file,
                    filename=f"{result['title'][:50]}.mp3",
                    caption=caption,
                    parse_mode='Markdown'
                )
            
            await status_message.delete()
            logger.info(f"Successfully processed: {result['title']} ({size_str})")
            
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error processing {url}: {error_msg}")
        
        # Определяем тип ошибки и даем соответствующий ответ
        if "Sign in to confirm" in error_msg or "bot" in error_msg.lower():
            await status_message.edit_text(
                "❌ **YouTube заблокировал доступ**\n\n"
                "Это происходит из-за защиты от ботов.\n"
                "Попробуйте:\n"
                "• Другое видео\n"
                "• Повторить через несколько минут\n"
                "• Более короткое видео"
            )
        elif "Private video" in error_msg:
            await status_message.edit_text(
                "❌ **Приватное видео**\n\n"
                "Можно скачивать только публичные видео."
            )
        elif "слишком длинное" in error_msg:
            await status_message.edit_text(
                f"❌ **Видео слишком длинное**\n\n"
                f"Максимальная длительность: {MAX_DURATION//60} минут"
            )
        elif "File too large" in error_msg:
            await status_message.edit_text(
                f"❌ **Файл слишком большой**\n\n"
                f"Максимальный размер: {MAX_FILE_SIZE//1024//1024}MB"
            )
        else:
            await status_message.edit_text(
                f"❌ **Ошибка скачивания**\n\n"
                f"Детали: {error_msg[:200]}\n\n"
                "Попробуйте другое видео или повторите позже."
            )

async def handle_other_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка других сообщений"""
    await update.message.reply_text(
        "🤔 Я понимаю только ссылки на YouTube.\n\n"
        "Отправьте ссылку на YouTube видео, например:\n"
        "https://youtube.com/watch?v=dQw4w9WgXcQ\n\n"
        "Используйте /help для получения помощи."
    )

def main():
    """Основная функция"""
    if not BOT_TOKEN:
        print("❌ Ошибка: Не установлен BOT_TOKEN!")
        print("\nКак исправить:")
        print("1. Найдите @BotFather в Telegram")
        print("2. Создайте нового бота: /newbot")
        print("3. Установите токен:")
        print("   export BOT_TOKEN='YOUR_TOKEN_HERE'")
        print("   # или в Windows:")
        print("   set BOT_TOKEN=YOUR_TOKEN_HERE")
        return
    
    print("🚀 Запускаю YouTube Audio Bot...")
    
    # Создаем приложение
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("stats", stats))
    
    # Обработчик YouTube ссылок
    application.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(r'(youtube\.com|youtu\.be)'), 
        download_youtube_audio
    ))
    
    # Обработчик остальных сообщений
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, 
        handle_other_messages
    ))
    
    # Запускаем бота
    print("✅ Бот запущен! Нажмите Ctrl+C для остановки")
    application.run_polling()

if __name__ == '__main__':
    main()
