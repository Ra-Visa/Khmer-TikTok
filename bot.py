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

# ==================== CONFIGURATION ====================
BOT_TOKEN = "8423380479:AAFS58QONQZzZnLGxy3q1_LBg4IfmOGfSzo"
RAPIDAPI_KEY = "8e126a962emshf6305bb2fe26993p14eeecjsn3438579f250c"
RAPIDAPI_HOST = "youtube-mp36.p.rapidapi.com"
ADSTERRA_LINK = "https://www.effectivegatecpm.com/hmc3n4g9?key=633ca2e22b9bf9e4fd318f9df03b032a"
DOMAIN = "https://khmer-tiktok-pe64.onrender.com"  # Your Render domain
PORT = int(os.environ.get('PORT', 5000))
REQUIRED_WAIT_TIME = 10  # seconds
MAX_AUDIO_SIZE = 50 * 1024 * 1024  # 50MB Telegram limit for audio files

# ==================== INITIALIZATION ====================
bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== THREAD-SAFE STORAGE ====================
from threading import Lock

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
        """Remove entries older than max_age seconds"""
        current_time = time.time()
        with self.lock:
            expired = [
                chat_id for chat_id, data in self.storage.items()
                if current_time - data.get('timestamp', 0) > max_age
            ]
            for chat_id in expired:
                del self.storage[chat_id]
            return len(expired)

# Initialize thread-safe storage
# Structure: {chat_id: {'mp3_link': str, 'title': str, 'timestamp': float, 'file_size': int}}
user_mp3_storage = ThreadSafeStorage()

# ==================== HELPER FUNCTIONS ====================
def extract_youtube_id(text):
    """Extract 11-character YouTube video ID from URL"""
    patterns = [
        r'(?:youtube\.com\/watch\?v=)([\w-]{11})',
        r'(?:youtube\.com\/shorts\/)([\w-]{11})',
        r'(?:youtu\.be\/)([\w-]{11})',
        r'(?:youtube\.com\/embed\/)([\w-]{11})',
        r'(?:youtube\.com\/v\/)([\w-]{11})'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    
    # Try to find any 11-character ID in the text if it's a shortened URL
    if 'youtu.be' in text or 'youtube.com' in text:
        # Look for 11-character alphanumeric string
        id_match = re.search(r'([\w-]{11})', text)
        if id_match:
            return id_match.group(1)
    
    return None

def get_file_size_from_url(url, timeout=10):
    """Get file size from URL without downloading"""
    try:
        response = requests.head(url, timeout=timeout, allow_redirects=True)
        if response.status_code == 200:
            return int(response.headers.get('content-length', 0))
    except Exception as e:
        logger.warning(f"Could not get file size: {e}")
    return 0

def download_audio_file(audio_url, timeout=60):
    """Download audio file from URL"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(audio_url, timeout=timeout, headers=headers, stream=True)
        
        if response.status_code == 200:
            content = b''
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    content += chunk
            return {'success': True, 'content': content}
        else:
            return {'success': False, 'error': f'Download failed: {response.status_code}'}
    except Exception as e:
        return {'success': False, 'error': str(e)}

def get_mp3_info(video_id, timeout=30):
    """
    Get MP3 download link and info from youtube-mp36.p.rapidapi.com
    """
    try:
        logger.info(f"🎵 Getting MP3 info for video ID: {video_id}")
        
        url = f"https://{RAPIDAPI_HOST}/dl"
        querystring = {"id": video_id}
        
        headers = {
            "X-RapidAPI-Key": RAPIDAPI_KEY,
            "X-RapidAPI-Host": RAPIDAPI_HOST
        }
        
        # Make API request with timeout
        response = requests.get(
            url, 
            headers=headers, 
            params=querystring, 
            timeout=timeout
        )
        
        if response.status_code != 200:
            return {
                'success': False, 
                'error': f'API Error: {response.status_code}'
            }
        
        data = response.json()
        
        # Check response format
        if data.get('status') != 'ok':
            error_msg = data.get('msg', 'Unknown error')
            return {'success': False, 'error': f'API Error: {error_msg}'}
        
        # Extract MP3 link and title
        mp3_link = data.get('link')
        title = data.get('title', 'Unknown Title')
        
        if not mp3_link:
            return {'success': False, 'error': 'No MP3 link found in response'}
        
        # Get file size
        file_size = get_file_size_from_url(mp3_link)
        
        return {
            'success': True,
            'mp3_link': mp3_link,
            'title': title,
            'video_id': video_id,
            'file_size': file_size
        }
        
    except requests.exceptions.Timeout:
        return {'success': False, 'error': '⏱️ Request timeout (30s). Please try again.'}
    except requests.exceptions.ConnectionError:
        return {'success': False, 'error': '🔌 Connection error. Please check your internet.'}
    except Exception as e:
        logger.error(f"MP3 API error: {e}")
        return {'success': False, 'error': f'❌ Error: {str(e)}'}

def send_audio_file(chat_id, audio_content, title, performer="YouTube"):
    """Send audio file to Telegram"""
    try:
        from io import BytesIO
        
        # Create file-like object
        audio_file = BytesIO(audio_content)
        audio_file.name = f"{title}.mp3"
        
        # Send as audio with proper metadata
        bot.send_audio(
            chat_id=chat_id,
            audio=audio_file,
            title=title[:100],  # Telegram title limit
            performer=performer,
            caption=f"📝 <b>ចំណងជើង:</b> {title}",
            parse_mode='HTML',
            timeout=120
        )
        return True
    except Exception as e:
        logger.error(f"Failed to send audio: {e}")
        return False

# ==================== BACKGROUND PROCESSING ====================
class MP3ConversionWorker:
    """Handles MP3 conversion in background threads"""
    
    def __init__(self):
        self.task_queue = Queue()
        self.workers = []
        self.start_workers()
    
    def start_workers(self, num_workers=3):
        """Start worker threads"""
        for i in range(num_workers):
            worker = threading.Thread(target=self._worker_loop, daemon=True)
            worker.start()
            self.workers.append(worker)
        logger.info(f"Started {num_workers} MP3 conversion workers")
    
    def _worker_loop(self):
        """Main worker loop"""
        while True:
            try:
                task = self.task_queue.get()
                if task:
                    self._process_task(task)
            except Exception as e:
                logger.error(f"Worker error: {e}")
            finally:
                self.task_queue.task_done()
    
    def _process_task(self, task):
        """Process a single conversion task"""
        chat_id = task['chat_id']
        message_id = task['message_id']
        video_id = task['video_id']
        original_message = task['original_message']
        
        try:
            # Get MP3 info with timeout
            result = get_mp3_info(video_id, timeout=30)
            
            if result['success']:
                # Store MP3 info
                user_mp3_storage.set(chat_id, {
                    'mp3_link': result['mp3_link'],
                    'title': result['title'],
                    'video_id': video_id,
                    'timestamp': time.time(),
                    'file_size': result.get('file_size', 0)
                })
                
                # Create inline keyboard
                from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
                
                markup = InlineKeyboardMarkup(row_width=1)
                ad_button = InlineKeyboardButton(
                    text="👁️ មើល Ads", 
                    url=ADSTERRA_LINK
                )
                download_button = InlineKeyboardButton(
                    text="🎵 ទាញយក MP3",
                    callback_data="download_mp3"
                )
                markup.add(ad_button, download_button)
                
                # Update message with title only - NO parse_mode for main text
                bot.edit_message_text(
                    text=(
                        f"✅ MP3 របស់អ្នករួចរាល់ហើយ!\n\n"
                        f"📝 ចំណងជើង: {result['title']}\n\n"
                        f"សូមចុចមើល Ads ១០ វិនាទី ដើម្បីទទួលបានឯកសារ MP3 ។"
                    ),
                    chat_id=chat_id,
                    message_id=message_id,
                    reply_markup=markup
                )
                
            else:
                # Handle error - NO parse_mode
                bot.edit_message_text(
                    text=(
                        f"❌ កំហុស: {result['error']}\n\n"
                        f"សូមព្យាយាមម្តងទៀត ឬប្រើវីដេអូផ្សេង។"
                    ),
                    chat_id=chat_id,
                    message_id=message_id
                )
                
        except Exception as e:
            logger.error(f"Background processing error: {e}")
            bot.edit_message_text(
                text=(
                    f"❌ កំហុសប្រព័ន្ធ: {str(e)}\n\n"
                    f"សូមព្យាយាមម្តងទៀត។"
                ),
                chat_id=chat_id,
                message_id=message_id
            )
    
    def add_task(self, chat_id, message_id, video_id, original_message):
        """Add a new conversion task to queue"""
        self.task_queue.put({
            'chat_id': chat_id,
            'message_id': message_id,
            'video_id': video_id,
            'original_message': original_message
        })

# Initialize worker
mp3_worker = MP3ConversionWorker()

# ==================== FLASK ROUTES ====================

@app.route('/', methods=['GET'])
def home():
    """Root endpoint"""
    return jsonify({
        'status': 'OK',
        'message': 'YouTube to MP3 Downloader Bot is running',
        'bot_username': '@khmer_tiktok_bot',
        'timestamp': datetime.now().isoformat()
    }), 200

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'bot': 'running',
        'queue_size': mp3_worker.task_queue.qsize(),
        'stored_mp3s': len(user_mp3_storage.storage)
    }), 200

@app.route('/webhook', methods=['POST'])
def webhook():
    """Telegram bot webhook endpoint"""
    if request.headers.get('content-type') != 'application/json':
        return 'Invalid request', 403
    
    try:
        json_str = request.get_data().decode('UTF-8')
        update = telebot.types.Update.de_json(json_str)
        bot.process_new_updates([update])
        return 'OK', 200
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return 'Error', 500

# ==================== TELEGRAM BOT HANDLERS ====================

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    """Welcome message handler with image using HTML parsing"""
    chat_id = message.chat.id
    
    # Welcome image URL
    photo_url = "https://i.ibb.co/d89kyw5/IMG-20260303-144521-941.jpg"
    
    # Styled caption with HTML formatting (using <b> for bold)
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
        # Send photo with caption using HTML parsing
        bot.send_photo(
            chat_id=chat_id,
            photo=photo_url,
            caption=caption,
            parse_mode='HTML'
        )
        logger.info(f"✅ Welcome image sent to chat {chat_id}")
        
    except Exception as e:
        # Log the error for debugging
        logger.error(f"Failed to send welcome image to chat {chat_id}: {e}")
        
        # Fallback: Send text-only message if image fails
        try:
            bot.send_message(
                chat_id=chat_id,
                text=(
                    "<b>𝗞𝗜𝗥𝗔𝗞 𝗗𝗢𝗪𝗡𝗟𝗢𝗔𝗗 𝗠𝗣𝟯 - 𝗕𝗢𝗧</b>\n\n"
                    "សួស្តី! ជម្រាបសួរមកកាន់ KIRAK Download MP3 Bot\n\n"
                    "📥 <b>របៀបប្រើប្រាស់:</b>\n"
                    "គ្រាន់តែផ្ញើតំណ YouTube មកខ្ញុំ\n\n"
                    "🌐 <b>គាំទ្រ:</b> YouTube, YouTube Shorts, YouTube Music\n"
                    "🎧 <b>គុណភាព:</b> MP3 320kbps\n\n"
                    "📞 <b>សម្រាប់ជំនួយ:</b> @kirak_itadori"
                ),
                parse_mode='HTML'
            )
            logger.info(f"✅ Fallback text message sent to chat {chat_id}")
        except Exception as fallback_error:
            logger.error(f"Critical: Even fallback message failed for chat {chat_id}: {fallback_error}")

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    """Handle user messages containing YouTube links"""
    chat_id = message.chat.id
    
    # Show typing indicator
    bot.send_chat_action(chat_id, 'typing')
    
    # Clean up old storage
    expired = user_mp3_storage.cleanup_old()
    if expired:
        logger.info(f"Cleaned up {expired} expired entries")
    
    # Extract YouTube video ID
    video_id = extract_youtube_id(message.text)
    
    if not video_id:
        bot.reply_to(
            message, 
            "❌ សូមផ្ញើតំណភ្ជាប់ YouTube ត្រឹមត្រូវ។\n\n"
            "ឧទាហរណ៍: https://www.youtube.com/watch?v=VIDEO_ID"
        )
        return
    
    logger.info(f"📹 Video ID detected: {video_id} from chat {chat_id}")
    
    # Send initial processing message
    processing_msg = bot.reply_to(
        message,
        "🔄 កំពុងបំប្លែងទៅជា MP3... សូមរង់ចាំបន្តិច។"
    )
    
    # Add to background queue
    mp3_worker.add_task(
        chat_id=chat_id,
        message_id=processing_msg.message_id,
        video_id=video_id,
        original_message=processing_msg
    )
    
    logger.info(f"📦 Added MP3 conversion task to queue for video: {video_id}")

@bot.callback_query_handler(func=lambda call: call.data == 'download_mp3')
def handle_download_mp3(call):
    """Handle the download MP3 button callback with time verification"""
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    current_time = time.time()
    
    try:
        logger.info(f"✅ Download MP3 clicked for chat_id: {chat_id}")
        
        # Get MP3 data
        mp3_data = user_mp3_storage.get(chat_id)
        
        if not mp3_data:
            bot.answer_callback_query(
                call.id,
                text="❌ MP3 ផុតកំណត់ហើយ! សូមផ្ញើតំណភ្ជាប់ម្តងទៀត។",
                show_alert=True
            )
            return
        
        # Calculate wait time
        time_diff = current_time - mp3_data['timestamp']
        
        if time_diff < REQUIRED_WAIT_TIME:
            remaining = int(REQUIRED_WAIT_TIME - time_diff) + 1
            bot.answer_callback_query(
                call.id,
                text=f"⚠️ សូមរង់ចាំ {remaining} វិនាទីទៀត ដើម្បីគាំទ្រ Server យើងខ្ញុំ។",
                show_alert=True
            )
            return
        
        # Sufficient time passed - process download
        bot.answer_callback_query(
            call.id,
            text="កំពុងទាញយក MP3... សូមរង់ចាំ",
            show_alert=False
        )
        
        # Update message to show downloading status
        bot.edit_message_text(
            text="⏳ កំពុងទាញយកឯកសារ MP3... សូមរង់ចាំបន្តិច។",
            chat_id=chat_id,
            message_id=message_id
        )
        
        # Check file size
        file_size = mp3_data.get('file_size', 0)
        
        # If file is too large for Telegram's 50MB limit
        if file_size > MAX_AUDIO_SIZE:
            # Create download link button instead
            from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
            
            markup = InlineKeyboardMarkup()
            download_button = InlineKeyboardButton(
                text="🎵 ទាញយកឯកសារ MP3",
                url=mp3_data['mp3_link']
            )
            markup.add(download_button)
            
            bot.edit_message_text(
                text=(
                    f"📝 <b>ចំណងជើង:</b> {mp3_data['title']}\n\n"
                    f"⚠️ ឯកសារមានទំហំធំ ({file_size // (1024*1024)}MB) មិនអាចផ្ញើតាម Telegram បានទេ។\n\n"
                    f"សូមចុចប៊ូតុងខាងក្រោមដើម្បីទាញយក៖"
                ),
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=markup,
                parse_mode='HTML'
            )
            
        else:
            # Download the audio file
            download_result = download_audio_file(mp3_data['mp3_link'], timeout=60)
            
            if download_result['success']:
                # Send as audio file
                success = send_audio_file(
                    chat_id, 
                    download_result['content'], 
                    mp3_data['title']
                )
                
                if success:
                    # Update the original message
                    bot.edit_message_text(
                        text=(
                            f"✅ MP3 របស់អ្នកបានផ្ញើដោយជោគជ័យ! 🎉\n\n"
                            f"📝 <b>ចំណងជើង:</b> {mp3_data['title']}\n\n"
                            f"ផ្ញើតំណភ្ជាប់ថ្មីដើម្បីបំប្លែងបន្ត!"
                        ),
                        chat_id=chat_id,
                        message_id=message_id,
                        parse_mode='HTML'
                    )
                    
                    # Clean up storage
                    user_mp3_storage.delete(chat_id)
                    
                else:
                    # Fallback to link button if sending fails
                    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
                    
                    markup = InlineKeyboardMarkup()
                    download_button = InlineKeyboardButton(
                        text="🎵 ទាញយកឯកសារ MP3",
                        url=mp3_data['mp3_link']
                    )
                    markup.add(download_button)
                    
                    bot.edit_message_text(
                        text=(
                            f"📝 <b>ចំណងជើង:</b> {mp3_data['title']}\n\n"
                            f"⚠️ មិនអាចផ្ញើឯកសារដោយផ្ទាល់បានទេ។\n\n"
                            f"សូមចុចប៊ូតុងខាងក្រោមដើម្បីទាញយក៖"
                        ),
                        chat_id=chat_id,
                        message_id=message_id,
                        reply_markup=markup,
                        parse_mode='HTML'
                    )
            else:
                # Download failed - provide link
                from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
                
                markup = InlineKeyboardMarkup()
                download_button = InlineKeyboardButton(
                    text="🎵 ទាញយកឯកសារ MP3",
                    url=mp3_data['mp3_link']
                )
                markup.add(download_button)
                
                bot.edit_message_text(
                    text=(
                        f"📝 <b>ចំណងជើង:</b> {mp3_data['title']}\n\n"
                        f"⚠️ ការទាញយកឯកសារបរាជ័យ។ សូមចុចប៊ូតុងខាងក្រោមដើម្បីទាញយក៖"
                    ),
                    chat_id=chat_id,
                    message_id=message_id,
                    reply_markup=markup,
                    parse_mode='HTML'
                )
            
    except Exception as e:
        logger.error(f"Callback handler error: {e}")
        bot.answer_callback_query(
            call.id,
            text="❌ មានបញ្ហាបច្ចេកទេស។ សូមព្យាយាមម្តងទៀត។",
            show_alert=True
        )

# ==================== MAIN ====================
if __name__ != '__main__':
    # Gunicorn mode
    gunicorn_logger = logging.getLogger('gunicorn.error')
    app.logger.handlers = gunicorn_logger.handlers
    app.logger.setLevel(gunicorn_logger.level)

if __name__ == '__main__':
    # Local development
    logger.info("🚀 Starting YouTube to MP3 Downloader Bot locally...")
    logger.info(f"🌐 Domain: {DOMAIN}")
    logger.info(f"📡 Webhook URL: {DOMAIN}/webhook")
    logger.info(f"⏱️ API Timeout: 30 seconds")
    logger.info(f"👥 Worker threads: 3")
    logger.info(f"📊 Max audio size: {MAX_AUDIO_SIZE // (1024*1024)}MB")
    logger.info("✅ Clean audio file sending enabled")
    logger.info("✅ Welcome image configured with HTML parsing")
    
    # Remove webhook for local testing
    bot.remove_webhook()
    logger.info("✅ Webhook removed for local testing")
    
    # Start Flask
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
