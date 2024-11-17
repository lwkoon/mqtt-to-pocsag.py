#!/root/.venv/bin/python3

import paho.mqtt.client as mqtt
import requests
import json
import base64
import sqlite3  # Import SQLite3 for database operations
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from meshtastic import (
    mesh_pb2,
    mqtt_pb2,
    portnums_pb2,
    telemetry_pb2,
    BROADCAST_NUM,
)  # Import from meshtastic

# Define your encryption key here
key = "1PG7OiApB1nwvP+rz05pAQ=="  # Replace with your actual key , This is the key for AQ== in long form
padded_key = key.ljust(len(key) + ((4 - (len(key) % 4)) % 4), "=")
replaced_key = padded_key.replace("-", "+").replace("_", "/")
key = replaced_key
root_topic = "msh/MY_919/2/e/"
channel = "LongFast"

# MQTT settings
MQTT_BROKER = "xlx.lucifernet.com"  # Replace with your MQTT broker's address
MQTT_PORT = 1883  # Replace with your MQTT broker's port
MQTT_TOPIC = "msh/MY_919"  # Replace with the topic you want to subscribe to
MQTT_USERNAME = "meshdev"  # Replace with your MQTT username
MQTT_PASSWORD = "large4cats"  # Replace with your MQTT password

# DAPNET API settings
DAPNET_API_URL = "http://hampager.de:8080/calls"
DAPNET_USER = "9w2lwk"  # Replace with your DAPNET username
DAPNET_PASSWORD = "xxxx"  # Replace with your DAPNET password
CALLSIGN = "9W2LWK"  # Replace with your pager's callsign
TRANSMITTER_GROUP = "9m-all"  # Replace with the DAPNET transmitter group



def decode_encrypted(message_packet):
    try:
        key_bytes = base64.b64decode(key.encode("ascii"))

        nonce_packet_id = getattr(message_packet, "id").to_bytes(8, "little")
        nonce_from_node = getattr(message_packet, "from").to_bytes(8, "little")
        nonce = nonce_packet_id + nonce_from_node

        cipher = Cipher(
            algorithms.AES(key_bytes), modes.CTR(nonce), backend=default_backend()
        )
        decryptor = cipher.decryptor()
        decrypted_bytes = (
            decryptor.update(getattr(message_packet, "encrypted"))
            + decryptor.finalize()
        )

        data = mesh_pb2.Data()
        data.ParseFromString(decrypted_bytes)
        message_packet.decoded.CopyFrom(data)

        client_id = create_node_id(getattr(message_packet, "from", None))
        print("----------->service_envelope.packet.from", client_id)

        if message_packet.decoded.portnum == portnums_pb2.TEXT_MESSAGE_APP:
            text_payload = message_packet.decoded.payload.decode("utf-8")
            send_to_dapnet_pocsag(
                text_payload, client_id
            )  # Send text payload to DAPNET

        elif message_packet.decoded.portnum == portnums_pb2.NODEINFO_APP:
            info = mesh_pb2.User()
            info.ParseFromString(message_packet.decoded.payload)

            if info.long_name is not None:
                print("----->nodeinfo", info)
                print(f"id:{info.id}, long_name:{info.long_name}")

                try:
                    conn = sqlite3.connect("meshtastic.db")
                    cursor = conn.cursor()

                    cursor.execute(
                        f"SELECT * FROM {channel} WHERE client_id = ?", (client_id,)
                    )
                    existing_row = cursor.fetchone()

                    if existing_row:
                        cursor.execute(
                            f"""UPDATE {channel} SET
                                        long_name = ?, short_name = ?
                                        WHERE client_id = ?""",
                            (info.long_name, info.short_name, client_id),
                        )
                    else:
                        cursor.execute(
                            f"""INSERT INTO {channel}
                                        (client_id, long_name, short_name)
                                        VALUES (?, ?, ?)""",
                            (client_id, info.long_name, info.short_name),
                        )

                    conn.commit()

                    conn.close()
                except Exception as e:
                    print(f"Update node Info failed: {str(e)}")

        elif message_packet.decoded.portnum == portnums_pb2.POSITION_APP:
            pos = mesh_pb2.Position()
            pos.ParseFromString(message_packet.decoded.payload)

            if pos.latitude_i != 0:
                # print('----->client_id',client_id)
                print("----->pos", pos)
                try:
                    conn = sqlite3.connect("meshtastic.db")
                    cursor = conn.cursor()

                    cursor.execute(
                        f"SELECT * FROM {channel} WHERE client_id = ?", (client_id,)
                    )
                    existing_row = cursor.fetchone()

                    if existing_row:
                        cursor.execute(
                            f"""UPDATE {channel} SET
                                        latitude_i = ?, longitude_i = ?, precision_bits = ?
                                        WHERE client_id = ?""",
                            (
                                pos.latitude_i,
                                pos.longitude_i,
                                pos.precision_bits,
                                client_id,
                            ),
                        )
                    else:
                        cursor.execute(
                            f"""INSERT INTO {channel}
                                        (client_id, latitude_i, longitude_i, precision_bits)
                                        VALUES (?, ?, ?, ?)""",
                            (
                                client_id,
                                pos.latitude_i,
                                pos.longitude_i,
                                pos.precision_bits,
                            ),
                        )

                    conn.commit()

                    conn.close()
                except Exception as e:
                    print(f"Update node Position failed: {str(e)}")

        elif message_packet.decoded.portnum == portnums_pb2.TELEMETRY_APP:
            env = telemetry_pb2.Telemetry()
            env.ParseFromString(message_packet.decoded.payload)
            print("----->env", env)

    except Exception as e:
        print(f"Decryption failed: {str(e)}")


# MQTT on_connect callback
def on_connect(client, userdata, flags, rc, properties):
    if rc == 0:
        print("Connected to MQTT broker successfully.")
        print(
            f"Connected to {MQTT_BROKER} on topic:{subscribe_topic} id:{BROADCAST_NUM}"
        )
    else:
        print(f"Failed to connect to MQTT broker with result code {str(rc)}")


def get_long_name(client_id):
    conn = sqlite3.connect("meshtastic.db")
    cursor = conn.cursor()

    cursor.execute("SELECT long_name FROM LongFast WHERE client_id = ?", (client_id,))
    row = cursor.fetchone()

    cursor.close()
    conn.close()

    if row:
        return row[0]
    else:
        return None


def escape_special_characters(text):
    special_characters = [
        "_",
        "*",
        "[",
        "]",
        "(",
        ")",
        "~",
        "`",
        ">",
        "#",
        "+",
        "-",
        "=",
        "|",
        "{",
        "}",
        ".",
        "!",
    ]
    escaped_text = ""
    for char in text:
        if char in special_characters:
            escaped_text += "\\" + char
        else:
            escaped_text += char
    return escaped_text


def create_node_id(node_number):
    return f"!{hex(node_number)[2:]}"


# Updated MQTT on_message callback
def on_message(client, userdata, msg):
    service_envelope = mqtt_pb2.ServiceEnvelope()
    try:
        service_envelope.ParseFromString(msg.payload)
        print('----------->service_envelope',service_envelope)
        message_packet = service_envelope.packet
        print('----------->message_packet',message_packet)
    except Exception as e:
        print(f"Error parsing message_packet: {str(e)}")
        return

    # Only process messages for a specific channel
    if message_packet.to == BROADCAST_NUM:
        # Check if the message has an encrypted field
        if message_packet.HasField("encrypted") and not message_packet.HasField(
            "decoded"
        ):
            decode_encrypted(message_packet)
        else:
            # If no decryption is required
            print(
                "----------------------------------*****no decode*****", mesh_pb2.Data()
            )

def send_to_dapnet_pocsag(text_payload, client_id):
    """
    Sends a message to a DAPNET POCSAG pager.
    """
    payload = {
        "text": text_payload,
        "callSignNames": [CALLSIGN],
        "transmitterGroupNames": [TRANSMITTER_GROUP],
        "emergency": False
    }

    try:
        print(f'client_id:{client_id} , text_payload: {text_payload}')
        if text_payload is not None:
            long_name = get_long_name(client_id)
            text_payload = escape_special_characters(text_payload)

            # Update the message format here as per your requirements
            if long_name is not None:
                print(f"The long name of client {client_id} is: {long_name}")
                msg_who = f"*{long_name} ({client_id})*:"
            else:
                print(f"No long name found for client {client_id}")
                msg_who = f"*{client_id}*:"
            
            # Set up authentication and headers
            auth = (CALLSIGN, DAPNET_PASSWORD)
            headers = {"Content-Type": "application/json"}

            # Send the POST request
            response = requests.post(
                DAPNET_API_URL,
                auth=auth,
                headers=headers,
                json=payload
            )

            # Check for any response errors
            if response.status_code == 200:
                print("Message sent successfully.")
            else:
                print(f"Failed to send message. Status code: {response.status_code}, Response: {response.text}")
        else:
            print("Received None payload. Ignoring...")
    
    except Exception as e:
        print(f"Send message to DAPNET error: {str(e)}")
        

conn = sqlite3.connect("meshtastic.db")
cursor = conn.cursor()


create_table_query = f"""CREATE TABLE IF NOT EXISTS LongFast (
                            client_id TEXT PRIMARY KEY NOT NULL,
                            long_name TEXT,
                            short_name TEXT,
                            macaddr TEXT,
                            latitude_i TEXT,
                            longitude_i TEXT,
                            altitude TEXT,
                            precision_bits TEXT,
                            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )"""

cursor.execute(create_table_query)
conn.commit()
conn.close()

# MQTT setup with Callback API version 2
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.username_pw_set(username=MQTT_USERNAME, password=MQTT_PASSWORD)
client.on_connect = on_connect
client.on_message = on_message
client.username_pw_set(username=MQTT_USERNAME, password=MQTT_PASSWORD)
client.connect(MQTT_BROKER, MQTT_PORT, 60)

subscribe_topic = f"{root_topic}{channel}/#"
client.subscribe(subscribe_topic, 0)


if __name__ == "__main__":
    while client.loop() == 0:
        pass

