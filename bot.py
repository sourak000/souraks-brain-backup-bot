import logging
import sqlite3
import os
import sys
import time
import asyncio
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from openai import OpenAI
import edge_tts

# Configuration
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

# Validate required env vars
if not TELEGRAM_BOT_TOKEN:
    print("ERROR: TELEGRAM_BOT_TOKEN not set!")
    sys.exit(1)
if not GROQ_API_KEY:
    print("ERROR: GROQ_API_KEY not set!")
    sys.exit(1)

# Use /tmp for writable storage on Railway
BASE_DIR = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "/tmp")
DB_PATH = os.path.join(BASE_DIR, "memory.db")
TEMP_DIR = "/tmp"

# Female Bengali voice for Edge TTS
VOICE_NAME = "bn-BD-NabanitaNeural"

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# Initialize Groq client (OpenAI-compatible)
groq_client = OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1"
)

# ============ HEALTH CHECK SERVER FOR RAILWAY ============
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'OK')
    
    def log_message(self, format, *args):
        pass  # Suppress health check logs

def start_health_server():
    """Start a simple HTTP health check server for Railway"""
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    logger.info(f"Health check server running on port {port}")
    server.serve_forever()

# ============ DATABASE FUNCTIONS ============
def init_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                user_id INTEGER,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                user_message TEXT,
                bot_response TEXT
            )
        """)
        conn.commit()
        conn.close()
        logger.info(f"Database initialized at {DB_PATH}")
    except Exception as e:
        logger.error(f"Database init error: {e}")

def save_memory(user_id, user_message, bot_response):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO memories (user_id, user_message, bot_response) VALUES (?, ?, ?)",
                       (user_id, user_message, bot_response))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Save memory error: {e}")

def get_memories(user_id, limit=20):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT user_message, bot_response FROM memories WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
                       (user_id, limit))
        memories = cursor.fetchall()
        conn.close()
        return memories[::-1]
    except Exception as e:
        logger.error(f"Get memories error: {e}")
        return []

# ============ AI FUNCTIONS ============
def get_ai_response(user_id, user_message):
    """Get response from Groq AI with memory context"""
    try:
        recent_memories = get_memories(user_id)
        
        messages = [
            {
                "role": "system",
                "content": (
                    "আপনি Rega Sir এর একজন ব্যক্তিগত সহকারী। "
                    "আপনি রাজশাহী, বাংলাদেশের একজন স্থানীয় ব্যক্তির মতো করে খুব সহজ এবং অনানুষ্ঠানিক (casual) বাংলায় কথা বলবেন। "
                    "আপনি সবসময় Rega Sir কে 'Rega Sir' বলে সম্বোধন করবেন। "
                    "আপনার প্রধান কাজ হলো Rega Sir যা বলেন তা মনে রাখা এবং পরে জিজ্ঞাসা করলে উত্তর দেওয়া। "
                    "স্মৃতিতে থাকা তথ্য ব্যবহার করে উত্তর দিন। "
                    "উত্তর সংক্ষিপ্ত এবং সরাসরি দিন।"
                )
            }
        ]
        
        # Add memory context as previous messages
        for um, br in recent_memories:
            messages.append({"role": "user", "content": um})
            messages.append({"role": "assistant", "content": br})
        
        # Add current message
        messages.append({"role": "user", "content": user_message})

        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            max_tokens=500,
            temperature=0.7
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Groq API error: {e}")
        return "Rega Sir, একটু সমস্যা হচ্ছে। আবার চেষ্টা করেন।"

def transcribe_audio(audio_path):
    """Transcribe audio using Groq Whisper"""
    try:
        with open(audio_path, 'rb') as audio_file:
            transcription = groq_client.audio.transcriptions.create(
                model="whisper-large-v3",
                file=audio_file,
                language="bn"
            )
        return transcription.text.strip()
    except Exception as e:
        logger.error(f"Transcription error: {e}")
        return None

async def text_to_voice(text, output_path):
    """Convert text to speech using Edge TTS with female Bengali voice"""
    try:
        communicate = edge_tts.Communicate(text, VOICE_NAME)
        await communicate.save(output_path)
    except Exception as e:
        logger.error(f"Edge TTS error: {e}")
        raise

# ============ BOT HANDLERS ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        welcome = "আরে Rega Sir! আমি আপনার ব্রেইন ব্যাকআপ বট। আপনি যা বলবেন সব মনে রাখব। কি মনে রাখতে হবে বলেন?"
        await update.message.reply_text(welcome)
        # Also send voice
        voice_path = os.path.join(TEMP_DIR, f"welcome_{update.effective_user.id}.mp3")
        try:
            await text_to_voice(welcome, voice_path)
            with open(voice_path, 'rb') as audio:
                await update.message.reply_voice(voice=audio)
        except Exception as e:
            logger.error(f"TTS error in start: {e}")
        finally:
            if os.path.exists(voice_path):
                os.remove(voice_path)
    except Exception as e:
        logger.error(f"Start handler error: {e}")

async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user_id = update.effective_user.id
        user_message = update.message.text

        bot_response = get_ai_response(user_id, user_message)
        save_memory(user_id, user_message, bot_response)

        # Send text reply
        await update.message.reply_text(bot_response)

        # Send voice reply
        voice_path = os.path.join(TEMP_DIR, f"reply_{user_id}_{int(time.time())}.mp3")
        try:
            await text_to_voice(bot_response, voice_path)
            with open(voice_path, 'rb') as audio:
                await update.message.reply_voice(voice=audio)
        except Exception as e:
            logger.error(f"TTS error in chat: {e}")
        finally:
            if os.path.exists(voice_path):
                os.remove(voice_path)
    except Exception as e:
        logger.error(f"Chat handler error: {e}")

async def voice_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle voice messages - transcribe using Groq Whisper and respond"""
    ogg_path = None
    try:
        user_id = update.effective_user.id
        voice_file = await update.message.voice.get_file()
        ogg_path = os.path.join(TEMP_DIR, f"{voice_file.file_id}.ogg")

        await voice_file.download_to_drive(ogg_path)

        # Transcribe using Groq Whisper
        transcribed_text = transcribe_audio(ogg_path)
        
        if not transcribed_text:
            await update.message.reply_text("Rega Sir, আপনার ভয়েসটা ঠিকঠাক বুঝতে পারলাম না। আরেকবার বলবেন?")
            return

        logger.info(f"Transcribed voice: {transcribed_text}")

        # Get AI response
        bot_response = get_ai_response(user_id, transcribed_text)
        save_memory(user_id, transcribed_text, bot_response)

        # Send text reply (showing what was heard + response)
        await update.message.reply_text(f"🎤 শুনেছি: {transcribed_text}\n\n{bot_response}")

        # Send voice reply
        voice_path = os.path.join(TEMP_DIR, f"reply_{user_id}_{int(time.time())}.mp3")
        try:
            await text_to_voice(bot_response, voice_path)
            with open(voice_path, 'rb') as audio:
                await update.message.reply_voice(voice=audio)
        except Exception as e:
            logger.error(f"TTS error in voice: {e}")
        finally:
            if os.path.exists(voice_path):
                os.remove(voice_path)

    except Exception as e:
        logger.error(f"Voice processing error: {e}")
        try:
            await update.message.reply_text("Rega Sir, আপনার ভয়েসটা ঠিকঠাক বুঝতে পারলাম না। আরেকবার বলবেন?")
        except:
            pass
    finally:
        if ogg_path and os.path.exists(ogg_path):
            os.remove(ogg_path)

# ============ MAIN ============
def main() -> None:
    logger.info("Starting Sourak's Brain Backup Bot...")
    
    # Initialize database
    init_db()
    
    # Start health check server in background thread (for Railway)
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()
    
    # Build application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))
    application.add_handler(MessageHandler(filters.VOICE, voice_message_handler))

    logger.info("Bot started polling...")
    
    # Run polling with drop_pending_updates to avoid processing old messages
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )

if __name__ == "__main__":
    main()
