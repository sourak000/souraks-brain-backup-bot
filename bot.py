import logging
import sqlite3
import os
import time
import subprocess
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from google import genai
import speech_recognition as sr

# Configuration - use environment variables with fallbacks
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8630873005:AAEO2Vhd9SIJI8AlDdbigCpkJU0AH_ZHQr8")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSyAwzOHeaidFatRxII2r0nTySbYoqmXenRE")

# Use /tmp for writable storage on Railway
BASE_DIR = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "/tmp")
DB_PATH = os.path.join(BASE_DIR, "memory.db")
TEMP_DIR = "/tmp"

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
    return memories[::-1]  # Return in chronological order

# Bot commands and handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "আরে Rega Sir! আমি আপনার ব্রেইন ব্যাকআপ বট। "
        "আপনি যা বলবেন সব মনে রাখব। কি মনে রাখতে হবে বলেন?"
    )

async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user_message = update.message.text

    # Retrieve recent memories to provide context to Gemini
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

    bot_response = None
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt
            )
            bot_response = response.text
            break
        except Exception as e:
            logger.error(f"Gemini API error (attempt {attempt+1}): {e}")
            if attempt < 2:
                time.sleep(30)
            else:
                bot_response = "Rega Sir, একটু সমস্যা হচ্ছে কথা বুঝতে। আবার বলবেন নাকি?"

    if bot_response:
        save_memory(user_id, user_message, bot_response)
        await update.message.reply_text(bot_response)

async def voice_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    voice_file = await update.message.voice.get_file()
    ogg_path = os.path.join(TEMP_DIR, f"{voice_file.file_id}.ogg")
    wav_path = os.path.join(TEMP_DIR, f"{voice_file.file_id}.wav")

    await voice_file.download_to_drive(ogg_path)

    try:
        subprocess.run(['ffmpeg', '-i', ogg_path, wav_path, '-y'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_path) as source:
            audio_data = recognizer.record(source)
            text = recognizer.recognize_google(audio_data, language='bn-BD')
            logger.info(f"Transcribed voice: {text}")

            update.message.text = text
            await chat(update, context)
    except Exception as e:
        logger.error(f"Voice processing error: {e}")
        await update.message.reply_text("Rega Sir, আপনার ভয়েসটা ঠিকঠাক বুঝতে পারলাম না। আরেকবার বলবেন?")
    finally:
        if os.path.exists(ogg_path):
            os.remove(ogg_path)
        if os.path.exists(wav_path):
            os.remove(wav_path)

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
