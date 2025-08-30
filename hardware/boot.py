import sys, time, gc, machine, esp

try:
    esp.osdebug(None)
except:
    pass

try:
    gc.threshold(gc.mem_free() // 4 + gc.mem_alloc())
except:
    pass

from machine import Pin, WDT

SAFE_PIN = 14
WDT_TIMEOUT_MS = 120000

def in_safe_mode() -> bool:
    try:
        p = Pin(SAFE_PIN, Pin.IN, Pin.PULL_UP)
        return p.value() == 0
    except:
        return False

def print_reset_cause():
    cause = machine.reset_cause()
    cause_map = {
        machine.PWRON_RESET:  "POWER-ON",
        machine.HARD_RESET:   "HARD",
        machine.WDT_RESET:    "WDT",
        machine.DEEPSLEEP_RESET: "DEEPSLEEP",
        machine.SOFT_RESET:   "SOFT",
        getattr(machine, 'BROWN_OUT_RESET', 100): "BROWNOUT",
    }
    print("[BOOT] Reset cause:", cause_map.get(cause, str(cause)))

print_reset_cause()

if in_safe_mode():
    print("[BOOT] SAFE-MODE: ข้ามการรันแอป รอ REPL/แฟลชโค้ด")
else:
    try:
        import uasyncio as asyncio
        import main as app

        wdt = WDT(timeout=WDT_TIMEOUT_MS)

        async def _pet_wdt():
            while True:
                try:
                    wdt.feed()
                except:
                    pass
                await asyncio.sleep_ms(2000)

        async def _runner():
            asyncio.create_task(_pet_wdt())
            await app.main()

        asyncio.run(_runner())

    except Exception as e:
        try:
            sys.print_exception(e)
        except:
            print("[BOOT] Exception without traceback")
        time.sleep(2)
        machine.reset()

    finally:
        try:
            asyncio.new_event_loop()
        except:
            pass
        gc.collect()
