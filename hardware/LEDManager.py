import uasyncio as asyncio
from machine import Pin

class LED_Manager:
    def __init__(self, led_pin=13):
        self.led = Pin(led_pin, Pin.OUT)
        self._dht22_alarm = False
        self._task = asyncio.create_task(self._led_loop())

    def set_dht22_alarm(self, status: bool):
        self._dht22_alarm = bool(status)

    def is_alarm_active(self):
        return self._dht22_alarm

    async def _led_loop(self):
        while True:
            if self.is_alarm_active():
                self.led.off()
                await asyncio.sleep(0.5)
                self.led.on()
                await asyncio.sleep(0.5)
            else:
                self.led.on()
                await asyncio.sleep(0.5)
