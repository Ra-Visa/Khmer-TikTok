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
from urllib.parse import quote, unquote, parse_qs, urlparse
from queue import Queue
import sys

# ==================== CONFIGURATION ====================
BOT_TOKEN = "8771490616:AAHLguFzc28SvbKZNUDS5_9KscJ_Ko8FRKs"
RAPIDAPI_KEY = "8e126a962emshf6305bb2fe26993p14eeecjsn3438579f250c"
RAPIDAPI_HOST = "youtube-mp3-audio-video-downloader.p.rapidapi.com"
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
def extract_youtube_url(text):
    """Extract YouTube URL from text"""
    # Pattern for various YouTube URL formats
    patterns = [
        r'(https?://(?:www\.)?youtube\.com/watch\?v=[\w-]+)',
        r'(https?://(?:www\.)?youtube\.com/shorts/[\w-]+)',
        r'(https?://(?:www\.)?youtu\.be/[\w-]+)',
        r'(https?://(?:www\.)?youtube\.com/embed/[\w-]+)',
        r'(https?://(?:www\.)?youtube\.com/v/[\w-]+)'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    
    return None

def extract_video_id(youtube_url):
    """Extract video ID from YouTube URL"""
    parsed_url = urlparse(youtube_url)
    
    if parsed_url.hostname in ['youtu.be', 'www.youtu.be']:
        return parsed_url.path[1:]
    
    if parsed_url.hostname in ['youtube.com', 'www.youtube.com', 'm.youtube.com']:
        if parsed_url.path == '/watch':
            query_params = parse_qs(parsed_url.query)
            return query_params.get('v', [None])[0]
        elif parsed_url.path.startswith('/embed/') or parsed_url.path.startswith('/v/'):
            return parsed_url.path.split('/')[2]
        elif parsed_url.path.startswith('/shorts/'):
            return parsed_url.path.split('/')[2]
    
    return None

def safe_text(text):
    """Escape special characters for Telegram HTML parsing"""
    if text is None:
        return ""
    return html.escape(str(text))

def download_youtube_content(youtube_url, content_type='video', timeout=60):
    """
    Download YouTube video or audio using RapidAPI
    content_type: 'video' for MP4, 'audio' for MP3
    """
    try:
        logger.info(f"📥 Downloading YouTube {content_type} from: {youtube_url}")
        
        # Extract video ID
        video_id = extract_video_id(youtube_url)
        if not video_id:
            return {'success': False, 'error': 'Invalid YouTube URL'}
        
        url = f"https://{RAPIDAPI_HOST}/"
        
        # Set parameters based on content type
        if content_type == 'audio':
            querystring = {"url": youtube_url, "format": "mp3"}
        else:
            querystring = {"url": youtube_url, "format": "mp4"}
        
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
        
        if data.get('status') != 'ok' and not data.get('downloadUrl'):
            return {'success': False, 'error': 'No download link found'}
        
        # Get download URL
        download_url = data.get('downloadUrl')
        title = data.get('title', 'YouTube Video')
        duration = data.get('duration', 0)
        
        if not download_url:
            return {'success': False, 'error': 'No download URL found'}
        
        # Step 2: Download actual content with streaming
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        
        # Use stream=True for large files
        content_response = requests.get(
            download_url, 
            timeout=timeout,
            headers=headers,
            stream=True
        )
        
        if content_response.status_code != 200:
            return {'success': False, 'error': f'Download failed: {content_response.status_code}'}
        
        # Read content in chunks for large files
        content_data = b''
        for chunk in content_response.iter_content(chunk_size=8192):
            if chunk:
                content_data += chunk
        
        file_extension = 'mp3' if content_type == 'audio' else 'mp4'
        
        return {
            'success': True,
            'content': content_data,
            'title': safe_text(title[:100]),
            'duration': duration,
            'video_id': video_id,
            'content_type': content_type,
            'file_extension': file_extension
        }
        
    except requests.exceptions.Timeout:
        return {'success': False, 'error': '⏱️ Request timeout (60s). The video might be too long.'}
    except requests.exceptions.ConnectionError:
        return {'success': False, 'error': '🔌 Connection error. Please check your internet.'}
    except Exception as e:
        logger.error(f"Download error: {e}")
        return {'success': False, 'error': f'❌ Error: {str(e)}'}

def send_content_to_chat(chat_id, content_data, filename, caption=""):
    """Send video or audio file to Telegram chat"""
    try:
        from io import BytesIO
        
        # Create file-like object
        content_file = BytesIO(content_data)
        content_file.name = filename
        
        # Show upload action
        if filename.endswith('.mp3'):
            bot.send_chat_action(chat_id, 'upload_audio')
        else:
            bot.send_chat_action(chat_id, 'upload_video')
        
        # Send based on file type
        if filename.endswith('.mp3'):
            bot.send_audio(
                chat_id=chat_id,
                audio=content_file,
                caption=caption,
                timeout=180,
                title=filename.replace('.mp3', '')
            )
        else:
            bot.send_video(
                chat_id=chat_id,
                video=content_file,
                caption=caption,
                timeout=180,
                supports_streaming=True
            )
        return True
        
    except Exception as e:
        logger.error(f"Failed to send content: {e}")
        try:
            # Fallback: Send as document
            from io import BytesIO
            content_file = BytesIO(content_data)
            content_file.name = filename
            
            bot.send_document(
                chat_id=chat_id,
                document=content_file,
                caption=caption + "\n\n📁 Sent as file",
                timeout=180
            )
            return True
        except Exception as e2:
            logger.error(f"Failed to send as document: {e2}")
            return False

# ==================== BACKGROUND PROCESSING ====================
class YouTubeDownloadWorker:
    """Handles YouTube downloading in background threads"""
    
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
        logger.info(f"Started {num_workers} YouTube download workers")
    
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
        youtube_url = task['youtube_url']
        processing_msg = task['processing_msg']
        content_type = task.get('content_type', 'video')
        
        try:
            # Download content with timeout
            result = download_youtube_content(youtube_url, content_type, timeout=60)
            
            if result['success']:
                # Store content data
                user_video_storage.set(chat_id, {
                    'content': result['content'],
                    'content_info': {
                        'title': result['title'],
                        'duration': result.get('duration', 0),
                        'content_type': result['content_type'],
                        'file_extension': result['file_extension']
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
                    text="✅ រួចរាល់ - ទាញយក",
                    callback_data="check_download"
                )
                markup.add(ad_button, check_button)
                
                content_type_display = "វីដេអូ" if content_type == 'video' else "MP3"
                
                # Update message - NO parse_mode
                bot.edit_message_text(
                    text=(
                        f"✅ រកឃើញ {content_type_display} ហើយ!\n\n"
                        f"📝 ចំណងជើង: {result['title']}\n"
                        f"⏱️ រយៈពេល: {result.get('duration', 0)}s\n\n"
                        f"ដើម្បីទាញយក៖\n\n"
                        f"1️⃣ ចុចប៊ូតុង 👁️ មើលពាណិជ្ជកម្ម\n"
                        f"2️⃣ រង់ចាំ ១០ វិនាទី\n"
                        f"3️⃣ ចុច ✅ រួចរាល់ - ទាញយក\n\n"
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
    
    def add_task(self, chat_id, message_id, youtube_url, processing_msg, content_type='video'):
        """Add a new download task to queue"""
        self.task_queue.put({
            'chat_id': chat_id,
            'message_id': message_id,
            'youtube_url': youtube_url,
            'processing_msg': processing_msg,
            'content_type': content_type
        })

# Initialize worker
youtube_worker = YouTubeDownloadWorker()

# ==================== FLASK ROUTES ====================

@app.route('/', methods=['GET'])
def home():
    """Root endpoint"""
    return jsonify({
        'status': 'OK',
        'message': 'YouTube Downloader Bot is running',
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
        'queue_size': youtube_worker.task_queue.qsize(),
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
        "🎥 សូមស្វាគមន៍មកកាន់ YouTube Downloader Bot!\n\n"
        "📱 របៀបប្រើប្រាស់:\n"
        "1️⃣ ផ្ញើតំណភ្ជាប់ YouTube មកកាន់ bot\n"
        "2️⃣ ជ្រើសរើសប្រភេទដែលចង់ទាញយក (វីដេអូ ឬ MP3)\n"
        "3️⃣ ចុចប៊ូតុង 👁️ មើលពាណិជ្ជកម្ម\n"
        "4️⃣ រង់ចាំ ១០ វិនាទី\n"
        "5️⃣ ចុច ✅ រួចរាល់ - ទាញយក\n\n"
        "✨ លក្ខណៈពិសេស:\n"
        "• ទាញយកវីដេអូ YouTube (MP4)\n"
        "• ទាញយកសំឡេង (MP3)\n"
        "• គុណភាព HD\n"
        "• គាំទ្រវីដេអូវែង (រហូតដល់ ៣ នាទី)\n"
        "• ដំណើរការផ្ទៃខាងក្រោយ\n\n"
        "🔗 ឧទាហរណ៍តំណភ្ជាប់:\n"
        "https://www.youtube.com/watch?v=VIDEO_ID\n"
        "https://youtu.be/VIDEO_ID\n"
        "https://youtube.com/shorts/VIDEO_ID\n\n"
        "ផ្ញើតំណភ្ជាប់ YouTube មកខ្ញុំដើម្បីចាប់ផ្តើម! 🚀"
    )
    bot.reply_to(message, welcome_text)

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    """Handle user messages containing YouTube links"""
    chat_id = message.chat.id
    
    # Show typing indicator
    bot.send_chat_action(chat_id, 'typing')
    
    # Clean up old storage
    expired = user_video_storage.cleanup_old()
    if expired:
        logger.info(f"Cleaned up {expired} expired entries")
    
    # Extract YouTube URL
    youtube_url = extract_youtube_url(message.text)
    
    if not youtube_url:
        bot.reply_to(
            message, 
            "❌ សូមផ្ញើតំណភ្ជាប់ YouTube ត្រឹមត្រូវ។\n\n"
            "ឧទាហរណ៍: https://www.youtube.com/watch?v=VIDEO_ID"
        )
        return
    
    # Create inline keyboard for format selection
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    markup = InlineKeyboardMarkup(row_width=2)
    video_button = InlineKeyboardButton(
        text="🎬 វីដេអូ (MP4)",
        callback_data=f"video_{youtube_url}"
    )
    audio_button = InlineKeyboardButton(
        text="🎵 MP3",
        callback_data=f"audio_{youtube_url}"
    )
    markup.add(video_button, audio_button)
    
    # Send format selection message
    bot.reply_to(
        message,
        "📥 សូមជ្រើសរើសប្រភេទដែលអ្នកចង់ទាញយក:",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith(('video_', 'audio_')))
def handle_format_selection(call):
    """Handle format selection callback"""
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    
    # Parse callback data
    content_type = 'video' if call.data.startswith('video_') else 'audio'
    youtube_url = call.data.replace('video_', '').replace('audio_', '')
    
    # Answer callback query
    bot.answer_callback_query(call.id)
    
    # Update message to show processing
    bot.edit_message_text(
        text=f"🔄 កំពុងដំណើរការ... សូមរង់ចាំ (អាចចំណាយពេលដល់ ៦០ វិនាទី)",
        chat_id=chat_id,
        message_id=message_id
    )
    
    # Send processing message
    processing_msg = bot.send_message(
        chat_id,
        f"🔄 កំពុងទាញយក{'វីដេអូ' if content_type == 'video' else 'MP3'}..."
    )
    
    # Add to background queue
    youtube_worker.add_task(
        chat_id=chat_id,
        message_id=processing_msg.message_id,
        youtube_url=youtube_url,
        processing_msg=processing_msg,
        content_type=content_type
    )
    
    logger.info(f"📦 Added {content_type} task to queue for chat_id: {chat_id}")

@bot.callback_query_handler(func=lambda call: call.data == 'check_download')
def handle_check_download(call):
    """Handle the check download button callback with time verification"""
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    current_time = time.time()
    
    try:
        logger.info(f"✅ Check download clicked for chat_id: {chat_id}")
        
        # Get content data
        content_data = user_video_storage.get(chat_id)
        
        if not content_data:
            bot.answer_callback_query(
                call.id,
                text="❌ ឯកសារផុតកំណត់ហើយ! សូមផ្ញើតំណភ្ជាប់ម្តងទៀត។",
                show_alert=True
            )
            return
        
        # Calculate wait time
        time_diff = current_time - content_data['timestamp']
        
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
            text="កំពុងផ្ញើឯកសារ... សូមរង់ចាំ",
            show_alert=False
        )
        
        # Update message - NO parse_mode
        bot.edit_message_text(
            text="⏳ កំពុងផ្ញើឯកសារ...\n\nសូមរង់ចាំបន្តិច...",
            chat_id=chat_id,
            message_id=message_id
        )
        
        # Create caption and filename
        content_info = content_data['content_info']
        filename = f"{content_info['title']}.{content_info['file_extension']}"
        
        caption = (
            f"🎥 YouTube Download\n\n"
            f"📝 ចំណងជើង: {content_info['title']}\n"
            f"⏱️ រយៈពេល: {content_info.get('duration', 0)}s\n\n"
            f"✅ ទាញយកដោយជោគជ័យ!"
        )
        
        # Send content
        if send_content_to_chat(chat_id, content_data['content'], filename, caption):
            # Success - NO parse_mode
            bot.edit_message_text(
                text=(
                    f"✅ ឯកសារត្រូវបានផ្ញើដោយជោគជ័យ! 🎉\n\n"
                    f"📝 ចំណងជើង: {content_info['title']}\n\n"
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
                text="❌ បរាជ័យក្នុងការផ្ញើឯកសារ\n\nសូមព្យាយាមម្តងទៀត។",
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
    logger.info("🚀 Starting YouTube Downloader Bot locally...")
    logger.info(f"🌐 Domain: {DOMAIN}")
    logger.info(f"📡 Webhook URL: {DOMAIN}/webhook")
    logger.info(f"⏱️ Download timeout: 60 seconds")
    logger.info(f"👥 Worker threads: 3")
    logger.info("✅ TikTok functionality removed")
    logger.info("✅ YouTube-only mode enabled")
    
    # Remove webhook for local testing
    bot.remove_webhook()
    logger.info("✅ Webhook removed for local testing")
    
    # Start Flask
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)