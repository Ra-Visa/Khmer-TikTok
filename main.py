import telebot
from flask import Flask, request, jsonify
import requests
import threading
import time
import logging
from urllib.parse import quote, unquote
import re
import os
from datetime import datetime
import sys

# ==================== CONFIGURATION ====================
# Replace these with your actual values
BOT_TOKEN = "8771490616:AAHLguFzc28SvbKZNUDS5_9KscJ_Ko8FRKs"  # Get from @BotFather on Telegram
RAPIDAPI_KEY = "8e126a962emshf6305bb2fe26993p14eeecjsn3438579f250c"  # Your RapidAPI key
SHRINKME_API_KEY = "02dd9552b2fb55d6d38f5f3a2f9d4cf46498c404"  # Optional: Get from shrinkme.io
DOMAIN = "https://your-domain.com"  # Your deployed domain
PORT = 5000

# Validate bot token format
if BOT_TOKEN == "8771490616:AAHLguFzc28SvbKZNUDS5_9KscJ_Ko8FRKs":
    print("❌ ERROR: Please replace BOT_TOKEN with your actual Telegram bot token!")
    print("Get your token from @BotFather on Telegram")
    sys.exit(1)

if ':' not in BOT_TOKEN:
    print("❌ ERROR: Invalid bot token format! Token must contain a colon.")
    print("Example format: '1234567890:ABCdefGHIjklMNOpqrsTUVwxyz'")
    sys.exit(1)

# ==================== INITIALIZATION ====================
bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== KEEP-ALIVE MECHANISM ====================
def keep_alive():
    """Background thread to keep the application alive"""
    while True:
        try:
            # Ping self every 10 minutes
            requests.get(f"{DOMAIN}/health", timeout=5)
            logger.info("✅ Keep-alive ping sent")
        except Exception as e:
            logger.error(f"❌ Keep-alive error: {e}")
        time.sleep(600)  # 10 minutes

# Start keep-alive thread
threading.Thread(target=keep_alive, daemon=True).start()

# ==================== HELPER FUNCTIONS ====================
def extract_tiktok_url(text):
    """Extract TikTok URL from text"""
    # Pattern for various TikTok URL formats
    patterns = [
        r'(https?://(?:www\.)?tiktok\.com/@[\w.-]+/video/\d+)',
        r'(https?://(?:www\.)?tiktok\.com/\w+/video/\d+)',
        r'(https?://(?:www\.)?vm\.tiktok\.com/\w+)',
        r'(https?://(?:www\.)?vt\.tiktok\.com/\w+)'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    
    # Try to find any tiktok link
    url_pattern = r'(https?://[^\s]+tiktok[^\s]+)'
    match = re.search(url_pattern, text)
    return match.group(0) if match else None

def download_tiktok_video(tiktok_url):
    """Download TikTok video without watermark using RapidAPI"""
    try:
        logger.info(f"📥 Downloading video from: {tiktok_url}")
        
        url = f"https://{RAPIDAPI_HOST}/"
        
        querystring = {"url": tiktok_url, "hd": "1"}
        
        headers = {
            "X-RapidAPI-Key": RAPIDAPI_KEY,
            "X-RapidAPI-Host": RAPIDAPI_HOST
        }
        
        response = requests.get(url, headers=headers, params=querystring, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            
            # Check different response formats
            if data.get('code') == 0 and data.get('data'):
                video_data = data['data']
                
                # Try different video URL formats
                video_url = (
                    video_data.get('hdplay') or 
                    video_data.get('play') or 
                    video_data.get('wmplay') or
                    video_data.get('video_url')
                )
                
                if video_url:
                    # Download video content
                    video_response = requests.get(video_url, timeout=30, stream=True)
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
    """Create shortened ad link using ShrinkMe.io API or fallback"""
    try:
        # Prepare the verification callback URL
        encoded_url = quote(tiktok_url)
        verification_url = f"{DOMAIN}/verify?chat_id={chat_id}&video_url={encoded_url}"
        
        # Try ShrinkMe.io if API key is provided
        if SHRINKME_API_KEY and SHRINKME_API_KEY != "YOUR_SHRINKME_API_KEY":
            api_url = "https://shrinkme.io/api"
            params = {
                'api': SHRINKME_API_KEY,
                'url': verification_url
            }
            
            response = requests.get(api_url, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('status') == 'success':
                    return {
                        'success': True,
                        'shortened_url': data.get('shortenedUrl'),
                        'ad_url': data.get('shortenedUrl')
                    }
        
        # Fallback: Use our own ad page
        fallback_url = f"{DOMAIN}/ad?chat_id={chat_id}&video_url={encoded_url}"
        return {
            'success': True,
            'shortened_url': fallback_url,
            'ad_url': fallback_url,
            'is_fallback': True
        }
        
    except Exception as e:
        logger.error(f"Ad link creation error: {e}")
        # Ultimate fallback - return the verification URL directly
        return {
            'success': True,
            'shortened_url': verification_url,
            'ad_url': verification_url,
            'is_fallback': True
        }

def send_video_to_chat(chat_id, video_content, caption=""):
    """Send video file to Telegram chat"""
    try:
        # Create a temporary file-like object
        from io import BytesIO
        video_file = BytesIO(video_content)
        video_file.name = 'tiktok_video.mp4'
        
        # Send video with progress
        bot.send_chat_action(chat_id, 'upload_video')
        
        # Send video
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
            # Try sending as document if video fails
            from io import BytesIO
            video_file = BytesIO(video_content)
            video_file.name = 'tiktok_video.mp4'
            
            bot.send_document(
                chat_id=chat_id,
                document=video_file,
                caption=caption + "\n\n📁 Sent as file due to size/format"
            )
            return True
        except:
            return False

# ==================== FLASK ROUTES ====================
@app.route('/webhook', methods=['POST'])
def webhook():
    """Telegram bot webhook endpoint"""
    if request.headers.get('content-type') == 'application/json':
        try:
            json_str = request.get_data().decode('UTF-8')
            update = telebot.types.Update.de_json(json_str)
            bot.process_new_updates([update])
            return 'OK', 200
        except Exception as e:
            logger.error(f"Webhook error: {e}")
            return 'Error', 500
    return 'Invalid request', 403

@app.route('/verify', methods=['GET'])
def verify():
    """Verification endpoint after ad completion"""
    try:
        chat_id = request.args.get('chat_id')
        video_url = request.args.get('video_url')
        
        if not chat_id or not video_url:
            return 'Missing parameters', 400
        
        # Decode the URL
        video_url = unquote(video_url)
        
        logger.info(f"🔄 Verification requested for chat {chat_id}")
        
        # Download the video
        result = download_tiktok_video(video_url)
        
        if result['success']:
            # Create caption
            caption = (
                f"🎥 **TikTok Video Downloaded**\n\n"
                f"📝 {result['description']}\n"
                f"👤 Author: {result['author']}\n"
                f"⏱️ Duration: {result.get('duration', 0)}s\n\n"
                f"✅ Downloaded without watermark!"
            )
            
            # Send video to user
            if send_video_to_chat(chat_id, result['video_content'], caption):
                # Also send a success message to chat
                bot.send_message(
                    chat_id,
                    "✅ Video sent successfully! Enjoy! 🎉\n\nSend another TikTok link to download more!"
                )
                return '<h2>✅ Video sent to Telegram! You can close this window.</h2>', 200
            else:
                bot.send_message(
                    chat_id, 
                    "❌ Failed to send video. The file might be too large. Try another video."
                )
                return '<h2>❌ Failed to send video. Please try again.</h2>', 500
        else:
            bot.send_message(chat_id, f"❌ Error: {result['error']}")
            return f"<h2>Error: {result['error']}</h2>", 500
            
    except Exception as e:
        logger.error(f"Verification error: {e}")
        return '<h2>Internal server error</h2>', 500

@app.route('/ad', methods=['GET'])
def ad_page():
    """Simple ad page fallback with 5-second timer"""
    chat_id = request.args.get('chat_id')
    video_url = request.args.get('video_url')
    
    if not chat_id or not video_url:
        return 'Missing parameters', 400
    
    # HTML template with modern design
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Advertisement - TikTok Downloader</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }}
            
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 20px;
            }}
            
            .container {{
                max-width: 500px;
                width: 100%;
                background: rgba(255, 255, 255, 0.95);
                border-radius: 20px;
                padding: 40px 30px;
                box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
                text-align: center;
                backdrop-filter: blur(10px);
            }}
            
            h1 {{
                color: #333;
                font-size: 28px;
                margin-bottom: 10px;
            }}
            
            .subtitle {{
                color: #666;
                margin-bottom: 30px;
                font-size: 16px;
            }}
            
            .timer-circle {{
                width: 120px;
                height: 120px;
                margin: 20px auto;
                position: relative;
            }}
            
            .timer-text {{
                position: absolute;
                top: 50%;
                left: 50%;
                transform: translate(-50%, -50%);
                font-size: 48px;
                font-weight: bold;
                color: #667eea;
            }}
            
            svg {{
                transform: rotate(-90deg);
                width: 120px;
                height: 120px;
            }}
            
            circle {{
                fill: none;
                stroke: #667eea;
                stroke-width: 4;
                stroke-dasharray: 345;
                stroke-dashoffset: 0;
                transition: stroke-dashoffset 1s linear;
            }}
            
            .progress-bg {{
                stroke: #e0e0e0;
                stroke-width: 4;
            }}
            
            .ad-space {{
                background: #f5f5f5;
                border-radius: 10px;
                padding: 20px;
                margin: 30px 0;
                border: 2px dashed #667eea;
            }}
            
            .ad-text {{
                color: #999;
                font-size: 14px;
                text-transform: uppercase;
                letter-spacing: 1px;
                margin-bottom: 10px;
            }}
            
            .ad-content {{
                color: #333;
                font-size: 18px;
                font-weight: bold;
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
                transition: transform 0.3s, box-shadow 0.3s;
                text-decoration: none;
                display: inline-block;
                margin-top: 20px;
                opacity: 0.5;
                pointer-events: none;
            }}
            
            .button.active {{
                opacity: 1;
                pointer-events: auto;
            }}
            
            .button.active:hover {{
                transform: translateY(-2px);
                box-shadow: 0 10px 20px rgba(102, 126, 234, 0.4);
            }}
            
            .footer {{
                margin-top: 20px;
                color: #999;
                font-size: 12px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🎥 TikTok Video Downloader</h1>
            <p class="subtitle">Please wait while we prepare your video</p>
            
            <div class="timer-circle">
                <svg>
                    <circle class="progress-bg" cx="60" cy="60" r="55"></circle>
                    <circle id="progress" cx="60" cy="60" r="55" stroke-dashoffset="345"></circle>
                </svg>
                <div class="timer-text" id="timer">5</div>
            </div>
            
            <div class="ad-space">
                <div class="ad-text">Advertisement</div>
                <div class="ad-content">Support us by waiting 5 seconds</div>
            </div>
            
            <button class="button" id="continueBtn" onclick="continueToVideo()">
                ⏳ Please wait...
            </button>
            
            <div class="footer">
                You'll be redirected automatically after the ad
            </div>
        </div>
        
        <script>
            let timeLeft = 5;
            const totalTime = 5;
            const timerElement = document.getElementById('timer');
            const progressCircle = document.getElementById('progress');
            const continueBtn = document.getElementById('continueBtn');
            
            const circumference = 345; // 2 * π * r (r=55)
            
            function updateTimer() {{
                timerElement.textContent = timeLeft;
                
                // Update circle progress
                const progress = (timeLeft / totalTime) * circumference;
                progressCircle.style.strokeDashoffset = progress;
                
                if (timeLeft <= 0) {{
                    timerElement.textContent = "✓";
                    continueBtn.innerHTML = "📥 Get Video Now";
                    continueBtn.classList.add('active');
                }}
            }}
            
            const countdown = setInterval(function() {{
                timeLeft--;
                updateTimer();
                
                if (timeLeft <= 0) {{
                    clearInterval(countdown);
                    // Auto redirect after 5 seconds
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
            
            // Initial update
            updateTimer();
        </script>
    </body>
    </html>
    """
    return html

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'bot_running': True
    }), 200

# ==================== TELEGRAM BOT HANDLERS ====================
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    """Welcome message handler"""
    welcome_text = """
🎥 **Welcome to TikTok Video Downloader Bot!**

I can help you download TikTok videos without watermarks, absolutely FREE!

**📱 How to use:**
1️⃣ Send me any TikTok video link
2️⃣ Click on the link I provide
3️⃣ Wait 5 seconds (supports us)
4️⃣ I'll automatically send you the video!

**✨ Features:**
• No watermark videos
• HD quality download
• Fast processing
• Free to use

**🔗 Example links I accept:**
• https://www.tiktok.com/@user/video/123456789
• https://vm.tiktok.com/XXXXXX
• https://vt.tiktok.com/XXXXXX

**⚠️ Note:** This service is ad-supported to keep it free!

Send me a TikTok link to get started! 🚀
    """
    bot.reply_to(message, welcome_text, parse_mode='Markdown')

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    """Handle user messages containing TikTok links"""
    chat_id = message.chat.id
    
    # Show typing indicator
    bot.send_chat_action(chat_id, 'typing')
    
    # Extract TikTok URL
    tiktok_url = extract_tiktok_url(message.text)
    
    if not tiktok_url:
        bot.reply_to(
            message, 
            "❌ Please send a valid TikTok video link.\n\n"
            "Example: https://www.tiktok.com/@user/video/123456789"
        )
        return
    
    # Send processing message
    processing_msg = bot.reply_to(
        message, 
        "🔄 **Processing your TikTok link...**\n\n"
        "⏱️ This may take a few seconds",
        parse_mode='Markdown'
    )
    
    # First verify the video exists
    result = download_tiktok_video(tiktok_url)
    
    if not result['success']:
        bot.edit_message_text(
            f"❌ **Error:** {result['error']}\n\n"
            f"Please try another video or try again later.",
            chat_id=chat_id,
            message_id=processing_msg.message_id,
            parse_mode='Markdown'
        )
        return
    
    # Create ad link
    ad_result = create_ad_link(tiktok_url, chat_id)
    
    if ad_result['success']:
        # Prepare message
        ad_message = (
            f"✅ **Video found!**\n\n"
            f"📝 **Title:** {result['description']}\n"
            f"👤 **Author:** {result['author']}\n\n"
            f"**To download:**\n"
            f"1️⃣ Click the link below\n"
            f"2️⃣ Wait 5 seconds\n"
            f"3️⃣ Video will be sent automatically!\n\n"
            f"👇 **Click here:**\n"
            f"{ad_result['ad_url']}\n\n"
        )
        
        if ad_result.get('is_fallback'):
            ad_message += "⚠️ *Note: Using built-in ad page*"
        
        bot.edit_message_text(
            ad_message,
            chat_id=chat_id,
            message_id=processing_msg.message_id,
            parse_mode='Markdown',
            disable_web_page_preview=False
        )
    else:
        bot.edit_message_text(
            f"❌ Failed to create download link: {ad_result['error']}",
            chat_id=chat_id,
            message_id=processing_msg.message_id
        )

# Error handler
@bot.message_handler(func=lambda message: True)
def error_handler(message):
    """Handle any unexpected errors"""
    try:
        bot.reply_to(
            message,
            "❌ An unexpected error occurred. Please try again later or contact support."
        )
    except:
        pass

# ==================== MAIN FUNCTION ====================
def main():
    """Main function to start the bot"""
    try:
        logger.info("🚀 Starting TikTok Downloader Bot...")
        logger.info(f"🤖 Bot Token: {BOT_TOKEN[:10]}...{BOT_TOKEN[-5:]}")
        logger.info(f"🌐 Domain: {DOMAIN}")
        
        # Remove webhook if exists
        bot.remove_webhook()
        time.sleep(1)
        
        # Set webhook
        webhook_url = f"{DOMAIN}/webhook"
        bot.set_webhook(url=webhook_url)
        logger.info(f"✅ Webhook set to {webhook_url}")
        
        # Start Flask app
        logger.info(f"✅ Flask server starting on port {PORT}")
        app.run(host='0.0.0.0', port=PORT, debug=False)
        
    except Exception as e:
        logger.error(f"❌ Failed to start bot: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()