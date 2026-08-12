import os
import sys
import asyncio
import logging
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from price_checker import check_deal_and_screenshot, extract_canonical_id, resolve_url
from forwarder_utils import (
    init_db, DB_PATH, is_already_forwarded, record_forwarded_deal,
    is_search_or_list_url, extract_primary_deal_url, clean_and_resolve_source_text
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

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
    sys.exit(0)

API_ID = int(os.environ.get("TG_API_ID", "32206759"))
API_HASH = os.environ.get("TG_API_HASH", "7db3022378b608c86cad321de9eb3261")

DEFAULT_SESSION = (
    "1BVtsOH4Bu3Dx8amqNZg9wNASIzCrlUtAX1DaMmJdzk5vcFV9t_97e9xV7eUKIU56OUvHxM_aDfCQYbghrM7qZZBrUCzF3uT_"
    "LGRekekoGYzDPZzGGfGr5-iG51BbSQ0HNFDO9vCx-3oQRD_kZoqm5QWInDrlibg4Ey2zMSyT75DEmOMC1mqN5mEBcoeQ5IdR4"
    "FocS6xwF01bIGx_WWw6GtlqwcN42VsrSCk2eemvoO61qGx2J1tD10HaiR_baW4Berl_8FXCMP6v7KCFB77AC_I4VpO9NvdQmj"
    "3xO36W2rZha8wXQN5gmSYwGenCo9GhKXk04iKsgVZl-UYMoKo7Fm55iGr65QU="
)

STRING_SESSION = os.environ.get("TG_STRING_SESSION", DEFAULT_SESSION)

try:
    import streamlit as st
    if hasattr(st, "secrets") and st.secrets:
        API_ID = int(st.secrets.get("TG_API_ID", API_ID))
        API_HASH = str(st.secrets.get("TG_API_HASH", API_HASH))
        STRING_SESSION = str(st.secrets.get("TG_STRING_SESSION", STRING_SESSION))
except Exception:
    pass

SOURCE_CHANNEL = -1001366716672
DESTINATION_BOT = "ExtraPeBot"
TEMP_IMG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
os.makedirs(TEMP_IMG_DIR, exist_ok=True)
MIN_POST_INTERVAL_SECONDS = 0
MIN_DISCOUNT_PERCENT = 0
LAST_POST_TIME = 0

client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH)

async def process_and_forward_deal(raw_url, event_msg):
    global LAST_POST_TIME
    if is_search_or_list_url(raw_url) or is_already_forwarded(raw_url):
        return
    canonical_id = extract_canonical_id(raw_url)
    if canonical_id and is_already_forwarded(raw_url, canonical_id=canonical_id):
        return
    msg_text = event_msg.text or ""
    has_image = bool(event_msg.media and (event_msg.photo or getattr(event_msg, 'document', None)))
    logger.info("Processing deal URL: " + str(raw_url))
    deal_info = {}
    try:
        deal_info = await check_deal_and_screenshot(raw_url, min_discount_pct=MIN_DISCOUNT_PERCENT)
    except Exception as e:
        logger.warning("Playwright warning: " + str(e))
    image_file_path = None
    resolved_prod_url = resolve_url(raw_url)
    if deal_info.get("screenshot_path") and os.path.exists(deal_info["screenshot_path"]):
        image_file_path = deal_info["screenshot_path"]
        if deal_info.get("product_url"):
            resolved_prod_url = deal_info["product_url"]
    if not image_file_path and has_image:
        try:
            image_file_path = await client.download_media(event_msg.media, file=TEMP_IMG_DIR)
        except Exception:
            pass
    c_id = deal_info.get("canonical_id") or extract_canonical_id(resolved_prod_url) or canonical_id
    if is_already_forwarded(raw_url, resolved_url=resolved_prod_url, canonical_id=c_id):
        return
    cleaned_caption = clean_and_resolve_source_text(msg_text)
    if not cleaned_caption and deal_info.get("title"):
        cleaned_caption = "🔥 **" + str(deal_info['title']) + "**\n\n🛒 **Buy Now:** " + str(resolved_prod_url)
    try:
        logger.info("Sending deal to @" + str(DESTINATION_BOT))
        if image_file_path and os.path.exists(image_file_path):
            await client.send_file(DESTINATION_BOT, image_file_path, caption=cleaned_caption, parse_mode='md')
            try:
                os.remove(image_file_path)
            except Exception:
                pass
        else:
            await client.send_message(DESTINATION_BOT, cleaned_caption, parse_mode='md')
        LAST_POST_TIME = asyncio.get_event_loop().time()
        record_forwarded_deal(raw_url, resolved_prod_url, c_id, "Deal Post", "", 0)
        logger.info("✅ Successfully forwarded deal!")
    except Exception as e:
        logger.error("Failed to send deal: " + str(e))

@client.on(events.NewMessage(chats=SOURCE_CHANNEL))
async def new_channel_post_handler(event):
    logger.info("New post received!")
    primary_url = extract_primary_deal_url(event.message.text, event.message.buttons)
    if primary_url:
        await process_and_forward_deal(primary_url, event.message)

async def main():
    init_db()
    await client.start()
    logger.info("Userbot logged in cleanly")
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
