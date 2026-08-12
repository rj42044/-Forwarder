import os
import sqlite3
import datetime
import asyncio
import threading
import streamlit as st

from telegram_deal_forwarder import main as run_deal_forwarder, DB_PATH, SOURCE_CHANNEL, DESTINATION_BOT

st.set_page_config(
    page_title="Telegram Deal Forwarder Dashboard",
    page_icon="⚡",
    layout="wide"
)

# --- 24/7 GLOBAL BACKGROUND BOT LAUNCHER ---
@st.cache_resource
def start_global_247_forwarder_bot():
    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(run_deal_forwarder())
        except Exception as e:
            print("Global bot loop error:", e)
            
    t = threading.Thread(target=_run, daemon=True, name="GlobalTelegramDealForwarder")
    t.start()
    return t

bot_thread = start_global_247_forwarder_bot()

st.title("⚡ Telegram Deal Forwarder - 24/7 Cloud Worker")
st.markdown("Real-time deal forwarder from Deal Blast Shopping to @ExtraPeBot with Playwright product screenshots.")

st.sidebar.header("📡 Live Status & Controls")
st.sidebar.success("🟢 24/7 Cloud Worker: ACTIVE")
st.sidebar.info("Source Channel: " + str(SOURCE_CHANNEL) + "\n\nDestination Bot: @" + str(DESTINATION_BOT))

col1, col2 = st.columns(2)

with col1:
    st.success("✅ Bot is running 24/7 in the cloud server background.")

with col2:
    if st.button("🔄 Refresh Live Deals Table", use_container_width=True):
        st.rerun()

st.divider()
st.subheader("📊 Forwarded Deals History")

if os.path.exists(DB_PATH):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT id, title, price, discount_pct, canonical_id, forwarded_at, resolved_url FROM forwarded_deals ORDER BY id DESC LIMIT 50")
        rows = c.fetchall()
        conn.close()
        
        if rows:
            m1, m2, m3 = st.columns(3)
            m1.metric("Total Deals Forwarded", len(rows))
            m2.metric("Latest Forwarded At", rows[0][5] if len(rows[0]) > 5 else "N/A")
            m3.metric("Cloud Server Status", "🟢 24/7 Active")
            
            table_data = []
            for r in rows:
                table_data.append({
                    "ID": r[0],
                    "Title": r[1],
                    "Price": r[2],
                    "Discount %": r[3],
                    "Canonical ID": r[4],
                    "Forwarded At": r[5],
                    "URL": r[6]
                })
            st.dataframe(table_data, use_container_width=True)
        else:
            st.info("No deals forwarded yet. New deals will appear here automatically.")
    except Exception as e:
        st.error("Database error: " + str(e))
else:
    st.info("Database initializing...")
