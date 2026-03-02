import telebot
from flask import Flask, request, jsonify
import requests
import logging
from urllib.parse import quote, unquote
import re
import os
from datetime import datetime
import sys
import json
import time

# ==================== CONFIGURATION ====================
BOT_TOKEN = "8771490616:AAHLguFzc28SvbKZNUDS5_9KscJ_Ko8FRKs"
RAPIDAPI_KEY = "8e126a962emshf6305bb2fe26993p14eeecjsn3438579f250c"
ADSTERRA_LINK = "https://www.effectivegatecpm.com/hmc3n4g9?key=633ca2e22b9bf9e4fd318f9df03b032a"  # Replace with your actual Adsterra link
DOMAIN = "https://khmer-tiktok.onrender.com"  # Your Render domain

# Get port from environment variable
PORT = int(os.environ.get('PORT', 5000))

# ==================== INITIALIZATION ====================
bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== TEMPORARY STORAGE ====================
# Store video data temporarily (in production, use Redis or database)
video_storage = {}
# Structure: {unique_id: {'chat_id': chat_id, 'video_data': video_data, 'timestamp': time.time()}}

# Cleanup old entries every hour (optional)
def cleanup_old_storage():
    """Remove entries older than 1 hour"""
    current_time = time.time()
    expired = [vid for vid, data in video_storage.items() 
               if current_time - data.get('timestamp', 0) > 3600]
    for vid in expired:
        del video_storage[vid]
    if expired:
        logger.info(f"Cleaned up {len(expired)} expired entries")

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
    
    url_pattern = r'(https?://[^\s]+tiktok[^\s]+)'
    match = re.search(url_pattern, text)
    return match.group(0) if match else None

def download_tiktok_video(tiktok_url):
    """Download TikTok video without watermark using RapidAPI"""
    try:
        logger.info(f"📥 Downloading video from: {tiktok_url}")
        
        url = "https://tiktok-scraper7.p.rapidapi.com/"
        querystring = {"url": tiktok_url, "hd": "1"}
        
        headers = {
            "X-RapidAPI-Key": RAPIDAPI_KEY,
            "X-RapidAPI-Host": "tiktok-scraper7.p.rapidapi.com"
        }
        
        response = requests.get(url, headers=headers, params=querystring, timeout=30)
        
        if response.status_code == 200:
            data = response.json()
            
            if data.get('code') == 0 and data.get('data'):
                video_data = data['data']
                
                video_url = (
                    video_data.get('hdplay') or 
                    video_data.get('play') or 
                    video_data.get('wmplay') or
                    video_data.get('video_url') or
                    video_data.get('download_url')
                )
                
                if video_url:
                    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                    video_response = requests.get(video_url, timeout=45, headers=headers)
                    
                    if video_response.status_code == 200:
                        video_content = video_response.content
                        
                        return {
                            'success': True,
                            'video_content': video_content,
                            'video_url': video_url,
                            'description': video_data.get('title', 'TikTok Video')[:100],
                            'author': video_data.get('author', {}).get('nickname', 'Unknown'),
                            'duration': video_data.get('duration', 0),
                            'cover': video_data.get('cover', '')
                        }
            
            return {'success': False, 'error': 'No video found in response'}
        
        elif response.status_code == 429:
            return {'success': False, 'error': '⚠️ API quota limit exceeded. Please try again later.'}
        else:
            return {'success': False, 'error': f'API Error: {response.status_code}'}
            
    except requests.exceptions.Timeout:
        return {'success': False, 'error': '⏱️ Request timeout. Please try again.'}
    except requests.exceptions.ConnectionError:
        return {'success': False, 'error': '🔌 Connection error. Please check your internet.'}
    except Exception as e:
        logger.error(f"Download error: {e}")
        return {'success': False, 'error': f'❌ Error: {str(e)}'}

def generate_unique_id():
    """Generate a unique ID for storing video data"""
    import hashlib
    timestamp = str(time.time()) + str(os.urandom(16))
    return hashlib.md5(timestamp.encode()).hexdigest()[:16]

def send_video_to_chat(chat_id, video_content, caption=""):
    """Send video file to Telegram chat"""
    try:
        from io import BytesIO
        video_file = BytesIO(video_content)
        video_file.name = 'tiktok_video.mp4'
        
        bot.send_chat_action(chat_id, 'upload_video')
        
        bot.send_video(
            chat_id=chat_id,
            video=video_file,
            caption=caption,
            timeout=120,
            supports_streaming=True
        )
        return True
    except Exception as e:
        logger.error(f"Failed to send video: {e}")
        try:
            from io import BytesIO
            video_file = BytesIO(video_content)
            video_file.name = 'tiktok_video.mp4'
            
            bot.send_document(
                chat_id=chat_id,
                document=video_file,
                caption=caption + "\n\n📁 Sent as file",
                timeout=120
            )
            return True
        except Exception as e2:
            logger.error(f"Failed to send as document: {e2}")
            return False

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
        'stored_videos': len(video_storage)
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

@app.route('/callback/<unique_id>', methods=['GET'])
def callback(unique_id):
    """Callback endpoint when user clicks the check button"""
    try:
        # Retrieve video data from storage
        video_data = video_storage.get(unique_id)
        
        if not video_data:
            return '''
            <html>
                <head><title>Error</title></head>
                <body style="font-family: Arial; text-align: center; padding: 50px;">
                    <h2 style="color: red;">❌ Video expired or not found</h2>
                    <p>Please send the TikTok link again to the bot.</p>
                    <p><a href="https://t.me/khmer_tiktok_bot">Go to Bot</a></p>
                </body>
            </html>
            ''', 404
        
        chat_id = video_data['chat_id']
        video_content = video_data['video_content']
        video_info = video_data['video_info']
        
        # Create caption
        caption = (
            f"🎥 **TikTok Video Downloaded**\n\n"
            f"📝 **Title:** {video_info['description']}\n"
            f"👤 **Author:** {video_info['author']}\n"
            f"⏱️ **Duration:** {video_info.get('duration', 0)}s\n\n"
            f"✅ Downloaded without watermark!"
        )
        
        # Send video to user
        if send_video_to_chat(chat_id, video_content, caption):
            # Clean up storage
            del video_storage[unique_id]
            
            return '''
            <html>
                <head><title>Success</title></head>
                <body style="font-family: Arial; text-align: center; padding: 50px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white;">
                    <h2>✅ Video sent to Telegram!</h2>
                    <p>Check your chat with @khmer_tiktok_bot</p>
                    <p>You can close this window.</p>
                </body>
            </html>
            ''', 200
        else:
            return '''
            <html>
                <head><title>Error</title></head>
                <body style="font-family: Arial; text-align: center; padding: 50px;">
                    <h2 style="color: red;">❌ Failed to send video</h2>
                    <p>Please try again or contact support.</p>
                </body>
            </html>
            ''', 500
            
    except Exception as e:
        logger.error(f"Callback error: {e}")
        return '''
        <html>
            <head><title>Error</title></head>
            <body style="font-family: Arial; text-align: center; padding: 50px;">
                <h2 style="color: red;">❌ Internal error</h2>
                <p>Please try again later.</p>
            </body>
        </html>
        ''', 500

# ==================== TELEGRAM BOT HANDLERS ====================

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    """Welcome message handler"""
    welcome_text = """
🎥 **សូមស្វាគមន៍មកកាន់ TikTok Video Downloader Bot!**

**📱 របៀបប្រើប្រាស់:**
1️⃣ ផ្ញើតំណភ្ជាប់ TikTok មកកាន់ bot
2️⃣ ចុចប៊ូតុង [ကြည့်​ពាណិជ្ជកម្ម] ដើម្បីមើលពាណិជ្ជកម្មរយៈពេល ៥ វិនាទី
3️⃣ បន្ទាប់ពីមើលពាណិជ្ជកម្មរួច ចុចប៊ូតុង [ឆែកមើល និងទាញយក]
4️⃣ Bot នឹងផ្ញើវីដេអូមកអ្នកដោយស្វ័យប្រវត្តិ

**✨ លក្ខណៈពិសេស:**
• គ្មានស្លាកសញ្ញា TikTok
• គុណភាព HD
• ដំណើរការលឿន
• ប្រើដោយឥតគិតថ្លៃ

**🔗 ឧទាហរណ៍តំណភ្ជាប់:**
`https://www.tiktok.com/@user/video/123456789`

ផ្ញើតំណភ្ជាប់ TikTok មកខ្ញុំដើម្បីចាប់ផ្តើម! 🚀
    """
    bot.reply_to(message, welcome_text, parse_mode='Markdown')

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    """Handle user messages containing TikTok links"""
    chat_id = message.chat.id
    bot.send_chat_action(chat_id, 'typing')
    
    # Clean up old storage periodically
    cleanup_old_storage()
    
    # Extract TikTok URL
    tiktok_url = extract_tiktok_url(message.text)
    
    if not tiktok_url:
        bot.reply_to(
            message, 
            "❌ សូមផ្ញើតំណភ្ជាប់ TikTok ត្រឹមត្រូវ។\n\n"
            "ឧទាហរណ៍: https://www.tiktok.com/@user/video/123456789"
        )
        return
    
    # Send processing message
    processing_msg = bot.reply_to(
        message, 
        "🔄 **កំពុងដំណើរការតំណភ្ជាប់របស់អ្នក...**\n\n"
        "⏱️ សូមរង់ចាំបន្តិច",
        parse_mode='Markdown'
    )
    
    # Download the video
    result = download_tiktok_video(tiktok_url)
    
    if not result['success']:
        bot.edit_message_text(
            f"❌ **កំហុស:** {result['error']}\n\n"
            f"សូមព្យាយាមម្តងទៀត ឬប្រើវីដេអូផ្សេង។",
            chat_id=chat_id,
            message_id=processing_msg.message_id,
            parse_mode='Markdown'
        )
        return
    
    # Generate unique ID for this video
    unique_id = generate_unique_id()
    
    # Store video data temporarily
    video_storage[unique_id] = {
        'chat_id': chat_id,
        'video_content': result['video_content'],
        'video_info': {
            'description': result['description'],
            'author': result['author'],
            'duration': result.get('duration', 0)
        },
        'timestamp': time.time()
    }
    
    logger.info(f"📦 Stored video with ID: {unique_id} for chat {chat_id}")
    
    # Create inline keyboard with two buttons
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    markup = InlineKeyboardMarkup(row_width=1)
    
    # Button 1: Adsterra Direct Link (View Ad)
    ad_button = InlineKeyboardButton(
        text="ကြည့်​ពាណិជ្ជកម្ម", 
        url=ADSTERRA_LINK
    )
    
    # Button 2: Check and Download (Callback)
    check_button = InlineKeyboardButton(
        text="ឆែកមើល និងទាញយក",
        callback_data=f"check_{unique_id}"
    )
    
    markup.add(ad_button, check_button)
    
    # Send message with buttons
    bot.edit_message_text(
        text=(
            f"✅ **រកឃើញវីដេអូហើយ!**\n\n"
            f"📝 **ចំណងជើង:** {result['description']}\n"
            f"👤 **អ្នកបង្ហោះ:** {result['author']}\n\n"
            f"**សូមមើលពាណិជ្ជកម្ម ៥ វិនាទី ដើម្បីដោះសោវីដេអូ!**\n\n"
            f"1. ចុចប៊ូតុង [ကြည့်​ពាណិជ្ជកម្ម] ដើម្បីមើលពាណិជ្ជកម្ម\n"
            f"2. បន្ទាប់មកចុចប៊ូតុង [ឆែកមើល និងទាញយក]\n\n"
            f"⚠️ *វីដេអូនឹងផុតកំណត់ក្នុងរយៈពេល ១ ម៉ោង*"
        ),
        chat_id=chat_id,
        message_id=processing_msg.message_id,
        reply_markup=markup,
        parse_mode='Markdown'
    )

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    """Handle callback queries from inline buttons"""
    try:
        chat_id = call.message.chat.id
        message_id = call.message.message_id
        
        # Extract unique_id from callback data
        if call.data.startswith('check_'):
            unique_id = call.data.replace('check_', '')
            
            logger.info(f"🔍 Check button clicked for ID: {unique_id}")
            
            # Retrieve video data
            video_data = video_storage.get(unique_id)
            
            if not video_data:
                bot.answer_callback_query(
                    call.id,
                    text="❌ វីដេអូផុតកំណត់ហើយ! សូមផ្ញើតំណភ្ជាប់ម្តងទៀត។",
                    show_alert=True
                )
                
                # Update message
                bot.edit_message_text(
                    text="❌ **វីដេអូផុតកំណត់ហើយ!**\n\nសូមផ្ញើតំណភ្ជាប់ TikTok ម្តងទៀត។",
                    chat_id=chat_id,
                    message_id=message_id,
                    parse_mode='Markdown'
                )
                return
            
            # Check if this is the same user
            if video_data['chat_id'] != chat_id:
                bot.answer_callback_query(
                    call.id,
                    text="❌ នេះមិនមែនជាវីដេអូរបស់អ្នកទេ។",
                    show_alert=True
                )
                return
            
            # Create direct link to callback endpoint
            callback_url = f"{DOMAIN}/callback/{unique_id}"
            
            # Answer callback query with a URL
            bot.answer_callback_query(
                call.id,
                text="កំពុងដំណើរការ... សូមរង់ចាំ",
                url=callback_url  # This will open the URL in user's browser
            )
            
            # Update message to show it's processing
            bot.edit_message_text(
                text=(
                    f"✅ **កំពុងដំណើរការ...**\n\n"
                    f"សូមរង់ចាំបន្តិច ប្រព័ន្ធកំពុងផ្ញើវីដេអូមកកាន់ Telegram របស់អ្នក។\n\n"
                    f"ប្រសិនបើមិនទទួលបានវីដេអូទេ សូមចុចតំណខាងក្រោម:\n"
                    f"{callback_url}"
                ),
                chat_id=chat_id,
                message_id=message_id,
                parse_mode='Markdown'
            )
            
        else:
            bot.answer_callback_query(call.id, text="មិនស្គាល់ពាក្យបញ្ជា")
            
    except Exception as e:
        logger.error(f"Callback handler error: {e}")
        bot.answer_callback_query(
            call.id,
            text="❌ មានបញ្ហាបច្ចេកទេស។ សូមព្យាយាមម្តងទៀត។",
            show_alert=True
        )

# ==================== MAIN ====================
if __name__ != '__main__':
    # When imported by Gunicorn
    gunicorn_logger = logging.getLogger('gunicorn.error')
    app.logger.handlers = gunicorn_logger.handlers
    app.logger.setLevel(gunicorn_logger.level)

if __name__ == '__main__':
    """Run the app directly (for local testing)"""
    logger.info("🚀 Starting TikTok Downloader Bot locally...")
    logger.info(f"🌐 Domain: {DOMAIN}")
    logger.info(f"📡 Webhook URL: {DOMAIN}/webhook")
    logger.info(f"🔍 Health check: {DOMAIN}/health")
    logger.info(f"📦 Adsterra Link: {ADSTERRA_LINK}")
    
    # Remove webhook for local testing
    bot.remove_webhook()
    
    # Start Flask
    app.run(host='0.0.0.0', port=PORT, debug=False)