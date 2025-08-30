import uasyncio as asyncio
import network
from machine import Pin, SPI, reset
import ujson
from ConfigManager import Config_Manager


class Ethernet_Manager:
    def __init__(self, config_file="ethernet_config.json", default_file="ethernet_default_config.json"):
        self.config = Config_Manager(config_file, default_config_file=default_file)
        self.spi = SPI(2, sck=Pin(18), mosi=Pin(23), miso=Pin(19))
        self.cs = Pin(self.config.get_config('cs_pin', 5), Pin.OUT)
        self.intp = Pin(self.config.get_config('int_pin', 27), Pin.IN)
        self.led = Pin(self.config.get_config('led_pin', 15), Pin.OUT)
        self.led.off()
        self.led_task = None
        self.rst_and_reset_pin = Pin(14, Pin.IN, Pin.PULL_UP)
        self.ip = self.config.get_config('eth_ip', '192.168.1.191')
        self.subnet = self.config.get_config('eth_subnet', '255.255.255.0')
        self.gateway = self.config.get_config('eth_gateway', '192.168.1.1')
        self.dns = self.config.get_config('eth_dns', '8.8.8.8')
        self.lan = None
        self.is_connecting = False
        self.mqtt_connected = False

    def init_lan(self):
        self.lan = network.LAN(
            spi=self.spi,
            cs=self.cs,
            int=self.intp,
            phy_type=network.PHY_W5500,
            phy_addr=0
        )

    async def hardware_reset_lan(self):
        print("[INFO]: Resetting W5500 (via GPIO14)")
        rst_out = Pin(14, Pin.OUT)
        rst_out.off()
        await asyncio.sleep_ms(200)
        rst_out.on()
        await asyncio.sleep(2.0)
        rst_out.init(Pin.IN, Pin.PULL_UP)

    async def led_blink_task(self, period_ms):
        while True:
            self.led.value(not self.led.value())
            await asyncio.sleep_ms(period_ms)

    def update_led(self, mode: str):
        if self.led_task:
            self.led_task.cancel()
            self.led_task = None
        if mode == 'on':
            self.led.off()
        elif mode == 'off':
            self.led.on()
        elif mode == 'connecting':
            self.led_task = asyncio.create_task(self.led_blink_task(2500))

    async def connect(self, max_retries=3, delay=5):
        if self.lan and self.lan.isconnected():
            print("[INFO]: Already connected")
            return True
        self.is_connecting = True
        await self.hardware_reset_lan()
        self.init_lan()
        self.lan.active(True)
        await asyncio.sleep(1.0)
        try:
            self.lan.ifconfig((self.ip, self.subnet, self.gateway, self.dns))
        except Exception as e:
            print("[WARNING]: ifconfig failed:", e)
        print("[INFO]: Trying to connect Ethernet")
        for attempt in range(max_retries):
            await asyncio.sleep(0.5)
            if self.lan.isconnected():
                print("[SUCCESS]: Ethernet connected", self.lan.ifconfig())
                self.update_led('on')
                self.is_connecting = False
                return True
            self.update_led('connecting')
            print(f"[INFO]: Attempt {attempt + 1}/{max_retries} failed, retrying")
            await asyncio.sleep(delay)
        print("[ERROR]: Ethernet connection failed after retries → wait 1 minute then reboot")
        self.update_led('off')
        self.is_connecting = False
        await asyncio.sleep(60)
        reset()
        return False

    def isconnected(self):
        return self.lan and self.lan.isconnected()

    def is_fully_connected(self):
        return self.isconnected() and self.mqtt_connected

    def get_mac(self):
        return ':'.join('%02X' % b for b in self.lan.config('mac'))

    async def led_status_manager(self):
        while True:
            if self.is_fully_connected():
                self.update_led('on')
            else:
                self.update_led('connecting')
            await asyncio.sleep(1)

    async def retry_connect_loop(self, retry_delay=10):
        while True:
            if not self.isconnected() and not self.is_connecting:
                print("[INFO]: Ethernet disconnected, retrying connect")
                await self.connect()
            await asyncio.sleep(retry_delay)

    async def check_reset_config(self, mqtt_manager, dht22_manager):
        await asyncio.sleep(3)
        print("[INFO]: Ready to detect reset config (GPIO14)")
        while True:
            if self.rst_and_reset_pin.value() == 0:
                print("[INFO]: Hold detected, waiting to confirm reset")
                hold_time = 0
                while self.rst_and_reset_pin.value() == 0:
                    await asyncio.sleep_ms(500)
                    hold_time += 0.5
                    if hold_time >= 10:
                        print("[INFO]: Overwriting config with default")
                        try:
                            self.config.reset_config()
                            mqtt_manager.reset_mqtt_config()
                            dht22_manager.reset_dht22_config()
                            print("[SUCCESS]: Default configs restored. Rebooting")
                        except Exception as e:
                            print("[ERROR]: Reset failed:", e)
                        await asyncio.sleep(1)
                        reset()
            await asyncio.sleep(1)

    def update_mqtt_status(self, is_connected):
        self.mqtt_connected = is_connected
    async def wait_until_connected(self, timeout=None):
        t = 0
        while True:
            if self.isconnected():
                return True
            await asyncio.sleep(1)
            if timeout is not None:
                t += 1
                if t >= timeout:
                    return False

    async def start_services_ethernet(self, mqtt_manager, dht22_manager):
        await self.connect()
        asyncio.create_task(self.led_status_manager())
        asyncio.create_task(self.retry_connect_loop())
        asyncio.create_task(self.check_reset_config(mqtt_manager, dht22_manager))
