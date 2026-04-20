#!/usr/bin/env python3
"""
Virtual Number Bot – Full UI + Force Join + Wallet + OTP Session + 2FA + Smart Buy Flow
=========================================================================================
Install:
  pip install python-telegram-bot==20.7 pyrogram==2.0.106 tgcrypto
"""

import asyncio
import logging
import re
import sqlite3
from pathlib import Path

from pyrogram import Client
from pyrogram.errors import SessionPasswordNeeded
from pyrogram.handlers import MessageHandler as PyroMsgHandler

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, filters, ContextTypes,
)

# ──────────────────────────────────────────────
#  CONFIG  ← Yahan apni details bharo
# ──────────────────────────────────────────────
BOT_TOKEN      = "8374340113:AAElS1BoY4qIL7yt-Tcq_pbVRJc07gG1q6A"
ADMIN_IDS      = [8263530800]
PAYMENT_INFO   = "UPI: solankiraghu7572-1@okhdfcbank"
API_ID         = 39917988
API_HASH       = "bd827dbeac6a55896ff11539bc80365b"

FORCE_CHANNEL_USERNAME = "yourchannel"
FORCE_CHANNEL_LINK     = "https://t.me/yourchannel"
FORCE_CHANNEL_ID       = -1001234567890

SUPPORT_GROUP_LINK   = "https://t.me/yoursupportgroup"
SUPPORT_CHANNEL_LINK = "https://t.me/yourchannel"

SESSIONS_DIR = Path("sessions")
SESSIONS_DIR.mkdir(exist_ok=True)
OTP_TIMEOUT  = 300

# ──────────────────────────────────────────────
#  DATABASE
# ──────────────────────────────────────────────
def get_con():
    con = sqlite3.connect("bot.db")
    con.row_factory = sqlite3.Row
    return con

def db_init():
    con = get_con()
    con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id   INTEGER PRIMARY KEY,
            username  TEXT,
            balance   INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS numbers (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            category     TEXT,
            country      TEXT DEFAULT 'India',
            number       TEXT UNIQUE,
            price        INTEGER,
            description  TEXT,
            session_file TEXT,
            status       TEXT DEFAULT 'available'
        );
        CREATE TABLE IF NOT EXISTS orders (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   INTEGER,
            username  TEXT,
            number_id INTEGER,
            status    TEXT DEFAULT 'pending'
        );
        CREATE TABLE IF NOT EXISTS topup_requests (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   INTEGER,
            username  TEXT,
            amount    INTEGER,
            status    TEXT DEFAULT 'pending'
        );
    """)
    # Purani DB ke liye country column add karo
    try:
        con.execute("ALTER TABLE numbers ADD COLUMN country TEXT DEFAULT 'India'")
        con.commit()
    except Exception:
        pass
    con.commit(); con.close()

db_init()

def db_one(q, p=()):
    con = get_con(); r = con.execute(q, p).fetchone(); con.close(); return r

def db_all(q, p=()):
    con = get_con(); r = con.execute(q, p).fetchall(); con.close(); return r

def db_run(q, p=()):
    con = get_con(); con.execute(q, p); con.commit(); con.close()

def db_insert(q, p=()):
    con = get_con(); cur = con.execute(q, p); con.commit()
    lid = cur.lastrowid; con.close(); return lid

def ensure_user(user_id, username):
    db_run("INSERT OR IGNORE INTO users (user_id,username,balance) VALUES (?,?,0)",
           (user_id, username))

def get_balance(user_id):
    row = db_one("SELECT balance FROM users WHERE user_id=?", (user_id,))
    return row["balance"] if row else 0

def extract_otp(text):
    m = re.search(r'\b(\d{4,8})\b', text or "")
    return m.group(1) if m else None

def is_admin(uid): return uid in ADMIN_IDS

# ──────────────────────────────────────────────
#  CONVERSATION STATES
# ──────────────────────────────────────────────
# Admin: Add number
ADD_CAT, ADD_COUNTRY, ADD_NUM, ADD_PRICE, ADD_DESC, ADD_OTP_WAIT, ADD_2FA_WAIT = range(7)

# User: Buy flow
BUY_SERVICE, BUY_COUNTRY, BUY_QTY, BUY_CONFIRM = range(10, 14)

TOPUP_AMOUNTS = [50, 100, 200, 500, 1000]
active_listeners: dict = {}

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ══════════════════════════════════════════════
#  FORCE JOIN CHECK
# ══════════════════════════════════════════════

async def check_joined(bot, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(FORCE_CHANNEL_ID, user_id)
        return member.status not in ["left", "kicked", "banned"]
    except Exception:
        return False

async def force_join_gate(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    user   = update.effective_user
    joined = await check_joined(ctx.bot, user.id)
    if not joined:
        btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Channel Join Karein", url=FORCE_CHANNEL_LINK)],
            [InlineKeyboardButton("✅ Maine Join Kar Liya", callback_data="check_join")],
        ])
        text = (
            "⚠️ *Bot Use Karne Ke Liye Channel Join Karein!*\n\n"
            "📢 Hamare official channel ko join karna zaroori hai.\n\n"
            "👇 Niche button se join karein, phir '✅ Maine Join Kar Liya' dabayein."
        )
        if update.callback_query:
            await update.callback_query.answer("Pehle channel join karein!", show_alert=True)
            await update.callback_query.message.reply_text(text, reply_markup=btn, parse_mode="Markdown")
        else:
            await update.message.reply_text(text, reply_markup=btn, parse_mode="Markdown")
        return False
    return True

async def cb_check_join(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q      = update.callback_query
    joined = await check_joined(ctx.bot, q.from_user.id)
    if joined:
        await q.answer("✅ Shukriya! Ab aap bot use kar sakte hain.", show_alert=True)
        await q.message.delete()
        await show_main_menu(q.message, q.from_user, ctx, edit=False)
    else:
        await q.answer("❌ Aapne abhi join nahi kiya. Pehle join karein!", show_alert=True)

# ══════════════════════════════════════════════
#  MAIN MENU
# ══════════════════════════════════════════════

async def show_main_menu(msg, user, ctx, edit=False):
    ensure_user(user.id, user.username or user.first_name)
    bal        = get_balance(user.id)
    nums_count = len(db_all("SELECT id FROM numbers WHERE status='available'"))

    text = (
        f"👋 *Namaste {user.first_name}!*\n\n"
        f"🏪 *Virtual Number Shop*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Aapka Balance: *₹{bal}*\n"
        f"📱 Available Numbers: *{nums_count}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Kya karna chahte hain? 👇"
    )

    btns = [
        [
            InlineKeyboardButton("🛒 Number Kharido",   callback_data="menu:buynumber"),
            InlineKeyboardButton("💰 Balance Dekho",    callback_data="menu:balance"),
        ],
        [
            InlineKeyboardButton("➕ Balance Add Karo", callback_data="menu:addbalance"),
            InlineKeyboardButton("📦 Mere Orders",      callback_data="menu:myorders"),
        ],
        [
            InlineKeyboardButton("💬 Support Group",    url=SUPPORT_GROUP_LINK),
            InlineKeyboardButton("📢 Our Channel",      url=SUPPORT_CHANNEL_LINK),
        ],
    ]
    if is_admin(user.id):
        btns.append([InlineKeyboardButton("🔐 Admin Panel", callback_data="menu:admin")])

    markup = InlineKeyboardMarkup(btns)
    if edit:
        try:    await msg.edit_text(text, reply_markup=markup, parse_mode="Markdown")
        except: await msg.reply_text(text, reply_markup=markup, parse_mode="Markdown")
    else:
        await msg.reply_text(text, reply_markup=markup, parse_mode="Markdown")

# ══════════════════════════════════════════════
#  /start
# ══════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await force_join_gate(update, ctx): return
    ensure_user(user.id, user.username or user.first_name)
    await show_main_menu(update.message, user, ctx)

# ══════════════════════════════════════════════
#  USER BUY FLOW – CONVERSATION
# ══════════════════════════════════════════════

async def buy_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Step 1: Service choose karo (WhatsApp/Telegram etc)"""
    q = update.callback_query
    if q: await q.answer()
    if not await force_join_gate(update, ctx): return ConversationHandler.END

    user = update.effective_user
    ensure_user(user.id, user.username or user.first_name)

    services = db_all("SELECT DISTINCT category FROM numbers WHERE status='available'")
    if not services:
        text   = "😔 *Abhi Koi Number Available Nahi Hai*\n\nBaad mein aayein."
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="menu:home")]])
        if q: await q.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
        else:  await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
        return ConversationHandler.END

    btns = [[InlineKeyboardButton(s["category"], callback_data=f"bsvc:{s['category']}")] for s in services]
    btns.append([InlineKeyboardButton("❌ Cancel", callback_data="menu:home")])

    text   = "📱 *Kaunsi Service Chahiye?*\n\nSelect karein 👇"
    markup = InlineKeyboardMarkup(btns)
    if q: await q.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
    else:  await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
    return BUY_SERVICE

async def buy_service_chosen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Step 2: Country choose karo"""
    q = update.callback_query; await q.answer()
    service = q.data.split(":", 1)[1]
    ctx.user_data["buy_service"] = service

    countries = db_all(
        "SELECT DISTINCT country FROM numbers WHERE status='available' AND category=?", (service,)
    )
    if not countries:
        await q.edit_message_text(
            f"❌ *{service}* ke liye koi number available nahi.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="menu:buynumber")]]))
        return ConversationHandler.END

    btns = [[InlineKeyboardButton(f"🌍 {c['country']}", callback_data=f"bcnt:{c['country']}")] for c in countries]
    btns.append([InlineKeyboardButton("◀️ Back", callback_data="menu:buynumber")])

    await q.edit_message_text(
        f"✅ Service: *{service}*\n\n🌍 *Kaunsa Country chahiye?*\n\nSelect karein 👇",
        reply_markup=InlineKeyboardMarkup(btns), parse_mode="Markdown"
    )
    return BUY_COUNTRY

async def buy_country_chosen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Step 3: Kitne numbers chahiye"""
    q = update.callback_query; await q.answer()
    country = q.data.split(":", 1)[1]
    ctx.user_data["buy_country"] = country
    service = ctx.user_data["buy_service"]

    nums = db_all(
        "SELECT * FROM numbers WHERE status='available' AND category=? AND country=?",
        (service, country)
    )
    if not nums:
        await q.edit_message_text(
            f"❌ *{country}* mein *{service}* ke liye koi number available nahi.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="menu:buynumber")]]))
        return ConversationHandler.END

    price       = nums[0]["price"]
    available   = len(nums)
    bal         = get_balance(update.effective_user.id)
    max_can_buy = min(available, bal // price, 5)

    ctx.user_data["buy_price"]     = price
    ctx.user_data["buy_available"] = available

    if max_can_buy == 0:
        await q.edit_message_text(
            f"❌ *Balance Kam Hai!*\n\n"
            f"💰 Aapka Balance: ₹{bal}\n"
            f"🏷 Price per number: ₹{price}\n\n"
            f"Pehle balance add karein 👇",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Balance Add Karo", callback_data="menu:addbalance")],
                [InlineKeyboardButton("🏠 Main Menu",        callback_data="menu:home")],
            ])
        )
        return ConversationHandler.END

    btns = [[InlineKeyboardButton(
        f"{i} Number{'s' if i > 1 else ''} – ₹{i * price}",
        callback_data=f"bqty:{i}"
    )] for i in range(1, max_can_buy + 1)]
    btns.append([InlineKeyboardButton("◀️ Back", callback_data="menu:buynumber")])

    await q.edit_message_text(
        f"✅ Service: *{service}* | Country: *{country}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Price: ₹{price} per number\n"
        f"📦 Available: {available}\n"
        f"💳 Aapka Balance: ₹{bal}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"*Kitne numbers chahiye?* 👇",
        reply_markup=InlineKeyboardMarkup(btns), parse_mode="Markdown"
    )
    return BUY_QTY

async def buy_qty_chosen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Step 4: Confirm"""
    q = update.callback_query; await q.answer()
    qty     = int(q.data.split(":")[1])
    service = ctx.user_data["buy_service"]
    country = ctx.user_data["buy_country"]
    price   = ctx.user_data["buy_price"]
    total   = qty * price
    bal     = get_balance(update.effective_user.id)
    ctx.user_data["buy_qty"] = qty

    await q.edit_message_text(
        f"🛒 *Purchase Confirm Karein*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📱 Service: *{service}*\n"
        f"🌍 Country: *{country}*\n"
        f"🔢 Quantity: *{qty}*\n"
        f"💰 Price: ₹{price} × {qty} = *₹{total}*\n"
        f"💳 Balance baad mein: ₹{bal - total}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Confirm karein? 👇",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(f"✅ Confirm – ₹{total} Pay", callback_data="bconfirm:yes")],
            [InlineKeyboardButton("❌ Cancel", callback_data="menu:home")],
        ])
    )
    return BUY_CONFIRM

async def buy_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Step 5: Purchase complete"""
    q = update.callback_query; await q.answer()
    user    = q.from_user
    service = ctx.user_data["buy_service"]
    country = ctx.user_data["buy_country"]
    price   = ctx.user_data["buy_price"]
    qty     = ctx.user_data["buy_qty"]
    total   = qty * price
    bal     = get_balance(user.id)

    if bal < total:
        await q.edit_message_text("❌ *Balance Kam Ho Gaya!*\n\nPehle balance add karein.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ Balance Add Karo", callback_data="menu:addbalance")]]))
        return ConversationHandler.END

    nums = db_all(
        "SELECT * FROM numbers WHERE status='available' AND category=? AND country=? LIMIT ?",
        (service, country, qty)
    )
    if len(nums) < qty:
        await q.edit_message_text(
            f"❌ Sirf *{len(nums)}* number available hai abhi.\n\nDobara try karein.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="menu:home")]]))
        return ConversationHandler.END

    db_run("UPDATE users SET balance = balance - ? WHERE user_id=?", (total, user.id))

    bought = []
    for n in nums:
        db_run("UPDATE numbers SET status='sold' WHERE id=?", (n["id"],))
        db_insert("INSERT INTO orders (user_id,username,number_id,status) VALUES (?,?,?,'confirmed')",
                  (user.id, user.username or user.first_name, n["id"]))
        bought.append(n)

    new_bal = get_balance(user.id)
    text = (
        f"🎉 *Purchase Successful!*\n\n"
        f"📱 Service: *{service}*\n"
        f"🌍 Country: *{country}*\n"
        f"💰 Paid: ₹{total}\n"
        f"💳 Remaining Balance: ₹{new_bal}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📞 *Aapke Numbers:*\n"
    )
    for i, n in enumerate(bought, 1):
        text += f"{i}. `{n['number']}`\n"
    text += f"\n⏳ OTP listener start ho gaya...\nMessage/OTP aane pe turant notify karunga! 🚀"

    await q.edit_message_text(text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📦 Mere Orders", callback_data="menu:myorders")],
            [InlineKeyboardButton("🏠 Main Menu",   callback_data="menu:home")],
        ])
    )

    for aid in ADMIN_IDS:
        try:
            await ctx.bot.send_message(aid,
                f"🛒 *Naya Purchase!*\n"
                f"👤 @{user.username or user.first_name} | `{user.id}`\n"
                f"📱 {service} | 🌍 {country} | {qty} numbers | ₹{total}",
                parse_mode="Markdown")
        except Exception: pass

    for n in bought:
        asyncio.create_task(start_otp_listener(ctx.application, n["id"], user.id, n["number"]))

    return ConversationHandler.END

async def buy_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="menu:home")]])
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text("❌ Cancel ho gaya.", reply_markup=markup)
    else:
        await update.message.reply_text("❌ Cancel ho gaya.", reply_markup=markup)
    return ConversationHandler.END

# ══════════════════════════════════════════════
#  MENU CALLBACKS
# ══════════════════════════════════════════════

async def cb_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query; await q.answer()
    user = q.from_user
    if not await force_join_gate(update, ctx): return
    action = q.data.split(":")[1]

    if action == "buynumber":
        await buy_start(update, ctx)

    elif action == "balance":
        bal = get_balance(user.id)
        await q.message.edit_text(
            f"💰 *Aapka Balance*\n\nAvailable: *₹{bal}*\n\nBalance add karne ke liye niche button dabayein. 👇",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Balance Add Karo", callback_data="menu:addbalance")],
                [InlineKeyboardButton("🏠 Main Menu",        callback_data="menu:home")],
            ]), parse_mode="Markdown"
        )

    elif action == "addbalance":
        btns = []; row = []
        for i, amt in enumerate(TOPUP_AMOUNTS):
            row.append(InlineKeyboardButton(f"₹{amt}", callback_data=f"topup:{amt}"))
            if len(row) == 3: btns.append(row); row = []
        if row: btns.append(row)
        btns.append([InlineKeyboardButton("🏠 Main Menu", callback_data="menu:home")])
        await q.message.edit_text("💳 *Balance Add Karein*\n\nKitna balance chahiye?",
                                  reply_markup=InlineKeyboardMarkup(btns), parse_mode="Markdown")

    elif action == "myorders":
        rows = db_all(
            "SELECT o.*,n.number,n.price,n.category,n.country FROM orders o JOIN numbers n ON o.number_id=n.id WHERE o.user_id=?",
            (user.id,)
        )
        if not rows:
            text = "📭 *Aapka Koi Order Nahi Hai*\n\nNumbers khareedne ke liye 'Number Kharido' dabayein."
        else:
            emo  = {"pending": "⏳", "confirmed": "✅", "cancelled": "❌"}
            text = "📦 *Aapke Orders:*\n\n"
            for o in rows:
                text += f"{emo.get(o['status'],'✅')} `#{o['id']}` | {o['category']} | {o['country']} | `{o['number']}` | ₹{o['price']}\n"
        await q.message.edit_text(text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="menu:home")]]),
            parse_mode="Markdown")

    elif action == "home":
        await show_main_menu(q.message, user, ctx, edit=True)

    elif action == "admin":
        if not is_admin(user.id):
            await q.answer("❌ Permission nahi.", show_alert=True); return
        await show_admin_panel(q.message, ctx)

# ══════════════════════════════════════════════
#  TOPUP
# ══════════════════════════════════════════════

async def cb_topup_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q      = update.callback_query; await q.answer()
    user   = q.from_user
    amount = int(q.data.split(":")[1])
    rid    = db_insert(
        "INSERT INTO topup_requests (user_id,username,amount,status) VALUES (?,?,?,'pending')",
        (user.id, user.username or user.first_name, amount)
    )
    await q.edit_message_text(
        f"💳 *Balance Recharge – ₹{amount}*\n\n"
        f"Niche details pe payment karein:\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{PAYMENT_INFO}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📌 Amount: *₹{amount}*\n"
        f"🆔 Request ID: `{rid}`\n\n"
        f"📸 Payment karne ke baad screenshot yahan bhejo.\n"
        f"Admin confirm karega, balance add ho jayega! ✅",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 Support",   url=SUPPORT_GROUP_LINK)],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="menu:home")],
        ])
    )
    for aid in ADMIN_IDS:
        try:
            await ctx.bot.send_message(aid,
                f"🔔 *Naya Top-up Request!*\n"
                f"👤 @{user.username or user.first_name} | `{user.id}`\n"
                f"💰 ₹{amount} | Request `#{rid}`\n\n"
                f"Confirm: `/topup {user.id} {amount}`",
                parse_mode="Markdown")
        except Exception: pass

async def handle_screenshot(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.username or user.first_name)
    for aid in ADMIN_IDS:
        try:
            await ctx.bot.forward_message(aid, update.effective_chat.id, update.message.message_id)
            await ctx.bot.send_message(aid,
                f"📸 *Payment Screenshot*\n"
                f"👤 @{user.username or user.first_name} | `{user.id}`\n\n"
                f"Balance add: `/topup {user.id} <amount>`",
                parse_mode="Markdown")
        except Exception: pass
    await update.message.reply_text(
        "✅ *Screenshot Bhej Diya!*\n\n"
        "⏳ Admin confirm karega, balance add ho jayega.\n"
        "Phir /start se numbers khareed sakte ho! 🎉",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 Support",   url=SUPPORT_GROUP_LINK)],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="menu:home")],
        ])
    )

# ══════════════════════════════════════════════
#  ADMIN PANEL UI
# ══════════════════════════════════════════════

async def show_admin_panel(msg, ctx):
    nums    = db_all("SELECT * FROM numbers")
    orders  = db_all("SELECT * FROM orders")
    pending = db_all("SELECT * FROM topup_requests WHERE status='pending'")
    avail   = len([n for n in nums if n["status"] == "available"])

    text = (
        f"🔐 *Admin Panel*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📱 Total Numbers: {len(nums)} | Available: {avail}\n"
        f"📦 Total Orders: {len(orders)}\n"
        f"💰 Pending Top-ups: {len(pending)}\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
    btns = [
        [
            InlineKeyboardButton("➕ Number Add (OTP)", callback_data="admin:addnumber"),
            InlineKeyboardButton("🗑 Number Hatao",      callback_data="admin:removenumber"),
        ],
        [
            InlineKeyboardButton("📋 Saare Orders",      callback_data="admin:orders"),
            InlineKeyboardButton("💰 Pending Top-ups",   callback_data="admin:topuprequests"),
        ],
        [InlineKeyboardButton("📁 Sessions List",        callback_data="admin:sessions")],
        [InlineKeyboardButton("🏠 Main Menu",            callback_data="menu:home")],
    ]
    await msg.edit_text(text, reply_markup=InlineKeyboardMarkup(btns), parse_mode="Markdown")

async def cb_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not is_admin(q.from_user.id):
        await q.answer("❌ Permission nahi.", show_alert=True); return

    action = q.data.split(":")[1]

    if action == "removenumber":
        nums = db_all("SELECT id,number,category,country,price FROM numbers")
        if not nums:
            await q.edit_message_text("📭 Koi number nahi.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="menu:admin")]])); return
        btns = [[InlineKeyboardButton(
            f"🗑 #{n['id']} {n['category']} {n['country']} {n['number']} ₹{n['price']}",
            callback_data=f"del:{n['id']}")] for n in nums]
        btns.append([InlineKeyboardButton("◀️ Back", callback_data="menu:admin")])
        await q.edit_message_text("🗑 *Kaun sa number hatana hai?*",
                                  reply_markup=InlineKeyboardMarkup(btns), parse_mode="Markdown")

    elif action == "orders":
        rows = db_all("SELECT o.*,n.number,n.price,n.country FROM orders o JOIN numbers n ON o.number_id=n.id")
        text = "📋 *Saare Orders:*\n\n"
        if not rows: text += "Koi order nahi."
        else:
            for o in rows:
                text += f"`#{o['id']}` @{o['username']} | `{o['number']}` | {o['country']} | ₹{o['price']} | *{o['status']}*\n"
        await q.edit_message_text(text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="menu:admin")]]))

    elif action == "topuprequests":
        rows = db_all("SELECT * FROM topup_requests WHERE status='pending'")
        text = "💰 *Pending Top-up Requests:*\n\n"
        if not rows: text += "Koi pending request nahi."
        else:
            for r in rows:
                text += (f"`#{r['id']}` @{r['username']} | `{r['user_id']}` | ₹{r['amount']}\n"
                         f"Confirm: `/topup {r['user_id']} {r['amount']}`\n\n")
        await q.edit_message_text(text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="menu:admin")]]))

    elif action == "sessions":
        files = list(SESSIONS_DIR.glob("*.session"))
        text  = "📁 *Sessions:*\n\n"
        if not files: text += "Koi session nahi."
        else:
            for f in files:
                row  = db_one("SELECT number,status,country FROM numbers WHERE session_file=?", (f.name,))
                info = f"`{row['number']}` ({row['country']}) – {row['status']}" if row else "unlinked"
                text += f"• `{f.name}` → {info}\n"
        await q.edit_message_text(text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="menu:admin")]]))

async def cb_delete_number(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not is_admin(q.from_user.id): return
    nid = int(q.data.split(":")[1])
    row = db_one("SELECT session_file FROM numbers WHERE id=?", (nid,))
    if row:
        db_run("DELETE FROM numbers WHERE id=?", (nid,))
        if row["session_file"]:
            p = SESSIONS_DIR / row["session_file"]
            if p.exists(): p.unlink()
        await q.answer(f"✅ Number #{nid} delete ho gaya.", show_alert=True)
    await show_admin_panel(q.message, ctx)

# ══════════════════════════════════════════════
#  ADMIN: ADD NUMBER VIA OTP + 2FA FLOW
# ══════════════════════════════════════════════

async def admin_addnumber_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        if update.message:
            await update.message.reply_text("❌ Permission nahi.")
        elif update.callback_query:
            await update.callback_query.answer("❌ Permission nahi.", show_alert=True)
        return ConversationHandler.END

    cats   = ["WhatsApp", "Telegram", "Instagram", "Gmail", "OTP", "Other"]
    btns   = [[InlineKeyboardButton(c, callback_data=f"setcat:{c}")] for c in cats]
    btns.append([InlineKeyboardButton("❌ Cancel", callback_data="menu:admin")])
    text   = "➕ *Naya Number Add (OTP se)*\n\nCategory choose karein:"
    markup = InlineKeyboardMarkup(btns)

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
    return ADD_CAT

async def add_cat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ctx.user_data["cat"] = q.data.split(":")[1]
    await q.edit_message_text(
        f"✅ Category: *{ctx.user_data['cat']}*\n\n"
        f"🌍 *Country name dalein* (e.g. India, USA, UK):",
        parse_mode="Markdown"
    )
    return ADD_COUNTRY

async def add_country(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["country"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ Country: *{ctx.user_data['country']}*\n\n"
        f"📞 Phone number dalein (+91XXXXXXXXXX):",
        parse_mode="Markdown"
    )
    return ADD_NUM

async def add_num(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["number"] = update.message.text.strip()
    await update.message.reply_text("💰 Price dalein (e.g. 99):")
    return ADD_PRICE

async def add_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try: ctx.user_data["price"] = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Sirf number dalein (e.g. 99):"); return ADD_PRICE
    await update.message.reply_text("📝 Description dalein (e.g. 'Fresh Indian Number'):")
    return ADD_DESC

async def add_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["desc"] = update.message.text.strip()
    phone = ctx.user_data["number"]

    await update.message.reply_text(
        f"📲 *OTP Bheja Ja Raha Hai...*\n\n📞 Number: `{phone}`\n\n⏳ Please wait...",
        parse_mode="Markdown"
    )

    safe_name    = phone.replace("+", "").replace(" ", "")
    session_path = str(SESSIONS_DIR / safe_name)

    try:
        client = Client(session_path, api_id=API_ID, api_hash=API_HASH)
        await client.connect()
        sent = await client.send_code(phone)
        ctx.user_data["phone_code_hash"] = sent.phone_code_hash
        ctx.user_data["session_path"]    = session_path
        ctx.user_data["safe_name"]       = safe_name
        ctx.user_data["pyro_client"]     = client

        await update.message.reply_text(
            f"✅ *OTP Bhej Diya!*\n\n📞 `{phone}` pe OTP aaya hoga.\n\n🔢 OTP type karein:",
            parse_mode="Markdown"
        )
        return ADD_OTP_WAIT

    except Exception as e:
        log.error(f"OTP send error: {e}")
        await update.message.reply_text(
            f"❌ *OTP Bhejne Mein Error!*\n\n`{e}`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Admin Panel", callback_data="menu:admin")]]))
        return ConversationHandler.END

async def add_otp_received(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    otp    = update.message.text.strip().replace(" ", "")
    phone  = ctx.user_data.get("number")
    client = ctx.user_data.get("pyro_client")

    if not client:
        await update.message.reply_text("❌ Session expire. Dobara /addnumber try karein.")
        return ConversationHandler.END

    await update.message.reply_text("⏳ Verify ho raha hai...")

    try:
        await client.sign_in(
            phone_number    = phone,
            phone_code_hash = ctx.user_data["phone_code_hash"],
            phone_code      = otp
        )
        await _save_number_session(update, ctx, client)
        return ConversationHandler.END

    except SessionPasswordNeeded:
        await update.message.reply_text(
            "🔐 *2FA (Two-Step Verification) On Hai!*\n\n"
            "Is number ka 2FA password dalein 👇",
            parse_mode="Markdown"
        )
        return ADD_2FA_WAIT

    except Exception as e:
        err = str(e)
        if "PHONE_CODE_INVALID" in err:
            await update.message.reply_text("❌ *OTP Galat Hai!*\n\nSahi OTP dalein:", parse_mode="Markdown")
            return ADD_OTP_WAIT
        elif "PHONE_CODE_EXPIRED" in err:
            msg = "⏰ *OTP Expire Ho Gaya!*\n\n/addnumber se dobara try karein."
        else:
            msg = f"❌ *Error:* `{err}`"
        try: await client.disconnect()
        except: pass
        ctx.user_data.pop("pyro_client", None)
        await update.message.reply_text(msg, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Admin Panel", callback_data="menu:admin")]]))
        return ConversationHandler.END

async def add_2fa_received(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """2FA password verify karo"""
    password = update.message.text.strip()
    client   = ctx.user_data.get("pyro_client")

    if not client:
        await update.message.reply_text("❌ Session expire. Dobara /addnumber try karein.")
        return ConversationHandler.END

    await update.message.reply_text("⏳ 2FA verify ho raha hai...")

    try:
        await client.check_password(password)
        await _save_number_session(update, ctx, client)
        return ConversationHandler.END

    except Exception as e:
        err = str(e)
        if "PASSWORD_HASH_INVALID" in err:
            await update.message.reply_text(
                "❌ *2FA Password Galat Hai!*\n\nSahi password dalein:",
                parse_mode="Markdown"
            )
            return ADD_2FA_WAIT
        try: await client.disconnect()
        except: pass
        ctx.user_data.pop("pyro_client", None)
        await update.message.reply_text(f"❌ *2FA Error:* `{err}`", parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Admin Panel", callback_data="menu:admin")]]))
        return ConversationHandler.END

async def _save_number_session(update, ctx, client):
    """Session save karo DB mein"""
    phone        = ctx.user_data["number"]
    safe_name    = ctx.user_data["safe_name"]
    session_file = f"{safe_name}.session"

    await client.disconnect()

    nid = db_insert(
        "INSERT INTO numbers (category,country,number,price,description,session_file,status) VALUES (?,?,?,?,?,?,'available')",
        (ctx.user_data["cat"], ctx.user_data["country"], phone,
         ctx.user_data["price"], ctx.user_data["desc"], session_file)
    )

    await update.message.reply_text(
        f"🎉 *Number Successfully Add Ho Gaya!*\n\n"
        f"🆔 ID: `{nid}`\n"
        f"📞 Number: `{phone}`\n"
        f"📂 Category: {ctx.user_data['cat']}\n"
        f"🌍 Country: {ctx.user_data['country']}\n"
        f"💰 Price: ₹{ctx.user_data['price']}\n"
        f"📁 Session: `{session_file}`\n\n"
        f"✅ Session save ho gaya!",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Aur Number Add", callback_data="admin:addnumber")],
            [InlineKeyboardButton("🔐 Admin Panel",    callback_data="menu:admin")],
        ])
    )
    ctx.user_data.pop("pyro_client", None)
    ctx.user_data.pop("phone_code_hash", None)

async def cancel_conv(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    client = ctx.user_data.pop("pyro_client", None)
    if client:
        try: await client.disconnect()
        except: pass
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Admin Panel", callback_data="menu:admin")]])
    if update.message:
        await update.message.reply_text("❌ Cancel ho gaya.", reply_markup=markup)
    elif update.callback_query:
        await update.callback_query.message.reply_text("❌ Cancel ho gaya.", reply_markup=markup)
    return ConversationHandler.END

# ══════════════════════════════════════════════
#  ADMIN TEXT COMMANDS
# ══════════════════════════════════════════════

async def admin_topup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if len(ctx.args) < 2:
        await update.message.reply_text("Usage: /topup <user_id> <amount>"); return
    try:
        uid = int(ctx.args[0]); amount = int(ctx.args[1])
    except ValueError:
        await update.message.reply_text("❌ Invalid values."); return
    row = db_one("SELECT * FROM users WHERE user_id=?", (uid,))
    if not row:
        await update.message.reply_text("❌ User nahi mila."); return
    db_run("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, uid))
    db_run("UPDATE topup_requests SET status='confirmed' WHERE user_id=? AND status='pending'", (uid,))
    new_bal = get_balance(uid)
    try:
        await ctx.bot.send_message(uid,
            f"✅ *Balance Add Ho Gaya!*\n\n"
            f"💰 Added: *₹{amount}*\n"
            f"💳 New Balance: *₹{new_bal}*\n\n"
            f"Ab /start se number khareedein! 🛒",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Number Kharido", callback_data="menu:buynumber")]]))
    except Exception: pass
    await update.message.reply_text(f"✅ User `{uid}` ko ₹{amount} add. New balance: ₹{new_bal}", parse_mode="Markdown")

# ══════════════════════════════════════════════
#  OTP LISTENER (After purchase)
# ══════════════════════════════════════════════

async def start_otp_listener(bot_app, number_id, buyer_id, number_str):
    row = db_one("SELECT session_file FROM numbers WHERE id=?", (number_id,))
    if not row or not row["session_file"]:
        await bot_app.bot.send_message(buyer_id, "❌ Session file missing."); return
    session_path = SESSIONS_DIR / row["session_file"]
    if not session_path.exists():
        await bot_app.bot.send_message(buyer_id, "❌ Session file disk pe nahi hai."); return

    client   = Client(str(session_path.with_suffix("")), api_id=API_ID, api_hash=API_HASH)
    active_listeners[number_id] = client
    received = asyncio.Event()

    async def on_message(c, message):
        text = message.text or message.caption or ""
        otp  = extract_otp(text)
        reply = f"📨 *Naya Message!*\n\n📞 `{number_str}`\n💬 `{text}`"
        if otp: reply += f"\n\n🔐 *OTP: `{otp}`*"
        try:
            await bot_app.bot.send_message(buyer_id, reply, parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="menu:home")]]))
        except Exception as e: log.error(e)
        received.set()

    client.add_handler(PyroMsgHandler(on_message))
    try:
        await client.start()
        try: await asyncio.wait_for(received.wait(), timeout=OTP_TIMEOUT)
        except asyncio.TimeoutError:
            await bot_app.bot.send_message(buyer_id,
                f"⏰ *Timeout!* {OTP_TIMEOUT} sec mein OTP nahi aaya.\n💬 Support se contact karein.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Support", url=SUPPORT_GROUP_LINK)]]))
    finally:
        try: await client.stop()
        except Exception: pass
        active_listeners.pop(number_id, None)

# ══════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Admin: Add number (OTP + 2FA support)
    add_conv = ConversationHandler(
        entry_points=[
            CommandHandler("addnumber", admin_addnumber_cmd),
            CallbackQueryHandler(admin_addnumber_cmd, pattern="^admin:addnumber$"),
        ],
        states={
            ADD_CAT:      [CallbackQueryHandler(add_cat,       pattern="^setcat:")],
            ADD_COUNTRY:  [MessageHandler(filters.TEXT & ~filters.COMMAND, add_country)],
            ADD_NUM:      [MessageHandler(filters.TEXT & ~filters.COMMAND, add_num)],
            ADD_PRICE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, add_price)],
            ADD_DESC:     [MessageHandler(filters.TEXT & ~filters.COMMAND, add_desc)],
            ADD_OTP_WAIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_otp_received)],
            ADD_2FA_WAIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_2fa_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conv)],
        allow_reentry=True,
        per_message=False,
    )

    # User: Buy number flow
    buy_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(buy_start, pattern="^menu:buynumber$"),
        ],
        states={
            BUY_SERVICE: [CallbackQueryHandler(buy_service_chosen, pattern="^bsvc:")],
            BUY_COUNTRY: [CallbackQueryHandler(buy_country_chosen, pattern="^bcnt:")],
            BUY_QTY:     [CallbackQueryHandler(buy_qty_chosen,     pattern="^bqty:")],
            BUY_CONFIRM: [CallbackQueryHandler(buy_confirm,        pattern="^bconfirm:")],
        },
        fallbacks=[
            CommandHandler("cancel", buy_cancel),
            CallbackQueryHandler(buy_cancel, pattern="^menu:home$"),
        ],
        allow_reentry=True,
        per_message=False,
    )

    app.add_handler(add_conv)
    app.add_handler(buy_conv)

    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("topup",     admin_topup))
    app.add_handler(CommandHandler("addnumber", admin_addnumber_cmd))

    app.add_handler(CallbackQueryHandler(cb_check_join,    pattern="^check_join$"))
    app.add_handler(CallbackQueryHandler(cb_menu,          pattern="^menu:"))
    app.add_handler(CallbackQueryHandler(cb_topup_amount,  pattern="^topup:"))
    app.add_handler(CallbackQueryHandler(cb_admin,         pattern="^admin:"))
    app.add_handler(CallbackQueryHandler(cb_delete_number, pattern="^del:"))

    app.add_handler(MessageHandler(filters.PHOTO, handle_screenshot))

    print("🤖 Bot chal raha hai...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
