import telebot
from flask import Flask, request, jsonify
import requests
import threading
import logging
import re
import os
import time
import html
from datetime import datetime
from urllib.parse import quote, unquote
from queue import Queue
import sys

# ==================== CONFIGURATION ====================
BOT_TOKEN = "8771490616:AAHLguFzc28SvbKZNUDS5_9KscJ_Ko8FRKs"
RAPIDAPI_KEY = "8e126a962emshf6305bb2fe26993p14eeecjsn3438579f250c"
RAPIDAPI_HOST = "tiktok-scraper7.p.rapidapi.com"
ADSTERRA_LINK = "https://www.effectivegatecpm.com/hmc3n4g9?key=633ca2e22b9bf9e4fd318f9df03b032a"
DOMAIN = "https://khmer-tiktok.onrender.com"  # Your Render domain
PORT = int(os.environ.get('PORT', 5000))
REQUIRED_WAIT_TIME = 10  # seconds

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
user_video_storage = ThreadSafeStorage()

# ==================== HELPER FUNCTIONS ====================
def extract_tiktok_url(text):
    """Extract TikTok URL from text"""
    patterns = [
        r'(https?://(?:www\.)?tiktok\.com/@[\w.-]+/video/\d+)',
        r'(https?://(?:www\.)?tiktok\.com/\w+/video/\d+)',
        r'(https?://(?:www\.)?vm\.tiktok\.com/\w+)',
        r'(https?://(?:www\.)?vt\.tiktok\.com/\w+)',
        r'(https?://(?:www\.)?tiktok\.com/[\w/%.-]+)'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    
    return None

def safe_text(text):
    """Escape special characters for Telegram HTML parsing"""
    if text is None:
        return ""
    return html.escape(str(text))

def download_tiktok_video(tiktok_url, timeout=60):
    """
    Download TikTok video without watermark using RapidAPI
    Increased timeout to 60 seconds for long videos
    """
    try:
        logger.info(f"📥 Downloading video from: {tiktok_url}")
        
        url = f"https://{RAPIDAPI_HOST}/"
        querystring = {"url": tiktok_url, "hd": "1"}
        
        headers = {
            "X-RapidAPI-Key": RAPIDAPI_KEY,
            "X-RapidAPI-Host": RAPIDAPI_HOST
        }
        
        # Step 1: Get video info from API
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
        
        if not (data.get('code') == 0 and data.get('data')):
            return {'success': False, 'error': 'No video found in response'}
        
        video_data = data['data']
        
        # Step 2: Get video URL
        video_url = (
            video_data.get('hdplay') or 
            video_data.get('play') or 
            video_data.get('wmplay') or
            video_data.get('video_url') or
            video_data.get('download_url')
        )
        
        if not video_url:
            return {'success': False, 'error': 'No video URL found'}
        
        # Step 3: Download actual video content with streaming
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        
        # Use stream=True for large files
        video_response = requests.get(
            video_url, 
            timeout=timeout,
            headers=headers,
            stream=True
        )
        
        if video_response.status_code != 200:
            return {'success': False, 'error': f'Video download failed: {video_response.status_code}'}
        
        # Read content in chunks for large files
        video_content = b''
        for chunk in video_response.iter_content(chunk_size=8192):
            if chunk:
                video_content += chunk
        
        return {
            'success': True,
            'video_content': video_content,
            'video_url': video_url,
            'description': safe_text(video_data.get('title', 'TikTok Video')[:100]),
            'author': safe_text(video_data.get('author', {}).get('nickname', 'Unknown')),
            'duration': video_data.get('duration', 0),
            'cover': video_data.get('cover', '')
        }
        
    except requests.exceptions.Timeout:
        return {'success': False, 'error': '⏱️ Request timeout (60s). The video might be too long.'}
    except requests.exceptions.ConnectionError:
        return {'success': False, 'error': '🔌 Connection error. Please check your internet.'}
    except Exception as e:
        logger.error(f"Download error: {e}")
        return {'success': False, 'error': f'❌ Error: {str(e)}'}

def send_video_to_chat(chat_id, video_content, caption=""):
    """Send video file to Telegram chat with chunked upload"""
    try:
        from io import BytesIO
        
        # Create file-like object
        video_file = BytesIO(video_content)
        video_file.name = 'tiktok_video.mp4'
        
        # Show upload action
        bot.send_chat_action(chat_id, 'upload_video')
        
        # Send video with increased timeout
        bot.send_video(
            chat_id=chat_id,
            video=video_file,
            caption=caption,
            timeout=180,  # 3 minutes timeout for long videos
            supports_streaming=True
        )
        return True
        
    except Exception as e:
        logger.error(f"Failed to send video: {e}")
        try:
            # Fallback: Send as document
            from io import BytesIO
            video_file = BytesIO(video_content)
            video_file.name = 'tiktok_video.mp4'
            
            bot.send_document(
                chat_id=chat_id,
                document=video_file,
                caption=caption + "\n\n📁 Sent as file (Telegram video limit)",
                timeout=180
            )
            return True
        except Exception as e2:
            logger.error(f"Failed to send as document: {e2}")
            return False

# ==================== BACKGROUND PROCESSING ====================
class VideoDownloadWorker:
    """Handles video downloading in background threads"""
    
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
        logger.info(f"Started {num_workers} video download workers")
    
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
        """Process a single download task"""
        chat_id = task['chat_id']
        message_id = task['message_id']
        tiktok_url = task['tiktok_url']
        processing_msg = task['processing_msg']
        
        try:
            # Download video with timeout
            result = download_tiktok_video(tiktok_url, timeout=60)
            
            if result['success']:
                # Store video data
                user_video_storage.set(chat_id, {
                    'video_content': result['video_content'],
                    'video_info': {
                        'description': result['description'],
                        'author': result['author'],
                        'duration': result.get('duration', 0)
                    },
                    'timestamp': time.time()
                })
                
                # Create inline keyboard
                from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
                
                markup = InlineKeyboardMarkup(row_width=1)
                ad_button = InlineKeyboardButton(
                    text="👁️ មើលពាណិជ្ជកម្ម (១០ វិនាទី)", 
                    url=ADSTERRA_LINK
                )
                check_button = InlineKeyboardButton(
                    text="✅ រួចរាល់ - ទាញយកវីដេអូ",
                    callback_data="check_download"
                )
                markup.add(ad_button, check_button)
                
                # Update message - NO parse_mode
                bot.edit_message_text(
                    text=(
                        f"✅ រកឃើញវីដេអូហើយ!\n\n"
                        f"📝 ចំណងជើង: {result['description']}\n"
                        f"👤 អ្នកបង្ហោះ: {result['author']}\n"
                        f"⏱️ រយៈពេល: {result.get('duration', 0)}s\n\n"
                        f"ដើម្បីទាញយកវីដេអូ៖\n\n"
                        f"1️⃣ ចុចប៊ូតុង 👁️ មើលពាណិជ្ជកម្ម\n"
                        f"2️⃣ រង់ចាំ ១០ វិនាទី\n"
                        f"3️⃣ ចុច ✅ រួចរាល់ - ទាញយកវីដេអូ\n\n"
                        f"⚠️ អ្នកត្រូវរង់ចាំ ១០ វិនាទី មុនពេលទាញយក"
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
    
    def add_task(self, chat_id, message_id, tiktok_url, processing_msg):
        """Add a new download task to queue"""
        self.task_queue.put({
            'chat_id': chat_id,
            'message_id': message_id,
            'tiktok_url': tiktok_url,
            'processing_msg': processing_msg
        })

# Initialize worker
video_worker = VideoDownloadWorker()

# ==================== FLASK ROUTES ====================

@app.route('/', methods=['GET'])
def home():
    """Root endpoint"""
    return jsonify({
        'status': 'OK',
        'message': 'TikTok Downloader Bot is running',
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
        'queue_size': video_worker.task_queue.qsize(),
        'stored_videos': len(user_video_storage.storage)
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
    """Welcome message handler - NO parse_mode"""
    welcome_text = (
        "🎥 សូមស្វាគមន៍មកកាន់ TikTok Video Downloader Bot!\n\n"
        "📱 របៀបប្រើប្រាស់:\n"
        "1️⃣ ផ្ញើតំណភ្ជាប់ TikTok មកកាន់ bot\n"
        "2️⃣ រង់ចាំបន្តិច (ប្រព័ន្ធកំពុងដំណើរការ)\n"
        "3️⃣ ចុចប៊ូតុង 👁️ មើលពាណិជ្ជកម្ម\n"
        "4️⃣ រង់ចាំ ១០ វិនាទី\n"
        "5️⃣ ចុច ✅ រួចរាល់ - ទាញយកវីដេអូ\n\n"
        "✨ លក្ខណៈពិសេស:\n"
        "• គ្មានស្លាកសញ្ញា TikTok\n"
        "• គុណភាព HD\n"
        "• គាំទ្រវីដេអូវែង (រហូតដល់ ៣ នាទី)\n"
        "• ដំណើរការផ្ទៃខាងក្រោយ\n\n"
        "🔗 ឧទាហរណ៍តំណភ្ជាប់:\n"
        "https://www.tiktok.com/@user/video/123456789\n\n"
        "ផ្ញើតំណភ្ជាប់ TikTok មកខ្ញុំដើម្បីចាប់ផ្តើម! 🚀"
    )
    bot.reply_to(message, welcome_text)

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    """Handle user messages containing TikTok links"""
    chat_id = message.chat.id
    
    # Show typing indicator
    bot.send_chat_action(chat_id, 'typing')
    
    # Clean up old storage
    expired = user_video_storage.cleanup_old()
    if expired:
        logger.info(f"Cleaned up {expired} expired entries")
    
    # Extract TikTok URL
    tiktok_url = extract_tiktok_url(message.text)
    
    if not tiktok_url:
        bot.reply_to(
            message, 
            "❌ សូមផ្ញើតំណភ្ជាប់ TikTok ត្រឹមត្រូវ។\n\n"
            "ឧទាហរណ៍: https://www.tiktok.com/@user/video/123456789"
        )
        return
    
    # Send processing message - NO parse_mode
    processing_msg = bot.reply_to(
        message, 
        "🔄 កំពុងដំណើរការតំណភ្ជាប់របស់អ្នក...\n\n"
        "⏱️ រង់ចាំបន្តិច (អាចចំណាយពេលដល់ទៅ ៦០ វិនាទី សម្រាប់វីដេអូវែង)"
    )
    
    # Add to background queue
    video_worker.add_task(
        chat_id=chat_id,
        message_id=processing_msg.message_id,
        tiktok_url=tiktok_url,
        processing_msg=processing_msg
    )
    
    logger.info(f"📦 Added task to queue for chat_id: {chat_id}")

@bot.callback_query_handler(func=lambda call: call.data == 'check_download')
def handle_check_download(call):
    """Handle the check download button callback with time verification"""
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    current_time = time.time()
    
    try:
        logger.info(f"✅ Check download clicked for chat_id: {chat_id}")
        
        # Get video data
        video_data = user_video_storage.get(chat_id)
        
        if not video_data:
            bot.answer_callback_query(
                call.id,
                text="❌ វីដេអូផុតកំណត់ហើយ! សូមផ្ញើតំណភ្ជាប់ម្តងទៀត។",
                show_alert=True
            )
            return
        
        # Calculate wait time
        time_diff = current_time - video_data['timestamp']
        
        if time_diff < REQUIRED_WAIT_TIME:
            remaining = int(REQUIRED_WAIT_TIME - time_diff) + 1
            bot.answer_callback_query(
                call.id,
                text=f"⚠️ សូមរង់ចាំ {remaining} វិនាទីទៀត!",
                show_alert=True
            )
            return
        
        # Sufficient time passed
        bot.answer_callback_query(
            call.id,
            text="កំពុងផ្ញើវីដេអូ... សូមរង់ចាំ",
            show_alert=False
        )
        
        # Update message - NO parse_mode
        bot.edit_message_text(
            text="⏳ កំពុងផ្ញើវីដេអូ...\n\nសូមរង់ចាំបន្តិច...",
            chat_id=chat_id,
            message_id=message_id
        )
        
        # Create caption - video_info fields are already escaped
        video_info = video_data['video_info']
        caption = (
            f"🎥 TikTok Video Downloaded\n\n"
            f"📝 ចំណងជើង: {video_info['description']}\n"
            f"👤 អ្នកបង្ហោះ: {video_info['author']}\n"
            f"⏱️ រយៈពេល: {video_info.get('duration', 0)}s\n\n"
            f"✅ ទាញយកដោយជោគជ័យ!"
        )
        
        # Send video
        if send_video_to_chat(chat_id, video_data['video_content'], caption):
            # Success - NO parse_mode
            bot.edit_message_text(
                text=(
                    f"✅ វីដេអូត្រូវបានផ្ញើដោយជោគជ័យ! 🎉\n\n"
                    f"📝 ចំណងជើង: {video_info['description']}\n\n"
                    f"ផ្ញើតំណភ្ជាប់ថ្មីដើម្បីទាញយកបន្ត!"
                ),
                chat_id=chat_id,
                message_id=message_id
            )
            
            # Clean up
            user_video_storage.delete(chat_id)
            
        else:
            # Failed - NO parse_mode
            bot.edit_message_text(
                text="❌ បរាជ័យក្នុងការផ្ញើវីដេអូ\n\nសូមព្យាយាមម្តងទៀត។",
                chat_id=chat_id,
                message_id=message_id
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
    logger.info("🚀 Starting TikTok Downloader Bot locally...")
    logger.info(f"🌐 Domain: {DOMAIN}")
    logger.info(f"📡 Webhook URL: {DOMAIN}/webhook")
    logger.info(f"⏱️ Video timeout: 60 seconds")
    logger.info(f"👥 Worker threads: 3")
    logger.info("✅ parse_mode='Markdown' removed from all messages")
    
    # Remove webhook for local testing
    bot.remove_webhook()
    logger.info("✅ Webhook removed for local testing")
    
    # Start Flask
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)