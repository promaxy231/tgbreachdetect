import os, asyncio, logging, json, re
from typing import List
from datetime import datetime, timezone

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.functions.messages import GetHistoryRequest
from groq import Groq  # Switched to Groq

import firebase_admin
from firebase_admin import credentials, firestore

# ══════════════════════════════════════════════════════════════
# 1. CONFIG & LOGGING
# ══════════════════════════════════════════════════════════════
load_dotenv()

# Telegram Credentials
API_ID = int(os.getenv("TG_API_ID"))
API_HASH = os.getenv("TG_API_HASH")
TARGET_CHANNELS: List[str] = [
    c.strip() for c in os.getenv("TARGET_CHANNELS", "").split(",") if c.strip()
]

# Groq Configuration
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
groq_client = Groq(api_key=GROQ_API_KEY)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("breachbot")

# ══════════════════════════════════════════════════════════════
# 2. FIREBASE & STATE MANAGEMENT
# ══════════════════════════════════════════════════════════════
def get_firestore_db():
    if not firebase_admin._apps:
        # Assumes GOOGLE_APPLICATION_CREDENTIALS env var points to your JSON file
        cred = credentials.Certificate(os.getenv("GOOGLE_APPLICATION_CREDENTIALS"))
        firebase_admin.initialize_app(cred)
    return firestore.client()

db = get_firestore_db()

def get_last_processed_id(channel_id: str) -> int:
    """Check Firestore to see where we left off."""
    doc = db.collection("scraper_state").document(channel_id).get()
    return doc.to_dict().get("last_id", 0) if doc.exists else 0

def update_last_processed_id(channel_id: str, last_id: int):
    """Save the new high-water mark to Firestore."""
    db.collection("scraper_state").document(channel_id).set({"last_id": last_id}, merge=True)

# ══════════════════════════════════════════════════════════════
# 3. GROQ PROCESSING
# ══════════════════════════════════════════════════════════════
def process_with_groq(text: str):
    """Analyzes message content using Groq's Llama-3 models."""
    prompt = (
        "Analyze this Telegram message for corporate security relevance. "
        "Return ONLY a JSON object: {\"target\": \"string\", \"description\": \"string\", \"usefulness\": 1|0}. "
        f"Input: {text}"
    )
    
    completion = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile", # High speed/low latency
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"} # Groq supports native JSON mode
    )
    
    return json.loads(completion.choices[0].message.content)

# ══════════════════════════════════════════════════════════════
# 4. CORE LOGIC
# ══════════════════════════════════════════════════════════════
async def run_cron_scrape():
    # 'breach_session' must be the name of your .session file
    client = TelegramClient("breach_session", API_ID, API_HASH)
    await client.start()
    
    for chan in TARGET_CHANNELS:
        try:
            entity = await client.get_entity(chan)
            channel_key = str(entity.id)
            last_id = get_last_processed_id(channel_key)
            
            # Fetch up to 20 messages newer than the last one we saw
            history = await client(GetHistoryRequest(
                peer=entity, limit=20, offset_date=None, offset_id=0,
                max_id=0, min_id=last_id, add_offset=0, hash=0
            ))

            if not history.messages:
                log.info(f"No new messages for {chan}")
                continue

            # Process oldest to newest
            new_messages = sorted(history.messages, key=lambda x: x.id)
            
            for msg in new_messages:
                if not msg.raw_text or len(msg.raw_text) < 10:
                    continue
                
                log.info(f"Analyzing ID {msg.id} from {chan}")
                ai_result = process_with_groq(msg.raw_text)
                
                if ai_result.get("usefulness") == 1:
                    payload = {
                        "date": datetime.now(timezone.utc).strftime("%B %d, %Y at %I:%M:%S %p UTC%z"),
                        "description": ai_result.get("description", ""),
                        "target": ai_result.get("target", ""),
                        "usefulness": True,
                        "original_tg_id": msg.id,
                        "channel": chan
                    }
                    db.collection("filtered_messages").add(payload)
                    log.info(f"✨ Useful info saved to Firebase from {chan}")

            # Update state so we don't process these again
            update_last_processed_id(channel_key, new_messages[-1].id)
            
        except Exception as e:
            log.error(f"Error processing channel {chan}: {e}")
            
    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(run_cron_scrape())
