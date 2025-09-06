import time
import ujson
import uos
import ubinascii
import machine
import uasyncio as asyncio
import dht
import gc
from machine import Pin
from TimeManager import Time_Manager
from ConfigManager import Config_Manager


class DHT22_Manager:
    def __init__(self, time_manager, ethernet, mqtt_manager, led_manager,
                 config_file='dht22_config.json', default_file='dht22_default_config.json'):
        self.config_manager = Config_Manager(config_file, default_config_file=default_file)
        config = self.config_manager.load_config()

        self.dht22_pins = config.get('DHT22_PINS', [25, 26, 32, 33])
        self.sensor_locations = {
            int(k): v for k, v in config.get('SENSOR_LOCATIONS', {str(p): f"Sensor{p}" for p in self.dht22_pins}).items()
        }
        self.led = Pin(config.get('LED_PIN', 13), Pin.OUT)
        self.sample_count = config.get('SAMPLE_COUNT', 7)
        self.read_delay = config.get('READ_DELAY', 2)
        self.min_temp_condition = config.get('CON_TEMP_MIN', 18) + config.get('Calibrate_temp', 0.5)
        self.max_temp_condition = config.get('CON_TEMP_MAX', 27) - config.get('Calibrate_temp', 0.5)
        self.min_hum_condition = config.get('CON_HUM_MIN', 40) + config.get('Calibrate_hum', 2)
        self.max_hum_condition = config.get('CON_HUM_MAX', 65) - config.get('Calibrate_hum', 2)
        self.min_temp_spec = config.get('TEMP_MIN', -40)
        self.max_temp_spec = config.get('TEMP_MAX', 100)
        self.min_hum_spec = config.get('HUM_MIN', 0)
        self.max_hum_spec = config.get('HUM_MAX', 100)
        self.per_temp_alarm = config.get('PER_TEMP_ALARM', 5)
        self.per_hum_alarm = config.get('PER_HUM_ALARM', 5)
        self.time_manager = time_manager
        self.mqtt_manager = mqtt_manager
        self.ethernet = ethernet
        self.backup_csv = 'dht22_backup.csv'
        self.mac = ethernet.get_mac()
        self.dht22_topic = f"esp32/{self.mac}/dht"
        self.dht22_interval = config.get('DHT22_INTERVAL', 2)
        self.led_manager = led_manager
        self.last_overall = {"Temperature": None, "Humidity": None}
        self.update_event = asyncio.Event()
        gc.collect()

    def check_config(self):
        if not (isinstance(self.dht22_pins, (list, tuple)) and self.dht22_pins):
            print("[ERROR]: No pins defined")
            return False
        if any((not isinstance(p, int) or p < 0) for p in self.dht22_pins):
            print("[ERROR]: Pins must be positive int")
            return False
        if not (isinstance(self.sample_count, int) and self.sample_count > 0):
            print("[ERROR]: Sample count invalid")
            return False
        if self.read_delay < 2:
            print("[ERROR]: Read delay < 2s")
            return False
        if self.min_temp_spec >= self.max_temp_spec or self.min_hum_spec >= self.max_hum_spec:
            print("[ERROR]: Specification min >= max")
            return False
        if self.min_temp_condition >= self.max_temp_condition or self.min_hum_condition >= self.max_hum_condition:
            print("[ERROR]: Condition min >= max")
            return False
        return True

    async def setup_pins(self):
        sensor_pin = {}
        for pin_num in self.dht22_pins:
            try:
                gpio = Pin(pin_num, mode=Pin.OPEN_DRAIN, pull=Pin.PULL_UP)
                sensor = dht.DHT22(gpio)
                await asyncio.sleep(0.7)
                sensor.measure()
                sensor_pin[pin_num] = sensor
                print(f"[SUCCESS]: Pin {pin_num} ready")
            except Exception as e:
                print(f"[ERROR]: Pin {pin_num} init failed: {e}")
        gc.collect()
        return sensor_pin if sensor_pin else None

    async def read_sensor(self, sensor):
        try:
            sensor.measure()
            await asyncio.sleep(0.25)
            temp = sensor.temperature()
            hum = sensor.humidity()
            if not (self.min_temp_spec <= temp <= self.max_temp_spec):
                temp = None
            if not (self.min_hum_spec <= hum <= self.max_hum_spec):
                hum = None
            return temp, hum
        except Exception:
            return None, None

    async def collect_data(self, sensor_pin):
        collected_data = {
            pin: dict(
                temp_sum=0, hum_sum=0,
                samples_temp=0, samples_hum=0,
                temp_max=None, temp_min=None,
                hum_max=None, hum_min=None
            )
            for pin in sensor_pin
        }
        for _ in range(self.sample_count):
            for pin, sensor_obj in sensor_pin.items():
                temp, hum = await self.read_sensor(sensor_obj)
                collect = collected_data[pin]
                if temp is not None:
                    collect['temp_sum'] += temp
                    collect['samples_temp'] += 1
                    collect['temp_max'] = temp if collect['temp_max'] is None else max(collect['temp_max'], temp)
                    collect['temp_min'] = temp if collect['temp_min'] is None else min(collect['temp_min'], temp)
                if hum is not None:
                    collect['hum_sum'] += hum
                    collect['samples_hum'] += 1
                    collect['hum_max'] = hum if collect['hum_max'] is None else max(collect['hum_max'], hum)
                    collect['hum_min'] = hum if collect['hum_min'] is None else min(collect['hum_min'], hum)
            await asyncio.sleep(self.read_delay)
        gc.collect()
        return collected_data

    def calculate_average(self, collected_data):
        per_sensor_data = {}
        total_temp = total_hum = count_temp = count_hum = 0
        for pin, data in collected_data.items():
            avg_temp = round(data['temp_sum'] / data['samples_temp'], 1) if data['samples_temp'] else None
            avg_hum = round(data['hum_sum'] / data['samples_hum'], 1) if data['samples_hum'] else None
            per_sensor_data[pin] = dict(
                temp=avg_temp, hum=avg_hum,
                temp_max=data['temp_max'], temp_min=data['temp_min'],
                hum_max=data['hum_max'], hum_min=data['hum_min']
            )
            if avg_temp is not None:
                total_temp += avg_temp * data['samples_temp']
                count_temp += data['samples_temp']
            if avg_hum is not None:
                total_hum += avg_hum * data['samples_hum']
                count_hum += data['samples_hum']
        overall = dict(
            Temperature=round(total_temp / count_temp, 1) if count_temp else None,
            Humidity=round(total_hum / count_hum, 1) if count_hum else None
        )
        gc.collect()
        return per_sensor_data, overall

    def calculate_overall_max_min(self, per_sensor_data):
        temp_val = [data['temp'] for data in per_sensor_data.values() if data['temp'] is not None]
        hum_val = [data['hum'] for data in per_sensor_data.values() if data['hum'] is not None]
        result = dict(
            Temperature=dict(max=max(temp_val) if temp_val else None,
                             min=min(temp_val) if temp_val else None),
            Humidity=dict(max=max(hum_val) if hum_val else None,
                          min=min(hum_val) if hum_val else None)
        )
        gc.collect()
        return result

    async def send_or_backup(self, mac, topic, per_sensor, overall):
        ready = (self.time_manager.ntp_sync and self.ethernet.isconnected() and self.mqtt_manager.is_mqtt_ready)
        print("[DEBUG]: NTP sync:", self.time_manager.ntp_sync)
        print("[DEBUG]: Ethernet connected:", self.ethernet.isconnected())
        print("[DEBUG]: MQTT ready:", self.mqtt_manager.is_mqtt_ready)

        data_row = []
        for pin_num, data in per_sensor.items():
            data_row.append(dict(
                mac=mac,
                pin=pin_num,
                avg_temp=data['temp'],
                avg_hum=data['hum'],
                max_temp=data['temp_max'],
                min_temp=data['temp_min'],
                max_hum=data['hum_max'],
                min_hum=data['hum_min']
            ))

        ovr = self.calculate_overall_max_min(per_sensor)
        data_row.append(dict(
            mac=mac,
            pin='OVERALL',
            avg_temp=overall['Temperature'],
            avg_hum=overall['Humidity'],
            max_temp=ovr['Temperature']['max'],
            min_temp=ovr['Temperature']['min'],
            max_hum=ovr['Humidity']['max'],
            min_hum=ovr['Humidity']['min']
        ))

        if ready:
            timestamp = self.time_manager.now()
            await self.resend_backup(topic)
            failed = []
            for data in data_row:
                data['timestamp'] = timestamp
                for key in ('avg_temp', 'avg_hum', 'max_temp', 'min_temp', 'max_hum', 'min_hum'):
                    if data.get(key) is not None:
                        data[key] = float(data[key])
                if not await self.mqtt_manager.safe_publish(topic, data):
                    failed.append((time.ticks_ms(), data.copy()))
            if failed:
                self.backup_to_csv(failed)
            gc.collect()
            return

        timestamp_anchor = time.ticks_ms()
        records = [(timestamp_anchor, data.copy()) for data in data_row]
        self.backup_to_csv(records)
        gc.collect()


    def backup_to_csv(self, records):
        first = self.backup_csv not in uos.listdir()
        with open(self.backup_csv, 'a') as f:
            if first:
                f.write("ticks_ms,json\n")
            for ts, payload in records:
                f.write(f"{ts},{ujson.dumps(payload)}\n")
        print(f"[SUCCESS]: Backup {len(records)} records")
        gc.collect()

    async def resend_backup(self, topic):
        if self.backup_csv not in uos.listdir() or not self.time_manager.ntp_sync or self.time_manager.sync_ticks is None:
            return
        print("[INFO]: Resend backup data")
        failures = []
        with open(self.backup_csv, 'r') as f:
            header = next(f)
            for line in f:
                ts_ms, json_str = line.rstrip().split(',', 1)
                delta = time.ticks_diff(int(ts_ms), self.time_manager.sync_ticks)
                payload = ujson.loads(json_str)
                payload['timestamp'] = self.time_manager.iso_add_ms(self.time_manager.sync_iso, delta)
                for key in ('avg_temp', 'avg_hum', 'max_temp', 'min_temp', 'max_hum', 'min_hum'):
                    if payload.get(key) is not None:
                        payload[key] = float(payload[key])
                try:
                    await self.mqtt_manager.safe_publish(topic, payload)
                except Exception:
                    failures.append(line)

        if failures:
            with open(self.backup_csv, 'w') as f:
                f.write(header)
                for line in failures:
                    f.write(line)
            print(f"[WARNING]: Backup retained {len(failures)} records")
        else:
            uos.remove(self.backup_csv)
            print(f"[SUCCESS]: Deleted {self.backup_csv}")
        gc.collect()

    def send_result(self, per_sensor, overall, result):
        is_alarm = False

        if overall['Temperature'] is not None and not (self.min_temp_condition <= overall['Temperature'] <= self.max_temp_condition):
            print(f"[WARNING]: Alarm Overall Temp {overall['Temperature']}°C")
            is_alarm = True
        if overall['Humidity'] is not None and not (self.min_hum_condition <= overall['Humidity'] <= self.max_hum_condition):
            print(f"[WARNING]: Alarm Overall Hum {overall['Humidity']}%")
            is_alarm = True
        self.led_manager.set_dht22_alarm(is_alarm)
        for pin, data in per_sensor.items():
            location = self.sensor_locations.get(pin, 'Unknown')
            print(
                f"[INFO]: Result Pin{pin}({location}) "
                f"(Temp {data['temp']}°C) (Hum {data['hum']}%) "
                f"(Min/Max Temp {data['temp_min']}°C/{data['temp_max']}°C) "
                f"(Min/Max Hum {data['hum_min']}%/{data['hum_max']}%)"
            )

        overall_str = f"(Temp: {overall['Temperature']}°C) (Hum: {overall['Humidity']}%)"
        result_str = (
            f"(Min/Max Temp: {result['Temperature']['min']}°C/{result['Temperature']['max']}°C) "
            f"(Min/Max Hum: {result['Humidity']['min']}%/{result['Humidity']['max']}%)"
        )
        print("[INFO]: Overall", overall_str, result_str)
        gc.collect()


    def reset_dht22_config(self):
        try:
            self.config_manager.reset_config(keys=["CON_TEMP_MIN", "CON_TEMP_MAX", "CON_HUM_MIN", "CON_HUM_MAX"])
            print("[SUCCESS]: DHT22 config reset to defaults")
        except Exception as e:
            print(f"[ERROR]: Failed to reset DHT22 config: {e}")

    async def start_service_dht22(self):
        self.led.on()
        if not self.check_config():
            print("[ERROR]: Invalid config")
            return None
        while True:
            try:
                pins = await self.setup_pins()
                if not pins:
                    print("[ERROR]: Setup pins failed")
                    await asyncio.sleep(self.dht22_interval * 5)
                    continue
                collect = await self.collect_data(pins)
                per_sensor, overall = self.calculate_average(collect)
                self.last_overall = overall
                self.update_event.set()
                result = self.calculate_overall_max_min(per_sensor)
                await self.send_or_backup(self.mac, self.dht22_topic, per_sensor, overall)
                self.send_result(per_sensor, overall, result)
                gc.collect()
            except Exception as e:
                print(f"[ERROR]: Start service DHT22 failed: {e}")
                gc.collect()
            await asyncio.sleep(self.dht22_interval)
            gc.collect()

