import gc
import uasyncio as asyncio
from machine import Pin, I2C
from i2c_lcd import I2cLcd

DEG = chr(223)

class Display_Manager:
    def __init__(self, dht22_manager, ethernet_manager, mqtt_manager, time_manager,
                 i2c_scl_pin=22, i2c_sda_pin=21,
                 i2c_id=0, lcd_addr=0x27,
                 lcd_cols=16, lcd_rows=2,
                 refresh_interval=5):

        self.dht22 = dht22_manager
        self.ethernet = ethernet_manager
        self.mqtt = mqtt_manager
        self.time = time_manager

        self.pages = ['dht', 'network_status', 'system_status']
        self.page = 0
        self.interval = max(refresh_interval, 1)
        self.cols = lcd_cols
        self.lcd = None

        try:
            i2c = I2C(i2c_id, scl=Pin(i2c_scl_pin), sda=Pin(i2c_sda_pin), freq=400_000)
            self.lcd = I2cLcd(i2c, lcd_addr, lcd_rows, lcd_cols)
            self.lcd.backlight_on()
            self.lcd.clear()
            self.last_line = [""] * lcd_rows
            self.cache = {'Temp': None, 'Hum': None}
            print("[DEBUG]: LCD initialized")
        except Exception as e:
            print(f"[WARNING]: LCD not initialized ({e})")
            self.lcd = None

    async def _update_screen(self):
        if self.lcd is None:
            return

        page_name = self.pages[self.page]
        if page_name == 'dht':
            self._show_dht()
        elif page_name == 'network_status':
            self._show_network_status()
        elif page_name == 'system_status':
            self._show_system_status()

        self.page = (self.page + 1) % len(self.pages)

    def _use_cached(self, key, new_val):
        if new_val is None:
            return self.cache.get(key)
        self.cache[key] = new_val
        return new_val

    def _put_line(self, row, text, center=False):
        if self.lcd is None:
            return
        
        # แปลง text เป็น string อย่างชัดเจนเสมอ
        text = str(text)

        # --- FIX: เขียนฟังก์ชัน ljust และ center ขึ้นมาเอง ---
        if center:
            pad = self.cols - len(text)
            if pad < 0:
                pad = 0
            left = pad // 2
            # สร้าง string ที่จัดกลางด้วยตัวเอง
            final_text = " " * left + text + " " * (pad - left)
        else:
            # สร้าง string ที่จัดชิดซ้าย (ljust) ด้วยตัวเอง
            pad = self.cols - len(text)
            if pad < 0:
                pad = 0
            final_text = text + " " * pad
        
        # ตรวจสอบว่าข้อความยาวเกินไปหรือไม่ และตัดออกถ้าจำเป็น
        if len(final_text) > self.cols:
            final_text = final_text[:self.cols]
        # ----------------------------------------------------

        if final_text != self.last_line[row]:
            self.last_line[row] = final_text
            self.lcd.move_to(0, row)
            self.lcd.putstr(final_text)

    def _show_dht(self):
        over = getattr(self.dht22, "last_overall", {})
        t = over.get("Temperature")
        h = over.get("Humidity")
        t = self._use_cached('Temp', t)
        h = self._use_cached('Hum', h)

        t_str = "-" if t is None else "{:4.1f}{}".format(t, DEG)
        h_str = "-" if h is None else "{:4.1f}%".format(h)

        self._put_line(0, f"Temp :{t_str}", center=True)
        self._put_line(1, f"Humi :{h_str}", center=True)

    def _show_network_status(self):
        eth_status = "Online" if self.ethernet.isconnected() else "Offline"
        mqtt_status = "Connected" if self.mqtt.is_mqtt_ready else "Offline"

        self._put_line(0, f"ETH :{eth_status}")
        self._put_line(1, f"MQTT:{mqtt_status}")

    def _show_system_status(self):
        time_status = "Synced" if self.time.ntp_sync else "No Sync"
        ip_addr = self.ethernet.lan.ifconfig()[0] if self.ethernet.isconnected() else "No IP"

        self._put_line(0, f"Time:{time_status}")
        self._put_line(1, f"{ip_addr}")


    async def start_service_display(self):
        while True:
            if self.lcd is None:
                await asyncio.sleep(5)
                continue
            try:
                await self._update_screen()
            except Exception as e:
                print(f"[ERROR]: DisplayManager: {e}")

            await asyncio.sleep(self.interval)
            gc.collect()