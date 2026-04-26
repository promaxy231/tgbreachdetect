import requests
from bs4 import BeautifulSoup
import os

# Channels provided via environment variable or hardcoded list
CHANNELS = ["breachdetector", "ANCFCC_LEAKS", "CVEDetector"]

def scrape_channels():
    # Ensure results directory exists
    if not os.path.exists('data'):
        os.makedirs('data')

    for channel in CHANNELS:
        print(f"Scraping: {channel}...")
        url = f"https://t.me/s/{channel}"
        
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            # Extracting the message text blocks
            messages = soup.find_all('div', class_='tgme_widget_message_text')
            
            # Save results to individual files
            with open(f"data/{channel}.txt", "w", encoding="utf-8") as f:
                for msg in messages:
                    text = msg.get_text(separator='\n', strip=True)
                    f.write(text + "\n" + ("-" * 20) + "\n")
                    
        except Exception as e:
            print(f"Failed to scrape {channel}: {e}")

if __name__ == "__main__":
    scrape_channels()
