# Use official Python 3.12 slim image
FROM python:3.12-slim

# Install OS dependencies needed for Playwright Chromium
RUN apt-get update && apt-get install -y \
    wget \
    curl \
    gnupg \
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libc6 \
    libcairo2 \
    libcups2 \
    libdbus-1-3 \
    libexpat1 \
    libfontconfig1 \
    libgbm1 \
    libgcc1 \
    libglib2.0-0 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libstdc++6 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxcomposite1 \
    libxcursor1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxi6 \
    libxrandr2 \
    libxrender1 \
    libxss1 \
    libxtst6 \
    lsb-release \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium browser binary
RUN python -m playwright install --with-deps chromium

# Copy application source code
COPY price_checker.py .
COPY telegram_deal_forwarder.py .

# Environment variables for Telethon Userbot
ENV TG_API_ID="32206759"
ENV TG_API_HASH="7db3022378b608c86cad321de9eb3261"
ENV TG_STRING_SESSION="1BVtsOH4BuzMhWW4Bhur2zS_0aT8ufbKDjd-HnLzxMWWDWkMDpm8xoaAKv4VA2xZy7zp5b5lwM97GBauLgiIywHOtH4NX-MEb-5fojWfTjSEL4mA9eUYktivUmipj4WCqHp4nf8ytChEG5FZIw8dKD3C049exjIkiFj2aBZqI9O5s95KP76GNU_t3hgmi-ZPni61k_E9mc2WkAj3NDuG7HWkXncRtGqkyuMOTKLMFF1UIOHvRpmr618AzH5T7wUTIiYhQmY8Uq7uVuJGcQqTyO_wGSpZjOA7bz4yK3BREuLJKKbCuEhFnG5h61beww6S-MGoOdnG_Yf8bTYyJNhSJb3obeob0pWw="

# Run the deal forwarder bot
CMD ["python", "-u", "telegram_deal_forwarder.py"]
