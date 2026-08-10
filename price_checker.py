import os
import re
import requests
import asyncio
import io
import sys
from playwright.async_api import async_playwright

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)

ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
os.makedirs(ASSETS_DIR, exist_ok=True)

def resolve_url(url):
    """Resolves short URLs to final e-commerce product destination."""
    if not url: return url
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0'}
        res = requests.head(url, allow_redirects=True, timeout=5, headers=headers)
        return res.url
    except Exception:
        try:
            res = requests.get(url, allow_redirects=True, timeout=5, headers=headers)
            return res.url
        except Exception:
            return url

import urllib.parse

def extract_canonical_id(url):
    """Extracts ASIN from Amazon or PID from Flipkart to uniquely identify products."""
    if not url: return None
    decoded_url = urllib.parse.unquote(url)
    
    # Amazon ASIN (10 alphanumeric chars after /dp/ or /gp/product/ or /product/ or ?asin=)
    m_amz = re.search(r'/(?:dp|gp/product|product)/([A-Z0-9]{10})', decoded_url, re.I)
    if m_amz:
        return f"AMZ_{m_amz.group(1).upper()}"
        
    m_amz2 = re.search(r'[?&]asin=([A-Z0-9]{10})', decoded_url, re.I)
    if m_amz2:
        return f"AMZ_{m_amz2.group(1).upper()}"
    
    # Flipkart PID
    m_fk = re.search(r'pid=([A-Z0-9]{12,16})', decoded_url, re.I)
    if m_fk:
        return f"FK_{m_fk.group(1).upper()}"
        
    return None

def is_generic_homepage_or_banner(title, url):
    """Detects if page is a generic homepage, banner, cart, or category search result."""
    if not url:
        return True
        
    url_low = url.lower()
    
    # Check for category / search / homepage URLs
    if url_low in ["https://www.amazon.in/", "https://amazon.in/", "https://www.flipkart.com/", "https://flipkart.com/"]:
        return True
    if "/s?" in url_low or "/b?" in url_low or "search?" in url_low or "collection-tab-name=" in url_low or "great-freedom-sale" in url_low:
        return True

    if not title:
        return True

    title_low = title.lower()
    banned_keywords = [
        "online shopping site in india",
        "shopping site in india",
        "great freedom sale",
        "amazon.in",
        "continue shopping",
        "shopping cart",
        "sign in",
        "sign-in",
        "best sellers",
        "today's deals"
    ]
    for b in banned_keywords:
        if b in title_low:
            return True
            
    return False

async def check_deal_and_screenshot(product_url, min_discount_pct=10):
    """
    1. Resolves final product URL.
    2. Navigates with Playwright to extract Title, Current Price, and MRP.
    3. Handles Amazon 'Continue shopping' bot interstitials.
    4. Enforces strict product validation (rejects homepages & pincode fallback prices).
    5. Captures screenshot of genuine product page only.
    """
    final_url = resolve_url(product_url)
    canonical_id = extract_canonical_id(final_url) or extract_canonical_id(product_url)
    print(f"Checking deal at URL: {final_url} (Canonical ID: {canonical_id})")
    
    result = {
        "is_valid_deal": False,
        "title": "Product Deal",
        "current_price": "",
        "mrp": "",
        "discount_pct": 0,
        "product_url": final_url,
        "canonical_id": canonical_id,
        "screenshot_path": None,
        "reason": ""
    }

    # Reject non-product URLs right away if canonical ID missing for major platforms
    if ("amazon.in" in final_url.lower() or "flipkart.com" in final_url.lower()) and not canonical_id:
        if is_generic_homepage_or_banner("", final_url):
            result["reason"] = "Not an individual product page (missing ASIN/PID or category URL)"
            print(f"Rejected: {result['reason']}")
            return result

    async with async_playwright() as p:
        # Launch Chromium with Anti-Bot Stealth Arguments
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-infobars",
                "--window-position=0,0",
                "--ignore-certificate-errors",
                "--disable-dev-shm-usage"
            ]
        )
        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="en-IN",
            timezone_id="Asia/Kolkata"
        )
        # Bypass navigator.webdriver detection
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = await context.new_page()

        try:
            await page.goto(final_url, wait_until="domcontentloaded", timeout=25000)
            await page.wait_for_timeout(2000)
            
            # --- Anti-Bot Detection Rule 1: Check for Amazon 'Continue shopping' Interstitial ---
            cont_btn = await page.query_selector('button:has-text("Continue shopping"), input[aria-labelledby*="continue"], .a-button-text:has-text("Continue shopping"), a:has-text("Continue shopping")')
            if cont_btn:
                print("⚡ Amazon Anti-Bot Interstitial detected! Clicking 'Continue shopping' button...")
                await cont_btn.click()
                await page.wait_for_timeout(3000)

            # --- Anti-Bot Detection Rule 2: Check for Amazon CAPTCHA page ---
            if "validateCaptcha" in page.url or await page.query_selector('form[action*="validateCaptcha"]'):
                print("⚠️ Amazon Captcha detected! Reloading with stealth context...")
                await page.reload(wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)

            page_title = await page.title()
            
            title_text = ""
            current_price = ""
            mrp_price = ""
            rating_count = 0
            rating_score = 0.0
            is_popular_badge = False
            
            # --- Amazon Price & Popularity Extraction ---
            if "amazon" in page.url.lower():
                t_el = await page.query_selector('#productTitle, #title')
                if t_el:
                    title_text = (await t_el.inner_text()).strip()
                
                # Priority price selectors
                p_el = await page.query_selector('#corePriceDisplay_desktop_feature_div .a-price-whole, #corePrice_feature_div .a-price-whole, .apexPriceToPay .a-offscreen, #priceblock_ourprice, #priceblock_dealprice, #price_inside_buybox')
                if p_el:
                    current_price = (await p_el.inner_text()).strip()
                else:
                    p_el2 = await page.query_selector('.a-price .a-offscreen')
                    if p_el2:
                        current_price = (await p_el2.inner_text()).strip()
                
                m_el = await page.query_selector('.basisPrice .a-offscreen, .a-price[data-a-strike="true"] .a-offscreen, #listPrice')
                if m_el:
                    mrp_price = (await m_el.inner_text()).strip()

                # Popularity metrics (Amazon)
                rc_el = await page.query_selector('#acrCustomerReviewText, #averageCustomerReviews')
                if rc_el:
                    rc_text = await rc_el.inner_text()
                    m_rc = re.search(r'([\d,]+)', rc_text)
                    if m_rc:
                        rating_count = int(m_rc.group(1).replace(',', ''))

                rs_el = await page.query_selector('#acrPopover .a-icon-alt, span.a-icon-alt')
                if rs_el:
                    rs_text = await rs_el.inner_text()
                    m_rs = re.search(r'([\d\.]+)\s*out of', rs_text, re.I)
                    if m_rs:
                        rating_score = float(m_rs.group(1))

                badge_el = await page.query_selector('#socialProofingAsinFaceout_feature_div, .badge-wrapper, #zeitgeistBadge')
                if badge_el:
                    is_popular_badge = True

            # --- Flipkart Price & Popularity Extraction ---
            elif "flipkart" in page.url.lower():
                t_el = await page.query_selector('.B_NuBc, .VU-LmN, span.B_NuBc')
                if t_el:
                    title_text = (await t_el.inner_text()).strip()
                
                p_el = await page.query_selector('._30jeq3._16JgWd, ._30jeq3, .Nx9bqj._4b5DiR')
                if p_el:
                    current_price = (await p_el.inner_text()).strip()
                    
                m_el = await page.query_selector('._3I9_wc._2p6Bfd, ._3I9_wc, .yRaY8j._26N9P2')
                if m_el:
                    mrp_price = (await m_el.inner_text()).strip()

                # Popularity metrics (Flipkart)
                rc_el = await page.query_selector('._2_R_ns, ._2d4v26, span.W_ujSu, ._3uWWRF')
                if rc_el:
                    rc_text = await rc_el.inner_text()
                    m_rc = re.search(r'([\d,]+)\s*Rating', rc_text, re.I)
                    if m_rc:
                        rating_count = int(m_rc.group(1).replace(',', ''))

                rs_el = await page.query_selector('._3LWZlK, div.XOB18z')
                if rs_el:
                    rs_text = await rs_el.inner_text()
                    m_rs = re.search(r'([\d\.]+)', rs_text)
                    if m_rs:
                        rating_score = float(m_rs.group(1))

            # Verify title is not generic homepage
            if is_generic_homepage_or_banner(title_text or page_title, page.url):
                result["reason"] = f"Generic homepage or banner page detected ('{title_text or page_title}')"
                print(f"Rejected: {result['reason']}")
                await browser.close()
                return result

            if not title_text:
                title_text = page_title.split(':')[0].split('|')[0].strip()

            if is_generic_homepage_or_banner(title_text, page.url):
                result["reason"] = f"Invalid generic title: {title_text}"
                print(f"Rejected: {result['reason']}")
                await browser.close()
                return result

            # Parse numeric values
            cur_num = int(re.sub(r'[^\d]', '', current_price)) if current_price and re.sub(r'[^\d]', '', current_price) else 0
            mrp_num = int(re.sub(r'[^\d]', '', mrp_price)) if mrp_price and re.sub(r'[^\d]', '', mrp_price) else 0

            # Fix if cur_num extracted multi-pack price or pincode
            if mrp_num > 0 and cur_num > mrp_num:
                alt_el = await page.query_selector('#corePriceDisplay_desktop_feature_div .a-price-whole, #corePrice_feature_div .a-price-whole')
                if alt_el:
                    alt_text = (await alt_el.inner_text()).strip()
                    cur_num = int(re.sub(r'[^\d]', '', alt_text)) if re.sub(r'[^\d]', '', alt_text) else cur_num

            if cur_num <= 0:
                result["reason"] = "Could not extract valid product DOM price."
                print(f"Rejected: {result['reason']}")
                await browser.close()
                return result

            # --- Ultra-Popular (1,000+ Reviews) & Deep Discount Engine ---
            discount_pct = 0
            savings_rs = 0
            if mrp_num > cur_num:
                savings_rs = mrp_num - cur_num
                discount_pct = round((savings_rs / mrp_num) * 100)

            # Rating Score Check (Must be >= 4.0★)
            if rating_score > 0 and rating_score < 4.0:
                result["reason"] = f"Skipped: Customer rating below 4.0★ ({rating_score}★ < 4.0★)"
                print(f"Rejected: {result['reason']}")
                await browser.close()
                return result

            # Tier Evaluation based on Popularity & Discount Depth:
            # - Tier 1: Ultra Popular (1,000+ Reviews or Bestseller Badge) -> Needs >= 20% OFF
            # - Tier 2: Popular (100 - 999 Reviews) -> Needs Deep Discount (>= 35% OFF)
            # - Tier 3: Budget Loot (< 100 Reviews) -> Needs Mega Deep Discount (>= 50% OFF)
            is_valid_quality = False
            tier_name = ""

            if rating_count >= 1000 or is_popular_badge:
                tier_name = "Ultra Popular (1,000+ Reviews)"
                is_valid_quality = (discount_pct >= 20)
            elif rating_count >= 100:
                tier_name = "Popular (100+ Reviews)"
                is_valid_quality = (discount_pct >= 35)
            else:
                tier_name = "Deep Discount Loot (<100 Reviews)"
                is_valid_quality = (discount_pct >= 50)

            if not is_valid_quality:
                result["reason"] = f"Skipped: Did not meet quality threshold for {tier_name} ({discount_pct}% OFF / {rating_count} reviews)"
                print(f"Rejected: {result['reason']}")
                await browser.close()
                return result

            if mrp_num >= 500 and savings_rs < 100:
                result["reason"] = f"Skipped: Savings too low (₹{savings_rs} < ₹100)"
                print(f"Rejected: {result['reason']}")
                await browser.close()
                return result

            # High quality deal verified!
            shot_name = f"deal_{canonical_id or 'prod'}_{int(asyncio.get_event_loop().time())}.png"
            shot_path = os.path.join(ASSETS_DIR, shot_name)
            await page.screenshot(path=shot_path, full_page=False)

            result["is_valid_deal"] = True
            result["title"] = title_text
            result["current_price"] = f"₹{cur_num:,}"
            result["mrp"] = f"₹{mrp_num:,}" if mrp_num > 0 else ""
            result["discount_pct"] = discount_pct
            result["screenshot_path"] = shot_path
            result["product_url"] = page.url
            result["canonical_id"] = canonical_id

        except Exception as e:
            result["reason"] = f"Error capturing deal: {e}"
            print(f"Rejected: {result['reason']}")
        finally:
            await browser.close()

    return result
