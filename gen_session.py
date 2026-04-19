#!/usr/bin/env python3
"""
gen_session.py – Manual Pyrogram Session Generator
====================================================
Jab bhi koi naya number add karna ho manually,
yeh script run karo aur .session file ban jayegi.

Install:
  pip install pyrogram==2.0.106 tgcrypto

Run:
  python gen_session.py
"""

from pyrogram import Client

# ──────────────────────────────────────────────
#  CONFIG – bot wali values hi daalo
# ──────────────────────────────────────────────
API_ID   = 12345678          # apna API ID
API_HASH = "your_api_hash"   # apna API Hash

# ──────────────────────────────────────────────

def main():
    print("=" * 45)
    print("  Pyrogram Session Generator")
    print("=" * 45)

    phone = input("\n📞 Phone number daalo (e.g. +919876543210): ").strip()

    if not phone.startswith("+"):
        print("❌ Number + se shuru hona chahiye! e.g. +91...")
        return

    safe_name = phone.replace("+", "").replace(" ", "")
    session_path = f"sessions/{safe_name}"

    print(f"\n⏳ OTP bheja ja raha hai {phone} pe...")

    app = Client(session_path, api_id=API_ID, api_hash=API_HASH)

    with app:
        print(f"\n✅ Login successful!")
        print(f"📁 Session saved: sessions/{safe_name}.session")
        print(f"\nAb yeh file bot mein /addnumber se upload kar sakte ho.")

if __name__ == "__main__":
    import os
    os.makedirs("sessions", exist_ok=True)
    main()
