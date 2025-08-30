import uasyncio as asyncio
import gc
from EthernetManager import Ethernet_Manager
from TimeManager import Time_Manager
from MQTTManager import MQTT_Manager
from DHT22Manager import DHT22_Manager
from LEDManager import LED_Manager
from DisplayManager import Display_Manager
from machine import reset

async def main():
    ethernet = Ethernet_Manager()
    led_mgr = LED_Manager()
    asyncio.create_task(ethernet.connect())
    await asyncio.sleep(2.5)
    mac = ethernet.get_mac()
    print("[INFO]: MAC: ", mac)
    time_mgr = Time_Manager(ethernet,timezone_offset=7)
    await time_mgr.sync_http_time()
    asyncio.create_task(time_mgr.start_service_ntp_sync())
    dht_mgr = DHT22_Manager(
        time_manager=time_mgr,
        ethernet=ethernet,
        mqtt_manager=None,
        led_manager=led_mgr
    )
    mqtt_mgr = MQTT_Manager(mac, ethernet, dht_mgr)
    display_mgr = Display_Manager(dht22_manager=dht_mgr,
        ethernet_manager=ethernet,
        mqtt_manager=mqtt_mgr,
        time_manager=time_mgr
    )
    dht_mgr.mqtt_manager = mqtt_mgr
    asyncio.create_task(ethernet.check_reset_config(mqtt_manager=mqtt_mgr, dht22_manager=dht_mgr))
    asyncio.create_task(ethernet.led_status_manager())
    asyncio.create_task(ethernet.retry_connect_loop())
    asyncio.create_task(mqtt_mgr.start_service_mqtt())
    asyncio.create_task(display_mgr.start_service_display())
    await dht_mgr.start_service_dht22()


