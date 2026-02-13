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

app = Flask(**name**)

# In-memory storage (lightweight for render.com)

tickets_storage = {}  # {ticket_id: {media_data, created_at, title}}
user_access = {}  # {user_id: {ticket_id: {paid, first_viewed_at, fingerprint}}}
pending_payments = {}  # {payment_id: {ticket, user_id, amount, created_at}}
ouo_link = “”  # Will be set via /link command
cloudflare_app_url = “”  # Will be auto-generated

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(**name**)

# Auto-deploy to Cloudflare Pages

def deploy_to_cloudflare():
“”“Deploy Mini App to Cloudflare Pages automatically”””
try:
# Get server URL (Render URL)
server_url = os.environ.get(“RENDER_EXTERNAL_URL”, f”http://localhost:{PORT}”)

```
    # Create worker script
    worker_script = f"""
```

// Cloudflare Worker - GoreSignal Mini App Proxy
const API_URL = “{server_url}”;

export default {{
async fetch(request) {{
const url = new URL(request.url);

```
// Proxy all requests to our backend
if (url.pathname.startsWith('/api/')) {{
  const apiUrl = API_URL + url.pathname + url.search;
  const response = await fetch(apiUrl, {{
    method: request.method,
    headers: request.headers,
    body: request.body
  }});
  
  return new Response(response.body, {{
    status: response.status,
    headers: {{
      ...response.headers,
      'Access-Control-Allow-Origin': '*'
    }}
  }});
}}

// Serve the HTML app
return fetch(API_URL + url.pathname, request);
```

}}
}}
“””

```
    # Try to use Cloudflare API to deploy
    # For now, we'll use the render URL directly as we can't deploy to CF without auth
    # But we'll create a deployment-ready worker script
    
    worker_file = '/tmp/worker.js'
    with open(worker_file, 'w') as f:
        f.write(worker_script)
    
    logger.info(f"Worker script created at {worker_file}")
    logger.info("To deploy to Cloudflare, use: wrangler deploy")
    
    return server_url
    
except Exception as e:
    logger.error(f"Cloudflare deployment error: {e}")
    return None
```

def get_app_url():
“”“Get the application URL”””
global cloudflare_app_url

```
if cloudflare_app_url:
    return cloudflare_app_url

# Try different sources
render_url = os.environ.get("RENDER_EXTERNAL_URL")
if render_url:
    cloudflare_app_url = render_url
    return render_url

# Try to get from request context
try:
    from flask import request
    if request:
        cloudflare_app_url = request.host_url.rstrip('/')
        return cloudflare_app_url
except:
    pass

# Fallback
cloudflare_app_url = f"http://localhost:{PORT}"
return cloudflare_app_url
```

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(**name**)

# TON Helper Functions

def get_ton_price_usd():
“”“Get current TON price in USD”””
try:
response = requests.get(‘https://api.coingecko.com/api/v3/simple/price?ids=the-open-network&vs_currencies=usd’, timeout=10)
if response.status_code == 200:
data = response.json()
return data.get(‘the-open-network’, {}).get(‘usd’, 5.0)  # Default fallback to $5
except Exception as e:
logger.error(f”Error fetching TON price: {e}”)
return 5.0  # Fallback price

def usd_to_ton(usd_amount):
“”“Convert USD to TON”””
ton_price = get_ton_price_usd()
return usd_amount / ton_price

def ton_to_nanoton(ton_amount):
“”“Convert TON to nanoTON”””
return int(ton_amount * 1_000_000_000)

def verify_ton_transaction(tx_hash, expected_amount_nano, expected_destination):
“”“Verify TON transaction using TON Center API”””
try:
headers = {
‘X-API-Key’: TON_API_KEY
}

```
    # Get transaction details from TON Center
    url = f'https://toncenter.com/api/v2/getTransactions?address={expected_destination}&limit=50'
    response = requests.get(url, headers=headers, timeout=15)
    
    if response.status_code == 200:
        data = response.json()
        transactions = data.get('result', [])
        
        for tx in transactions:
            # Check transaction hash
            tx_id = tx.get('transaction_id', {}).get('hash', '')
            
            # Check incoming messages
            in_msg = tx.get('in_msg', {})
            if in_msg:
                value = int(in_msg.get('value', 0))
                source = in_msg.get('source', '')
                destination = in_msg.get('destination', '')
                
                # Verify transaction matches our criteria
                if destination == expected_destination and value >= expected_amount_nano * 0.95:  # 5% tolerance
                    return True, tx_id, value, source
    
    return False, None, 0, None
    
except Exception as e:
    logger.error(f"Error verifying transaction: {e}")
    return False, None, 0, None
```

def check_incoming_transactions(wallet_address, min_amount_nano, since_timestamp):
“”“Check for incoming transactions to wallet since timestamp”””
try:
headers = {
‘X-API-Key’: TON_API_KEY
}

```
    url = f'https://toncenter.com/api/v2/getTransactions?address={wallet_address}&limit=100'
    response = requests.get(url, headers=headers, timeout=15)
    
    if response.status_code == 200:
        data = response.json()
        transactions = data.get('result', [])
        
        valid_transactions = []
        
        for tx in transactions:
            tx_time = tx.get('utime', 0)
            
            # Only check transactions after our timestamp
            if tx_time < since_timestamp:
                continue
            
            in_msg = tx.get('in_msg', {})
            if in_msg:
                value = int(in_msg.get('value', 0))
                source = in_msg.get('source', '')
                
                if value >= min_amount_nano * 0.95:  # 5% tolerance
                    valid_transactions.append({
                        'hash': tx.get('transaction_id', {}).get('hash', ''),
                        'value': value,
                        'source': source,
                        'time': tx_time,
                        'comment': in_msg.get('message', '')
                    })
        
        return valid_transactions
    
    return []
    
except Exception as e:
    logger.error(f"Error checking transactions: {e}")
    return []
```

# HTML Template for Mini App with TON Connect

MINI_APP_HTML = “””

<!DOCTYPE html>

<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-select=none">
    <title>G🅾️reSignal</title>
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

```
    body {
        font-family: 'Arial', sans-serif;
        background: linear-gradient(135deg, #1a1a1a 0%, #2d2d2d 100%);
        color: #fff;
        min-height: 100vh;
        padding: 20px;
        overflow-x: hidden;
    }
    
    .logo {
        text-align: center;
        margin-bottom: 30px;
    }
    
    .logo img {
        width: 80px;
        height: 80px;
        border-radius: 50%;
        border: 3px solid #ff4444;
        pointer-events: none;
    }
    
    .logo h1 {
        margin-top: 10px;
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
        transition: 0.3s;
    }
    
    .search-box button:active {
        transform: scale(0.95);
        background: #cc0000;
    }
    
    .content-viewer {
        display: none;
        background: rgba(255,255,255,0.1);
        border-radius: 15px;
        padding: 20px;
        backdrop-filter: blur(10px);
    }
    
    .content-viewer.active {
        display: block;
    }
    
    .media-container {
        position: relative;
        width: 100%;
        max-width: 100%;
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
        pointer-events: none;
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
    
    .payment-content .ton-connect-button {
        width: 100%;
        padding: 15px;
        margin-top: 15px;
        border: none;
        border-radius: 10px;
        background: #0098ea;
        color: #fff;
        font-size: 16px;
        font-weight: bold;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 10px;
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
    
    .payment-content .alternative-payment {
        margin-top: 20px;
        padding-top: 20px;
        border-top: 1px solid rgba(255,255,255,0.2);
    }
    
    .payment-content .wallet-address {
        background: rgba(0,0,0,0.5);
        padding: 10px;
        border-radius: 8px;
        font-family: monospace;
        font-size: 12px;
        word-break: break-all;
        margin: 10px 0;
        cursor: pointer;
    }
    
    .payment-content .copy-button {
        background: #444;
        padding: 8px 15px;
        border-radius: 5px;
        font-size: 14px;
        margin-top: 10px;
    }
    
    .error-message {
        background: rgba(255,68,68,0.2);
        border: 2px solid #ff4444;
        border-radius: 10px;
        padding: 15px;
        margin-top: 10px;
        text-align: center;
    }
    
    .success-message {
        background: rgba(68,255,68,0.2);
        border: 2px solid #44ff44;
        border-radius: 10px;
        padding: 15px;
        margin-top: 10px;
        text-align: center;
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
```

</head>
<body>
    <div class="logo">
        <img src="data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iODAiIGhlaWdodD0iODAiIHZpZXdCb3g9IjAgMCA4MCA4MCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPGNpcmNsZSBjeD0iNDAiIGN5PSI0MCIgcj0iMzgiIHN0cm9rZT0iI2ZmNDQ0NCIgc3Ryb2tlLXdpZHRoPSI0Ii8+CjxwYXRoIGQ9Ik0zMCAyNUw0NSAyNUw1MCAzNUw0NSA0NUw1MCA1NUwzNSA1NUwzMCA0NUwzNSAzNUwzMCAyNVoiIGZpbGw9IiNmZjQ0NDQiLz4KPC9zdmc+Cg==" alt="Logo">
        <h1>G🅾️reSignal</h1>
    </div>

```
<div class="container">
    <div class="search-box" id="searchBox">
        <input type="text" id="ticketInput" placeholder="أدخل التيكت هنا" maxlength="8">
        <button onclick="searchTicket()">🔍 بحث</button>
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
    ⏱️ الوقت المتبقي: <span id="timerText">00:00</span>
</div>

<div class="payment-modal" id="paymentModal">
    <div class="payment-content">
        <h2>💎 فتح محتوى غير محدود</h2>
        <p>صلاحية 3 أيام كاملة</p>
        <div class="price" id="tonPrice">⏳ جاري الحساب...</div>
        
        <div id="tonConnectContainer"></div>
        
        <div class="alternative-payment">
            <p style="font-size: 14px; color: #aaa;">أو أرسل يدوياً إلى:</p>
            <div class="wallet-address" id="walletAddress" onclick="copyWallet()">
                UQABSEcWzJVmtLdZDUMyCs5EGrKOHWKWq3ftFNY0IItHgYTa
            </div>
            <button class="copy-button" onclick="copyWallet()">📋 نسخ العنوان</button>
            
            <div class="payment-status" id="paymentStatus" style="display: none;">
                <div class="loading"></div>
                <p style="margin-top: 10px;">جاري التحقق من الدفع...</p>
            </div>
        </div>
        
        <button onclick="closePaymentModal()" style="background: #666; margin-top: 20px;">إلغاء</button>
    </div>
</div>

<script>
    let tg = window.Telegram.WebApp;
    tg.expand();
    tg.disableVerticalSwipes();
    
    let currentTicket = '';
    let timerInterval = null;
    let paymentCheckInterval = null;
    let userFingerprint = '';
    let currentPaymentId = '';
    let tonConnectUI = null;
    let requiredTonAmount = 0;
    
    // Initialize TON Connect
    try {
        tonConnectUI = new TON_CONNECT_UI.TonConnectUI({
            manifestUrl: window.location.origin + '/tonconnect-manifest.json',
            buttonRootId: 'tonConnectContainer'
        });
    } catch (e) {
        console.error('TON Connect initialization error:', e);
    }
    
    // Generate user fingerprint
    function generateFingerprint() {
        const data = {
            userAgent: navigator.userAgent,
            language: navigator.language,
            platform: navigator.platform,
            screenResolution: screen.width + 'x' + screen.height,
            timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
            userId: tg.initDataUnsafe?.user?.id || 'unknown'
        };
        return btoa(JSON.stringify(data));
    }
    
    userFingerprint = generateFingerprint();
    
    // Prevent screen recording
    document.addEventListener('visibilitychange', function() {
        if (document.hidden) {
            const mediaContainer = document.getElementById('mediaContainer');
            const media = mediaContainer.querySelector('video, img');
            if (media && !document.querySelector('.content-viewer').dataset.hasAccess) {
                media.style.display = 'none';
            }
        } else {
            const media = document.querySelector('video, img');
            if (media) media.style.display = 'block';
        }
    });
    
    // Disable screenshot
    document.addEventListener('keyup', function(e) {
        if (e.key === 'PrintScreen') {
            navigator.clipboard.writeText('');
            alert('التقاط الشاشة غير مسموح! 🚫');
        }
    });
    
    // Disable right-click and long-press
    document.addEventListener('contextmenu', e => e.preventDefault());
    
    async function searchTicket() {
        const ticket = document.getElementById('ticketInput').value.trim();
        const message = document.getElementById('message');
        
        if (!ticket) {
            message.innerHTML = '<div class="error-message">❌ الرجاء إدخال التيكت</div>';
            return;
        }
        
        message.innerHTML = '<div style="text-align: center;">⏳ جاري البحث...</div>';
        
        try {
            const response = await fetch('/api/get-content', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
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
                message.innerHTML = '';
            } else {
                message.innerHTML = '<div class="error-message">❌ ' + data.error + '</div>';
            }
        } catch (error) {
            message.innerHTML = '<div class="error-message">❌ خطأ في الاتصال</div>';
        }
    }
    
    function displayContent(data) {
        const viewer = document.getElementById('contentViewer');
        const searchBox = document.getElementById('searchBox');
        const mediaContainer = document.getElementById('mediaContainer');
        const titleElement = document.getElementById('contentTitle');
        
        titleElement.textContent = data.title || 'محتوى حصري';
        viewer.dataset.hasAccess = data.has_access;
        
        // Clear previous content
        const oldMedia = mediaContainer.querySelector('video, img');
        if (oldMedia) oldMedia.remove();
        
        // Display media
        if (data.media_type === 'video') {
            const video = document.createElement('video');
            video.src = 'data:video/mp4;base64,' + data.media_data;
            video.controls = data.has_access;
            video.controlsList = 'nodownload';
            video.disablePictureInPicture = true;
            video.autoplay = true;
            mediaContainer.insertBefore(video, mediaContainer.firstChild);
            
            if (!data.has_access && data.duration) {
                startTimer(data.duration);
            }
        } else if (data.media_type === 'image') {
            const img = document.createElement('img');
            img.src = 'data:image/jpeg;base64,' + data.media_data;
            mediaContainer.insertBefore(img, mediaContainer.firstChild);
            
            if (!data.has_access) {
                startTimer(30);
            }
        }
        
        searchBox.style.display = 'none';
        viewer.classList.add('active');
    }
    
    function startTimer(duration) {
        const timerOverlay = document.getElementById('timerOverlay');
        const timerText = document.getElementById('timerText');
        timerOverlay.style.display = 'block';
        
        let remaining = duration;
        
        timerInterval = setInterval(() => {
            remaining--;
            const minutes = Math.floor(remaining / 60);
            const seconds = remaining % 60;
            timerText.textContent = `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
            
            if (remaining <= 0) {
                clearInterval(timerInterval);
                showPaymentModal();
            }
        }, 1000);
    }
    
    async function showPaymentModal() {
        document.getElementById('paymentModal').classList.add('active');
        
        // Pause video if playing
        const video = document.querySelector('video');
        if (video) video.pause();
        
        // Get TON price
        try {
            const response = await fetch('/api/get-ton-price');
            const data = await response.json();
            requiredTonAmount = data.ton_amount;
            document.getElementById('tonPrice').textContent = `${requiredTonAmount.toFixed(4)} TON`;
        } catch (e) {
            document.getElementById('tonPrice').textContent = '~0.20 TON';
            requiredTonAmount = 0.20;
        }
    }
    
    function closePaymentModal() {
        document.getElementById('paymentModal').classList.remove('active');
        if (paymentCheckInterval) {
            clearInterval(paymentCheckInterval);
        }
    }
    
    async function initPayment() {
        try {
            if (!tonConnectUI || !tonConnectUI.connected) {
                alert('الرجاء توصيل محفظة TON أولاً');
                return;
            }
            
            const response = await fetch('/api/create-payment', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    ticket: currentTicket,
                    user_id: tg.initDataUnsafe?.user?.id || 0
                })
            });
            
            const data = await response.json();
            
            if (data.success) {
                currentPaymentId = data.payment_id;
                
                // Send transaction via TON Connect
                const transaction = {
                    validUntil: Math.floor(Date.now() / 1000) + 600,
                    messages: [
                        {
                            address: data.wallet_address,
                            amount: data.amount_nano.toString(),
                            payload: data.comment
                        }
                    ]
                };
                
                try {
                    const result = await tonConnectUI.sendTransaction(transaction);
                    
                    // Start checking for payment
                    document.getElementById('paymentStatus').style.display = 'block';
                    checkPaymentStatus(data.payment_id);
                    
                } catch (e) {
                    console.error('Transaction error:', e);
                    alert('فشلت العملية. حاول الدفع يدوياً إلى العنوان أدناه');
                }
            } else {
                alert('خطأ في إنشاء الدفع: ' + data.error);
            }
        } catch (error) {
            alert('خطأ في الاتصال');
            console.error(error);
        }
    }
    
    async function checkPaymentStatus(paymentId) {
        document.getElementById('paymentStatus').style.display = 'block';
        
        paymentCheckInterval = setInterval(async () => {
            try {
                const response = await fetch('/api/check-payment', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
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
                    document.getElementById('timerOverlay').style.display = 'none';
                    
                    // Show success message
                    const message = document.getElementById('message');
                    message.innerHTML = '<div class="success-message">✅ تم الدفع بنجاح! استمتع بالمحتوى</div>';
                    
                    // Reload content with full access
                    setTimeout(() => {
                        searchTicket();
                    }, 2000);
                }
            } catch (error) {
                console.error('Error checking payment:', error);
            }
        }, 3000);
        
        // Stop checking after 10 minutes
        setTimeout(() => {
            if (paymentCheckInterval) {
                clearInterval(paymentCheckInterval);
                document.getElementById('paymentStatus').innerHTML = 
                    '<p>⚠️ انتهى وقت الانتظار. إذا دفعت، سيتم تفعيل الوصول تلقائياً</p>';
            }
        }, 600000);
    }
    
    function copyWallet() {
        const wallet = 'UQABSEcWzJVmtLdZDUMyCs5EGrKOHWKWq3ftFNY0IItHgYTa';
        
        if (navigator.clipboard) {
            navigator.clipboard.writeText(wallet).then(() => {
                alert('✅ تم نسخ العنوان!');
                
                // Start checking for manual payment
                if (!paymentCheckInterval) {
                    createManualPayment();
                }
            });
        } else {
            alert('العنوان: ' + wallet);
        }
    }
    
    async function createManualPayment() {
        try {
            const response = await fetch('/api/create-payment', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
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
            console.error('Error creating manual payment:', e);
        }
    }
    
    // Auto-check payment on page visibility
    document.addEventListener('visibilitychange', function() {
        if (!document.hidden && currentTicket && !paymentCheckInterval) {
            // Check if user paid while app was in background
            checkPaymentStatus('check');
        }
    });
</script>
```

</body>
</html>
"""

# TON Connect Manifest

TON_MANIFEST = {
“url”: “”,  # Will be set dynamically
“name”: “G🅾️reSignal”,
“iconUrl”: “https://raw.githubusercontent.com/ton-blockchain/ton-connect/main/assets/ton_symbol.png”
}

# Helper Functions

def generate_ticket():
“”“Generate unique 8-character ticket ID”””
return secrets.token_urlsafe(6)[:8]

def add_watermark(image_bytes, text=”@GoreSignal”):
“”“Add watermark to image”””
try:
img = Image.open(BytesIO(image_bytes))
draw = ImageDraw.Draw(img)

```
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
    img.save(output, format='JPEG')
    return output.getvalue()
except Exception as e:
    logger.error(f"Watermark error: {e}")
    return image_bytes
```

def cleanup_expired_tickets():
“”“Remove expired tickets”””
current_time = datetime.now()
expired = []

```
for ticket_id, data in tickets_storage.items():
    created_at = datetime.fromisoformat(data['created_at'])
    if current_time - created_at > timedelta(days=TICKET_EXPIRY_DAYS):
        expired.append(ticket_id)

for ticket_id in expired:
    del tickets_storage[ticket_id]
    logger.info(f"Deleted expired ticket: {ticket_id}")
```

def cleanup_expired_payments():
“”“Remove expired pending payments”””
current_time = datetime.now()
expired = []

```
for payment_id, data in pending_payments.items():
    created_at = datetime.fromisoformat(data['created_at'])
    if current_time - created_at > timedelta(hours=1):  # 1 hour expiry
        expired.append(payment_id)

for payment_id in expired:
    del pending_payments[payment_id]
```

# Flask Routes

@app.route(’/’)
def index():
return render_template_string(MINI_APP_HTML)

@app.route(’/tonconnect-manifest.json’)
def ton_manifest():
“”“TON Connect manifest”””
manifest = TON_MANIFEST.copy()
manifest[‘url’] = get_app_url()
return jsonify(manifest)

@app.route(’/health’)
def health():
“”“Uptime endpoint for render.com”””
return jsonify({
“status”: “ok”,
“timestamp”: datetime.now().isoformat(),
“tickets”: len(tickets_storage),
“pending_payments”: len(pending_payments)
})

@app.route(’/api/get-ton-price’)
def get_ton_price_api():
“”“Get current TON price and required amount”””
ton_amount = usd_to_ton(PAYMENT_AMOUNT_USD)
return jsonify({
“ton_amount”: ton_amount,
“usd_amount”: PAYMENT_AMOUNT_USD,
“ton_price_usd”: get_ton_price_usd()
})

@app.route(’/api/get-content’, methods=[‘POST’])
def get_content():
“”“API endpoint to retrieve content by ticket”””
try:
data = request.json
ticket = data.get(‘ticket’, ‘’).strip()
user_id = data.get(‘user_id’, 0)
fingerprint = data.get(‘fingerprint’, ‘’)

```
    cleanup_expired_tickets()
    
    if ticket not in tickets_storage:
        return jsonify({"success": False, "error": "تيكت غير صالح أو منتهي"})
    
    ticket_data = tickets_storage[ticket]
    
    # Check if ticket is expired
    created_at = datetime.fromisoformat(ticket_data['created_at'])
    if datetime.now() - created_at > timedelta(days=TICKET_EXPIRY_DAYS):
        return jsonify({"success": False, "error": "التيكت منتهي الصلاحية"})
    
    # Check user access
    user_key = str(user_id)
    has_paid_access = False
    
    if user_key in user_access and ticket in user_access[user_key]:
        user_ticket_data = user_access[user_key][ticket]
        
        # Check fingerprint match
        if user_ticket_data.get('fingerprint') != fingerprint:
            return jsonify({"success": False, "error": "تم الكشف عن محاولة وصول غير مصرح بها"})
        
        if user_ticket_data.get('paid'):
            # Check if payment is still valid (3 days)
            paid_at = datetime.fromisoformat(user_ticket_data.get('paid_at', ticket_data['created_at']))
            if datetime.now() - paid_at <= timedelta(days=TICKET_EXPIRY_DAYS):
                has_paid_access = True
    else:
        # First time viewing - record fingerprint
        if user_key not in user_access:
            user_access[user_key] = {}
        user_access[user_key][ticket] = {
            'paid': False,
            'first_viewed_at': datetime.now().isoformat(),
            'fingerprint': fingerprint
        }
    
    return jsonify({
        "success": True,
        "media_type": ticket_data['media_type'],
        "media_data": ticket_data['media_data'],
        "title": ticket_data.get('title', ''),
        "has_access": has_paid_access,
        "duration": ticket_data.get('duration', 120)
    })
    
except Exception as e:
    logger.error(f"Get content error: {e}")
    return jsonify({"success": False, "error": "خطأ في السيرفر"})
```

@app.route(’/api/create-payment’, methods=[‘POST’])
def create_payment():
“”“Create TON payment request”””
try:
data = request.json
ticket = data.get(‘ticket’)
user_id = data.get(‘user_id’)

```
    cleanup_expired_payments()
    
    # Generate payment ID
    payment_id = str(uuid.uuid4())
    
    # Calculate TON amount
    ton_amount = usd_to_ton(PAYMENT_AMOUNT_USD)
    amount_nano = ton_to_nanoton(ton_amount)
    
    # Store pending payment
    pending_payments[payment_id] = {
        'ticket': ticket,
        'user_id': user_id,
        'amount_nano': amount_nano,
        'amount_ton': ton_amount,
        'created_at': datetime.now().isoformat(),
        'status': 'pending'
    }
    
    # Create payment comment for tracking
    comment = f"GoreSignal_{payment_id[:8]}"
    
    logger.info(f"Created payment: {payment_id} for user {user_id}, ticket {ticket}, amount {ton_amount} TON")
    
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
```

@app.route(’/api/check-payment’, methods=[‘POST’])
def check_payment():
“”“Check if payment was completed”””
try:
data = request.json
payment_id = data.get(‘payment_id’)
ticket = data.get(‘ticket’)
user_id = str(data.get(‘user_id’))

```
    # If payment_id is 'check', look for any recent payment for this user/ticket
    if payment_id == 'check':
        # Find recent payment for this user and ticket
        for pid, pdata in pending_payments.items():
            if pdata.get('ticket') == ticket and str(pdata.get('user_id')) == user_id:
                payment_id = pid
                break
        
        if not payment_id or payment_id == 'check':
            return jsonify({"paid": False})
    
    if payment_id not in pending_payments:
        # Check if already processed
        if user_id in user_access and ticket in user_access.get(user_id, {}):
            if user_access[user_id][ticket].get('paid'):
                return jsonify({"paid": True})
        return jsonify({"paid": False})
    
    payment_data = pending_payments[payment_id]
    
    # Check if already marked as paid
    if payment_data.get('status') == 'completed':
        return jsonify({"paid": True})
    
    # Get payment creation timestamp
    created_at = datetime.fromisoformat(payment_data['created_at'])
    since_timestamp = int(created_at.timestamp())
    
    # Check for incoming transactions
    transactions = check_incoming_transactions(
        HOT_WALLET,
        payment_data['amount_nano'],
        since_timestamp
    )
    
    if transactions:
        # Payment found!
        tx = transactions[0]  # Get most recent
        
        logger.info(f"Payment verified! TxHash: {tx['hash']}, Amount: {tx['value']} nano, User: {user_id}, Ticket: {ticket}")
        
        # Grant access
        if user_id not in user_access:
            user_access[user_id] = {}
        
        user_access[user_id][ticket] = {
            'paid': True,
            'paid_at': datetime.now().isoformat(),
            'fingerprint': user_access.get(user_id, {}).get(ticket, {}).get('fingerprint', ''),
            'tx_hash': tx['hash'],
            'amount_paid': tx['value']
        }
        
        # Mark payment as completed
        payment_data['status'] = 'completed'
        payment_data['tx_hash'] = tx['hash']
        payment_data['completed_at'] = datetime.now().isoformat()
        
        # Notify admin
        asyncio.create_task(notify_admin_payment(
            user_id, 
            ticket, 
            payment_id, 
            tx['value'] / 1_000_000_000,  # Convert to TON
            tx['hash']
        ))
        
        return jsonify({"paid": True, "tx_hash": tx['hash']})
    
    return jsonify({"paid": False})
    
except Exception as e:
    logger.error(f"Check payment error: {e}")
    return jsonify({"paid": False, "error": str(e)})
```

# Telegram Bot Handlers

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
“”“Handle /start command”””
user_id = update.effective_user.id

```
if user_id == ADMIN_ID:
    app_url = get_app_url()
    
    await update.message.reply_text(
        f"🔥 مرحباً بك أيها الأدمن!\n\n"
        f"🌐 رابط التطبيق:\n{app_url}\n\n"
        f"📊 الإحصائيات:\n"
        f"📝 التيكتات النشطة: {len(tickets_storage)}\n"
        f"💰 الدفعات المعلقة: {len(pending_payments)}\n"
        f"👥 المستخدمين: {len(user_access)}\n\n"
        f"📤 أرسل محتوى (فيديو/صورة) لبدء إنشاء تيكت جديد\n"
        f"🔗 استخدم /link لتعيين رابط ouo.io\n"
        f"📊 استخدم /stats لعرض الإحصائيات التفصيلية\n\n"
        f"💡 الرابط يتم اكتشافه تلقائياً من السيرفر!",
        disable_web_page_preview=True
    )
else:
    await update.message.reply_text(
        "⚠️ هذا البوت مخصص للأدمن فقط!\n"
        "للوصول للمحتوى، استخدم التطبيق المصغر."
    )
```

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
“”“Handle /stats command”””
if update.effective_user.id != ADMIN_ID:
return

```
# Calculate stats
total_paid = sum(1 for user_tickets in user_access.values() 
                 for ticket_data in user_tickets.values() 
                 if ticket_data.get('paid'))

total_revenue_nano = sum(ticket_data.get('amount_paid', 0) 
                         for user_tickets in user_access.values() 
                         for ticket_data in user_tickets.values() 
                         if ticket_data.get('paid'))

total_revenue_ton = total_revenue_nano / 1_000_000_000
ton_price = get_ton_price_usd()
total_revenue_usd = total_revenue_ton * ton_price

completed_payments = sum(1 for p in pending_payments.values() if p.get('status') == 'completed')
pending = len(pending_payments) - completed_payments

stats_text = (
    f"📊 إحصائيات G🅾️reSignal\n\n"
    f"📝 التيكتات النشطة: {len(tickets_storage)}\n"
    f"👥 إجمالي المستخدمين: {len(user_access)}\n\n"
    f"💰 الدفعات:\n"
    f"✅ مكتملة: {total_paid}\n"
    f"⏳ معلقة: {pending}\n\n"
    f"💵 الإيرادات:\n"
    f"🔷 {total_revenue_ton:.4f} TON\n"
    f"💵 ${total_revenue_usd:.2f} USD\n\n"
    f"📈 سعر TON الحالي: ${ton_price:.2f}"
)

await update.message.reply_text(stats_text)
```

async def link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
“”“Handle /link command to set ouo.io link”””
global ouo_link

```
if update.effective_user.id != ADMIN_ID:
    return

if context.args:
    ouo_link = context.args[0]
    await update.message.reply_text(f"✅ تم تعيين الرابط:\n{ouo_link}")
else:
    await update.message.reply_text(
        f"📎 الرابط الحالي:\n{ouo_link if ouo_link else 'لم يتم التعيين'}\n\n"
        f"لتغيير الرابط: /link <رابط_جديد>"
    )
```

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
“”“Handle media uploads from admin”””
if update.effective_user.id != ADMIN_ID:
return

```
try:
    # Generate ticket
    ticket_id = generate_ticket()
    
    # Ask for title
    await update.message.reply_text(
        f"🎫 تم إنشاء التيكت: `{ticket_id}`\n\n"
        f"📝 أرسل عنوان المحتوى:",
        parse_mode='Markdown'
    )
    
    # Store media temporarily
    media_type = None
    media_data = None
    duration = 120  # Default
    
    if update.message.photo:
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        photo_bytes = await file.download_as_bytearray()
        
        watermarked = add_watermark(bytes(photo_bytes))
        media_data = base64.b64encode(watermarked).decode()
        media_type = 'image'
        duration = 30
        
    elif update.message.video:
        video = update.message.video
        file = await context.bot.get_file(video.file_id)
        video_bytes = await file.download_as_bytearray()
        
        media_data = base64.b64encode(bytes(video_bytes)).decode()
        media_type = 'video'
        duration = video.duration if video.duration else 120
        
    elif update.message.document:
        document = update.message.document
        if document.mime_type and 'video' in document.mime_type:
            file = await context.bot.get_file(document.file_id)
            video_bytes = await file.download_as_bytearray()
            media_data = base64.b64encode(bytes(video_bytes)).decode()
            media_type = 'video'
        elif document.mime_type and 'image' in document.mime_type:
            file = await context.bot.get_file(document.file_id)
            image_bytes = await file.download_as_bytearray()
            watermarked = add_watermark(bytes(image_bytes))
            media_data = base64.b64encode(watermarked).decode()
            media_type = 'image'
            duration = 30
    
    if media_data:
        tickets_storage[ticket_id] = {
            'media_type': media_type,
            'media_data': media_data,
            'created_at': datetime.now().isoformat(),
            'title': '',
            'duration': duration
        }
        
        context.user_data['pending_ticket'] = ticket_id
        
except Exception as e:
    logger.error(f"Handle media error: {e}")
    await update.message.reply_text(f"❌ خطأ: {str(e)}")
```

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
“”“Handle text messages (titles)”””
if update.effective_user.id != ADMIN_ID:
return

```
pending_ticket = context.user_data.get('pending_ticket')

if pending_ticket and pending_ticket in tickets_storage:
    title = update.message.text
    tickets_storage[pending_ticket]['title'] = title
    
    await post_to_group(context.bot, pending_ticket, title)
    
    context.user_data.pop('pending_ticket', None)
    
    await update.message.reply_text(
        f"✅ تم نشر المحتوى بنجاح!\n\n"
        f"🎫 التيكت: `{pending_ticket}`\n"
        f"📝 العنوان: {title}\n"
        f"⏱️ صلاحية: {TICKET_EXPIRY_DAYS} أيام",
        parse_mode='Markdown'
    )
```

async def post_to_group(bot, ticket_id, title):
“”“Post content to group with watermarked preview”””
try:
ticket_data = tickets_storage[ticket_id]

```
    keyboard = [[InlineKeyboardButton("🔥 شاهد مجاناً", url=ouo_link if ouo_link else "https://t.me/GoreSignal")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    ton_amount = usd_to_ton(PAYMENT_AMOUNT_USD)
    
    message_text = (
        f"🔥 {title}\n\n"
        f"🎫 التيكت: `{ticket_id}`\n\n"
        f"💎 الوصول الكامل:\n"
        f"💵 ${PAYMENT_AMOUNT_USD} USD\n"
        f"🔷 ~{ton_amount:.4f} TON\n"
        f"⏱️ صلاحية 3 أيام\n\n"
        f"📌 كيفية المشاهدة:\n"
        f"1️⃣ انسخ التيكت أعلاه\n"
        f"2️⃣ اضغط على الزر بالأسفل\n"
        f"3️⃣ الصق التيكت في التطبيق\n"
        f"4️⃣ شاهد مجاناً أو ادفع للوصول الكامل"
    )
    
    if ticket_data['media_type'] == 'image':
        image_bytes = base64.b64decode(ticket_data['media_data'])
        await bot.send_photo(
            chat_id=GROUP_CHAT_ID,
            photo=BytesIO(image_bytes),
            caption=message_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    else:
        await bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text=message_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
except Exception as e:
    logger.error(f"Post to group error: {e}")
```

async def notify_admin_payment(user_id, ticket, payment_id, amount_ton, tx_hash):
“”“Notify admin about payment”””
try:
bot = telegram_app.bot
ton_price = get_ton_price_usd()
amount_usd = amount_ton * ton_price

```
    await bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            f"💰 دفعة جديدة!\n\n"
            f"👤 المستخدم: `{user_id}`\n"
            f"🎫 التيكت: `{ticket}`\n"
            f"💳 معرف الدفع: `{payment_id[:16]}...`\n"
            f"💵 المبلغ: {amount_ton:.4f} TON (${amount_usd:.2f})\n"
            f"🔗 المعاملة: `{tx_hash[:16]}...`\n"
            f"⏰ الوقت: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        ),
        parse_mode='Markdown'
    )
except Exception as e:
    logger.error(f"Notify admin error: {e}")
```

# Initialize Telegram Bot

telegram_app = Application.builder().token(BOT_TOKEN).build()

telegram_app.add_handler(CommandHandler(“start”, start_command))
telegram_app.add_handler(CommandHandler(“link”, link_command))
telegram_app.add_handler(CommandHandler(“stats”, stats_command))
telegram_app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, handle_media))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

# Run both Flask and Telegram bot

def run_flask():
“”“Run Flask app”””
app.run(host=‘0.0.0.0’, port=PORT)

def run_telegram():
“”“Run Telegram bot”””
asyncio.set_event_loop(asyncio.new_event_loop())
telegram_app.run_polling(allowed_updates=Update.ALL_TYPES)

if **name** == ‘**main**’:
logger.info(“Starting G🅾️reSignal Bot…”)
logger.info(f”Admin ID: {ADMIN_ID}”)
logger.info(f”Group Chat ID: {GROUP_CHAT_ID}”)
logger.info(f”Hot Wallet: {HOT_WALLET}”)
logger.info(f”Payment Amount: ${PAYMENT_AMOUNT_USD} USD”)

```
# Deploy to Cloudflare (create worker script)
deploy_to_cloudflare()

# Log the app URL
app_url = get_app_url()
logger.info(f"🌐 App URL: {app_url}")
logger.info(f"📱 Mini App: {app_url}")
logger.info(f"🔗 Share this URL with users!")

# Start Flask in separate thread
flask_thread = Thread(target=run_flask)
flask_thread.daemon = True
flask_thread.start()

# Run Telegram bot in main thread
run_telegram()
```

# HTML Template for Mini App

MINI_APP_HTML = “””

<!DOCTYPE html>

<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-select=none">
    <title>G🅾️reSignal</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
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

```
    body {
        font-family: 'Arial', sans-serif;
        background: linear-gradient(135deg, #1a1a1a 0%, #2d2d2d 100%);
        color: #fff;
        min-height: 100vh;
        padding: 20px;
        overflow-x: hidden;
    }
    
    .logo {
        text-align: center;
        margin-bottom: 30px;
    }
    
    .logo img {
        width: 80px;
        height: 80px;
        border-radius: 50%;
        border: 3px solid #ff4444;
        pointer-events: none;
    }
    
    .logo h1 {
        margin-top: 10px;
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
        transition: 0.3s;
    }
    
    .search-box button:active {
        transform: scale(0.95);
        background: #cc0000;
    }
    
    .content-viewer {
        display: none;
        background: rgba(255,255,255,0.1);
        border-radius: 15px;
        padding: 20px;
        backdrop-filter: blur(10px);
    }
    
    .content-viewer.active {
        display: block;
    }
    
    .media-container {
        position: relative;
        width: 100%;
        max-width: 100%;
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
        pointer-events: none;
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
    
    .error-message {
        background: rgba(255,68,68,0.2);
        border: 2px solid #ff4444;
        border-radius: 10px;
        padding: 15px;
        margin-top: 10px;
        text-align: center;
    }
    
    .success-message {
        background: rgba(68,255,68,0.2);
        border: 2px solid #44ff44;
        border-radius: 10px;
        padding: 15px;
        margin-top: 10px;
        text-align: center;
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
</style>
```

</head>
<body>
    <div class="logo">
        <img src="data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iODAiIGhlaWdodD0iODAiIHZpZXdCb3g9IjAgMCA4MCA4MCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPGNpcmNsZSBjeD0iNDAiIGN5PSI0MCIgcj0iMzgiIHN0cm9rZT0iI2ZmNDQ0NCIgc3Ryb2tlLXdpZHRoPSI0Ii8+CjxwYXRoIGQ9Ik0zMCAyNUw0NSAyNUw1MCAzNUw0NSA0NUw1MCA1NUwzNSA1NUwzMCA0NUwzNSAzNUwzMCAyNVoiIGZpbGw9IiNmZjQ0NDQiLz4KPC9zdmc+Cg==" alt="Logo">
        <h1>G🅾️reSignal</h1>
    </div>

```
<div class="container">
    <div class="search-box" id="searchBox">
        <input type="text" id="ticketInput" placeholder="أدخل التيكت هنا" maxlength="8">
        <button onclick="searchTicket()">🔍 بحث</button>
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
    ⏱️ الوقت المتبقي: <span id="timerText">00:00</span>
</div>

<div class="payment-modal" id="paymentModal">
    <div class="payment-content">
        <h2>💎 فتح محتوى غير محدود</h2>
        <p>ادفع 0.99$ TON للوصول الكامل لمدة 3 أيام</p>
        <button onclick="initPayment()">💳 الدفع الآن</button>
        <button onclick="closePaymentModal()" style="background: #666; margin-top: 10px;">إلغاء</button>
    </div>
</div>

<script>
    let tg = window.Telegram.WebApp;
    tg.expand();
    tg.disableVerticalSwipes();
    
    let currentTicket = '';
    let timerInterval = null;
    let userFingerprint = '';
    
    // Generate user fingerprint
    function generateFingerprint() {
        const data = {
            userAgent: navigator.userAgent,
            language: navigator.language,
            platform: navigator.platform,
            screenResolution: screen.width + 'x' + screen.height,
            timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
            userId: tg.initDataUnsafe?.user?.id || 'unknown'
        };
        return btoa(JSON.stringify(data));
    }
    
    userFingerprint = generateFingerprint();
    
    // Prevent screen recording
    document.addEventListener('visibilitychange', function() {
        if (document.hidden) {
            const mediaContainer = document.getElementById('mediaContainer');
            mediaContainer.innerHTML = '<div class="watermark-overlay"><span>@GoreSignal</span></div>';
        }
    });
    
    // Disable screenshot (limited browser support)
    document.addEventListener('keyup', function(e) {
        if (e.key === 'PrintScreen') {
            navigator.clipboard.writeText('');
            alert('التقاط الشاشة غير مسموح! 🚫');
        }
    });
    
    // Disable right-click and long-press
    document.addEventListener('contextmenu', e => e.preventDefault());
    document.addEventListener('touchstart', preventLongPress);
    document.addEventListener('touchend', preventLongPress);
    
    function preventLongPress(e) {
        if (e.target.tagName === 'VIDEO' || e.target.tagName === 'IMG') {
            e.preventDefault();
        }
    }
    
    async function searchTicket() {
        const ticket = document.getElementById('ticketInput').value.trim();
        const message = document.getElementById('message');
        
        if (!ticket) {
            message.innerHTML = '<div class="error-message">❌ الرجاء إدخال التيكت</div>';
            return;
        }
        
        message.innerHTML = '<div style="text-align: center;">⏳ جاري البحث...</div>';
        
        try {
            const response = await fetch('/api/get-content', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
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
                message.innerHTML = '';
            } else {
                message.innerHTML = '<div class="error-message">❌ ' + data.error + '</div>';
            }
        } catch (error) {
            message.innerHTML = '<div class="error-message">❌ خطأ في الاتصال</div>';
        }
    }
    
    function displayContent(data) {
        const viewer = document.getElementById('contentViewer');
        const searchBox = document.getElementById('searchBox');
        const mediaContainer = document.getElementById('mediaContainer');
        const titleElement = document.getElementById('contentTitle');
        
        titleElement.textContent = data.title || 'محتوى حصري';
        
        // Clear previous content
        const oldMedia = mediaContainer.querySelector('video, img');
        if (oldMedia) oldMedia.remove();
        
        // Display media
        if (data.media_type === 'video') {
            const video = document.createElement('video');
            video.src = 'data:video/mp4;base64,' + data.media_data;
            video.controls = data.has_access;
            video.controlsList = 'nodownload';
            video.disablePictureInPicture = true;
            video.autoplay = true;
            mediaContainer.insertBefore(video, mediaContainer.firstChild);
            
            if (!data.has_access && data.duration) {
                startTimer(data.duration);
            }
        } else if (data.media_type === 'image') {
            const img = document.createElement('img');
            img.src = 'data:image/jpeg;base64,' + data.media_data;
            mediaContainer.insertBefore(img, mediaContainer.firstChild);
            
            if (!data.has_access) {
                startTimer(30); // 30 seconds for images
            }
        }
        
        searchBox.style.display = 'none';
        viewer.classList.add('active');
    }
    
    function startTimer(duration) {
        const timerOverlay = document.getElementById('timerOverlay');
        const timerText = document.getElementById('timerText');
        timerOverlay.style.display = 'block';
        
        let remaining = duration;
        
        timerInterval = setInterval(() => {
            remaining--;
            const minutes = Math.floor(remaining / 60);
            const seconds = remaining % 60;
            timerText.textContent = `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
            
            if (remaining <= 0) {
                clearInterval(timerInterval);
                showPaymentModal();
            }
        }, 1000);
    }
    
    function showPaymentModal() {
        document.getElementById('paymentModal').classList.add('active');
        // Pause video if playing
        const video = document.querySelector('video');
        if (video) video.pause();
    }
    
    function closePaymentModal() {
        document.getElementById('paymentModal').classList.remove('active');
    }
    
    async function initPayment() {
        try {
            const response = await fetch('/api/create-payment', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    ticket: currentTicket,
                    user_id: tg.initDataUnsafe?.user?.id || 0
                })
            });
            
            const data = await response.json();
            
            if (data.success) {
                // Open TON payment URL
                window.open(data.payment_url, '_blank');
                
                // Poll for payment confirmation
                checkPaymentStatus(data.payment_id);
            } else {
                alert('خطأ في إنشاء الدفع: ' + data.error);
            }
        } catch (error) {
            alert('خطأ في الاتصال');
        }
    }
    
    async function checkPaymentStatus(paymentId) {
        const interval = setInterval(async () => {
            try {
                const response = await fetch('/api/check-payment', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        payment_id: paymentId,
                        ticket: currentTicket,
                        user_id: tg.initDataUnsafe?.user?.id || 0
                    })
                });
                
                const data = await response.json();
                
                if (data.paid) {
                    clearInterval(interval);
                    closePaymentModal();
                    clearInterval(timerInterval);
                    document.getElementById('timerOverlay').style.display = 'none';
                    
                    // Reload content with full access
                    searchTicket();
                }
            } catch (error) {
                console.error('Error checking payment:', error);
            }
        }, 3000); // Check every 3 seconds
    }
    
    // Prevent video download
    document.addEventListener('DOMContentLoaded', function() {
        document.addEventListener('contextmenu', e => e.preventDefault());
    });
</script>
```

</body>
</html>
"""

# Helper Functions

def generate_ticket():
“”“Generate unique 8-character ticket ID”””
return secrets.token_urlsafe(6)[:8]

def add_watermark(image_bytes, text=”@GoreSignal”):
“”“Add watermark to image”””
try:
img = Image.open(BytesIO(image_bytes))
draw = ImageDraw.Draw(img)

```
    # Try to use a font, fallback to default
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 30)
    except:
        font = ImageFont.load_default()
    
    # Add watermark in multiple positions
    width, height = img.size
    positions = [
        (50, 50), (width - 200, 50),
        (50, height - 80), (width - 200, height - 80),
        (width // 2 - 100, height // 2)
    ]
    
    for pos in positions:
        draw.text(pos, text, fill=(255, 255, 255, 128), font=font)
    
    # Convert back to bytes
    output = BytesIO()
    img.save(output, format='JPEG')
    return output.getvalue()
except Exception as e:
    logger.error(f"Watermark error: {e}")
    return image_bytes
```

def cleanup_expired_tickets():
“”“Remove expired tickets”””
current_time = datetime.now()
expired = []

```
for ticket_id, data in tickets_storage.items():
    created_at = datetime.fromisoformat(data['created_at'])
    if current_time - created_at > timedelta(days=TICKET_EXPIRY_DAYS):
        expired.append(ticket_id)

for ticket_id in expired:
    del tickets_storage[ticket_id]
    logger.info(f"Deleted expired ticket: {ticket_id}")
```

# Flask Routes

@app.route(’/’)
def index():
return render_template_string(MINI_APP_HTML)

@app.route(’/health’)
def health():
“”“Uptime endpoint for render.com”””
return jsonify({“status”: “ok”, “timestamp”: datetime.now().isoformat()})

@app.route(’/api/get-content’, methods=[‘POST’])
def get_content():
“”“API endpoint to retrieve content by ticket”””
try:
data = request.json
ticket = data.get(‘ticket’, ‘’).strip()
user_id = data.get(‘user_id’, 0)
fingerprint = data.get(‘fingerprint’, ‘’)

```
    cleanup_expired_tickets()
    
    if ticket not in tickets_storage:
        return jsonify({"success": False, "error": "تيكت غير صالح أو منتهي"})
    
    ticket_data = tickets_storage[ticket]
    
    # Check if ticket is expired
    created_at = datetime.fromisoformat(ticket_data['created_at'])
    if datetime.now() - created_at > timedelta(days=TICKET_EXPIRY_DAYS):
        return jsonify({"success": False, "error": "التيكت منتهي الصلاحية"})
    
    # Check user access
    user_key = str(user_id)
    has_paid_access = False
    
    if user_key in user_access and ticket in user_access[user_key]:
        user_ticket_data = user_access[user_key][ticket]
        
        # Check fingerprint match
        if user_ticket_data.get('fingerprint') != fingerprint:
            return jsonify({"success": False, "error": "تم الكشف عن محاولة وصول غير مصرح بها"})
        
        if user_ticket_data.get('paid'):
            # Check if payment is still valid (3 days)
            paid_at = datetime.fromisoformat(user_ticket_data.get('paid_at', ticket_data['created_at']))
            if datetime.now() - paid_at <= timedelta(days=TICKET_EXPIRY_DAYS):
                has_paid_access = True
    else:
        # First time viewing - record fingerprint
        if user_key not in user_access:
            user_access[user_key] = {}
        user_access[user_key][ticket] = {
            'paid': False,
            'first_viewed_at': datetime.now().isoformat(),
            'fingerprint': fingerprint
        }
    
    return jsonify({
        "success": True,
        "media_type": ticket_data['media_type'],
        "media_data": ticket_data['media_data'],
        "title": ticket_data.get('title', ''),
        "has_access": has_paid_access,
        "duration": ticket_data.get('duration', 120)
    })
    
except Exception as e:
    logger.error(f"Get content error: {e}")
    return jsonify({"success": False, "error": "خطأ في السيرفر"})
```

@app.route(’/api/create-payment’, methods=[‘POST’])
def create_payment():
“”“Create TON payment request”””
try:
data = request.json
ticket = data.get(‘ticket’)
user_id = data.get(‘user_id’)

```
    # Generate payment ID
    payment_id = secrets.token_urlsafe(16)
    
    # Create TON payment URL (simplified - you'll need to integrate with TON Connect)
    payment_url = f"ton://transfer/{HOT_WALLET}?amount={int(PAYMENT_AMOUNT * 1e9)}&text=Payment_{payment_id}_Ticket_{ticket}"
    
    return jsonify({
        "success": True,
        "payment_id": payment_id,
        "payment_url": payment_url
    })
    
except Exception as e:
    logger.error(f"Create payment error: {e}")
    return jsonify({"success": False, "error": str(e)})
```

@app.route(’/api/check-payment’, methods=[‘POST’])
def check_payment():
“”“Check if payment was completed”””
try:
data = request.json
payment_id = data.get(‘payment_id’)
ticket = data.get(‘ticket’)
user_id = str(data.get(‘user_id’))

```
    # TODO: Implement actual TON payment verification using TON Center API
    # For now, this is a placeholder
    
    # Simulate payment verification (replace with actual API call)
    paid = False  # Set to True when payment is verified
    
    if paid:
        # Grant access
        if user_id not in user_access:
            user_access[user_id] = {}
        
        user_access[user_id][ticket] = {
            'paid': True,
            'paid_at': datetime.now().isoformat(),
            'fingerprint': user_access[user_id].get(ticket, {}).get('fingerprint', '')
        }
        
        # Notify admin
        asyncio.create_task(notify_admin_payment(user_id, ticket, payment_id))
    
    return jsonify({"paid": paid})
    
except Exception as e:
    logger.error(f"Check payment error: {e}")
    return jsonify({"paid": False})
```

# Telegram Bot Handlers

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
“”“Handle /start command”””
user_id = update.effective_user.id

```
if user_id == ADMIN_ID:
    # Get the app URL from environment or use ngrok/render URL
    app_url = os.environ.get("RENDER_EXTERNAL_URL", "https://your-app.onrender.com")
    
    await update.message.reply_text(
        f"🔥 مرحباً بك أيها الأدمن!\n\n"
        f"🌐 رابط التطبيق:\n{app_url}\n\n"
        f"📤 أرسل محتوى (فيديو/صورة) لبدء إنشاء تيكت جديد\n"
        f"🔗 استخدم /link لتعيين رابط ouo.io",
        disable_web_page_preview=True
    )
else:
    await update.message.reply_text(
        "⚠️ هذا البوت مخصص للأدمن فقط!\n"
        "للوصول للمحتوى، استخدم التطبيق المصغر."
    )
```

async def link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
“”“Handle /link command to set ouo.io link”””
global ouo_link

```
if update.effective_user.id != ADMIN_ID:
    return

if context.args:
    ouo_link = context.args[0]
    await update.message.reply_text(f"✅ تم تعيين الرابط:\n{ouo_link}")
else:
    await update.message.reply_text(
        f"📎 الرابط الحالي:\n{ouo_link if ouo_link else 'لم يتم التعيين'}\n\n"
        f"لتغيير الرابط: /link <رابط_جديد>"
    )
```

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
“”“Handle media uploads from admin”””
if update.effective_user.id != ADMIN_ID:
return

```
try:
    # Generate ticket
    ticket_id = generate_ticket()
    
    # Ask for title
    await update.message.reply_text(
        f"🎫 تم إنشاء التيكت: `{ticket_id}`\n\n"
        f"📝 أرسل عنوان المحتوى:",
        parse_mode='Markdown'
    )
    
    # Store media temporarily
    media_type = None
    media_data = None
    duration = 120  # Default
    
    if update.message.photo:
        # Get highest quality photo
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        photo_bytes = await file.download_as_bytearray()
        
        # Add watermark
        watermarked = add_watermark(bytes(photo_bytes))
        media_data = base64.b64encode(watermarked).decode()
        media_type = 'image'
        duration = 30
        
    elif update.message.video:
        video = update.message.video
        file = await context.bot.get_file(video.file_id)
        video_bytes = await file.download_as_bytearray()
        
        media_data = base64.b64encode(bytes(video_bytes)).decode()
        media_type = 'video'
        duration = video.duration if video.duration else 120
        
    elif update.message.document:
        document = update.message.document
        if document.mime_type and 'video' in document.mime_type:
            file = await context.bot.get_file(document.file_id)
            video_bytes = await file.download_as_bytearray()
            media_data = base64.b64encode(bytes(video_bytes)).decode()
            media_type = 'video'
        elif document.mime_type and 'image' in document.mime_type:
            file = await context.bot.get_file(document.file_id)
            image_bytes = await file.download_as_bytearray()
            watermarked = add_watermark(bytes(image_bytes))
            media_data = base64.b64encode(watermarked).decode()
            media_type = 'image'
            duration = 30
    
    if media_data:
        # Store ticket
        tickets_storage[ticket_id] = {
            'media_type': media_type,
            'media_data': media_data,
            'created_at': datetime.now().isoformat(),
            'title': '',  # Will be updated
            'duration': duration
        }
        
        # Store context for title
        context.user_data['pending_ticket'] = ticket_id
        
except Exception as e:
    logger.error(f"Handle media error: {e}")
    await update.message.reply_text(f"❌ خطأ: {str(e)}")
```

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
“”“Handle text messages (titles)”””
if update.effective_user.id != ADMIN_ID:
return

```
# Check if there's a pending ticket
pending_ticket = context.user_data.get('pending_ticket')

if pending_ticket and pending_ticket in tickets_storage:
    title = update.message.text
    tickets_storage[pending_ticket]['title'] = title
    
    # Post to group
    await post_to_group(context.bot, pending_ticket, title)
    
    # Clear pending
    context.user_data.pop('pending_ticket', None)
    
    await update.message.reply_text(
        f"✅ تم نشر المحتوى بنجاح!\n\n"
        f"🎫 التيكت: `{pending_ticket}`\n"
        f"📝 العنوان: {title}",
        parse_mode='Markdown'
    )
```

async def post_to_group(bot, ticket_id, title):
“”“Post content to group with watermarked preview”””
try:
ticket_data = tickets_storage[ticket_id]

```
    # Create inline keyboard
    keyboard = [[InlineKeyboardButton("🔥 شاهد مجاناً", url=ouo_link if ouo_link else "https://t.me/your_bot")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message_text = (
        f"🔥 {title}\n\n"
        f"🎫 التيكت: `{ticket_id}`\n\n"
        f"📌 كيفية المشاهدة:\n"
        f"1️⃣ انسخ التيكت أعلاه\n"
        f"2️⃣ اضغط على الزر بالأسفل\n"
        f"3️⃣ الصق التيكت في التطبيق"
    )
    
    # Send preview (watermarked thumbnail)
    if ticket_data['media_type'] == 'image':
        image_bytes = base64.b64decode(ticket_data['media_data'])
        await bot.send_photo(
            chat_id=GROUP_CHAT_ID,
            photo=BytesIO(image_bytes),
            caption=message_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    else:
        await bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text=message_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
except Exception as e:
    logger.error(f"Post to group error: {e}")
```

async def notify_admin_payment(user_id, ticket, payment_id):
“”“Notify admin about payment”””
try:
bot = telegram_app.bot
await bot.send_message(
chat_id=ADMIN_ID,
text=(
f”💰 دفعة جديدة!\n\n”
f”👤 المستخدم: {user_id}\n”
f”🎫 التيكت: {ticket}\n”
f”💳 معرف الدفع: {payment_id}\n”
f”💵 المبلغ: {PAYMENT_AMOUNT} TON”
)
)
except Exception as e:
logger.error(f”Notify admin error: {e}”)

# Initialize Telegram Bot

telegram_app = Application.builder().token(BOT_TOKEN).build()

telegram_app.add_handler(CommandHandler(“start”, start_command))
telegram_app.add_handler(CommandHandler(“link”, link_command))
telegram_app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, handle_media))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

# Run both Flask and Telegram bot

def run_flask():
“”“Run Flask app”””
app.run(host=‘0.0.0.0’, port=PORT)

def run_telegram():
“”“Run Telegram bot”””
asyncio.set_event_loop(asyncio.new_event_loop())
telegram_app.run_polling(allowed_updates=Update.ALL_TYPES)

if **name** == ‘**main**’:
# Start Flask in separate thread
flask_thread = Thread(target=run_flask)
flask_thread.start()

```
# Run Telegram bot in main thread
run_telegram()
```