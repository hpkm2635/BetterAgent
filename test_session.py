import asyncio
from telethon import TelegramClient
import os

api_id = int(os.getenv("TELEGRAM_API_ID", "12345678"))
api_hash = os.getenv("TELEGRAM_API_HASH", "YOUR_TELEGRAM_API_HASH_HERE")

client = TelegramClient('meowclient', api_id, api_hash)

async def main():
    await client.connect()
    is_auth = await client.is_user_authorized()
    print('Telethon Authorized:', is_auth)
    if is_auth:
        me = await client.get_me()
        print(f"Logged in as: {me.first_name} (@{me.username}) ID: {me.id}")
    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
