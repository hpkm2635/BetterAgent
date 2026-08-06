import asyncio
import nats

async def main():
    try:
        nc = await nats.connect("nats://localhost:4222", connect_timeout=1)
        print("NATS Status: ONLINE 🟢")
        await nc.close()
    except Exception as e:
        print(f"NATS Status: OFFLINE 🔴 ({e})")

if __name__ == "__main__":
    asyncio.run(main())
