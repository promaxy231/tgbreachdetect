import os, json, requests, logging
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from groq import Groq
import firebase_admin
from firebase_admin import credentials, firestore

# Setup
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("web_scraper")

# Initialize Clients
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

def get_db():
    if not firebase_admin._apps:
        # Load Firebase JSON from Secret
        sa_json = json.loads(os.getenv("FIREBASE_SERVICE_ACCOUNT"))
        cred = credentials.Certificate(sa_json)
        firebase_admin.initialize_app(cred)
    return firestore.client()

db = get_db()

def get_last_text(channel):
    doc = db.collection("scraper_state").document(channel).get()
    return doc.to_dict().get("last_text", "") if doc.exists else ""

def save_state(channel, text):
    db.collection("scraper_state").document(channel).set({"last_text": text}, merge=True)

def process_with_groq(text):
    prompt = f"Analyze this Telegram message for security leaks. Return ONLY JSON: {{\"target\": \"string\", \"description\": \"string\", \"usefulness\": 1|0}}. Input: {text}"
    completion = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"}
    )
    return json.loads(completion.choices[0].message.content)

def main():
    channels = os.getenv("TARGET_CHANNELS", "").split(",")
    
    for chan in channels:
        chan = chan.strip()
        log.info(f"Scraping {chan}...")
        
        # 1. Scrape Web Preview
        resp = requests.get(f"https://t.me/s/{chan}")
        soup = BeautifulSoup(resp.text, "html.parser")
        msg_elements = soup.find_all("div", class_="tgme_widget_message_text")
        
        if not msg_elements: continue
        
        # Get the latest message (last in the list)
        latest_msg_text = msg_elements[-1].get_text(strip=True)
        stored_text = get_last_text(chan)
        
        if latest_msg_text == stored_text:
            log.info(f"No new messages for {chan}")
            continue

        # 2. Process with AI
        ai_result = process_with_groq(latest_msg_text)
        
        if ai_result.get("usefulness") == 1:
            payload = {
                "date": datetime.now(timezone.utc).isoformat(),
                "description": ai_result.get("description"),
                "target": ai_result.get("target"),
                "channel": chan
            }
            db.collection("filtered_messages").add(payload)
            log.info(f"Saved leak from {chan}")

        # 3. Update State
        save_state(chan, latest_msg_text)

if __name__ == "__main__":
    main()
