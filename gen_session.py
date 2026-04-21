#!/usr/bin/env python3
"""
gen_session.py – Manual Telethon Session Generator
====================================================
Jab bhi koi naya number manually add karna ho,
yeh script run karo aur .session file ban jayegi.

Install:
  pip install telethon

Run:
  python gen_session.py
"""

import asyncio
import os
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError

API_ID   = 39917988
API_HASH = "bd827dbeac6a55896ff11539bc80365b"

async def main():
    print("=" * 45)
    print("  Telethon Session Generator")
    print("=" * 45)

    phone = input("\n📞 Phone number daalo (e.g. +919876543210): ").strip()

    if not phone.startswith("+"):
        print("❌ Number + se shuru hona chahiye!")
        return

    safe_name    = phone.replace("+", "").replace(" ", "")
    session_path = f"sessions/{safe_name}"

    print(f"\n⏳ OTP bheja ja raha hai {phone} pe...")

    client = TelegramClient(session_path, API_ID, API_HASH)
    await client.connect()

    try:
        sent = await client.send_code_request(phone)

        otp = input("🔢 OTP daalo: ").strip().replace(" ", "")

        try:
            await client.sign_in(phone, otp, phone_code_hash=sent.phone_code_hash)

        except SessionPasswordNeededError:
            print("🔐 2FA on hai!")
            password = input("🔑 2FA password daalo: ").strip()
            await client.sign_in(password=password)

        except PhoneCodeInvalidError:
            print("❌ OTP galat hai! Dobara run karo.")
            await client.disconnect()
            return

        print(f"\n✅ Login successful!")
        print(f"📁 Session saved: sessions/{safe_name}.session")
        print(f"\nAb bot automatically use karega is session ko.")

    except Exception as e:
        print(f"❌ Error: {e}")

    finally:
        await client.disconnect()

if __name__ == "__main__":
    os.makedirs("sessions", exist_ok=True)
    asyncio.run(main())
