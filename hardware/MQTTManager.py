import uasyncio as asyncio
from mqtt_as import MQTTClient, config as mqtt_config
import ujson
import machine
import gc
import re
from ConfigManager import Config_Manager


class MQTT_Manager:
    def __init__(self, mac, ethernet, dht22_manager):
        self.config_manager = Config_Manager(
            "mqtt_config.json",
            default_config_file="mqtt_default_config.json"
        )

        self.ethernet = ethernet
        self.dht22_manager = dht22_manager
        self.is_mqtt_ready = False
        self.mac = self.ethernet.get_mac()
        client_id = self.mac
        lwt_topic = self.config_manager.get_config("lwt_topic", "esp32/status")
        lwt_payload = ujson.dumps({"status": "offline", "mac": self.mac})

        mqtt_config["will"] = (lwt_topic, lwt_payload, True, 1)
        mqtt_config["server"] = self.config_manager.get_config("broker")
        mqtt_config["port"] = self.config_manager.get_config("port", 1883)
        mqtt_config["user"] = self.config_manager.get_config("user", "")
        mqtt_config["password"] = self.config_manager.get_config("password", "")
        mqtt_config["keepalive"] = self.config_manager.get_config("keepalive", 120)
        mqtt_config["client_id"] = client_id
        mqtt_config["queue_len"] = 1

        MQTTClient.DEBUG = True
        self.client = MQTTClient(mqtt_config)

        self._status_topic = self.config_manager.get_config(
            "status_topic",
            "esp32/{}/status".format(client_id)
        )

        self.subscribe_topics = self.config_manager.get_config("subscribe_topics", [])
        if "esp32/control/+/reboot" not in self.subscribe_topics:
            self.subscribe_topics.append("esp32/control/+/reboot")

        gc.collect()

    # ---------- Utils ----------
    def is_connected(self):
        return self.client.isconnected()

    async def safe_publish(self, topic, data, retain=False, qos=0):
        if not self.is_mqtt_ready:
            print("[ERROR]: Publish failed (MQTT not ready)")
            return False
        try:
            payload_str = ujson.dumps(data)
            await self.client.publish(topic, payload_str, retain=retain, qos=qos)
            print("[DEBUG]: Published to", topic)
            return True
        except Exception as e:
            print("[ERROR]: Publish failed:", e)
            return False

    # ---------- Periodic status ----------
    async def publish_status_task(self):
        while True:
            await asyncio.sleep(19)
            if self.is_mqtt_ready:
                payload = {"status": "online", "mac": self.mac}
                await self.safe_publish(self._status_topic, payload, retain=False)

    # ---------- Incoming messages ----------
    async def message_handler(self):
        # Requires mqtt_config["queue_len"] >= 1
        async for topic, msg, retained in self.client.queue:
            try:
                t = topic.decode("utf-8")
                p = msg.decode("utf-8")
                print("[DEBUG]: MQTT message on '{}': {}".format(t, p))

                try:
                    data = ujson.loads(p) if p else {}
                except Exception:
                    data = {}

                command = data.get("command")
                
                # ===== GET CONFIG =====
                if t == "esp32/commands" and command == "get_config":
                    print("[INFO]: get_config received, collecting...")
                    ethernet_config = self.ethernet.config.load_config()
                    mqtt_config_data = self.config_manager.load_config()
                    dht22_config = self.dht22_manager.config_manager.load_config()
                    request_id = data.get("requestId")
                    response_payload = {
                        "mac_address": self.mac,
                        "ethernet": ethernet_config,
                        "mqtt": mqtt_config_data,
                        "alerts": {
                            "temp_crit_low":  dht22_config.get("CON_TEMP_MIN"),
                            "temp_warn_low":  dht22_config.get("CON_TEMP_WARN_LOW"),
                            "temp_warn_high": dht22_config.get("CON_TEMP_WARN_HIGH"),
                            "temp_crit_high": dht22_config.get("CON_TEMP_MAX"),
                            "hum_crit_low":   dht22_config.get("CON_HUM_MIN"),
                            "hum_warn_low":   dht22_config.get("CON_HUM_WARN_LOW"),
                            "hum_warn_high":  dht22_config.get("CON_HUM_WARN_HIGH"),
                            "hum_crit_high":  dht22_config.get("CON_HUM_MAX"),
                        },
                    }
                    if request_id:
                        response_payload["requestId"] = request_id
                    response_topic = "esp32/response/{}/config".format(self.mac.replace(":", ""))
                    await self.safe_publish(response_topic, response_payload)

                # ===== SET CONFIG =====
                elif t == "esp32/set_config" and command == "set_config":
                    settings = data.get("settings", {})
                    print("[INFO]: set_config received, applying...")

                    if "ethernet" in settings:
                        eth_conf = settings["ethernet"]
                        formatted_eth = {
                            "eth_ip":      eth_conf.get("ip"),
                            "eth_subnet":  eth_conf.get("subnet"),
                            "eth_gateway": eth_conf.get("gateway"),
                            "eth_dns":     eth_conf.get("dns"),
                        }
                        self.ethernet.config.save_config(formatted_eth)
                        print("[SUCCESS]: Ethernet config updated")

                    if "mqtt" in settings:
                        mconf = settings["mqtt"]
                        formatted_mqtt = {
                            "broker":   mconf.get("broker"),
                            "port":     int(mconf.get("port") or 1883),
                            "user":     mconf.get("user"),
                            "password": mconf.get("pass"),
                        }
                        self.config_manager.save_config(formatted_mqtt)
                        print("[SUCCESS]: MQTT config updated")

                    if "alerts" in settings:
                        alerts_conf = settings.get("alerts", {})
                        temp_alerts = alerts_conf.get("temp", {})
                        hum_alerts  = alerts_conf.get("hum",  {})

                        formatted_alerts = {
                            "CON_TEMP_MIN":       temp_alerts.get("critLow"),
                            "CON_TEMP_WARN_LOW":  temp_alerts.get("warnLow"),
                            "CON_TEMP_WARN_HIGH": temp_alerts.get("warnHigh"),
                            "CON_TEMP_MAX":       temp_alerts.get("critHigh"),
                            "CON_HUM_MIN":        hum_alerts.get("critLow"),
                            "CON_HUM_WARN_LOW":   hum_alerts.get("warnLow"),
                            "CON_HUM_WARN_HIGH":  hum_alerts.get("warnHigh"),
                            "CON_HUM_MAX":        hum_alerts.get("critHigh"),
                        }
                        self.dht22_manager.config_manager.save_config(formatted_alerts)
                        print("[SUCCESS]: Alerts config updated")

                    print("[INFO]: Rebooting in 3 seconds to apply changes...")
                    await asyncio.sleep(3)
                    machine.reset()

                # ===== REBOOT (ใช้ MAC ตรวจสอบ) =====
                elif re.match(r"^esp32/control/[^/]+/reboot$", t):
                    target_mac = (data.get("mac") or "").upper()
                    action_id  = data.get("actionId")
                    room_id    = (data.get("room_id") or "").lower()
                    my_mac = self.mac.upper()
                    m = re.match(r"^esp32/control/([^/]+)/reboot$", t)
                    path_key = m.group(1) if m else ""

                    should_reboot = False
                    if target_mac and target_mac == my_mac:
                        should_reboot = True
                    elif path_key and path_key.upper() == my_mac:
                        should_reboot = True
                    else:
                        should_reboot = False

                    if should_reboot:
                        print("[INFO]: Reboot command matched this device. Ack then reboot.")
                        
                        ack_topic = ""
                        if room_id:
                            ack_topic = "esp32/ack/{}/reboot".format(room_id)
                        else:
                            ack_topic = "esp32/ack/{}/reboot".format(self.mac)
                        
                        ack_payload = {"ok": True, "message": "rebooting", "actionId": action_id, "mac": self.mac}
                        
                        await self.safe_publish(ack_topic, ack_payload)

                        await asyncio.sleep(0.25)
                        machine.reset()
                    else:
                        print("[INFO]: Reboot command ignored (not my MAC)")

            except Exception as e:
                print("[ERROR]: Processing message failed:", e)

    # ---------- Connection lifecycle ----------
    async def connection_handler(self):
        while True:
            await self.client.up.wait()
            self.client.up.clear()
            self.is_mqtt_ready = True
            print("[INFO]: MQTT connected")

            if hasattr(self, "ethernet") and self.ethernet:
                self.ethernet.update_mqtt_status(True)

            payload = {"status": "online", "mac": self.mac}
            await self.safe_publish(self._status_topic, payload, retain=True)

            for topic in self.subscribe_topics:
                try:
                    await self.client.subscribe(topic, 1)
                    print("[DEBUG]: Subscribed to:", topic)
                except Exception as e:
                    print("[ERROR]: Subscribe failed for", topic, "->", e)

            await self.client.down.wait()
            self.client.down.clear()
            self.is_mqtt_ready = False
            print("[WARNING]: MQTT disconnected")

            if hasattr(self, "ethernet") and self.ethernet:
                self.ethernet.update_mqtt_status(False)

    async def start_service_mqtt(self):
        while not self.ethernet.isconnected():
            print("[ERROR]: Cannot start MQTT — Ethernet not connected")
            await asyncio.sleep(5)

        while not self.dht22_manager.time_manager.ntp_sync:
            print("[ERROR]: Cannot start MQTT — Time not synced")
            await asyncio.sleep(5)

        asyncio.create_task(self.connection_handler())
        asyncio.create_task(self.publish_status_task())
        asyncio.create_task(self.message_handler())

        while True:
            try:
                await self.client.connect()
                break
            except OSError as e:
                print("[WARNING]: Connect failed, retry in 10s:", e)
                await asyncio.sleep(10)

    # ---------- Maintenance ----------
    def reset_mqtt_config(self):
        try:
            self.config_manager.reset_config(
                keys=["broker", "port", "user", "password"]
            )
            print("[SUCCESS]: MQTT config reset to defaults")
        except Exception as e:
            print("[ERROR]: Reset MQTT config failed:", e)