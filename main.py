import telebot
from flask import Flask, request, jsonify
import requests
import logging
from urllib.parse import quote, unquote
import re
import os
from datetime import datetime
import sys

# ==================== CONFIGURATION ====================
BOT_TOKEN = "8771490616:AAHLguFzc28SvbKZNUDS5_9KscJ_Ko8FRKs"
RAPIDAPI_KEY = "8e126a962emshf6305bb2fe26993p14eeecjsn3438579f250c"
SHRINKME_API_KEY = "02dd9552b2fb55d6d38f5f3a2f9d4cf46498c404"
DOMAIN = "https://khmer-tiktok.onrender.com"  # Your Render domain

# Get port from environment variable (Render assigns this automatically)
PORT = int(os.environ.get('PORT', 5000))

# ==================== INITIALIZATION ====================
bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)  # Flask app object named 'app' for Gunicorn

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== NOTE: INTERNAL KEEP-ALIVE REMOVED ====================
# No threading or self-pinging - use external service like cron-job.org instead

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
                            'duration': video_data.get('duration', 0)
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

def create_ad_link(tiktok_url, chat_id):
    """Create shortened ad link using ShrinkMe.io API"""
    try:
        encoded_url = quote(tiktok_url)
        verification_url = f"{DOMAIN}/verify?chat_id={chat_id}&video_url={encoded_url}"
        
        # Try ShrinkMe.io
        api_url = "https://shrinkme.io/api"
        params = {'api': SHRINKME_API_KEY, 'url': verification_url}
        
        response = requests.get(api_url, params=params, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            if data.get('status') == 'success':
                shortened_url = data.get('shortenedUrl') or data.get('shortened')
                if shortened_url:
                    return {
                        'success': True,
                        'shortened_url': shortened_url,
                        'ad_url': shortened_url
                    }
        
        # Fallback
        fallback_url = f"{DOMAIN}/ad?chat_id={chat_id}&video_url={encoded_url}"
        return {
            'success': True,
            'shortened_url': fallback_url,
            'ad_url': fallback_url,
            'is_fallback': True
        }
        
    except Exception as e:
        logger.error(f"Ad link creation error: {e}")
        verification_url = f"{DOMAIN}/verify?chat_id={chat_id}&video_url={quote(tiktok_url)}"
        return {
            'success': True,
            'shortened_url': verification_url,
            'ad_url': verification_url,
            'is_fallback': True
        }

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
    """Root endpoint - Returns OK for external monitoring services"""
    return jsonify({
        'status': 'OK',
        'message': 'TikTok Downloader Bot is running',
        'bot_username': '@khmer_tiktok_bot',
        'health_check': 'Use this endpoint with cron-job.org every 10 minutes',
        'timestamp': datetime.now().isoformat()
    }), 200

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint for external monitoring"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'bot': 'running'
    }), 200

@app.route('/webhook', methods=['POST'])
def webhook():
    """Telegram bot webhook endpoint - handles incoming updates"""
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

@app.route('/verify', methods=['GET'])
def verify():
    """Verification endpoint after ad completion"""
    try:
        chat_id = request.args.get('chat_id')
        video_url = request.args.get('video_url')
        
        if not chat_id or not video_url:
            return 'Missing parameters', 400
        
        video_url = unquote(video_url)
        logger.info(f"🔄 Verification for chat {chat_id}")
        
        result = download_tiktok_video(video_url)
        
        if result['success']:
            caption = (
                f"🎥 **TikTok Video Downloaded**\n\n"
                f"📝 **Title:** {result['description']}\n"
                f"👤 **Author:** {result['author']}\n"
                f"⏱️ **Duration:** {result.get('duration', 0)}s\n\n"
                f"✅ Downloaded without watermark!"
            )
            
            if send_video_to_chat(chat_id, result['video_content'], caption):
                bot.send_message(
                    chat_id,
                    "✅ **Video sent successfully!** 🎉\n\nSend another TikTok link to download more!",
                    parse_mode='Markdown'
                )
                return jsonify({'status': 'success', 'message': 'Video sent'}), 200
            else:
                bot.send_message(chat_id, "❌ Failed to send video. Please try again.")
                return jsonify({'status': 'error', 'message': 'Failed to send'}), 500
        else:
            bot.send_message(chat_id, f"❌ Error: {result['error']}")
            return jsonify({'status': 'error', 'message': result['error']}), 500
            
    except Exception as e:
        logger.error(f"Verification error: {e}")
        return jsonify({'status': 'error', 'message': 'Internal error'}), 500

@app.route('/ad', methods=['GET'])
def ad_page():
    """Ad page with 5-second timer"""
    chat_id = request.args.get('chat_id')
    video_url = request.args.get('video_url')
    
    if not chat_id or not video_url:
        return 'Missing parameters', 400
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Advertisement - TikTok Downloader</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                margin: 0;
                padding: 20px;
            }}
            .container {{
                max-width: 500px;
                width: 100%;
                background: white;
                border-radius: 20px;
                padding: 40px 30px;
                box-shadow: 0 20px 60px rgba(0,0,0,0.3);
                text-align: center;
            }}
            h1 {{ color: #333; margin-bottom: 10px; }}
            .timer {{ font-size: 72px; color: #667eea; margin: 20px 0; }}
            .ad-space {{
                background: #f5f5f5;
                border-radius: 10px;
                padding: 20px;
                margin: 30px 0;
                border: 2px dashed #667eea;
            }}
            .button {{
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                border: none;
                padding: 15px 40px;
                border-radius: 50px;
                font-size: 18px;
                font-weight: bold;
                cursor: pointer;
                width: 100%;
                opacity: 0.5;
                pointer-events: none;
                transition: opacity 0.3s;
            }}
            .button.active {{
                opacity: 1;
                pointer-events: auto;
            }}
            .footer {{ color: #999; font-size: 12px; margin-top: 20px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🎥 TikTok Downloader</h1>
            <p>Please wait 5 seconds...</p>
            <div class="timer" id="timer">5</div>
            <div class="ad-space">
                <strong>Advertisement</strong><br>
                Support us by waiting
            </div>
            <button class="button" id="continueBtn" onclick="continueToVideo()">
                ⏳ Please wait...
            </button>
            <div class="footer">You'll be redirected automatically</div>
        </div>
        
        <script>
            let timeLeft = 5;
            const timer = document.getElementById('timer');
            const btn = document.getElementById('continueBtn');
            
            const countdown = setInterval(function() {{
                timeLeft--;
                timer.textContent = timeLeft;
                
                if (timeLeft <= 0) {{
                    clearInterval(countdown);
                    timer.textContent = "✓";
                    btn.innerHTML = "📥 Get Video Now";
                    btn.classList.add('active');
                    setTimeout(function() {{
                        window.location.href = '/verify?chat_id={chat_id}&video_url={video_url}';
                    }}, 1000);
                }}
            }}, 1000);
            
            function continueToVideo() {{
                if (timeLeft <= 0) {{
                    window.location.href = '/verify?chat_id={chat_id}&video_url={video_url}';
                }}
            }}
        </script>
    </body>
    </html>
    """
    return html

# ==================== TELEGRAM BOT HANDLERS ====================

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    """Welcome message handler"""
    welcome_text = """
🎥 **Welcome to TikTok Video Downloader Bot!**

**📱 How to use:**
1️⃣ Send me any TikTok video link
2️⃣ Click on the link I provide
3️⃣ Wait 5 seconds
4️⃣ I'll send you the video!

**✨ Features:**
• No watermark
• HD quality
• Fast processing
• Free to use

**🔗 Example:**
`https://www.tiktok.com/@user/video/123456789`

Send me a TikTok link to get started! 🚀
    """
    bot.reply_to(message, welcome_text, parse_mode='Markdown')

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    """Handle user messages containing TikTok links"""
    chat_id = message.chat.id
    bot.send_chat_action(chat_id, 'typing')
    
    tiktok_url = extract_tiktok_url(message.text)
    
    if not tiktok_url:
        bot.reply_to(message, "❌ Please send a valid TikTok video link.")
        return
    
    processing_msg = bot.reply_to(message, "🔄 Processing your link...")
    
    result = download_tiktok_video(tiktok_url)
    
    if not result['success']:
        bot.edit_message_text(
            f"❌ Error: {result['error']}",
            chat_id=chat_id,
            message_id=processing_msg.message_id
        )
        return
    
    ad_result = create_ad_link(tiktok_url, chat_id)
    
    if ad_result['success']:
        ad_message = (
            f"✅ **Video found!**\n\n"
            f"📝 **Title:** {result['description']}\n"
            f"👤 **Author:** {result['author']}\n\n"
            f"👇 **Click to download:**\n"
            f"{ad_result['ad_url']}\n\n"
            f"_Wait 5 seconds after clicking_"
        )
        
        bot.edit_message_text(
            ad_message,
            chat_id=chat_id,
            message_id=processing_msg.message_id,
            parse_mode='Markdown',
            disable_web_page_preview=False
        )
    else:
        bot.edit_message_text(
            f"❌ Failed to create link: {ad_result['error']}",
            chat_id=chat_id,
            message_id=processing_msg.message_id
        )

# ==================== MAIN ====================
if __name__ != '__main__':
    # When imported by Gunicorn, set up logging
    gunicorn_logger = logging.getLogger('gunicorn.error')
    app.logger.handlers = gunicorn_logger.handlers
    app.logger.setLevel(gunicorn_logger.level)

if __name__ == '__main__':
    """Run the app directly (for local testing)"""
    logger.info("🚀 Starting TikTok Downloader Bot locally...")
    logger.info(f"🌐 Domain: {DOMAIN}")
    logger.info(f"📡 Webhook URL: {DOMAIN}/webhook")
    logger.info(f"🔍 Health check: {DOMAIN}/health")
    logger.info("⚠️ Note: Use cron-job.org to ping /health endpoint")
    
    # Remove webhook for local testing
    bot.remove_webhook()
    
    # Start Flask
    app.run(host='0.0.0.0', port=PORT, debug=False)