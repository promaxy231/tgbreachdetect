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
        # Still use Secret for the sensitive Firebase JSON
        sa_json = json.loads(os.getenv("FIREBASE_SERVICE_ACCOUNT"))
        cred = credentials.Certificate(sa_json)
        firebase_admin.initialize_app(cred)
    return firestore.client()

db = get_db()

def load_channels_from_file(filepath="channels.txt"):
    """Reads channel names from a local file."""
    if not os.path.exists(filepath):
        log.warning(f"{filepath} not found. No channels to scrape.")
        return []
    with open(filepath, "r") as f:
        # Filter out empty lines and comments
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]

# ... [keep get_last_text, save_state, and process_with_groq from previous code] ...

def main():
    # Load from file instead of Environment Variable
    channels = load_channels_from_file()
    
    for chan in channels:
        log.info(f"Scraping {chan}...")
        try:
            resp = requests.get(f"https://t.me/s/{chan}", timeout=10)
            soup = BeautifulSoup(resp.text, "html.parser")
            msg_elements = soup.find_all("div", class_="tgme_widget_message_text")
            
            if not msg_elements: 
                log.info(f"No messages found for {chan} (possibly private or empty).")
                continue
            
            latest_msg_text = msg_elements[-1].get_text(strip=True)
            stored_text = get_last_text(chan)
            
            if latest_msg_text == stored_text:
                log.info(f"No new messages for {chan}")
                continue

            ai_result = process_with_groq(latest_msg_text)
            
            if ai_result.get("usefulness") == 1:
                payload = {
                    "date": datetime.now(timezone.utc).isoformat(),
                    "description": ai_result.get("description"),
                    "target": ai_result.get("target"),
                    "channel": chan,
                    "raw_text": latest_msg_text # Optional: keep raw text for audit
                }
                db.collection("filtered_messages").add(payload)
                log.info(f"✅ Useful content detected in {chan}")

            save_state(chan, latest_msg_text)
        except Exception as e:
            log.error(f"Error scraping {chan}: {e}")

if __name__ == "__main__":
    main()
