import time
import urequests
import ure
import uasyncio as asyncio
import gc
import machine


class Time_Manager:
    ISO_RE = ure.compile(r"^(\d{4})-(\d{2})-(\d{2})T"r"(\d{2}):(\d{2}):(\d{2})"r"(?:\.\d+)?Z?$")
    
    def __init__(
        self,
        ethernet,
        timezone_offset=7,
        http_time_url="http://192.168.42.9:1880/api/time",
    ):
        self.ethernet = ethernet
        self.timezone_offset = timezone_offset * 3600  # hours -> seconds
        self.http_time_url = http_time_url
        self.ntp_sync = False
        self.sync_iso = None
        self.sync_ticks = None
        self.boot_ticks = time.ticks_ms()
        
        gc.collect()
    
    def get_iso_timestamp(self):
        # Build ISO from localtime (epoch + tz offset)
        sec_local = time.time() + self.timezone_offset
        y, m, d, H, M, S, *_ = time.localtime(sec_local)
        s = f"{y:04d}-{m:02d}-{d:02d}T{H:02d}:{M:02d}:{S:02d}"
        gc.collect()
        return s

    def parse_iso(self, iso_str):
        try:
            s = iso_str.split('Z')[0].split('.')[0]
            date_part, time_part = s.split('T')
            y, mo, d = map(int, date_part.split('-'))
            hh, mm, ss = map(int, time_part.split(':'))
            return y, mo, d, hh, mm, ss
        except:
            return None

    def iso_add_ms(self, iso_anchor, delta_ms):
        t = self.parse_iso(iso_anchor)
        if not t:
            return self.now()
        y, m, d, H, M, S = t
        sec = time.mktime((y, m, d, H, M, S, 0, 0))
        sec += delta_ms // 1000
        y2, m2, d2, H2, M2, S2, _, _ = time.localtime(sec + self.timezone_offset)
        return f"{y2:04d}-{m2:02d}-{d2:02d}T{H2:02d}:{M2:02d}:{S2:02d}"
    async def sync_http_time(self):
        if not self.ethernet.isconnected():
            print("[TIME]: Ethernet not connected → skip HTTP time sync")
            return False
        res = None
        try:
            res = urequests.get(self.http_time_url)
            data = res.json()
            iso = data.get("iso")
            if not iso:
                return False
            s = iso.split('Z')[0].split('.')[0]
            date_part, time_part = s.split('T')
            y, mo, d = map(int, date_part.split('-'))
            hh, mm, ss = map(int, time_part.split(':'))
            utc_time = time.mktime((y, mo, d, hh, mm, ss, 0, 0))
            th_time = utc_time + self.timezone_offset
            y_th, mo_th, d_th, hh_th, mm_th, ss_th, _, _ = time.localtime(th_time)
            rtc = machine.RTC()
            rtc.datetime((y_th, mo_th, d_th, 0, hh_th, mm_th, ss_th, 0))
            self.sync_iso = iso
            self.sync_ticks = time.ticks_ms()
            self.ntp_sync = True
            print("[INFO]: HTTP time synced @", self.sync_iso)
            gc.collect()
            return True
        except Exception as e:
            print("[ERROR]: HTTP time sync failed:", e)
            self.ntp_sync = False
            return False
        finally:
            try:
                if res:
                    res.close()
            except Exception:
                pass

    async def sync_ntp_task(self):
        if not self.ethernet.isconnected():
            print("[DEBUG]: Ethernet not connected → skip time sync")
            return False

        ok = await self.sync_http_time()
        if ok:
            return True
        else:
            print("[DEBUG]: Time sync failed")
            self.ntp_sync = False
            return False

    async def start_service_ntp_sync(self, interval=10):
        await self.sync_ntp_task()
        while True:
            await self.sync_ntp_task()
            await asyncio.sleep(interval)
            gc.collect()

    def now(self):
        y, mo, d, _, hh, mm, ss, _ = machine.RTC().datetime()
        gc.collect()
        return f"{y:04d}-{mo:02d}-{d:02d}T{hh:02d}:{mm:02d}:{ss:02d}"

    def uptime(self):
        up = time.ticks_diff(time.ticks_ms(), self.boot_ticks) / 1000
        gc.collect()
        return up