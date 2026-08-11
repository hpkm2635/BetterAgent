import asyncio
import os
import sys

import nats
from dotenv import load_dotenv

load_dotenv()


async def main():
    nats_user = os.getenv("NATS_USER")
    nats_password = os.getenv("NATS_PASSWORD")
    if not nats_user or not nats_password:
        print("NATS Status: UNKNOWN ⚠️  (NATS_USER / NATS_PASSWORD not set in .env)")
        sys.exit(1)

    try:
        # 127.0.0.1, not "localhost" -- see docs/SECURITY.md §2.8.
        nc = await nats.connect(
            "nats://127.0.0.1:4222",
            user=nats_user,
            password=nats_password,
            connect_timeout=2,
        )
        print("NATS Status: ONLINE 🟢")
        await nc.close()
    except Exception as e:
        print(f"NATS Status: OFFLINE 🔴 ({e})")


if __name__ == "__main__":
    asyncio.run(main())
