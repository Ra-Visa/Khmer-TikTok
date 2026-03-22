import telebot
from flask import Flask, request, jsonify
import requests
import threading
import logging
import re
import os
import time
from datetime import datetime
from queue import Queue
from threading import Lock
from io import BytesIO

# ==================== CONFIGURATION ====================
# ដាក់ BOT_TOKEN របស់អ្នកនៅទីនេះ
BOT_TOKEN = "8423380479:AAFS58QONQZzZnLGxy3q1_LBg4IfmOGfSzo"
RAPIDAPI_KEY = "8e126a962emshf6305bb2fe26993p14eeecjsn3438579f250c"
RAPIDAPI_HOST = "youtube-mp36.p.rapidapi.com"
PORT = int(os.environ.get('PORT', 5000))
MAX_AUDIO_SIZE = 50 * 1024 * 1024  # 50MB ដែនកំណត់របស់ Telegram

# ==================== INITIALIZATION ====================
bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class ThreadSafeStorage:
    def __init__(self):
        self.storage = {}
        self.lock = Lock()
    
    def set(self, chat_id, data):
        with self.lock: self.storage[chat_id] = data
    
    def get(self, chat_id):
        with self.lock: return self.storage.get(chat_id)
    
    def delete(self, chat_id):
        with self.lock: return self.storage.pop(chat_id, None)

    def cleanup_old(self, max_age=3600):
        current_time = time.time()
        with self.lock:
            expired = [k for k, v in self.storage.items() if current_time - v.get('timestamp', 0) > max_age]
            for k in expired: del self.storage[k]

user_mp3_storage = ThreadSafeStorage()

# ==================== HELPERS ====================
def extract_youtube_id(text):
    patterns = [r'v=([\w-]{11})', r'shorts/([\w-]{11})', r'youtu\.be/([\w-]{11})', r'embed/([\w-]{11})']
    for p in patterns:
        match = re.search(p, text)
        if match: return match.group(1)
    return None

def get_mp3_info(video_id):
    try:
        url = f"https://{RAPIDAPI_HOST}/dl"
        headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
        response = requests.get(url, headers=headers, params={"id": video_id}, timeout=20)
        data = response.json()
        if data.get('status') == 'ok':
            return {'success': True, 'link': data['link'], 'title': data['title']}
        return {'success': False, 'error': data.get('msg', 'API Error')}
    except Exception as e:
        return {'success': False, 'error': str(e)}

# ==================== WORKER ====================
class MP3Worker:
    def __init__(self):
        self.queue = Queue()
        for _ in range(3): threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            task = self.queue.get()
            self._process(task)
            self.queue.task_done()

    def _process(self, task):
        chat_id, msg_id, video_id = task['chat_id'], task['msg_id'], task['video_id']
        res = get_mp3_info(video_id)
        if res['success']:
            user_mp3_storage.set(chat_id, {'link': res['link'], 'title': res['title'], 'timestamp': time.time()})
            markup = telebot.types.InlineKeyboardMarkup()
            markup.add(telebot.types.InlineKeyboardButton("🎵 ទាញយក MP3 ឥឡូវនេះ", callback_data="download_now"))
            bot.edit_message_text(f"✅ រួចរាល់ហើយ!\n\n📝 ចំណងជើង: {res['title']}\n\nសូមចុចប៊ូតុងខាងក្រោមដើម្បីទាញយក។", chat_id, msg_id, reply_markup=markup)
        else:
            bot.edit_message_text(f"❌ បរាជ័យ: {res['error']}", chat_id, msg_id)

worker = MP3Worker()

# ==================== BOT HANDLERS ====================
@bot.message_handler(commands=['start', 'help'])
def welcome(message):
    photo_url = "https://i.ibb.co/d89kyw5/IMG-20260303-144521-941.jpg"
    caption = (
        "<b>𝗞𝗜𝗥𝗔𝗞 𝗗𝗢𝗪𝗡𝗟𝗢𝗔𝗗 𝗠𝗣𝟯 - 𝗕𝗢𝗧</b>\n\n"
        "សួស្តី! ជម្រាបសួរមកកាន់ KIRAK Download MP3 Bot\n\n"
        "📥 <b>របៀបប្រើប្រាស់:</b>\n"
        "គ្រាន់តែផ្ញើតំណ YouTube មកខ្ញុំ\n\n"
        "🌐 <b>គាំទ្រ:</b> YouTube, YouTube Shorts, YouTube Music\n"
        "🎧 <b>គុណភាព:</b> MP3 320kbps\n\n"
        "📞 <b>សម្រាប់ជំនួយ:</b> @kirak_itadori"
    )
    try:
        bot.send_photo(message.chat.id, photo_url, caption=caption, parse_mode='HTML')
    except:
        bot.send_message(message.chat.id, caption, parse_mode='HTML')

@bot.message_handler(func=lambda m: True)
def handle_link(message):
    vid = extract_youtube_id(message.text)
    if vid:
        user_mp3_storage.cleanup_old()
        wait_msg = bot.reply_to(message, "🔄 កំពុងបំប្លែង... សូមរង់ចាំបន្តិច។")
        worker.queue.put({'chat_id': message.chat.id, 'msg_id': wait_msg.message_id, 'video_id': vid})
    else:
        bot.reply_to(message, "❌ សូមផ្ញើតំណភ្ជាប់ YouTube ដែលត្រឹមត្រូវ។")

@bot.callback_query_handler(func=lambda c: c.data == "download_now")
def download(call):
    chat_id = call.message.chat.id
    data = user_mp3_storage.get(chat_id)
    if not data:
        bot.answer_callback_query(call.id, "❌ ផុតកំណត់ហើយ! សូមផ្ញើ Link ម្តងទៀត។", show_alert=True)
        return

    bot.edit_message_text("⏳ កំពុងរៀបចំផ្ញើឯកសារ... សូមរង់ចាំបន្តិច។", chat_id, call.message.message_id)
    try:
        # ទាញយក file ចូល memory រួចផ្ញើទៅ Telegram
        resp = requests.get(data['link'], timeout=60)
        audio = BytesIO(resp.content)
        audio.name = f"{data['title']}.mp3"
        bot.send_audio(chat_id, audio, title=data['title'], caption=f"✅ ទាញយកដោយជោគជ័យ!")
        bot.delete_message(chat_id, call.message.message_id)
        user_mp3_storage.delete(chat_id)
    except:
        # បើផ្ញើ File មិនរួច (ទំហំលើស 50MB) វានឹងឱ្យ Link ជំនួស
        markup = telebot.types.InlineKeyboardMarkup().add(telebot.types.InlineKeyboardButton("🔗 ចុចទីនេះដើម្បីទាញយកតាម Web", url=data['link']))
        bot.edit_message_text("⚠️ មិនអាចផ្ញើ File ផ្ទាល់បានទេ (ប្រហែលមកពីទំហំធំពេក)។\nសូមទាញយកតាម Link ខាងក្រោមវិញ៖", chat_id, call.message.message_id, reply_markup=markup)

# ==================== FLASK SERVER ====================
@app.route('/')
def index(): return "Bot is running!"

@app.route('/webhook', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return '!', 200
    return 'Forbidden', 403

if __name__ == '__main__':
    bot.remove_webhook()
    # បើចង់ប្រើ Webhook នៅលើ Render ត្រូវបន្ថែម bot.set_webhook(url=DOMAIN + "/webhook")
    app.run(host='0.0.0.0', port=PORT)
        
