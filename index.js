const TelegramBot = require('node-telegram-bot-api');
const fs = require('fs-extra');
const path = require('path');
const ytdl = require('ytdl-core');
const axios = require('axios');
const { v4: uuidv4 } = require('uuid');
const ffmpeg = require('fluent-ffmpeg');

// ==================== КОНФИГУРАЦИЯ ====================
const BOT_TOKEN = process.env.BOT_TOKEN || '8567153378:AAEX5GRFscQPFj83pCnc5Y7MZjMM-x2Nk2o';
const DOWNLOAD_DIR = path.join(__dirname, 'downloads');
const MAX_FILE_SIZE = 50 * 1024 * 1024; // 50MB
const MAX_DURATION = 300; // 5 минут

// Создаем папку для загрузок
fs.ensureDirSync(DOWNLOAD_DIR);

// Создаем бота
const bot = new TelegramBot(BOT_TOKEN, { polling: true });

// ==================== УТИЛИТЫ ====================
function formatBytes(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

function formatTime(seconds) {
    const minutes = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${minutes}:${secs.toString().padStart(2, '0')}`;
}

// ==================== СКАЧИВАНИЕ YOUTUBE ====================
async function downloadYouTube(url, format = 'video') {
    try {
        console.log(`Скачиваю YouTube: ${url} (${format})`);
        
        // Получаем информацию о видео
        const info = await ytdl.getInfo(url);
        const videoDetails = info.videoDetails;
        
        // Проверяем длительность
        const duration = parseInt(videoDetails.lengthSeconds);
        if (duration > MAX_DURATION) {
            return { error: `Видео слишком длинное (${formatTime(duration)}). Максимум ${formatTime(MAX_DURATION)}` };
        }
        
        // Генерируем имя файла
        const fileId = uuidv4().slice(0, 8);
        const safeTitle = videoDetails.title.replace(/[<>:"/\\|?*]/g, '_').substring(0, 100);
        
        let filePath, fileSize;
        
        if (format === 'audio') {
            // Скачиваем аудио
            filePath = path.join(DOWNLOAD_DIR, `${fileId}_${safeTitle}.mp3`);
            
            return new Promise((resolve, reject) => {
                const stream = ytdl(url, { quality: 'highestaudio' });
                
                ffmpeg(stream)
                    .audioBitrate(128)
                    .save(filePath)
                    .on('end', () => {
                        fileSize = fs.statSync(filePath).size;
                        if (fileSize > MAX_FILE_SIZE) {
                            fs.unlinkSync(filePath);
                            resolve({ error: `Файл слишком большой (${formatBytes(fileSize)})` });
                        } else {
                            resolve({
                                path: filePath,
                                title: videoDetails.title,
                                size: fileSize,
                                duration: duration
                            });
                        }
                    })
                    .on('error', (err) => {
                        reject({ error: `Ошибка конвертации: ${err.message}` });
                    });
            });
            
        } else {
            // Скачиваем видео
            filePath = path.join(DOWNLOAD_DIR, `${fileId}_${safeTitle}.mp4`);
            
            const stream = ytdl(url, { quality: 'lowest' });
            const writeStream = fs.createWriteStream(filePath);
            
            return new Promise((resolve, reject) => {
                stream.pipe(writeStream);
                
                writeStream.on('finish', () => {
                    fileSize = fs.statSync(filePath).size;
                    if (fileSize > MAX_FILE_SIZE) {
                        fs.unlinkSync(filePath);
                        resolve({ error: `Файл слишком большой (${formatBytes(fileSize)})` });
                    } else {
                        resolve({
                            path: filePath,
                            title: videoDetails.title,
                            size: fileSize,
                            duration: duration
                        });
                    }
                });
                
                writeStream.on('error', (err) => {
                    reject({ error: `Ошибка записи: ${err.message}` });
                });
            });
        }
        
    } catch (error) {
        console.error('Ошибка скачивания YouTube:', error);
        return { error: `Ошибка: ${error.message}` };
    }
}

// ==================== СКАЧИВАНИЕ PINTEREST ====================
async function downloadPinterest(url) {
    try {
        console.log(`Скачиваю Pinterest: ${url}`);
        
        // Получаем HTML страницы
        const response = await axios.get(url, {
            headers: {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
        });
        
        const html = response.data;
        
        // Ищем видео URL (упрощенный парсинг)
        let videoUrl = null;
        
        // Ищем в JSON-LD
        const jsonLdMatch = html.match(/<script type="application\/ld\+json">(.*?)<\/script>/s);
        if (jsonLdMatch) {
            try {
                const jsonData = JSON.parse(jsonLdMatch[1]);
                if (jsonData.video && jsonData.video.contentUrl) {
                    videoUrl = jsonData.video.contentUrl;
                }
            } catch (e) {}
        }
        
        // Ищем в meta тегах
        if (!videoUrl) {
            const metaMatch = html.match(/<meta property="og:video" content="(.*?)"/);
            if (metaMatch) {
                videoUrl = metaMatch[1];
            }
        }
        
        if (!videoUrl) {
            return { error: 'Видео не найдено на странице Pinterest' };
        }
        
        // Скачиваем видео
        const fileId = uuidv4().slice(0, 8);
        const filePath = path.join(DOWNLOAD_DIR, `${fileId}_pinterest.mp4`);
        
        const videoResponse = await axios({
            url: videoUrl,
            method: 'GET',
            responseType: 'stream'
        });
        
        const writeStream = fs.createWriteStream(filePath);
        videoResponse.data.pipe(writeStream);
        
        return new Promise((resolve, reject) => {
            writeStream.on('finish', () => {
                const fileSize = fs.statSync(filePath).size;
                if (fileSize > MAX_FILE_SIZE) {
                    fs.unlinkSync(filePath);
                    resolve({ error: `Файл слишком большой (${formatBytes(fileSize)})` });
                } else {
                    resolve({
                        path: filePath,
                        title: 'Pinterest Video',
                        size: fileSize,
                        duration: 0
                    });
                }
            });
            
            writeStream.on('error', (err) => {
                reject({ error: `Ошибка скачивания: ${err.message}` });
            });
        });
        
    } catch (error) {
        console.error('Ошибка скачивания Pinterest:', error);
        return { error: `Ошибка: ${error.message}` };
    }
}

// ==================== ОБРАБОТЧИКИ КОМАНД ====================

// Команда /start
bot.onText(/\/start/, (msg) => {
    const chatId = msg.chat.id;
    const text = `
🎬 <b>Video Downloader Bot</b>

✅ <b>Работает на Node.js + Render</b>
🚀 <b>Быстро и стабильно</b>

<b>Поддерживает:</b>
• YouTube — видео (MP4)
• YouTube — аудио (MP3)
• Pinterest — видео

<b>Ограничения:</b>
• Макс. размер: ${formatBytes(MAX_FILE_SIZE)}
• Макс. длительность: ${formatTime(MAX_DURATION)}

<b>Как использовать:</b>
1. Отправьте ссылку
2. Для YouTube выберите формат
3. Получите файл

<b>Примеры ссылок:</b>
https://youtube.com/shorts/...
https://youtu.be/...
https://pin.it/...

Отправьте ссылку 👇
    `;
    
    bot.sendMessage(chatId, text, { parse_mode: 'HTML' });
});

// Команда /help
bot.onText(/\/help/, (msg) => {
    const chatId = msg.chat.id;
    const text = `
<b>Помощь по использованию:</b>

<b>Для быстрой работы:</b>
• Используйте YouTube Shorts
• Видео до 5 минут
• Низкое качество для скорости

<b>Если не работает:</b>
• Проверьте ссылку
• Убедитесь что видео публичное
• Попробуйте позже

<b>Команды:</b>
/start — начало работы
/help — эта справка
/ping — проверка бота

<b>Техническая информация:</b>
• Платформа: Node.js
• Хостинг: Render.com
• Автоочистка файлов
    `;
    
    bot.sendMessage(chatId, text, { parse_mode: 'HTML' });
});

// Команда /ping
bot.onText(/\/ping/, (msg) => {
    const chatId = msg.chat.id;
    bot.sendMessage(chatId, '🏓 <b>Бот работает!</b>\n\nСтатус: ✅ Активен\nПлатформа: Node.js', { parse_mode: 'HTML' });
});

// Команда /status
bot.onText(/\/status/, async (msg) => {
    const chatId = msg.chat.id;
    
    try {
        const files = await fs.readdir(DOWNLOAD_DIR);
        const totalFiles = files.length;
        
        const text = `
<b>Статус бота:</b>

🏠 <b>Платформа:</b> Node.js + Render
📁 <b>Файлов в папке:</b> ${totalFiles}
💾 <b>Макс. размер файла:</b> ${formatBytes(MAX_FILE_SIZE)}
⏱️ <b>Макс. длительность:</b> ${formatTime(MAX_DURATION)}

<b>Версии:</b>
• Node.js: ${process.version}
• ytdl-core: ${require('ytdl-core/package.json').version}

<b>Память:</b>
• Всего: ${formatBytes(process.memoryUsage().heapTotal)}
• Использовано: ${formatBytes(process.memoryUsage().heapUsed)}

<b>Совет:</b> Используйте YouTube Shorts!
        `;
        
        bot.sendMessage(chatId, text, { parse_mode: 'HTML' });
    } catch (error) {
        bot.sendMessage(chatId, `❌ Ошибка статуса: ${error.message}`);
    }
});

// Обработчик текстовых сообщений
bot.on('message', async (msg) => {
    const chatId = msg.chat.id;
    const text = msg.text;
    
    // Пропускаем команды
    if (text.startsWith('/')) return;
    
    // Проверяем URL
    if (!text.startsWith('http://') && !text.startsWith('https://')) {
        bot.sendMessage(chatId, '❌ <b>Отправьте корректную ссылку.</b>\n\nПример: https://youtube.com/shorts/...', { parse_mode: 'HTML' });
        return;
    }
    
    // Определяем платформу
    const url = text.toLowerCase();
    
    if (url.includes('youtube.com') || url.includes('youtu.be')) {
        // YouTube - показываем выбор формата
        const keyboard = {
            inline_keyboard: [
                [
                    { text: '🎬 Видео (MP4)', callback_data: `video:${text}` },
                    { text: '🎵 Аудио (MP3)', callback_data: `audio:${text}` }
                ]
            ]
        };
        
        bot.sendMessage(chatId, '📺 <b>YouTube ссылка обнаружена!</b>\nВыберите формат:', {
            parse_mode: 'HTML',
            reply_markup: keyboard
        });
        
    } else if (url.includes('pinterest.com') || url.includes('pin.it')) {
        // Pinterest - сразу скачиваем
        const message = await bot.sendMessage(chatId, '⏳ <b>Скачиваю Pinterest видео...</b>\nПожалуйста, подождите 15-30 секунд', { parse_mode: 'HTML' });
        
        const result = await downloadPinterest(text);
        
        if (result.error) {
            bot.editMessageText(`❌ <b>Ошибка:</b>\n${result.error}`, {
                chat_id: chatId,
                message_id: message.message_id,
                parse_mode: 'HTML'
            });
        } else {
            try {
                // Отправляем файл
                const videoStream = fs.createReadStream(result.path);
                await bot.sendVideo(chatId, videoStream, {
                    caption: `🎬 <b>Pinterest видео</b>\n📦 ${formatBytes(result.size)}`,
                    parse_mode: 'HTML'
                });
                
                bot.deleteMessage(chatId, message.message_id);
                
                // Удаляем файл
                fs.unlinkSync(result.path);
                
            } catch (sendError) {
                bot.editMessageText(`❌ <b>Ошибка отправки файла:</b>\n${sendError.message}`, {
                    chat_id: chatId,
                    message_id: message.message_id,
                    parse_mode: 'HTML'
                });
                if (fs.existsSync(result.path)) {
                    fs.unlinkSync(result.path);
                }
            }
        }
        
    } else {
        bot.sendMessage(chatId, '❌ <b>Неподдерживаемая платформа.</b>\n\nПоддерживается:\n• YouTube\n• Pinterest', { parse_mode: 'HTML' });
    }
});

// Обработчик callback кнопок
bot.on('callback_query', async (callbackQuery) => {
    const chatId = callbackQuery.message.chat.id;
    const messageId = callbackQuery.message.message_id;
    const data = callbackQuery.data;
    
    await bot.answerCallbackQuery(callbackQuery.id);
    
    const [action, url] = data.split(':');
    const isAudio = action === 'audio';
    
    // Обновляем сообщение
    const actionText = isAudio ? 'аудио' : 'видео';
    const message = await bot.editMessageText(`⏳ <b>Скачиваю ${actionText} с YouTube...</b>\nПожалуйста, подождите 20-40 секунд`, {
        chat_id: chatId,
        message_id: messageId,
        parse_mode: 'HTML'
    });
    
    // Скачиваем
    const result = await downloadYouTube(url, action);
    
    if (result.error) {
        bot.editMessageText(`❌ <b>Ошибка:</b>\n${result.error}`, {
            chat_id: chatId,
            message_id: messageId,
            parse_mode: 'HTML'
        });
    } else {
        try {
            if (isAudio) {
                // Отправляем аудио
                const audioStream = fs.createReadStream(result.path);
                await bot.sendAudio(chatId, audioStream, {
                    caption: `🎵 <b>${result.title}</b>\n📦 ${formatBytes(result.size)}\n⏱️ ${formatTime(result.duration)}`,
                    parse_mode: 'HTML',
                    title: result.title.substring(0, 64)
                });
            } else {
                // Отправляем видео
                const videoStream = fs.createReadStream(result.path);
                await bot.sendVideo(chatId, videoStream, {
                    caption: `🎬 <b>${result.title}</b>\n📦 ${formatBytes(result.size)}\n⏱️ ${formatTime(result.duration)}`,
                    parse_mode: 'HTML'
                });
            }
            
            bot.deleteMessage(chatId, messageId);
            
            // Удаляем файл
            fs.unlinkSync(result.path);
            
        } catch (sendError) {
            bot.editMessageText(`❌ <b>Ошибка отправки файла:</b>\n${sendError.message}\n\nПопробуйте более короткое видео.`, {
                chat_id: chatId,
                message_id: messageId,
                parse_mode: 'HTML'
            });
            
            if (fs.existsSync(result.path)) {
                fs.unlinkSync(result.path);
            }
        }
    }
});

// ==================== АВТООЧИСТКА ====================
async function cleanupOldFiles() {
    try {
        const files = await fs.readdir(DOWNLOAD_DIR);
        const now = Date.now();
        
        for (const file of files) {
            const filePath = path.join(DOWNLOAD_DIR, file);
            const stats = await fs.stat(filePath);
            
            // Удаляем файлы старше 1 часа
            if (now - stats.mtimeMs > 60 * 60 * 1000) {
                await fs.unlink(filePath);
                console.log(`Удален старый файл: ${file}`);
            }
        }
    } catch (error) {
        console.error('Ошибка очистки файлов:', error);
    }
}

// Запускаем очистку каждые 30 минут
setInterval(cleanupOldFiles, 30 * 60 * 1000);

// Очищаем при запуске
cleanupOldFiles();

// ==================== ЗАПУСК БОТА ====================
console.log('='.repeat(50));
console.log('🚀 ЗАПУСК VIDEO DOWNLOADER BOT');
console.log(`Время: ${new Date().toLocaleString()}`);
console.log(`Node.js: ${process.version}`);
console.log(`Токен: ${BOT_TOKEN.substring(0, 10)}...`);
console.log(`Папка загрузок: ${DOWNLOAD_DIR}`);
console.log('='.repeat(50));
console.log('🤖 Бот запущен и готов к работе!');
console.log('📱 Ожидание сообщений...');

// Обработка ошибок
bot.on('polling_error', (error) => {
    console.error('Ошибка polling:', error.message);
});

bot.on('webhook_error', (error) => {
    console.error('Ошибка webhook:', error.message);
});

process.on('uncaughtException', (error) => {
    console.error('Необработанное исключение:', error);
});

process.on('unhandledRejection', (reason, promise) => {
    console.error('Необработанный промис:', reason);
});
