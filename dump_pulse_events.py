import asyncio
import signal
import contextlib
import pulsectl_asyncio
import pulsectl

print('Event types:', pulsectl.PulseEventTypeEnum)
print('Event facilities:', pulsectl.PulseEventFacilityEnum)
print('Event masks:', pulsectl.PulseEventMaskEnum)

async def listen():
    async with pulsectl_asyncio.PulseAsync('dump-events') as pulse:
        print(await pulse.server_info())
        async for ev in pulse.subscribe_events('all'):
            print(ev)

async def main():
    task_listen = asyncio.create_task(listen())
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        asyncio.get_event_loop().add_signal_handler(sig, task_listen.cancel)

    with contextlib.suppress(asyncio.CancelledError):
        await task_listen

if __name__ == '__main__':
    asyncio.run(main())
