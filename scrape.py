import os, json, requests, logging
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from groq import Groq
import firebase_admin
from firebase_admin import credentials, firestore

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("web_scraper")

# Initialize Clients
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
STATE_FILE = "last_seen.json"

def get_db():
    if not firebase_admin._apps:
        sa_json = json.loads(os.getenv("FIREBASE_SERVICE_ACCOUNT"))
        cred = credentials.Certificate(sa_json)
        firebase_admin.initialize_app(cred)
    return firestore.client()

db = get_db()

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def process_with_groq(text):
    prompt = f"Analyze this Telegram message for security leaks. Return ONLY JSON: {{\"target\": \"string\", \"description\": \"string\", \"usefulness\": 1|0}}. Input: {text}"
    completion = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"}
    )
    return json.loads(completion.choices[0].message.content)

def send_push_notifications(breach_id, target):
    """Fetch all push tokens from Firestore and send notifications via Expo."""
    try:
        users = db.collection("users").stream()
        push_tokens = []
        
        for user in users:
            user_data = user.to_dict()
            tokens = user_data.get("pushTokens", [])
            push_tokens.extend(tokens)
            
        if not push_tokens:
            log.info("No push tokens registered in database.")
            return

        log.info(f"Preparing push notifications for {len(push_tokens)} tokens...")
        
        # Build the payloads for Expo
        payloads = []
        for token in push_tokens:
            payloads.append({
                "to": token,
                "sound": "default",
                "title": "New Breach Detected!",
                "body": f"A new security breach targeting {target or 'Unknown'} has been reported.",
                "data": {"type": "breach", "id": breach_id}
            })

        # Expo API limits payloads to 100 messages per request, so we chunk them
        chunk_size = 100
        for i in range(0, len(payloads), chunk_size):
            chunk = payloads[i:i + chunk_size]
            headers = {
                "Accept": "application/json",
                "Accept-encoding": "gzip, deflate",
                "Content-Type": "application/json"
            }
            resp = requests.post(
                "https://exp.host/--/api/v2/push/send", 
                json=chunk, 
                headers=headers, 
                timeout=10
            )
            log.info(f"Expo response status (chunk {i // chunk_size + 1}): {resp.status_code}")
            
    except Exception as e:
        log.error(f"Failed to send push notifications: {e}")

def main():
    # Load channels and the last seen state
    if not os.path.exists("channels.txt"):
        return
        
    with open("channels.txt", "r") as f:
        channels = [line.strip() for line in f if line.strip()]
    
    current_state = load_state()
    state_changed = False

    for chan in channels:
        log.info(f"Scraping {chan}...")
        try:
            resp = requests.get(f"https://t.me/s/{chan}", timeout=10)
            soup = BeautifulSoup(resp.text, "html.parser")
            msg_elements = soup.find_all("div", class_="tgme_widget_message_text")
            
            if not msg_elements: continue
            
            latest_text = msg_elements[-1].get_text(strip=True)
            
            # Compare with local JSON state
            if current_state.get(chan) == latest_text:
                log.info(f"No new messages for {chan}")
                continue

            ai_result = process_with_groq(latest_text)
            
            if ai_result.get("usefulness") == 1:
                # Using document() then set() is safer and gives us the ID before insertion
                doc_ref = db.collection("filtered_messages").document()
                
                doc_ref.set({
                    "date": datetime.now(timezone.utc).isoformat(),
                    "description": ai_result.get("description"),
                    "target": ai_result.get("target"),
                    "channel": chan
                })
                
                log.info(f"✅ Leak detected in {chan} with Firestore ID: {doc_ref.id}")

                # Send the push notifications using the new ID
                send_push_notifications(doc_ref.id, ai_result.get("target"))

            # Update the state object
            current_state[chan] = latest_text
            state_changed = True
            
        except Exception as e:
            log.error(f"Error: {e}")

    # Save only if there was a new message
    if state_changed:
        save_state(current_state)

if __name__ == "__main__":
    main()
