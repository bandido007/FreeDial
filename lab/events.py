import asyncio
import json

import websockets

ARI_URL = "ws://127.0.0.1:8091/ari/events?app=algodial&api_key=algodial:ChangeMeARI"


async def main():
    async with websockets.connect(ARI_URL) as ws:
        print("Connected to ARI. Waiting for events...")
        async for message in ws:
            event = json.loads(message)
            channel = event.get("channel", {})
            print(f"\n=== {event['type']} ===  channel={channel.get('name')}  state={channel.get('state')}")
            print(json.dumps(event, indent=2))


asyncio.run(main())
