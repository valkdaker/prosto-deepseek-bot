import os
import sys
import json
import re
import logging
import asyncio
import hashlib
import traceback
import subprocess
from datetime import datetime
from urllib.parse import urlparse, quote
import uuid
import time

# ==================== ИМПОРТ БИБЛИОТЕК ====================
try:
    from aiogram import Bot, Dispatcher, types, F, Router
    from aiogram.filters import Command
    from aiogram.types import Message, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
    from aiogram.enums import ParseMode
    from aiogram.client.default import DefaultBotProperties
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.state import State, StatesGroup
    from aiogram.fsm.storage.memory import MemoryStorage
    
    import yt_dlp
    import aiohttp
    from bs4 import BeautifulSoup
    
    logger = logging.getLogger(__name__)
    
except ImportError as e:
    print(f"❌ Ошибка импорта библиотек: {e}")
    print("Установите: pip install aiogram==3.7.0 yt-dlp aiohttp beautifulsoup4")
    sys.exit(1)

# ==================== КОНФИГУРАЦИЯ ====================
BOT_TOKEN = "8567153378:AAHG0WXZDgI4gorGfa4a28xBHMgzg1KRmlY"  # Ваш токен
DOWNLOAD_DIR = "downloads"
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB для хостинга
MAX_DURATION = 180  # 3 минуты максимум для стабильности

# Создаем папки
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Настройка FSM
storage = MemoryStorage()

# Создаем роутер
router = Router()

# ==================== ХРАНИЛИЩЕ ДЛЯ URL ====================
class URLStorage:
    """Хранилище URL для кнопок"""
    def __init__(self):
        self.urls = {}
    
    def add_url(self, url: str, audio: bool = False) -> str:
        """Добавляет URL и возвращает короткий хэш"""
        key = f"{url}:{audio}"
        url_hash = hashlib.md5(key.encode()).hexdigest()[:12]
        self.urls[url_hash] = (url, audio)
        return url_hash
    
    def get_url(self, url_hash: str):
        """Получает URL и тип по хэшу"""
        return self.urls.get(url_hash)

url_storage = URLStorage()

# ==================== ПРОВЕРКА И УСТАНОВКА FFMPEG ====================
def setup_ffmpeg():
    """Проверяет и устанавливает ffmpeg если нужно"""
    try:
        # Проверяем наличие в системе
        result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True)
        if result.returncode == 0:
            logger.info("✅ FFmpeg установлен в системе")
            return True
    except:
        pass
    
    # Проверяем в папке с ботом
    ffmpeg_path = os.path.join(os.path.dirname(__file__), 'ffmpeg')
    if os.path.exists(ffmpeg_path) or os.path.exists(ffmpeg_path + '.exe'):
        logger.info("✅ FFmpeg найден в папке бота")
        return True
    
    logger.warning("❌ FFmpeg не найден. Аудио будет недоступно.")
    return False

HAS_FFMPEG = setup_ffmpeg()

# ==================== УНИВЕРСАЛЬНОЕ СКАЧИВАНИЕ С YT-DLP ====================
async def smart_download(url: str, is_audio: bool = False) -> tuple:
    """
    Умное скачивание с оптимизациями для хостинга
    Возвращает (путь_к_файлу, ошибка_или_название)
    """
    try:
        # Определяем платформу
        if 'pinterest.com' in url.lower() or 'pin.it' in url.lower():
            platform = "Pinterest"
            use_audio = False  # Pinterest не поддерживает аудио отдельно
        else:
            platform = "YouTube"
            use_audio = is_audio and HAS_FFMPEG
        
        logger.info(f"Скачиваю {platform} {'аудио' if use_audio else 'видео'}: {url[:50]}...")
        
        # Генерируем уникальное имя файла
        file_id = str(uuid.uuid4())[:8]
        ext = "mp3" if use_audio else "mp4"
        temp_filename = os.path.join(DOWNLOAD_DIR, f"{file_id}.{ext}")
        
        # Настройки для yt-dlp
        ydl_opts = {
            'outtmpl': temp_filename.replace(f'.{ext}', '.%(ext)s'),
            'quiet': False,
            'no_warnings': False,
            'ignoreerrors': True,
            'socket_timeout': 30,
            'retries': 2,
            'fragment_retries': 2,
            'extract_flat': False,
            'noplaylist': True,
            'concurrent_fragment_downloads': 3,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': '*/*',
            },
            'match_filter': lambda info, *args: (
                None if info.get('duration', 9999) > MAX_DURATION 
                else f"Видео слишком длинное ({info.get('duration', 0)} сек)"
            ),
        }
        
        # Настройки формата
        if use_audio:
            if HAS_FFMPEG:
                ydl_opts.update({
                    'format': 'bestaudio/best',
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '128',
                    }],
                    'extractor_args': {'youtube': {'format': 'bestaudio'}},
                })
            else:
                return None, "FFmpeg не установлен. Аудио недоступно."
        else:
            # Для видео выбираем оптимальный баланс качества/размера
            ydl_opts.update({
                'format': 'best[height<=480][ext=mp4]/best[ext=mp4]/best',
                'merge_output_format': 'mp4',
                'postprocessor_args': ['-fs', str(MAX_FILE_SIZE)],
            })
        
        # Особые настройки для Pinterest
        if platform == "Pinterest":
            ydl_opts.update({
                'format': 'best',
                'extractor_args': {'pinterest': {'format': 'best'}},
            })
        
        # Скачиваем
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                # Получаем информацию о видео
                info = ydl.extract_info(url, download=False)
                
                if not info:
                    return None, "Не удалось получить информацию о видео"
                
                # Проверяем длительность
                duration = info.get('duration', 0)
                if duration > MAX_DURATION:
                    return None, f"Видео слишком длинное ({duration//60}:{duration%60:02d}). Максимум {MAX_DURATION//60} минут."
                
                # Получаем название
                title = info.get('title', platform)
                safe_title = re.sub(r'[<>:"/\\|?*]', '', title)[:50]
                
                # Скачиваем
                ydl.download([url])
                
                # Ищем скачанный файл
                actual_file = None
                for f in os.listdir(DOWNLOAD_DIR):
                    if f.startswith(file_id):
                        actual_file = os.path.join(DOWNLOAD_DIR, f)
                        break
                
                if actual_file and os.path.exists(actual_file):
                    # Переименовываем в удобное имя
                    final_ext = '.mp3' if use_audio else '.mp4'
                    final_filename = os.path.join(DOWNLOAD_DIR, f"{file_id}_{safe_title}{final_ext}")
                    
                    # Ограничиваем длину имени файла
                    if len(final_filename) > 255:
                        final_filename = os.path.join(DOWNLOAD_DIR, f"{file_id}{final_ext}")
                    
                    os.rename(actual_file, final_filename)
                    
                    # Проверяем размер
                    file_size = os.path.getsize(final_filename)
                    if file_size > MAX_FILE_SIZE:
                        os.remove(final_filename)
                        return None, f"Файл слишком большой ({file_size//(1024*1024)}MB)"
                    
                    logger.info(f"✅ Скачано: {final_filename} ({file_size//1024}KB)")
                    return final_filename, safe_title
                else:
                    return None, "Файл не найден после скачивания"
                    
            except yt_dlp.utils.DownloadError as e:
                error_msg = str(e).lower()
                if 'too long' in error_msg:
                    return None, f"Видео слишком длинное. Максимум {MAX_DURATION//60} минут."
                elif 'private' in error_msg or 'unavailable' in error_msg:
                    return None, "Видео приватное или недоступно."
                else:
                    return None, f"Ошибка скачивания: {str(e)[:100]}"
                    
    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)[:100]}"
        logger.error(f"Ошибка smart_download: {error_msg}\n{traceback.format_exc()}")
        return None, error_msg

# ==================== ОБРАБОТЧИКИ КОМАНД ====================
@router.message(Command("start"))
async def cmd_start(message: Message):
    """Команда /start"""
    try:
        audio_status = "✅ Доступно" if HAS_FFMPEG else "❌ Недоступно (нет FFmpeg)"
        
        text = f"""
🎬 <b>Video Downloader Bot</b>

<b>Поддерживаемые платформы:</b>
✅ Pinterest — видео
✅ YouTube — видео
{audio_status} YouTube — аудио (MP3)

<b>Оптимизировано для хостинга:</b>
• Макс. размер: {MAX_FILE_SIZE//(1024*1024)}MB
• Макс. длительность: {MAX_DURATION//60} мин
• Быстрая отправка
• Автоочистка файлов

<b>Как использовать:</b>
1. Отправьте ссылку
2. Для YouTube выберите формат
3. Получите файл за 10-30 секунд

<b>Рекомендуем:</b>
• YouTube Shorts (до 60 секунд)
• Pinterest Reels
• Короткие видео для быстрой загрузки

Отправьте ссылку 👇
        """
        
        await message.answer(text)
        
    except Exception as e:
        logger.error(f"Ошибка в /start: {e}")

@router.message(Command("help"))
async def cmd_help(message: Message):
    """Команда /help"""
    help_text = f"""
<b>Советы для быстрой загрузки на хостинге:</b>

1. <b>Короткие видео</b> (до {MAX_DURATION//60} мин) работают быстрее
2. <b>YouTube Shorts</b> — оптимальный вариант
3. <b>480p качество</b> — баланс качества и скорости

<b>Ограничения хостинга:</b>
• Размер файла: до {MAX_FILE_SIZE//(1024*1024)}MB
• Время обработки: до 60 секунд
• Память: ограничена

<b>Если не работает:</b>
• Попробуйте более короткое видео
• Проверьте ссылку
• Подождите 1 минуту и попробуйте снова

<b>Статус FFmpeg:</b> {'Установлен ✅' if HAS_FFMPEG else 'Не установлен ❌'}
    """
    await message.answer(help_text)

@router.message(Command("status"))
async def cmd_status(message: Message):
    """Статус бота"""
    import shutil
    import psutil
    
    try:
        # Информация о системе
        disk = shutil.disk_usage(".")
        memory = psutil.virtual_memory()
        
        status_text = f"""
<b>Статус системы:</b>

💾 <b>Диск:</b>
• Всего: {disk.total // (1024**3)} GB
• Использовано: {disk.used // (1024**3)} GB
• Свободно: {disk.free // (1024**3)} GB

🧠 <b>Память:</b>
• Всего: {memory.total // (1024**3)} GB
• Доступно: {memory.available // (1024**3)} GB
• Использовано: {memory.percent}%

⚙️ <b>Бот:</b>
• FFmpeg: {'✅' if HAS_FFMPEG else '❌'}
• Макс. размер: {MAX_FILE_SIZE//(1024*1024)}MB
• Макс. длительность: {MAX_DURATION//60} мин
• Файлов в папке: {len(os.listdir(DOWNLOAD_DIR))}

📊 <b>Процессор:</b>
• Ядер: {psutil.cpu_count()}
• Загрузка: {psutil.cpu_percent()}%
        """
        await message.answer(status_text)
    except Exception as e:
        await message.answer(f"❌ Не удалось получить статус: {str(e)}")

@router.message(F.text)
async def handle_message(message: Message):
    """Обработчик текстовых сообщений"""
    try:
        url = message.text.strip()
        
        # Проверка URL
        if not re.match(r'^https?://\S+', url):
            await message.answer("❌ <b>Отправьте корректную ссылку.</b>")
            return
        
        url_lower = url.lower()
        
        if 'pinterest.com' in url_lower or 'pin.it' in url_lower:
            # Pinterest - сразу скачиваем видео
            status_msg = await message.answer(
                "⏳ <b>Скачиваю Pinterest видео...</b>\n"
                "⏱️ Ожидайте 15-30 секунд\n"
                "📦 Макс. размер: 20MB"
            )
            
            filepath, error = await smart_download(url, False)
            await handle_download_result(message, status_msg, filepath, error, "Pinterest", False)
            
        elif 'youtube.com' in url_lower or 'youtu.be' in url_lower:
            # YouTube - предлагаем выбор
            video_hash = url_storage.add_url(url, False)
            
            keyboard_buttons = [
                [InlineKeyboardButton(text="🎬 Видео (480p MP4)", callback_data=f"dl:{video_hash}")]
            ]
            
            if HAS_FFMPEG:
                audio_hash = url_storage.add_url(url, True)
                keyboard_buttons[0].append(
                    InlineKeyboardButton(text="🎵 Аудио (MP3)", callback_data=f"dl:{audio_hash}")
                )
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
            
            await message.answer(
                "📺 <b>YouTube ссылка обнаружена!</b>\n\n"
                f"<b>Рекомендуем:</b>\n"
                f"• Видео до {MAX_DURATION//60} минут\n"
                f"• YouTube Shorts\n"
                f"• 480p для быстрой загрузки\n\n"
                f"<b>Выберите формат:</b>",
                reply_markup=keyboard
            )
            
        else:
            await message.answer(
                "❌ <b>Неподдерживаемая платформа.</b>\n\n"
                "<b>Поддерживается:</b>\n"
                "• Pinterest (видео)\n"
                "• YouTube (видео/аудио)\n\n"
                "<b>Примеры:</b>\n"
                "https://youtube.com/shorts/...\n"
                "https://pin.it/..."
            )
            
    except Exception as e:
        logger.error(f"Ошибка обработки сообщения: {e}\n{traceback.format_exc()}")
        await message.answer("❌ <b>Ошибка обработки.</b>\nПопробуйте другую ссылку.")

@router.callback_query(F.data.startswith("dl:"))
async def handle_download_callback(callback: types.CallbackQuery):
    """Обработчик кнопок скачивания"""
    try:
        await callback.answer()
        
        data_hash = callback.data.split(":", 1)[1]
        url_data = url_storage.get_url(data_hash)
        
        if not url_data:
            await callback.message.answer("❌ <b>Ссылка устарела.</b>\nОтправьте ссылку заново.")
            return
        
        url, is_audio = url_data
        
        # Статусное сообщение
        action = "аудио" if is_audio else "видео"
        status_msg = await callback.message.answer(
            f"⏳ <b>Скачиваю {action}...</b>\n"
            f"⏱️ Ожидайте 15-30 секунд\n"
            f"📦 Макс. размер: {MAX_FILE_SIZE//(1024*1024)}MB\n"
            f"🕐 Макс. время: {MAX_DURATION//60} мин"
        )
        
        # Скачиваем
        filepath, error = await smart_download(url, is_audio)
        
        # Обрабатываем результат
        await handle_download_result(
            callback.message, 
            status_msg, 
            filepath, 
            error, 
            "YouTube", 
            is_audio
        )
            
    except Exception as e:
        logger.error(f"Ошибка в callback: {e}\n{traceback.format_exc()}")
        try:
            await callback.message.answer("❌ <b>Произошла ошибка.</b>\nПопробуйте другую ссылку.")
        except:
            pass

async def handle_download_result(message: Message, status_msg: Message, filepath: str, 
                                error: str, platform: str, is_audio: bool):
    """Обрабатывает результат скачивания"""
    try:
        if filepath and os.path.exists(filepath):
            file_size = os.path.getsize(filepath)
            size_mb = file_size / (1024 * 1024)
            
            # Обновляем статус
            await status_msg.edit_text(f"✅ <b>Скачано! Отправляю файл...</b>\n📦 {size_mb:.1f}MB")
            
            try:
                # Отправляем файл
                if is_audio:
                    await message.answer_audio(
                        FSInputFile(filepath, filename=f"{platform}.mp3"),
                        caption=f"🎵 <b>{platform}</b>\n📦 {size_mb:.1f}MB"
                    )
                else:
                    await message.answer_video(
                        FSInputFile(filepath, filename=f"{platform}.mp4"),
                        caption=f"🎬 <b>{platform}</b>\n📦 {size_mb:.1f}MB",
                        supports_streaming=True
                    )
                
                await status_msg.delete()
                logger.info(f"✅ Файл отправлен: {os.path.basename(filepath)} ({size_mb:.1f}MB)")
                
            except Exception as send_error:
                logger.error(f"Ошибка отправки: {send_error}")
                await status_msg.edit_text(
                    f"❌ <b>Ошибка отправки файла.</b>\n"
                    f"Причина: {str(send_error)[:100]}\n\n"
                    f"Попробуйте:\n"
                    f"1. Более короткое видео\n"
                    f"2. Другую ссылку\n"
                    f"3. Подождать 1 минуту"
                )
            
            # Очищаем файл
            try:
                os.remove(filepath)
            except:
                pass
                
        else:
            error_msg = error or "Неизвестная ошибка"
            await status_msg.edit_text(
                f"❌ <b>Ошибка скачивания {platform}:</b>\n"
                f"{error_msg}\n\n"
                f"<b>Попробуйте:</b>\n"
                f"• Более короткое видео (до {MAX_DURATION//60} мин)\n"
                f"• Другую ссылку\n"
                f"• Проверить доступность видео"
            )
            
    except Exception as e:
        logger.error(f"Ошибка обработки результата: {e}")
        try:
            await status_msg.edit_text("❌ <b>Ошибка обработки.</b>")
        except:
            pass

# ==================== АВТООЧИСТКА ====================
async def auto_cleanup():
    """Автоматическая очистка старых файлов"""
    while True:
        try:
            now = time.time()
            for filename in os.listdir(DOWNLOAD_DIR):
                filepath = os.path.join(DOWNLOAD_DIR, filename)
                if os.path.isfile(filepath):
                    # Удаляем файлы старше 1 часа
                    if now - os.path.getmtime(filepath) > 3600:
                        try:
                            os.remove(filepath)
                            logger.debug(f"Удален старый файл: {filename}")
                        except:
                            pass
        except Exception as e:
            logger.error(f"Ошибка автоочистки: {e}")
        
        await asyncio.sleep(300)  # Проверяем каждые 5 минут

# ==================== ЗАПУСК БОТА ====================
async def main():
    """Основная функция"""
    # Настройка логирования
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('bot.log', encoding='utf-8'),
            logging.StreamHandler()
        ]
    )
    
    logger.info("=" * 60)
    logger.info(f"🚀 ЗАПУСК БОТА ДЛЯ ХОСТИНГА")
    logger.info(f"Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"Токен: {BOT_TOKEN[:10]}...")
    logger.info(f"FFmpeg: {'ДА' if HAS_FFMPEG else 'НЕТ'}")
    logger.info(f"Лимит размера: {MAX_FILE_SIZE//(1024*1024)}MB")
    logger.info(f"Лимит времени: {MAX_DURATION//60} мин")
    logger.info("=" * 60)
    
    # Создаем бота и диспетчер
    bot = Bot(
        token=BOT_TOKEN, 
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        timeout=60  # Увеличиваем таймаут для больших файлов
    )
    
    dp = Dispatcher(storage=storage)
    dp.include_router(router)
    
    try:
        # Запускаем автоочистку в фоне
        asyncio.create_task(auto_cleanup())
        
        # Очищаем старые файлы при запуске
        for filename in os.listdir(DOWNLOAD_DIR):
            try:
                os.remove(os.path.join(DOWNLOAD_DIR, filename))
            except:
                pass
        
        # Запускаем бота
        logger.info("🤖 Бот запущен и готов к работе!")
        logger.info("📱 Ожидание сообщений...")
        
        await dp.start_polling(
            bot, 
            allowed_updates=dp.resolve_used_update_types(),
            polling_timeout=30,
            close_bot_session=True
        )
        
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"💥 Критическая ошибка: {e}\n{traceback.format_exc()}")
    finally:
        try:
            await bot.session.close()
        except:
            pass
        logger.info("✅ Бот завершил работу")

# ==================== ТОЧКА ВХОДА ====================
if __name__ == "__main__":
    try:
        # Проверяем наличие всех библиотек
        import aiohttp
        from bs4 import BeautifulSoup
        import yt_dlp
        
        # Пробуем импортировать psutil для статуса (необязательно)
        try:
            import psutil
        except:
            logger.warning("psutil не установлен. Команда /status будет ограничена.")
        
        # Запускаем бота
        asyncio.run(main())
        
    except ImportError as e:
        print(f"\n❌ ОШИБКА: Не установлены библиотеки!")
        print(f"Отсутствует: {e.name}")
        print("\n📦 Установите все зависимости:")
        print("pip install aiogram==3.7.0 yt-dlp aiohttp beautifulsoup4")
        
        if input("\nУстановить автоматически? (y/n): ").lower() == 'y':
            import subprocess
            subprocess.run([sys.executable, "-m", "pip", "install", 
                          "aiogram==3.7.0", "yt-dlp", "aiohttp", "beautifulsoup4"])
            print("\n✅ Зависимости установлены. Перезапустите бота.")
        
        sys.exit(1)
