#!/usr/bin/env python3
"""
Virtual Number Bot – Full UI + Force Join + Wallet + OTP Session
=================================================================
Install:
  pip install python-telegram-bot==20.7 pyrogram==2.0.106 tgcrypto
"""

import asyncio
import logging
import re
import sqlite3
from pathlib import Path

from pyrogram import Client
from pyrogram.handlers import MessageHandler as PyroMsgHandler

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, filters, ContextTypes,
)

# ──────────────────────────────────────────────
#  CONFIG  ← Yahan apni details bharo
# ──────────────────────────────────────────────
BOT_TOKEN      = "YOUR_BOT_TOKEN_HERE"
ADMIN_IDS      = [123456789]
PAYMENT_INFO   = "UPI: yourname@upi\nBank: XXXX-XXXX"
API_ID         = 12345678
API_HASH       = "your_api_hash_here"

# Force Join – apne channel ka username (@ ke bina) ya ID
FORCE_CHANNEL_USERNAME = "yourchannel"
FORCE_CHANNEL_LINK     = "https://t.me/yourchannel"
FORCE_CHANNEL_ID       = -1001234567890

# Support
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
# Add number via OTP flow states
ADD_CAT, ADD_NUM, ADD_PRICE, ADD_DESC, ADD_OTP_WAIT, REMOVE_ID = range(6)

CATEGORIES    = ["WhatsApp", "Telegram", "Instagram", "Gmail", "OTP", "Other"]
TOPUP_AMOUNTS = [50, 100, 200, 500, 1000]

active_listeners: dict = {}
pending_logins:   dict = {}   # number_id -> pyrogram Client (login ke waqt)

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
            InlineKeyboardButton("📱 Numbers Dekho",    callback_data="menu:numbers"),
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
#  MENU CALLBACKS
# ══════════════════════════════════════════════

async def cb_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q      = update.callback_query; await q.answer()
    user   = q.from_user

    if not await force_join_gate(update, ctx): return

    action = q.data.split(":")[1]

    if action == "numbers":
        await show_numbers_menu(q.message, user, ctx, edit=True)

    elif action == "balance":
        bal  = get_balance(user.id)
        btns = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Balance Add Karo", callback_data="menu:addbalance")],
            [InlineKeyboardButton("🏠 Main Menu",        callback_data="menu:home")],
        ])
        await q.message.edit_text(
            f"💰 *Aapka Balance*\n\nAvailable: *₹{bal}*\n\nBalance add karne ke liye niche button dabayein. 👇",
            reply_markup=btns, parse_mode="Markdown"
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
            "SELECT o.*,n.number,n.price,n.category FROM orders o JOIN numbers n ON o.number_id=n.id WHERE o.user_id=?",
            (user.id,)
        )
        if not rows:
            text = "📭 *Aapka Koi Order Nahi Hai*\n\nNumbers khareedne ke liye 'Numbers Dekho' dabayein."
        else:
            emo  = {"pending": "⏳", "confirmed": "✅", "cancelled": "❌"}
            text = "📦 *Aapke Orders:*\n\n"
            for o in rows:
                text += f"{emo.get(o['status'], '✅')} `#{o['id']}` | {o['category']} | `{o['number']}` | ₹{o['price']}\n"
        btns = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="menu:home")]])
        await q.message.edit_text(text, reply_markup=btns, parse_mode="Markdown")

    elif action == "home":
        await show_main_menu(q.message, user, ctx, edit=True)

    elif action == "admin":
        if not is_admin(user.id):
            await q.answer("❌ Permission nahi.", show_alert=True); return
        await show_admin_panel(q.message, ctx)

# ══════════════════════════════════════════════
#  NUMBERS UI
# ══════════════════════════════════════════════

async def show_numbers_menu(msg, user, ctx, edit=False):
    nums = db_all("SELECT * FROM numbers WHERE status='available'")
    bal  = get_balance(user.id)

    if not nums:
        btns = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="menu:home")]])
        text = "😔 *Abhi Koi Number Available Nahi Hai*\n\nBaad mein aayein ya support se contact karein."
        if edit: await msg.edit_text(text, reply_markup=btns, parse_mode="Markdown")
        else:    await msg.reply_text(text, reply_markup=btns, parse_mode="Markdown")
        return

    cats = sorted({n["category"] for n in nums})
    btns = []; row = []
    for i, c in enumerate(cats):
        count = len([n for n in nums if n["category"] == c])
        row.append(InlineKeyboardButton(f"📂 {c} ({count})", callback_data=f"cat:{c}"))
        if len(row) == 2: btns.append(row); row = []
    if row: btns.append(row)
    btns.append([InlineKeyboardButton("📋 Sab Numbers", callback_data="cat:ALL")])
    btns.append([InlineKeyboardButton("🏠 Main Menu",   callback_data="menu:home")])

    text = (
        f"📱 *Available Numbers*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Aapka Balance: *₹{bal}*\n"
        f"✅ Total Available: *{len(nums)}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Category choose karein 👇"
    )
    if edit: await msg.edit_text(text, reply_markup=InlineKeyboardMarkup(btns), parse_mode="Markdown")
    else:    await msg.reply_text(text, reply_markup=InlineKeyboardMarkup(btns), parse_mode="Markdown")

async def cb_category(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not await force_join_gate(update, ctx): return

    cat  = q.data.split(":", 1)[1]
    user = q.from_user
    nums = (db_all("SELECT * FROM numbers WHERE status='available'")
            if cat == "ALL"
            else db_all("SELECT * FROM numbers WHERE status='available' AND category=?", (cat,)))
    bal  = get_balance(user.id)

    if not nums:
        await q.edit_message_text("❌ Is category mein koi number nahi."); return

    text = (
        f"📂 *{cat} Numbers*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Aapka Balance: *₹{bal}*\n"
        f"✅ Available: *{len(nums)}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
    )
    btns = []
    for n in nums:
        can   = bal >= n["price"]
        text += f"🔢 `#{n['id']}` | 📞 `{n['number']}` | 💰 ₹{n['price']}\n📝 {n['description']}\n\n"
        label = f"🛒 Buy #{n['id']} – ₹{n['price']}" if can else f"❌ #{n['id']} – ₹{n['price']} (Balance kam)"
        btns.append([InlineKeyboardButton(label, callback_data=f"buy:{n['id']}")])

    btns.append([
        InlineKeyboardButton("◀️ Wapas", callback_data="menu:numbers"),
        InlineKeyboardButton("🏠 Home",  callback_data="menu:home"),
    ])
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))

async def cb_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not await force_join_gate(update, ctx): return

    user = q.from_user
    ensure_user(user.id, user.username or user.first_name)
    nid  = int(q.data.split(":")[1])
    n    = db_one("SELECT * FROM numbers WHERE id=? AND status='available'", (nid,))

    if not n:
        await q.edit_message_text("❌ Number already sold ya exist nahi karta."); return

    bal = get_balance(user.id)
    if bal < n["price"]:
        short = n["price"] - bal
        await q.edit_message_text(
            f"❌ *Balance Kam Hai!*\n\n"
            f"💰 Aapka Balance: ₹{bal}\n"
            f"🏷 Number Price: ₹{n['price']}\n"
            f"💸 Aur Chahiye: *₹{short}*\n\n"
            f"Pehle balance add karein 👇",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Balance Add Karo", callback_data="menu:addbalance")],
                [InlineKeyboardButton("🏠 Main Menu",        callback_data="menu:home")],
            ])
        ); return

    db_run("UPDATE users SET balance = balance - ? WHERE user_id=?", (n["price"], user.id))
    db_run("UPDATE numbers SET status='sold' WHERE id=?", (nid,))
    oid     = db_insert(
        "INSERT INTO orders (user_id,username,number_id,status) VALUES (?,?,?,'confirmed')",
        (user.id, user.username or user.first_name, nid)
    )
    new_bal = get_balance(user.id)

    await q.edit_message_text(
        f"🎉 *Purchase Successful!*\n\n"
        f"📞 Number: `{n['number']}`\n"
        f"📱 Category: {n['category']}\n"
        f"💰 Price Paid: ₹{n['price']}\n"
        f"💳 Remaining Balance: ₹{new_bal}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⏳ OTP listener start ho gaya...\n"
        f"Jaise hi OTP/SMS aayega, *turant* aapko milega! 🚀",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📦 Mere Orders", callback_data="menu:myorders")],
            [InlineKeyboardButton("🏠 Main Menu",   callback_data="menu:home")],
        ])
    )

    for aid in ADMIN_IDS:
        try:
            await ctx.bot.send_message(aid,
                f"🛒 *Naya Purchase!*\n👤 @{user.username or user.first_name}\n"
                f"📞 `{n['number']}` | ₹{n['price']}", parse_mode="Markdown")
        except Exception: pass

    asyncio.create_task(start_otp_listener(ctx.application, nid, user.id, n["number"]))

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
            [InlineKeyboardButton("💬 Support",  url=SUPPORT_GROUP_LINK)],
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
            [InlineKeyboardButton("💬 Support",  url=SUPPORT_GROUP_LINK)],
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
            InlineKeyboardButton("➕ Number Add (OTP)",  callback_data="admin:addnumber"),
            InlineKeyboardButton("🗑 Number Hatao",       callback_data="admin:removenumber"),
        ],
        [
            InlineKeyboardButton("📋 Saare Orders",       callback_data="admin:orders"),
            InlineKeyboardButton("💰 Pending Top-ups",    callback_data="admin:topuprequests"),
        ],
        [
            InlineKeyboardButton("📁 Sessions List",      callback_data="admin:sessions"),
        ],
        [InlineKeyboardButton("🏠 Main Menu",             callback_data="menu:home")],
    ]
    await msg.edit_text(text, reply_markup=InlineKeyboardMarkup(btns), parse_mode="Markdown")

async def cb_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not is_admin(q.from_user.id):
        await q.answer("❌ Permission nahi.", show_alert=True); return

    action = q.data.split(":")[1]

    if action == "addnumber":
        btns = [[InlineKeyboardButton(c, callback_data=f"setcat:{c}")] for c in CATEGORIES]
        btns.append([InlineKeyboardButton("❌ Cancel", callback_data="menu:admin")])
        await q.edit_message_text(
            "➕ *Naya Number Add (OTP se)*\n\nPehle category choose karein:",
            reply_markup=InlineKeyboardMarkup(btns), parse_mode="Markdown"
        )

    elif action == "removenumber":
        nums = db_all("SELECT id,number,category,price FROM numbers")
        if not nums:
            await q.edit_message_text("📭 Koi number nahi.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="menu:admin")]])); return
        btns = [[InlineKeyboardButton(
            f"🗑 #{n['id']} {n['category']} {n['number']} ₹{n['price']}",
            callback_data=f"del:{n['id']}")] for n in nums]
        btns.append([InlineKeyboardButton("◀️ Back", callback_data="menu:admin")])
        await q.edit_message_text("🗑 *Kaun sa number hatana hai?*",
                                  reply_markup=InlineKeyboardMarkup(btns), parse_mode="Markdown")

    elif action == "orders":
        rows = db_all("SELECT o.*,n.number,n.price FROM orders o JOIN numbers n ON o.number_id=n.id")
        text = "📋 *Saare Orders:*\n\n"
        if not rows: text += "Koi order nahi."
        else:
            for o in rows:
                text += f"`#{o['id']}` @{o['username']} | `{o['number']}` | ₹{o['price']} | *{o['status']}*\n"
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
                row  = db_one("SELECT number,status FROM numbers WHERE session_file=?", (f.name,))
                info = f"`{row['number']}` – {row['status']}" if row else "unlinked"
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
#  ADD NUMBER VIA OTP – CONVERSATION FLOW
# ══════════════════════════════════════════════

async def admin_addnumber_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Permission nahi."); return ConversationHandler.END
    btns = [[InlineKeyboardButton(c, callback_data=f"setcat:{c}")] for c in CATEGORIES]
    btns.append([InlineKeyboardButton("❌ Cancel", callback_data="menu:admin")])
    await update.message.reply_text(
        "➕ *Naya Number Add (OTP se)*\n\nCategory choose karein:",
        reply_markup=InlineKeyboardMarkup(btns), parse_mode="Markdown"
    )
    return ADD_CAT

async def add_cat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ctx.user_data["cat"] = q.data.split(":")[1]
    await q.edit_message_text(
        f"✅ Category: *{ctx.user_data['cat']}*\n\n"
        f"📞 Ab phone number dalein jisme OTP bhejana hai:\n"
        f"_(Format: +91XXXXXXXXXX)_",
        parse_mode="Markdown"
    )
    return ADD_NUM

async def add_num(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    ctx.user_data["number"] = phone
    await update.message.reply_text("💰 Is number ki price dalein (e.g. 99):")
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
        f"📲 *OTP Bheja Ja Raha Hai...*\n\n"
        f"📞 Number: `{phone}`\n\n"
        f"⏳ Please wait...",
        parse_mode="Markdown"
    )

    # Pyrogram client banao aur OTP bhejo
    safe_name  = phone.replace("+", "").replace(" ", "")
    session_path = str(SESSIONS_DIR / safe_name)

    try:
        client = Client(session_path, api_id=API_ID, api_hash=API_HASH)
        await client.connect()
        sent   = await client.send_code(phone)
        ctx.user_data["phone_code_hash"] = sent.phone_code_hash
        ctx.user_data["session_path"]    = session_path
        ctx.user_data["safe_name"]       = safe_name
        ctx.user_data["pyro_client"]     = client  # client save karo

        await update.message.reply_text(
            f"✅ *OTP Bhej Diya!*\n\n"
            f"📞 `{phone}` pe OTP aaya hoga.\n\n"
            f"🔢 Woh OTP yahan type karein:",
            parse_mode="Markdown"
        )
        return ADD_OTP_WAIT

    except Exception as e:
        log.error(f"OTP send error: {e}")
        await update.message.reply_text(
            f"❌ *OTP Bhejne Mein Error!*\n\n`{e}`\n\n"
            f"Check karein:\n• Number sahi hai?\n• API ID/Hash sahi hai?\n• Number Telegram pe registered hai?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Admin Panel", callback_data="menu:admin")]]))
        return ConversationHandler.END

async def add_otp_received(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    otp    = update.message.text.strip().replace(" ", "")
    phone  = ctx.user_data.get("number")
    client = ctx.user_data.get("pyro_client")

    if not client:
        await update.message.reply_text("❌ Session expire ho gaya. Dobara /addnumber try karein.")
        return ConversationHandler.END

    await update.message.reply_text("⏳ Verify ho raha hai...")

    try:
        await client.sign_in(
            phone_number    = phone,
            phone_code_hash = ctx.user_data["phone_code_hash"],
            phone_code      = otp
        )
        await client.disconnect()

        safe_name    = ctx.user_data["safe_name"]
        session_file = f"{safe_name}.session"

        # DB mein save karo
        nid = db_insert(
            "INSERT INTO numbers (category,number,price,description,session_file,status) VALUES (?,?,?,?,?,'available')",
            (ctx.user_data["cat"], phone, ctx.user_data["price"],
             ctx.user_data["desc"], session_file)
        )

        await update.message.reply_text(
            f"🎉 *Number Successfully Add Ho Gaya!*\n\n"
            f"🆔 ID: `{nid}`\n"
            f"📞 Number: `{phone}`\n"
            f"📂 Category: {ctx.user_data['cat']}\n"
            f"💰 Price: ₹{ctx.user_data['price']}\n"
            f"📁 Session: `{session_file}`\n\n"
            f"✅ Session `sessions/` folder mein save ho gaya!",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Aur Number Add", callback_data="admin:addnumber")],
                [InlineKeyboardButton("🔐 Admin Panel",    callback_data="menu:admin")],
            ])
        )

    except Exception as e:
        log.error(f"OTP verify error: {e}")
        err = str(e)
        if "PHONE_CODE_INVALID" in err:
            msg = "❌ *OTP Galat Hai!*\n\nSahi OTP dalein:"
            await update.message.reply_text(msg, parse_mode="Markdown")
            return ADD_OTP_WAIT  # dobara try karne do
        elif "PHONE_CODE_EXPIRED" in err:
            msg = "⏰ *OTP Expire Ho Gaya!*\n\n/addnumber se dobara try karein."
        elif "SESSION_PASSWORD_NEEDED" in err:
            msg = "🔐 *2FA Password Laga Hua Hai!*\n\nIs number ki 2FA (Two-Step Verification) on hai.\nPehle 2FA band karein, phir add karein."
        else:
            msg = f"❌ *Error:* `{err}`"

        try: await client.disconnect()
        except: pass

        await update.message.reply_text(msg, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Admin Panel", callback_data="menu:admin")]]))

    # cleanup
    ctx.user_data.pop("pyro_client", None)
    ctx.user_data.pop("phone_code_hash", None)
    return ConversationHandler.END

async def cancel_conv(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # Client agar connected hai toh disconnect karo
    client = ctx.user_data.pop("pyro_client", None)
    if client:
        try: await client.disconnect()
        except: pass
    await update.message.reply_text("❌ Cancel ho gaya.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Admin Panel", callback_data="menu:admin")]]))
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
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Numbers Dekho", callback_data="menu:numbers")]]))
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

    # OTP-based add number conversation
    add_conv = ConversationHandler(
        entry_points=[
            CommandHandler("addnumber", admin_addnumber_cmd),
            CallbackQueryHandler(lambda u, c: admin_addnumber_cmd(u, c), pattern="^admin:addnumber$"),
        ],
        states={
            ADD_CAT:      [CallbackQueryHandler(add_cat,          pattern="^setcat:")],
            ADD_NUM:      [MessageHandler(filters.TEXT & ~filters.COMMAND, add_num)],
            ADD_PRICE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, add_price)],
            ADD_DESC:     [MessageHandler(filters.TEXT & ~filters.COMMAND, add_desc)],
            ADD_OTP_WAIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_otp_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conv)],
        allow_reentry=True,
    )

    app.add_handler(add_conv)  # ← pehle add karo (priority)

    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("topup",     admin_topup))
    app.add_handler(CommandHandler("addnumber", admin_addnumber_cmd))

    app.add_handler(CallbackQueryHandler(cb_check_join,    pattern="^check_join$"))
    app.add_handler(CallbackQueryHandler(cb_menu,          pattern="^menu:"))
    app.add_handler(CallbackQueryHandler(cb_category,      pattern="^cat:"))
    app.add_handler(CallbackQueryHandler(cb_buy,           pattern="^buy:"))
    app.add_handler(CallbackQueryHandler(cb_topup_amount,  pattern="^topup:"))
    app.add_handler(CallbackQueryHandler(cb_admin,         pattern="^admin:"))
    app.add_handler(CallbackQueryHandler(cb_delete_number, pattern="^del:"))

    app.add_handler(MessageHandler(filters.PHOTO, handle_screenshot))

    print("🤖 Bot chal raha hai...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
