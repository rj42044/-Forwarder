import os
import re
import sys
import asyncio
import sqlite3
import datetime
import logging
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from price_checker import check_deal_and_screenshot, extract_canonical_id, resolve_url

# Setup Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# --- Single Instance Lock (Windows msvcrt / Linux fcntl) ---
LOCK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "deal_forwarder.lock")
try:
    lock_fp = open(LOCK_FILE, 'w')
    if os.name == 'nt':
        import msvcrt
        msvcrt.locking(lock_fp.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
except Exception:
    logger.error("❌ Another instance of telegram_deal_forwarder is already running! Exiting immediately.")
    sys.exit(0)

# --- Configuration ---
API_ID = int(os.environ.get("TG_API_ID", "32206759"))
API_HASH = os.environ.get("TG_API_HASH", "7db3022378b608c86cad321de9eb3261")
STRING_SESSION = os.environ.get("TG_STRING_SESSION", "1BVtsOH4BuzMhWW4Bhur2zS_0aT8ufbKDjd-HnLzxMWWDWkMDpm8xoaAKv4VA2xZy7zp5b5lwM97GBauLgiIywHOtH4NX-MEb-5fojWfTjSEL4mA9eUYktivUmipj4WCqHp4nf8ytChEG5FZIw8dKD3C049exjIkiFj2aBZqI9O5s95KP76GNU_t3hgmi-ZPni61k_E9mc2WkAj3NDuG7HWkXncRtGqkyuMOTKLMFF1UIOHvRpmr618AzH5T7wUTIiYhQmY8Uq7uVuJGcQqTyO_wGSpZjOA7bz4yK3BREuLJKKbCuEhFnG5h61beww6S-MGoOdnG_Yf8bTYyJNhSJb3obeob0pWw=")

# Attempt to load from Streamlit Secrets if available
try:
    import streamlit as st
    if hasattr(st, "secrets") and st.secrets:
        API_ID = int(st.secrets.get("TG_API_ID", API_ID))
        API_HASH = str(st.secrets.get("TG_API_HASH", API_HASH))
        STRING_SESSION = str(st.secrets.get("TG_STRING_SESSION", STRING_SESSION))
except Exception:
    pass

# Source Channel: Deal Blast Shopping by Indiafreestuff (https://t.me/+nHuLKLYJMFxhNmRl)
SOURCE_CHANNEL = -1001366716672
DESTINATION_BOT = "ExtraPeBot"

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "forwarded_deals.db")
TEMP_IMG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
os.makedirs(TEMP_IMG_DIR, exist_ok=True)

# Real-Time Delivery & Quality Settings
MIN_POST_INTERVAL_SECONDS = 0   # Real-time instant delivery (0-second delay)
MIN_DISCOUNT_PERCENT = 30       # Minimum 30% discount off MRP

LAST_POST_TIME = 0

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS forwarded_deals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    raw_url TEXT,
                    resolved_url TEXT UNIQUE,
                    canonical_id TEXT UNIQUE,
                    title TEXT,
                    price TEXT,
                    discount_pct INTEGER,
                    forwarded_at TIMESTAMP
                )''')
    conn.commit()
    conn.close()

def is_already_forwarded(raw_url, resolved_url=None, canonical_id=None):
    """Checks if a deal has already been forwarded by raw URL, resolved URL, or canonical ASIN/PID."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute("SELECT id FROM forwarded_deals WHERE raw_url = ? OR resolved_url = ?", (raw_url, raw_url))
    if c.fetchone():
        conn.close()
        return True
        
    if resolved_url:
        c.execute("SELECT id FROM forwarded_deals WHERE resolved_url = ? OR raw_url = ?", (resolved_url, resolved_url))
        if c.fetchone():
            conn.close()
            return True

    if canonical_id:
        c.execute("SELECT id FROM forwarded_deals WHERE canonical_id = ?", (canonical_id,))
        if c.fetchone():
            conn.close()
            return True

    conn.close()
    return False

def record_forwarded_deal(raw_url, resolved_url, canonical_id, title, price, discount_pct):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""INSERT INTO forwarded_deals 
                     (raw_url, resolved_url, canonical_id, title, price, discount_pct, forwarded_at) 
                     VALUES (?, ?, ?, ?, ?, ?, ?)""",
                  (raw_url, resolved_url, canonical_id, title, price, discount_pct, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"DB record warning: {e}")

def is_search_or_list_url(url):
    """Ignores generic search result or category list pages."""
    if not url: return True
    url_low = url.lower()
    if "/s?" in url_low or "/b?" in url_low or "search?" in url_low or "collection-tab-name=" in url_low or "param=dwnk" in url_low:
        return True
    return False

def extract_primary_deal_url(text, buttons=None):
    """Extracts primary deal link from text or buttons, ignoring ReadMore links."""
    if text:
        # Check for explicit "Buy Now: <url>" pattern first
        m = re.search(r'buy\s*now\s*:?\s*(https?://[^\s]+)', text, re.I)
        if m:
            clean_u = re.sub(r'[\)\],\.!]+$', '', m.group(1))
            if not is_search_or_list_url(clean_u):
                return clean_u
                
        # Fallback to any non-ReadMore URL
        found = re.findall(r'(https?://[^\s]+)', text)
        for u in found:
            clean_u = re.sub(r'[\)\],\.!]+$', '', u)
            if "readmore" not in clean_u.lower() and "read-more" not in clean_u.lower() and not is_search_or_list_url(clean_u):
                return clean_u

    if buttons:
        for row in buttons:
            for btn in row:
                if hasattr(btn, 'url') and btn.url:
                    clean_b = re.sub(r'[\)\],\.!]+$', '', btn.url)
                    if not is_search_or_list_url(clean_b):
                        return clean_b
    return None

def clean_and_resolve_source_text(text):
    """
    1. Removes ONLY 'Read More' lines and their links.
    2. Keeps ALL other lines (Title, Coupons, Buy Now, Collect Offer, More Products).
    3. Resolves EVERY SINGLE remaining URL to its final e-commerce destination URL (Amazon / Flipkart).
    """
    if not text:
        return ""
        
    lines = []
    for line in text.split('\n'):
        line_str = line.strip()
        if not line_str:
            lines.append("")
            continue
            
        # Strictly remove ONLY Read More lines
        if "read more" in line_str.lower() or "readmore" in line_str.lower():
            continue
            
        # Find any URLs in this line and resolve every single one to Amazon/Flipkart target
        urls = re.findall(r'https?://[^\s]+', line_str)
        for raw_u in urls:
            clean_u = re.sub(r'[\)\],\.!]+$', '', raw_u)
            resolved_u = resolve_url(clean_u)
            if resolved_u and resolved_u != clean_u:
                logger.info(f"🔗 Resolved link in post: {clean_u} -> {resolved_u}")
                line_str = line_str.replace(raw_u, resolved_u)

        lines.append(line_str)
            
    result = "\n".join(lines).strip()
    result = re.sub(r'\n{3,}', '\n\n', result)
    return result

client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH)

async def process_and_forward_deal(raw_url, event_msg):
    global LAST_POST_TIME

    if is_search_or_list_url(raw_url):
        logger.info(f"Skipping generic category/search list URL: {raw_url}")
        return

    if is_already_forwarded(raw_url):
        logger.info(f"Skipping already forwarded raw URL: {raw_url}")
        return

    canonical_id = extract_canonical_id(raw_url)
    if canonical_id and is_already_forwarded(raw_url, canonical_id=canonical_id):
        logger.info(f"Skipping already forwarded canonical ID ({canonical_id}): {raw_url}")
        return

    # Anti-Flooding Time Delay Check
    now = asyncio.get_event_loop().time()
    time_since_last = now - LAST_POST_TIME
    if time_since_last < MIN_POST_INTERVAL_SECONDS:
        wait_seconds = round(MIN_POST_INTERVAL_SECONDS - time_since_last)
        logger.info(f"⏳ Waiting {wait_seconds}s before processing next deal...")
        await asyncio.sleep(wait_seconds)

    msg_text = event_msg.text or ""
    has_image = bool(event_msg.media and (event_msg.photo or getattr(event_msg, 'document', None)))

    logger.info(f"Processing deal URL: {raw_url}")
    
    # Capture Playwright product screenshot & resolve primary store URL
    deal_info = {}
    try:
        deal_info = await check_deal_and_screenshot(raw_url, min_discount_pct=MIN_DISCOUNT_PERCENT)
    except Exception as e:
        logger.warning(f"Playwright engine warning (falling back to fast HTTP resolution): {e}")
    
    image_file_path = None
    resolved_prod_url = resolve_url(raw_url)

    if deal_info.get("screenshot_path") and os.path.exists(deal_info["screenshot_path"]):
        image_file_path = deal_info["screenshot_path"]
        if deal_info.get("product_url"):
            resolved_prod_url = deal_info["product_url"]

    # Fallback to downloading Telegram attached photo if Playwright screenshot failed
    if not image_file_path and has_image:
        try:
            logger.info("Downloading attached photo from Telegram message...")
            image_file_path = await client.download_media(event_msg.media, file=TEMP_IMG_DIR)
        except Exception as e:
            logger.warning(f"Could not download attached Telegram image: {e}")

    c_id = deal_info.get("canonical_id") or extract_canonical_id(resolved_prod_url) or canonical_id

    if is_already_forwarded(raw_url, resolved_url=resolved_prod_url, canonical_id=c_id):
        logger.info(f"Skipping duplicate resolved product URL ({c_id or resolved_prod_url})")
        return

    # Clean text body: remove ReadMore only, and resolve ALL remaining links to Amazon/Flipkart
    cleaned_caption = clean_and_resolve_source_text(msg_text)
    if not cleaned_caption and deal_info.get("title"):
        cleaned_caption = f"🔥 **{deal_info['title']}**\n\n🛒 **Buy Now:** {resolved_prod_url}"

    try:
        logger.info(f"Sending real-time deal to @{DESTINATION_BOT}...")
        if image_file_path and os.path.exists(image_file_path):
            await client.send_file(DESTINATION_BOT, image_file_path, caption=cleaned_caption, parse_mode='md')
            try: os.remove(image_file_path)
            except Exception: pass
        else:
            await client.send_message(DESTINATION_BOT, cleaned_caption, parse_mode='md')

        LAST_POST_TIME = asyncio.get_event_loop().time()
        record_forwarded_deal(raw_url, resolved_prod_url, c_id, "Deal Post", "", 0)
        logger.info(f"✅ Successfully forwarded deal to @{DESTINATION_BOT}!")
    except Exception as e:
        logger.error(f"❌ Failed to send deal to @{DESTINATION_BOT}: {e}")

@client.on(events.NewMessage(chats=SOURCE_CHANNEL))
async def new_channel_post_handler(event):
    """Event handler for new posts in Deal Blast Shopping by Indiafreestuff."""
    logger.info("New post received from Deal Blast Shopping channel!")
    buttons = event.message.buttons
    primary_url = extract_primary_deal_url(event.message.text, buttons)
    
    if primary_url:
        await process_and_forward_deal(primary_url, event.message)
    else:
        logger.warning("No valid deal URL found in post.")

async def main():
    init_db()
    print("==================================================")
    print("  Telegram Deal Forwarder - Universal Link Resolver")
    print(f"  Source Channel : Deal Blast Shopping ({SOURCE_CHANNEL})")
    print(f"  Destination Bot: @{DESTINATION_BOT}")
    print(f"  Rule           : Remove ReadMore ONLY")
    print(f"  Link Resolution: Resolve ALL links to Amazon/Flipkart")
    print("==================================================")

    await client.start()
    me = await client.get_me()
    print(f"✅ Userbot logged in as: Raja (@rj4204)")

    # Only listen to LIVE real-time deals (no old history sync on startup)
    print(f"📡 Real-time listener active for Deal Blast Shopping...")
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
