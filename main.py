import gc
import os
import asyncio
import logging
import secrets
import time
import json
import base64
import hashlib
from datetime import datetime, timedelta
from io import BytesIO
from threading import Thread
import uuid
import subprocess

from flask import Flask, request, jsonify, render_template_string
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.error import TelegramError
import requests
from PIL import Image, ImageDraw, ImageFont

# Configuration
BOT_TOKEN = "8588413389:AAFi-k5lYA3KE9vfE9nYyMd2TgPSPc34h3o"
ADMIN_ID = 1801208219
GROUP_CHAT_ID = -1003844211170
HOT_WALLET = "UQABSEcWzJVmtLdZDUMyCs5EGrKOHWKWq3ftFNY0IItHgYTa"
TON_API_KEY = "4c3e06303fb6a2b11dbc522c8ada5891eade8106197589b1478e2f35ef3814a2"
PAYMENT_AMOUNT_USD = 0.99
TICKET_EXPIRY_DAYS = 3
PORT = int(os.environ.get("PORT", 10000))

# Flask App
app = Flask(__name__)

# In-memory storage (lightweight for render.com)
tickets_storage = {}
user_access = {}
pending_payments = {}
ouo_link = ""
cloudflare_app_url = ""

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_app_url():
    global cloudflare_app_url
    
    if cloudflare_app_url:
        return cloudflare_app_url
    
    render_url = os.environ.get("RENDER_EXTERNAL_URL")
    if render_url:
        cloudflare_app_url = render_url
        return render_url
    
    try:
        if request:
            cloudflare_app_url = request.host_url.rstrip("/")
            return cloudflare_app_url
    except:
        pass
    
    cloudflare_app_url = f"http://localhost:{PORT}"
    return cloudflare_app_url

def get_ton_price_usd():
    try:
        response = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=the-open-network&vs_currencies=usd", timeout=10)
        if response.status_code == 200:
            data = response.json()
            return data.get("the-open-network", {}).get("usd", 5.0)
    except Exception as e:
        logger.error(f"Error fetching TON price: {e}")
    return 5.0

def usd_to_ton(usd_amount):
    ton_price = get_ton_price_usd()
    return usd_amount / ton_price

def ton_to_nanoton(ton_amount):
    return int(ton_amount * 1_000_000_000)

def check_incoming_transactions(wallet_address, min_amount_nano, since_timestamp):
    try:
        headers = {"X-API-Key": TON_API_KEY}
        url = f"https://toncenter.com/api/v2/getTransactions?address={wallet_address}&limit=100"
        response = requests.get(url, headers=headers, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            transactions = data.get("result", [])
            valid_transactions = []
            
            for tx in transactions:
                tx_time = tx.get("utime", 0)
                if tx_time < since_timestamp:
                    continue
                
                in_msg = tx.get("in_msg", {})
                if in_msg:
                    value = int(in_msg.get("value", 0))
                    source = in_msg.get("source", "")
                    
                    if value >= min_amount_nano * 0.95:
                        valid_transactions.append({
                            "hash": tx.get("transaction_id", {}).get("hash", ""),
                            "value": value,
                            "source": source,
                            "time": tx_time,
                            "comment": in_msg.get("message", "")
                        })
            
            return valid_transactions
        return []
    except Exception as e:
        logger.error(f"Error checking transactions: {e}")
        return []

MINI_APP_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-select=none">
    <title>GoreSignal</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <script src="https://unpkg.com/@tonconnect/ui@latest/dist/tonconnect-ui.min.js"></script>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            -webkit-user-select: none;
            -moz-user-select: none;
            -ms-user-select: none;
            user-select: none;
        }
        
        body {
            font-family: Arial, sans-serif;
            background: linear-gradient(135deg, #1a1a1a 0%, #2d2d2d 100%);
            color: #fff;
            min-height: 100vh;
            padding: 20px;
        }
        
        .logo {
            text-align: center;
            margin-bottom: 30px;
        }
        
        .logo h1 {
            font-size: 28px;
            color: #ff4444;
        }
        
        .container {
            max-width: 600px;
            margin: 0 auto;
        }
        
        .search-box {
            background: rgba(255,255,255,0.1);
            border-radius: 15px;
            padding: 20px;
            margin-bottom: 20px;
            backdrop-filter: blur(10px);
        }
        
        .search-box input {
            width: 100%;
            padding: 15px;
            border: 2px solid #ff4444;
            border-radius: 10px;
            background: rgba(0,0,0,0.5);
            color: #fff;
            font-size: 16px;
            font-family: monospace;
            text-align: center;
        }
        
        .search-box button {
            width: 100%;
            padding: 15px;
            margin-top: 10px;
            border: none;
            border-radius: 10px;
            background: #ff4444;
            color: #fff;
            font-size: 18px;
            font-weight: bold;
            cursor: pointer;
        }
        
        .content-viewer {
            display: none;
            background: rgba(255,255,255,0.1);
            border-radius: 15px;
            padding: 20px;
        }
        
        .content-viewer.active {
            display: block;
        }
        
        .media-container {
            position: relative;
            width: 100%;
            border-radius: 10px;
            overflow: hidden;
            background: #000;
            pointer-events: none;
        }
        
        .media-container video,
        .media-container img {
            width: 100%;
            height: auto;
            display: block;
        }
        
        .timer-overlay {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            background: rgba(255,68,68,0.9);
            color: #fff;
            padding: 15px;
            text-align: center;
            font-size: 18px;
            font-weight: bold;
            z-index: 1000;
        }
        
        .payment-modal {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0,0,0,0.95);
            z-index: 2000;
            padding: 20px;
            align-items: center;
            justify-content: center;
        }
        
        .payment-modal.active {
            display: flex;
        }
        
            <div style="margin-top: 20px; padding-top: 20px; border-top: 1px solid rgba(255,255,255,0.2);">
                <p style="font-size: 14px; color: #aaa;">Or pay manually:</p>
                
                <button onclick="payWithDeepLink()" style="background: #0098ea; margin-bottom: 15px;">
                    Open Wallet App ↗️
                </button>

                <p style="font-size: 14px; color: #aaa;">Or copy address:</p>
                <div class="wallet-address" id="walletAddress" onclick="copyWallet()">
                    UQABSEcWzJVmtLdZDUMyCs5EGrKOHWKWq3ftFNY0IItHgYTa
                </div>

        
        .payment-content {
            background: linear-gradient(135deg, #2d2d2d 0%, #1a1a1a 100%);
            border-radius: 20px;
            padding: 30px;
            max-width: 400px;
            width: 100%;
            text-align: center;
            border: 2px solid #ff4444;
        }
        
        .payment-content h2 {
            color: #ff4444;
            margin-bottom: 20px;
        }
        
        .payment-content .price {
            font-size: 32px;
            color: #0098ea;
            margin: 20px 0;
            font-weight: bold;
        }
        
        .payment-content button {
            width: 100%;
            padding: 15px;
            margin-top: 15px;
            border: none;
            border-radius: 10px;
            background: #ff4444;
            color: #fff;
            font-size: 16px;
            font-weight: bold;
            cursor: pointer;
        }
        
        .wallet-address {
            background: rgba(0,0,0,0.5);
            padding: 10px;
            border-radius: 8px;
            font-family: monospace;
            font-size: 12px;
            word-break: break-all;
            margin: 10px 0;
            cursor: pointer;
        }
        
        .error-message {
            background: rgba(255,68,68,0.2);
            border: 2px solid #ff4444;
            border-radius: 10px;
            padding: 15px;
            margin-top: 10px;
        }
        
        .success-message {
            background: rgba(68,255,68,0.2);
            border: 2px solid #44ff44;
            border-radius: 10px;
            padding: 15px;
            margin-top: 10px;
            color: #44ff44;
        }
        
        .watermark-overlay {
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            pointer-events: none;
            z-index: 100;
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            grid-template-rows: repeat(3, 1fr);
            opacity: 0.3;
        }
        
        .watermark-overlay span {
            display: flex;
            align-items: center;
            justify-content: center;
            color: #fff;
            font-weight: bold;
            font-size: 14px;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.8);
        }
        
        .loading {
            display: inline-block;
            width: 20px;
            height: 20px;
            border: 3px solid rgba(255,255,255,.3);
            border-radius: 50%;
            border-top-color: #fff;
            animation: spin 1s ease-in-out infinite;
        }
        
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
        
        .payment-status {
            margin-top: 15px;
            padding: 10px;
            border-radius: 8px;
            background: rgba(0,152,234,0.2);
            border: 1px solid #0098ea;
        }
    </style>
</head>
<body>
    <div class="logo">
        <h1>GoreSignal</h1>
    </div>
    
    <div class="container">
        <div class="search-box" id="searchBox">
            <input type="text" id="ticketInput" placeholder="Enter ticket code" maxlength="8">
            <button onclick="searchTicket()">Search</button>
            <div id="message"></div>
        </div>
        
        <div class="content-viewer" id="contentViewer">
            <h2 id="contentTitle" style="margin-bottom: 15px; text-align: center;"></h2>
            <div class="media-container" id="mediaContainer">
                <div class="watermark-overlay">
                    <span>@GoreSignal</span>
                    <span>@GoreSignal</span>
                    <span>@GoreSignal</span>
                    <span>@GoreSignal</span>
                    <span>@GoreSignal</span>
                    <span>@GoreSignal</span>
                    <span>@GoreSignal</span>
                    <span>@GoreSignal</span>
                    <span>@GoreSignal</span>
                </div>
            </div>
        </div>
    </div>
    
    <div class="timer-overlay" id="timerOverlay" style="display: none;">
        Time remaining: <span id="timerText">00:00</span>
    </div>
    
    <div class="payment-modal" id="paymentModal">
        <div class="payment-content">
            <h2>Unlock Full Access</h2>
            <p>3 days unlimited access</p>
            <div class="price" id="tonPrice">Loading...</div>
            
            <div id="tonConnectContainer"></div>
            
            <div style="margin-top: 20px; padding-top: 20px; border-top: 1px solid rgba(255,255,255,0.2);">
                <p style="font-size: 14px; color: #aaa;">Or send manually to:</p>
                <div class="wallet-address" id="walletAddress" onclick="copyWallet()">
                    UQABSEcWzJVmtLdZDUMyCs5EGrKOHWKWq3ftFNY0IItHgYTa
                </div>
                <button onclick="copyWallet()" style="background: #444; padding: 8px 15px; font-size: 14px;">Copy Address</button>
                
                <div class="payment-status" id="paymentStatus" style="display: none;">
                    <div class="loading"></div>
                    <p style="margin-top: 10px;">Checking payment...</p>
                </div>
            </div>
            
            <button onclick="closePaymentModal()" style="background: #666; margin-top: 20px;">Cancel</button>
        </div>
    </div>
    
    <script>
        let tg = window.Telegram.WebApp;
        tg.expand();
        tg.disableVerticalSwipes();
        
        let currentTicket = "";
        let timerInterval = null;
        let paymentCheckInterval = null;
        let userFingerprint = "";
        let currentPaymentId = "";
        let tonConnectUI = null;
        let requiredTonAmount = 0;
        
        try {
            tonConnectUI = new TON_CONNECT_UI.TonConnectUI({
                manifestUrl: window.location.origin + "/tonconnect-manifest.json",
                buttonRootId: "tonConnectContainer"
            });
        } catch (e) {
            console.error("TON Connect error:", e);
        }
        
        function generateFingerprint() {
            const data = {
                userAgent: navigator.userAgent,
                language: navigator.language,
                platform: navigator.platform,
                screenResolution: screen.width + "x" + screen.height,
                timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                userId: tg.initDataUnsafe?.user?.id || "unknown"
            };
            return btoa(JSON.stringify(data));
        }
        
        userFingerprint = generateFingerprint();
        
        document.addEventListener("visibilitychange", function() {
            if (document.hidden) {
                const media = document.querySelector("video, img");
                if (media && !document.querySelector(".content-viewer").dataset.hasAccess) {
                    media.style.display = "none";
                }
            } else {
                const media = document.querySelector("video, img");
                if (media) media.style.display = "block";
            }
        });
        
        document.addEventListener("contextmenu", e => e.preventDefault());
        
        async function searchTicket() {
            const ticket = document.getElementById("ticketInput").value.trim();
            const message = document.getElementById("message");
            
            if (!ticket) {
                message.innerHTML = '<div class="error-message">Please enter a ticket</div>';
                return;
            }
            
            message.innerHTML = '<div style="text-align: center;">Searching...</div>';
            
            try {
                const response = await fetch("/api/get-content", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({
                        ticket: ticket,
                        user_id: tg.initDataUnsafe?.user?.id || 0,
                        fingerprint: userFingerprint
                    })
                });
                
                const data = await response.json();
                
                if (data.success) {
                    currentTicket = ticket;
                    displayContent(data);
                    message.innerHTML = "";
                } else {
                    message.innerHTML = '<div class="error-message">' + data.error + "</div>";
                }
            } catch (error) {
                message.innerHTML = '<div class="error-message">Connection error</div>';
            }
        }
        
        function displayContent(data) {
            const viewer = document.getElementById("contentViewer");
            const searchBox = document.getElementById("searchBox");
            const mediaContainer = document.getElementById("mediaContainer");
            const titleElement = document.getElementById("contentTitle");
            
            titleElement.textContent = data.title || "Exclusive Content";
            viewer.dataset.hasAccess = data.has_access;
            
            const oldMedia = mediaContainer.querySelector("video, img");
            if (oldMedia) oldMedia.remove();
            
            if (data.media_type === "video") {
                const video = document.createElement("video");
                video.src = "data:video/mp4;base64," + data.media_data;
                video.controls = data.has_access;
                video.controlsList = "nodownload";
                video.disablePictureInPicture = true;
                video.autoplay = true;
                video.loop = true;
                mediaContainer.insertBefore(video, mediaContainer.firstChild);
                
                if (!data.has_access && data.duration) {
                    startTimer(data.duration);
                }
            } else if (data.media_type === "image") {
                const img = document.createElement("img");
                img.src = "data:image/jpeg;base64," + data.media_data;
                mediaContainer.insertBefore(img, mediaContainer.firstChild);
                
                if (!data.has_access) {
                    startTimer(30);
                }
            }
            
            searchBox.style.display = "none";
            viewer.classList.add("active");
        }
        
        function startTimer(duration) {
            const timerOverlay = document.getElementById("timerOverlay");
            const timerText = document.getElementById("timerText");
            timerOverlay.style.display = "block";
            
            let remaining = duration;
            
            timerInterval = setInterval(() => {
                remaining--;
                const minutes = Math.floor(remaining / 60);
                const seconds = remaining % 60;
                timerText.textContent = minutes.toString().padStart(2, "0") + ":" + seconds.toString().padStart(2, "0");
                
                if (remaining <= 0) {
                    clearInterval(timerInterval);
                    showPaymentModal();
                }
            }, 1000);
        }
        
        async function showPaymentModal() {
            document.getElementById("paymentModal").classList.add("active");
            const video = document.querySelector("video");
            if (video) video.pause();
            
            try {
                const response = await fetch("/api/get-ton-price");
                const data = await response.json();
                requiredTonAmount = data.ton_amount;
                document.getElementById("tonPrice").textContent = requiredTonAmount.toFixed(4) + " TON";
            } catch (e) {
                document.getElementById("tonPrice").textContent = "~0.20 TON";
                requiredTonAmount = 0.20;
            }
        }
        
        async function payWithDeepLink() {
            const button = document.querySelector('button[onclick="payWithDeepLink()"]');
            const originalText = button.innerText;
            button.innerText = "Loading...";
            
            try {
                // 1. نطلب من السيرفر إنشاء عملية دفع لتسجيلها
                const response = await fetch("/api/create-payment", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({
                        ticket: currentTicket,
                        user_id: tg.initDataUnsafe?.user?.id || 0
                    })
                });
                
                const data = await response.json();
                
                if (data.success) {
                    // 2. تشغيل المراقبة للتأكد من وصول الدفع
                    if (!paymentCheckInterval) {
                        checkPaymentStatus(data.payment_id);
                    }

                    // 3. تجهيز رابط الدفع العميق
                    // الصيغة: ton://transfer/<ADDRESS>?amount=<NANO>&text=<COMMENT>
                    const address = data.wallet_address;
                    const amount = data.amount_nano;
                    const comment = data.comment;
                    
                    const deepLink = `ton://transfer/${address}?amount=${amount}&text=${comment}`;
                    
                    // 4. فتح المحفظة
                    window.location.href = deepLink;
                }
            } catch (e) {
                console.error("Error opening wallet:", e);
                alert("Could not open wallet automatically. Please copy the address.");
            } finally {
                button.innerText = originalText;
            }
        }

        
        function closePaymentModal() {
            document.getElementById("paymentModal").classList.remove("active");
            if (paymentCheckInterval) {
                clearInterval(paymentCheckInterval);
            }
        }
        
        async function checkPaymentStatus(paymentId) {
            document.getElementById("paymentStatus").style.display = "block";
            
            paymentCheckInterval = setInterval(async () => {
                try {
                    const response = await fetch("/api/check-payment", {
                        method: "POST",
                        headers: {"Content-Type": "application/json"},
                        body: JSON.stringify({
                            payment_id: paymentId,
                            ticket: currentTicket,
                            user_id: tg.initDataUnsafe?.user?.id || 0
                        })
                    });
                    
                    const data = await response.json();
                    
                    if (data.paid) {
                        clearInterval(paymentCheckInterval);
                        closePaymentModal();
                        clearInterval(timerInterval);
                        document.getElementById("timerOverlay").style.display = "none";
                        
                        const message = document.getElementById("message");
                        message.innerHTML = '<div class="success-message">Payment successful!</div>';
                        
                        setTimeout(() => {
                            searchTicket();
                        }, 2000);
                    }
                } catch (error) {
                    console.error("Payment check error:", error);
                }
            }, 3000);
            
            setTimeout(() => {
                if (paymentCheckInterval) {
                    clearInterval(paymentCheckInterval);
                    document.getElementById("paymentStatus").innerHTML = "<p>Timeout. Access will activate automatically if payment was made.</p>";
                }
            }, 600000);
        }
        
        function copyWallet() {
            const wallet = "UQABSEcWzJVmtLdZDUMyCs5EGrKOHWKWq3ftFNY0IItHgYTa";
            
            if (navigator.clipboard) {
                navigator.clipboard.writeText(wallet).then(() => {
                    alert("Address copied!");
                    if (!paymentCheckInterval) {
                        createManualPayment();
                    }
                });
            } else {
                alert("Address: " + wallet);
            }
        }
        
        async function createManualPayment() {
            try {
                const response = await fetch("/api/create-payment", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({
                        ticket: currentTicket,
                        user_id: tg.initDataUnsafe?.user?.id || 0
                    })
                });
                
                const data = await response.json();
                
                if (data.success) {
                    checkPaymentStatus(data.payment_id);
                }
            } catch (e) {
                console.error("Payment creation error:", e);
            }
        }
    </script>
</body>
</html>
"""

TON_MANIFEST = {
    "url": "",
    "name": "GoreSignal",
    "iconUrl": "https://raw.githubusercontent.com/ton-blockchain/ton-connect/main/assets/ton_symbol.png"
}

def generate_ticket():
    return secrets.token_urlsafe(6)[:8]

def add_watermark(image_bytes, text="@GoreSignal"):
    try:
        img = Image.open(BytesIO(image_bytes))
        draw = ImageDraw.Draw(img)
        
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 30)
        except:
            font = ImageFont.load_default()
        
        width, height = img.size
        positions = [
            (50, 50), (width - 200, 50),
            (50, height - 80), (width - 200, height - 80),
            (width // 2 - 100, height // 2)
        ]
        
        for pos in positions:
            draw.text(pos, text, fill=(255, 255, 255, 128), font=font)
        
        output = BytesIO()
        img.save(output, format="JPEG")
        return output.getvalue()
    except Exception as e:
        logger.error(f"Watermark error: {e}")
        return image_bytes

def cleanup_expired_tickets():
    current_time = datetime.now()
    expired = []
    
    for ticket_id, data in tickets_storage.items():
        created_at = datetime.fromisoformat(data["created_at"])
        if current_time - created_at > timedelta(days=TICKET_EXPIRY_DAYS):
            expired.append(ticket_id)
    
    for ticket_id in expired:
        del tickets_storage[ticket_id]
        logger.info(f"Deleted expired ticket: {ticket_id}")

def cleanup_expired_payments():
    current_time = datetime.now()
    expired = []
    
    for payment_id, data in pending_payments.items():
        created_at = datetime.fromisoformat(data["created_at"])
        if current_time - created_at > timedelta(hours=1):
            expired.append(payment_id)
    
    for payment_id in expired:
        del pending_payments[payment_id]

@app.route("/")
def index():
    return render_template_string(MINI_APP_HTML)

@app.route("/tonconnect-manifest.json")
def ton_manifest():
    manifest = TON_MANIFEST.copy()
    manifest["url"] = get_app_url()
    return jsonify(manifest)

@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "tickets": len(tickets_storage),
        "pending_payments": len(pending_payments)
    })

@app.route("/api/get-ton-price")
def get_ton_price_api():
    ton_amount = usd_to_ton(PAYMENT_AMOUNT_USD)
    return jsonify({
        "ton_amount": ton_amount,
        "usd_amount": PAYMENT_AMOUNT_USD,
        "ton_price_usd": get_ton_price_usd()
    })

@app.route("/api/get-content", methods=["POST"])
def get_content():
    try:
        data = request.json
        ticket = data.get("ticket", "").strip()
        user_id = data.get("user_id", 0)
        fingerprint = data.get("fingerprint", "")
        
        cleanup_expired_tickets()
        
        if ticket not in tickets_storage:
            return jsonify({"success": False, "error": "Invalid or expired ticket"})
        
        ticket_data = tickets_storage[ticket]
        created_at = datetime.fromisoformat(ticket_data["created_at"])
        if datetime.now() - created_at > timedelta(days=TICKET_EXPIRY_DAYS):
            return jsonify({"success": False, "error": "Ticket expired"})
        
        user_key = str(user_id)
        has_paid_access = False
        
        if user_key in user_access and ticket in user_access[user_key]:
            user_ticket_data = user_access[user_key][ticket]
            
        if user_key in user_access and ticket in user_access[user_key]:
            user_ticket_data = user_access[user_key][ticket]
            
            # --- تم تعطيل الحماية للسماح بتعدد الأجهزة ---
            # if user_ticket_data.get("fingerprint") != fingerprint:
            #     return jsonify({"success": False, "error": "Unauthorized access detected"})
            # ---------------------------------------------
            
            if user_ticket_data.get("paid"):
                paid_at = datetime.fromisoformat(user_ticket_data.get("paid_at", ticket_data["created_at"]))
        else:
            if user_key not in user_access:
                user_access[user_key] = {}
            user_access[user_key][ticket] = {
                "paid": False,
                "first_viewed_at": datetime.now().isoformat(),
                "fingerprint": fingerprint
            }
        
        return jsonify({
            "success": True,
            "media_type": ticket_data["media_type"],
            "media_data": ticket_data["media_data"],
            "title": ticket_data.get("title", ""),
            "has_access": has_paid_access,
            "duration": ticket_data.get("duration", 120)
        })
        
    except Exception as e:
        logger.error(f"Get content error: {e}")
        return jsonify({"success": False, "error": "Server error"})

@app.route("/api/create-payment", methods=["POST"])
def create_payment():
    try:
        data = request.json
        ticket = data.get("ticket")
        user_id = data.get("user_id")
        
        cleanup_expired_payments()
        
        payment_id = str(uuid.uuid4())
        ton_amount = usd_to_ton(PAYMENT_AMOUNT_USD)
        amount_nano = ton_to_nanoton(ton_amount)
        
        pending_payments[payment_id] = {
            "ticket": ticket,
            "user_id": user_id,
            "amount_nano": amount_nano,
            "amount_ton": ton_amount,
            "created_at": datetime.now().isoformat(),
            "status": "pending"
        }
        
        comment = f"GoreSignal_{payment_id[:8]}"
        
        logger.info(f"Payment created: {payment_id} for user {user_id}, ticket {ticket}, amount {ton_amount} TON")
        
        return jsonify({
            "success": True,
            "payment_id": payment_id,
            "wallet_address": HOT_WALLET,
            "amount_nano": amount_nano,
            "amount_ton": ton_amount,
            "comment": comment
        })
        
    except Exception as e:
        logger.error(f"Create payment error: {e}")
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/check-payment", methods=["POST"])
def check_payment():
    try:
        data = request.json
        payment_id = data.get("payment_id")
        ticket = data.get("ticket")
        user_id = str(data.get("user_id"))
        
        if payment_id == "check":
            for pid, pdata in pending_payments.items():
                if pdata.get("ticket") == ticket and str(pdata.get("user_id")) == user_id:
                    payment_id = pid
                    break
            
            if not payment_id or payment_id == "check":
                return jsonify({"paid": False})
        
        if payment_id not in pending_payments:
            if user_id in user_access and ticket in user_access.get(user_id, {}):
                if user_access[user_id][ticket].get("paid"):
                    return jsonify({"paid": True})
            return jsonify({"paid": False})
        
        payment_data = pending_payments[payment_id]
        
        if payment_data.get("status") == "completed":
            return jsonify({"paid": True})
        
        created_at = datetime.fromisoformat(payment_data["created_at"])
        since_timestamp = int(created_at.timestamp())
        
        transactions = check_incoming_transactions(
            HOT_WALLET,
            payment_data["amount_nano"],
            since_timestamp
        )
        
        if transactions:
            tx = transactions[0]
            
            logger.info(f"Payment verified! TxHash: {tx['hash']}, Amount: {tx['value']} nano, User: {user_id}, Ticket: {ticket}")
            
            if user_id not in user_access:
                user_access[user_id] = {}
            
            user_access[user_id][ticket] = {
                "paid": True,
                "paid_at": datetime.now().isoformat(),
                "fingerprint": user_access.get(user_id, {}).get(ticket, {}).get("fingerprint", ""),
                "tx_hash": tx["hash"],
                "amount_paid": tx["value"]
            }
            
            payment_data["status"] = "completed"
            payment_data["tx_hash"] = tx["hash"]
            payment_data["completed_at"] = datetime.now().isoformat()
            
            asyncio.create_task(notify_admin_payment(
                user_id,
                ticket,
                payment_id,
                tx["value"] / 1_000_000_000,
                tx["hash"]
            ))
            
            return jsonify({"paid": True, "tx_hash": tx["hash"]})
        
        return jsonify({"paid": False})
        
    except Exception as e:
        logger.error(f"Check payment error: {e}")
        return jsonify({"paid": False, "error": str(e)})
        
async def clean_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # الحماية: التأكد أن المرسل هو الأدمن فقط
    if update.effective_user.id != ADMIN_ID:
        return

    # 1. مسح جميع التذاكر والمحتوى (فيديوهات وصور)
    tickets_storage.clear()
    
    # 2. مسح جميع عمليات الدفع المعلقة
    pending_payments.clear()
    
    # 3. (اختياري) مسح سجلات دخول المستخدمين
    # ملاحظة: هذا سيمسح بيانات من دفعوا سابقاً، لكن بما أن التذاكر حذفت فلا يهم
    user_access.clear()

    # 4. إجبار النظام على تنظيف الذاكرة فوراً
    gc.collect()
    
    await update.message.reply_text(
        "🧹 **Memory Cleaned!**\n\n"
        "All tickets, media, and pending payments have been deleted.\n"
        "Server memory is now free.",
        parse_mode="Markdown"
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id == ADMIN_ID:
        app_url = get_app_url()
        
        await update.message.reply_text(
            f"Welcome Admin!\n\n"
            f"App URL:\n{app_url}\n\n"
            f"Stats:\n"
            f"Active tickets: {len(tickets_storage)}\n"
            f"Pending payments: {len(pending_payments)}\n"
            f"Users: {len(user_access)}\n\n"
            f"Send media (video/photo) to create a new ticket\n"
            f"Use /link to set ouo.io link\n"
            f"Use /stats for detailed statistics\n\n"
            f"URL is auto-detected from server!",
            disable_web_page_preview=True
        )
    else:
        await update.message.reply_text(
            "This bot is for admin only!\n"
            "To access content, use the mini app."
        )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    
    total_paid = sum(1 for user_tickets in user_access.values()
                     for ticket_data in user_tickets.values()
                     if ticket_data.get("paid"))
    
    total_revenue_nano = sum(ticket_data.get("amount_paid", 0)
                             for user_tickets in user_access.values()
                             for ticket_data in user_tickets.values()
                             if ticket_data.get("paid"))
    
    total_revenue_ton = total_revenue_nano / 1_000_000_000
    ton_price = get_ton_price_usd()
    total_revenue_usd = total_revenue_ton * ton_price
    
    completed_payments = sum(1 for p in pending_payments.values() if p.get("status") == "completed")
    pending = len(pending_payments) - completed_payments
    
    stats_text = (
        f"GoreSignal Statistics\n\n"
        f"Active tickets: {len(tickets_storage)}\n"
        f"Total users: {len(user_access)}\n\n"
        f"Payments:\n"
        f"Completed: {total_paid}\n"
        f"Pending: {pending}\n\n"
        f"Revenue:\n"
        f"{total_revenue_ton:.4f} TON\n"
        f"${total_revenue_usd:.2f} USD\n\n"
        f"Current TON price: ${ton_price:.2f}"
    )
    
    await update.message.reply_text(stats_text)

async def link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ouo_link
    
    if update.effective_user.id != ADMIN_ID:
        return
    
    if context.args:
        ouo_link = context.args[0]
        await update.message.reply_text(f"Link set:\n{ouo_link}")
    else:
        await update.message.reply_text(
            f"Current link:\n{ouo_link if ouo_link else 'Not set'}\n\n"
            f"To change: /link <new_link>"
        )

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    
    try:
        ticket_id = generate_ticket()
        
        await update.message.reply_text(
            f"Ticket created: `{ticket_id}`\n\n"
            f"Send title for this content:",
            parse_mode="Markdown"
        )
        
        media_type = None
        media_data = None
        duration = 120
        
        if update.message.photo:
            photo = update.message.photo[-1]
            file = await context.bot.get_file(photo.file_id)
            photo_bytes = await file.download_as_bytearray()
            
            watermarked = add_watermark(bytes(photo_bytes))
            media_data = base64.b64encode(watermarked).decode()
            media_type = "image"
            duration = 30
            
        elif update.message.video:
            video = update.message.video
            file = await context.bot.get_file(video.file_id)
            video_bytes = await file.download_as_bytearray()
            
            media_data = base64.b64encode(bytes(video_bytes)).decode()
            media_type = "video"
            duration = video.duration if video.duration else 120
            
            # --- إضافة: حفظ الصورة المصغرة (Thumbnail) ---
            thumbnail_data = None
            if video.thumbnail:
                thumb_file = await context.bot.get_file(video.thumbnail.file_id)
                thumb_bytes = await thumb_file.download_as_bytearray()
                # إضافة العلامة المائية للصورة المصغرة أيضاً (اختياري)
                watermarked_thumb = add_watermark(bytes(thumb_bytes))
                thumbnail_data = base64.b64encode(watermarked_thumb).decode()
            # ---------------------------------------------
            
        elif update.message.document:
            document = update.message.document
            if document.mime_type and "video" in document.mime_type:
                file = await context.bot.get_file(document.file_id)
                video_bytes = await file.download_as_bytearray()
                media_data = base64.b64encode(bytes(video_bytes)).decode()
                media_type = "video"
            elif document.mime_type and "image" in document.mime_type:
                file = await context.bot.get_file(document.file_id)
                image_bytes = await file.download_as_bytearray()
                watermarked = add_watermark(bytes(image_bytes))
                media_data = base64.b64encode(watermarked).decode()
                media_type = "image"
                duration = 30
        
        if media_data:
            tickets_storage[ticket_id] = {
                "media_type": media_type,
                "media_data": media_data,
                "created_at": datetime.now().isoformat(),
                "title": "",
                "duration": duration,
                "thumbnail": thumbnail_data if 'thumbnail_data' in locals() else None  # <--- أضف هذا السطر
            }
            
            context.user_data["pending_ticket"] = ticket_id
            
    except Exception as e:
        logger.error(f"Handle media error: {e}")
        await update.message.reply_text(f"Error: {str(e)}")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    
    pending_ticket = context.user_data.get("pending_ticket")
    
    if pending_ticket and pending_ticket in tickets_storage:
        title = update.message.text
        tickets_storage[pending_ticket]["title"] = title
        
        await post_to_group(context.bot, pending_ticket, title)
        
        context.user_data.pop("pending_ticket", None)
        
        await update.message.reply_text(
            f"Content published!\n\n"
            f"Ticket: `{pending_ticket}`\n"
            f"Title: {title}\n"
            f"Validity: {TICKET_EXPIRY_DAYS} days",
            parse_mode="Markdown"
        )

async def post_to_group(bot, ticket_id, title):
    try:
        ticket_data = tickets_storage[ticket_id]
        
        keyboard = [[InlineKeyboardButton("Watch for free", url=ouo_link if ouo_link else "https://t.me/GoreSignal")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        ton_amount = usd_to_ton(PAYMENT_AMOUNT_USD)
        
        message_text = (
            f"{title}\n\n"
            f"Ticket: `{ticket_id}`\n\n"
            f"Full access:\n"
            f"${PAYMENT_AMOUNT_USD} USD\n"
            f"~{ton_amount:.4f} TON\n"
            f"3 days validity\n\n"
            f"How to watch:\n"
            f"1. Copy ticket above\n"
            f"2. Click button below\n"
            f"3. Paste ticket in app\n"
            f"4. Watch free or pay for full access"
        )
        
        # إذا كان المحتوى صورة
        if ticket_data["media_type"] == "image":
            image_bytes = base64.b64decode(ticket_data["media_data"])
            await bot.send_photo(
                chat_id=GROUP_CHAT_ID,
                photo=BytesIO(image_bytes),
                caption=message_text,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
        
        # إذا كان فيديو ولديه صورة مصغرة (Thumbnail)
        elif ticket_data.get("thumbnail"):
            thumb_bytes = base64.b64decode(ticket_data["thumbnail"])
            await bot.send_photo(
                chat_id=GROUP_CHAT_ID,
                photo=BytesIO(thumb_bytes), # نرسل الصورة المصغرة
                caption=message_text,       # النص يكون كابشن للصورة
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
            
        # إذا كان فيديو ولا يوجد صورة مصغرة (احتياط)
        else:
            await bot.send_message(
                chat_id=GROUP_CHAT_ID,
                text=message_text,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
            
    except Exception as e:
        logger.error(f"Post to group error: {e}")

async def notify_admin_payment(user_id, ticket, payment_id, amount_ton, tx_hash):
    try:
        bot = telegram_app.bot
        ton_price = get_ton_price_usd()
        amount_usd = amount_ton * ton_price
        
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"New payment!\n\n"
                f"User: `{user_id}`\n"
                f"Ticket: `{ticket}`\n"
                f"Payment ID: `{payment_id[:16]}...`\n"
                f"Amount: {amount_ton:.4f} TON (${amount_usd:.2f})\n"
                f"Transaction: `{tx_hash[:16]}...`\n"
                f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            ),
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Notify admin error: {e}")

telegram_app = Application.builder().token(BOT_TOKEN).build()

telegram_app.add_handler(CommandHandler("start", start_command))
telegram_app.add_handler(CommandHandler("link", link_command))
telegram_app.add_handler(CommandHandler("stats", stats_command))
telegram_app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, handle_media))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

def run_flask():
    app.run(host="0.0.0.0", port=PORT)

def run_telegram():
    asyncio.set_event_loop(asyncio.new_event_loop())
    telegram_app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    logger.info("Starting GoreSignal Bot...")
    logger.info(f"Admin ID: {ADMIN_ID}")
    logger.info(f"Group Chat ID: {GROUP_CHAT_ID}")
    logger.info(f"Hot Wallet: {HOT_WALLET}")
    logger.info(f"Payment Amount: ${PAYMENT_AMOUNT_USD} USD")
    
    app_url = get_app_url()
    logger.info(f"App URL: {app_url}")
    logger.info(f"Mini App: {app_url}")
    logger.info(f"Share this URL with users!")
    
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    run_telegram()