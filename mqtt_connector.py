import json

import paho.mqtt.client as mqtt
from qgis.PyQt.QtCore import QThread, pyqtSignal


class EDISConnector(QThread):
    new_message = pyqtSignal(dict)
    status_msg = pyqtSignal(str)
    error_msg = pyqtSignal(str)

    mqtt_client: mqtt.Client

    def __init__(self, parent=None, hostname=None, port=None, username=None, password=None):
        super().__init__(parent)

        self.hostname = hostname
        self.port = port
        self.username = username
        self.password = password

        mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        mqttc.on_connect = self.on_connect
        mqttc.on_message = self.on_message
        mqttc.on_disconnect = self.on_disconnect

        mqttc.tls_set()

        mqttc.username_pw_set(self.username, self.password)
        mqttc.connect(self.hostname, self.port, 60)

        self.mqtt_client = mqttc

    def run(self):
        try:
            self.status_msg.emit(f"Opening Connection to {self.hostname}")
            self.mqtt_client.loop_forever()
        except Exception as e:
            self.error_msg.emit(f"Serial error: {e}")

    def stop(self):
        self.mqtt_client.disconnect()
        self.mqtt_client.loop_stop()

    def on_connect(self, client, userdata, flags, reason_code, properties):
        print(f"Connected with result code {reason_code}")
        #msg = f"Connected with result code {reason_code}"
        self.status_msg.emit(str(reason_code))

    def subscribe(self, topic: str):
        print(f"subscribed to {topic}")
        self.mqtt_client.subscribe(topic)

    def unsubscribe(self, topic: str):
        print(f"UNsubscribed to {topic}")
        self.mqtt_client.unsubscribe(topic)

    def on_message(self, client, userdata, msg):
        self.new_message.emit(json.loads(msg.payload))
        ##DEBUG ONLY
        message = msg.topic + " " + str(msg.payload)
        print(msg.topic)

    def on_disconnect(self, client, _, flags, reason_code, properties):
        print(f"Disconnected with result code {reason_code}")
