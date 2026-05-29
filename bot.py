import logging
import sqlite3
import os
import time
import asyncio
import subprocess
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from google import genai
import edge_tts

# Configuration
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Use /tmp for writable storage
BASE_DIR = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "/tmp")
DB_PATH = os.path.join(BASE_DIR, "memory.db")
TEMP_DIR = "/tmp"

# Female Bengali voice for Edge TTS
VOICE_NAME = "bn-BD-NabanitaNeural"  # Bengali female voice

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize Gemini
client = genai.Client(api_key=GEMINI_API_KEY)

# Database functions
def init_db():
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

def save_memory(user_id, user_message, bot_response):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO memories (user_id, user_message, bot_response) VALUES (?, ?, ?)",
                   (user_id, user_message, bot_response))
    conn.commit()
    conn.close()

def get_memories(user_id, limit=20):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT user_message, bot_response FROM memories WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
                   (user_id, limit))
    memories = cursor.fetchall()
    conn.close()
    return memories[::-1]

def get_ai_response(user_id, user_message):
    """Get response from Gemini AI with memory context"""
    recent_memories = get_memories(user_id)
    memory_context = ""
    if recent_memories:
        memory_context = "স্মৃতি (পূর্বের কথা):\n"
        for um, br in recent_memories:
            memory_context += f"Rega Sir: {um}\nসহকারী: {br}\n"

    system_instruction = (
        "আপনি Rega Sir এর একজন ব্যক্তিগত সহকারী। "
        "আপনি রাজশাহী, বাংলাদেশের একজন স্থানীয় ব্যক্তির মতো করে খুব সহজ এবং অনানুষ্ঠানিক (casual) বাংলায় কথা বলবেন। "
        "আপনি সবসময় Rega Sir কে 'Rega Sir' বলে সম্বোধন করবেন। "
        "আপনার প্রধান কাজ হলো Rega Sir যা বলেন তা মনে রাখা এবং পরে জিজ্ঞাসা করলে উত্তর দেওয়া। "
        "স্মৃতিতে থাকা তথ্য ব্যবহার করে উত্তর দিন। "
        "উত্তর সংক্ষিপ্ত এবং সরাসরি দিন।"
    )

    prompt = f"{system_instruction}\n\n{memory_context}\nRega Sir: {user_message}\nসহকারী:"

    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model='gemini-2.0-flash-lite',
                contents=prompt
            )
            return response.text
        except Exception as e:
            logger.error(f"Gemini API error (attempt {attempt+1}): {e}")
            if attempt < 2:
                time.sleep(5)
    return "Rega Sir, একটু সমস্যা হচ্ছে। আবার বলবেন নাকি?"

async def text_to_voice(text, output_path):
    """Convert text to speech using Edge TTS with female Bengali voice"""
    communicate = edge_tts.Communicate(text, VOICE_NAME)
    await communicate.save(output_path)

# Bot commands and handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    welcome = "আরে Rega Sir! আমি আপনার ব্রেইন ব্যাকআপ বট। আপনি যা বলবেন সব মনে রাখব। কি মনে রাখতে হবে বলেন?"
    await update.message.reply_text(welcome)
    # Also send voice
    voice_path = os.path.join(TEMP_DIR, f"welcome_{update.effective_user.id}.mp3")
    try:
        await text_to_voice(welcome, voice_path)
        with open(voice_path, 'rb') as audio:
            await update.message.reply_voice(voice=audio)
    except Exception as e:
        logger.error(f"TTS error: {e}")
    finally:
        if os.path.exists(voice_path):
            os.remove(voice_path)

async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        logger.error(f"TTS error: {e}")
    finally:
        if os.path.exists(voice_path):
            os.remove(voice_path)

async def voice_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle voice messages - transcribe using Gemini and respond"""
    user_id = update.effective_user.id
    voice_file = await update.message.voice.get_file()
    ogg_path = os.path.join(TEMP_DIR, f"{voice_file.file_id}.ogg")

    await voice_file.download_to_drive(ogg_path)

    try:
        # Use Gemini to transcribe the audio
        with open(ogg_path, 'rb') as f:
            audio_data = f.read()

        # Upload audio to Gemini for transcription
        transcribe_response = client.models.generate_content(
            model='gemini-2.0-flash-lite',
            contents=[
                {
                    'role': 'user',
                    'parts': [
                        {'text': 'এই অডিওতে কী বলা হয়েছে? শুধু কথাটা হুবহু লিখে দাও, অন্য কিছু বলো না।'},
                        {'inline_data': {'mime_type': 'audio/ogg', 'data': __import__('base64').b64encode(audio_data).decode()}}
                    ]
                }
            ]
        )
        transcribed_text = transcribe_response.text.strip()
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
            logger.error(f"TTS error: {e}")
        finally:
            if os.path.exists(voice_path):
                os.remove(voice_path)

    except Exception as e:
        logger.error(f"Voice processing error: {e}")
        await update.message.reply_text("Rega Sir, আপনার ভয়েসটা ঠিকঠাক বুঝতে পারলাম না। আরেকবার বলবেন?")
    finally:
        if os.path.exists(ogg_path):
            os.remove(ogg_path)

def main() -> None:
    init_db()
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))
    application.add_handler(MessageHandler(filters.VOICE, voice_message_handler))

    logger.info("Bot started polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
