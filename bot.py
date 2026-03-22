import telebot
from flask import Flask, request, jsonify
import requests
import threading
import logging
import re
import os
import time
from datetime import datetime
from urllib.parse import quote, unquote, parse_qs, urlparse
from queue import Queue
import sys
from threading import Lock

# ==================== CONFIGURATION ====================
BOT_TOKEN = "8423380479:AAFS58QONQZzZnLGxy3q1_LBg4IfmOGfSzo"
RAPIDAPI_KEY = "8e126a962emshf6305bb2fe26993p14eeecjsn3438579f250c"
RAPIDAPI_HOST = "youtube-mp36.p.rapidapi.com"
DOMAIN = "https://khmer-tiktok-pe64.onrender.com" 
PORT = int(os.environ.get('PORT', 5000))
MAX_AUDIO_SIZE = 50 * 1024 * 1024  # 50MB Telegram limit

# ==================== INITIALIZATION ====================
bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class ThreadSafeStorage:
    def __init__(self):
        self.storage = {}
        self.lock = Lock()
    
    def set(self, chat_id, data):
        with self.lock:
            self.storage[chat_id] = data
    
    def get(self, chat_id):
        with self.lock:
            return self.storage.get(chat_id)
    
    def delete(self, chat_id):
        with self.lock:
            if chat_id in self.storage:
                del self.storage[chat_id]
                return True
            return False
    
    def cleanup_old(self, max_age=3600):
        current_time = time.time()
        with self.lock:
            expired = [
                chat_id for chat_id, data in self.storage.items()
                if current_time - data.get('timestamp', 0) > max_age
            ]
            for chat_id in expired:
                del self.storage[chat_id]
            return len(expired)

user_mp3_storage = ThreadSafeStorage()

# ==================== HELPER FUNCTIONS ====================
def extract_youtube_id(text):
    patterns = [
        r'(?:youtube\.com\/watch\?v=)([\w-]{11})',
        r'(?:youtube\.com\/shorts\/)([\w-]{11})',
        r'(?:youtu\.be\/)([\w-]{11})',
        r'(?:youtube\.com\/embed\/)([\w-]{11})',
        r'(?:youtube\.com\/v\/)([\w-]{11})'
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match: return match.group(1)
    if 'youtu.be' in text or 'youtube.com' in text:
        id_match = re.search(r'([\w-]{11})', text)
        if id_match: return id_match.group(1)
    return None

def get_file_size_from_url(url, timeout=10):
    try:
        response = requests.head(url, timeout=timeout, allow_redirects=True)
        if response.status_code == 200:
            return int(response.headers.get('content-length', 0))
    except: pass
    return 0

def download_audio_file(audio_url, timeout=60):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(audio_url, timeout=timeout, headers=headers, stream=True)
        if response.status_code == 200:
            content = b''
            for chunk in response.iter_content(chunk_size=8192):
                if chunk: content += chunk
            return {'success': True, 'content': content}
        return {'success': False, 'error': f'Download failed: {response.status_code}'}
    except Exception as e:
        return {'success': False, 'error': str(e)}

def get_mp3_info(video_id, timeout=30):
    try:
        url = f"https://{RAPIDAPI_HOST}/dl"
        headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
        response = requests.get(url, headers=headers, params={"id": video_id}, timeout=timeout)
        data = response.json()
        
        if data.get('status') != 'ok':
            return {'success': False, 'error': data.get('msg', 'Unknown error')}
        
        mp3_link = data.get('link')
        title = data.get('title', 'Unknown Title')
        file_size = get_file_size_from_url(mp3_link)
        
        return {
            'success': True,
            'mp3_link': mp3_link,
            'title': title,
            'video_id': video_id,
            'file_size': file_size
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}

def send_audio_file(chat_id, audio_content, title, performer="YouTube"):
    try:
        from io import BytesIO
        audio_file = BytesIO(audio_content)
        audio_file.name = f"{title}.mp3"
        bot.send_audio(
            chat_id=chat_id,
            audio=audio_file,
            title=title[:100],
            performer=performer,
            caption=f"📝 <b>ចំណងជើង:</b> {title}",
            parse_mode='HTML'
        )
        return True
    except Exception as e:
        logger.error(f"Failed to send audio: {e}")
        return False

# ==================== BACKGROUND PROCESSING ====================
class MP3ConversionWorker:
    def __init__(self):
        self.task_queue = Queue()
        self.start_workers()
    
    def start_workers(self, num_workers=3):
        for _ in range(num_workers):
            threading.Thread(target=self._worker_loop, daemon=True).start()

    def _worker_loop(self):
        while True:
            task = self.task_queue.get()
            if task: self._process_task(task)
            self.task_queue.task_done()
    
    def _process_task(self, task):
        chat_id, message_id, video_id = task['chat_id'], task['message_id'], task['video_id']
        try:
            result = get_mp3_info(video_id)
            if result['success']:
                user_mp3_storage.set(chat_id, {
                    'mp3_link': result['mp3_link'],
                    'title': result['title'],
                    'timestamp': time.time(),
                    'file_size': result['file_size']
                })
                
                from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
                markup = InlineKeyboardMarkup()
                markup.add(InlineKeyboardButton("🎵 ទាញយក MP3 ឥឡូវនេះ", callback_data="download_mp3"))
                
                bot.edit_message_text(
                    text=f"✅ រួចរាល់ហើយ!\n\n📝 ចំណងជើង: {result['title']}\n\nសូមចុចប៊ូតុងខាងក្រោមដើម្បីទាញយក។",
                    chat_id=chat_id,
                    message_id=message_id,
                    reply_markup=markup
                )
            else:
                bot.edit_message_text(text=f"❌ កំហុស: {result['error']}", chat_id=chat_id, message_id=message_id)
        except Exception as e:
            bot.edit_message_text(text=f"❌ កំហុសប្រព័ន្ធ: {str(e)}", chat_id=chat_id, message_id=message_id)

    def add_task(self, chat_id, message_id, video_id):
        self.task_queue.put({'chat_id': chat_id, 'message_id': message_id, 'video_id': video_id})

mp3_worker = MP3ConversionWorker()

# ==================== FLASK ROUTES ====================
@app.route('/')
def home(): return jsonify({'status': 'OK'}), 200

@app.route('/webhook', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_str = request.get_data().decode('UTF-8')
        update = telebot.types.Update.de_json(json_str)
        bot.process_new_updates([update])
        return 'OK', 200
    return 'Forbidden', 403

# ==================== TELEGRAM BOT HANDLERS ====================
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    photo_url = "https://i.ibb.co/d89kyw5/IMG-20260303-144521-941.jpg"
    caption = (
        "<b>𝗞𝗜𝗥𝗔𝗞 𝗗𝗢𝗪𝗡𝗟𝗢𝗔𝗗 𝗠𝗣𝟯 - 𝗕𝗢𝗧</b>\n\n"
        "សួស្តី! ផ្ញើតំណ YouTube មកខ្ញុំដើម្បីទាញយក MP3\n\n"
        "🎧 <b>គុណភាព:</b> MP3 320kbps\n"
        "📞 <b>ជំនួយ:</b> @kirak_itadori"
    )
    try:
        bot.send_photo(message.chat.id, photo_url, caption=caption, parse_mode='HTML')
    except:
        bot.send_message(message.chat.id, caption, parse_mode='HTML')

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    video_id = extract_youtube_id(message.text)
    if not video_id:
        bot.reply_to(message, "❌ សូមផ្ញើតំណភ្ជាប់ YouTube ត្រឹមត្រូវ។")
        return
    
    user_mp3_storage.cleanup_old()
    processing_msg = bot.reply_to(message, "🔄 កំពុងបំប្លែង... សូមរង់ចាំ។")
    mp3_worker.add_task(message.chat.id, processing_msg.message_id, video_id)

@bot.callback_query_handler(func=lambda call: call.data == 'download_mp3')
def handle_download_mp3(call):
    chat_id = call.message.chat.id
    mp3_data = user_mp3_storage.get(chat_id)
    
    if not mp3_data:
        bot.answer_callback_query(call.id, "❌ ផុតកំណត់ហើយ! សូមផ្ញើតំណម្តងទៀត។", show_alert=True)
        return

    bot.answer_callback_query(call.id, "⏳ កំពុងរៀបចំឯកសារ...")
    bot.edit_message_text("⏳ កំពុងផ្ញើឯកសារ MP3... សូមរង់ចាំបន្តិច។", chat_id, call.message.message_id)
    
    if mp3_data['file_size'] > MAX_AUDIO_SIZE:
        from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
        markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🔗 ចុចទីនេះដើម្បីទាញយក (ឯកសារធំ)", url=mp3_data['mp3_link']))
        bot.edit_message_text(f"⚠️ ឯកសារមានទំហំធំពេកសម្រាប់ Telegram។\n\n📝 {mp3_data['title']}", chat_id, call.message.message_id, reply_markup=markup)
    else:
        dl = download_audio_file(mp3_data['mp3_link'])
        if dl['success'] and send_audio_file(chat_id, dl['content'], mp3_data['title']):
            bot.delete_message(chat_id, call.message.message_id)
            user_mp3_storage.delete(chat_id)
        else:
            bot.edit_message_text("❌ ការទាញយកបរាជ័យ។", chat_id, call.message.message_id)

if __name__ == '__main__':
    bot.remove_webhook()
    app.run(host='0.0.0.0', port=PORT)

