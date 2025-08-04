#!/root/.venv/bin/python3

import paho.mqtt.client as mqtt
import requests
import json
import base64
import sqlite3
import logging
import time
import signal
import sys
import threading
import os
from datetime import datetime
from dotenv import load_dotenv
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from meshtastic import (
    mesh_pb2,
    mqtt_pb2,
    portnums_pb2,
    telemetry_pb2,
    BROADCAST_NUM,
)

# Load environment variables from .env file
load_dotenv()

# Global variables for graceful shutdown
shutdown_event = threading.Event()
mqtt_client = None
db_connection = None

# Configure logging with rotation and console output
def setup_logging():
    """Setup comprehensive logging with both file and console output"""
    try:
        # Create logger
        logger = logging.getLogger()
        logger.setLevel(logging.DEBUG)
        
        # Clear any existing handlers
        logger.handlers.clear()
        
        # File handler with rotation
        log_file = os.getenv('LOG_FILE', 'meshtastic_debug.log')
        file_handler = logging.FileHandler(log_file, mode="a")
        file_handler.setLevel(logging.DEBUG)
        
        # Console handler for immediate feedback
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        
        # Formatter
        formatter = logging.Formatter(
            "%(asctime)s - %(levelname)s - [%(funcName)s:%(lineno)d] - %(message)s"
        )
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)
        
        # Add handlers
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)
        
        logging.info("Logging system initialized successfully")
        return True
    except Exception as e:
        print(f"CRITICAL: Failed to setup logging: {e}")
        return False

# Initialize logging first
if not setup_logging():
    sys.exit(1)

logging.info("=" * 60)
logging.info("Starting Meshtastic MQTT Listener - Enhanced Version with .env")
logging.info("=" * 60)

def load_config_from_env():
    """Load configuration from environment variables"""
    config = {
        # Encryption settings
        "encryption_key": os.getenv('ENCRYPTION_KEY', ''),
        
        # MQTT Topic settings
        "root_topic": os.getenv('ROOT_TOPIC', 'msh/MY_919/2/e/'),
        "channel": os.getenv('CHANNEL', 'LongFast'),
        
        # MQTT Connection settings
        "mqtt_broker": os.getenv('MQTT_BROKER', ''),
        "mqtt_port": int(os.getenv('MQTT_PORT', '1883')),
        "mqtt_topic": os.getenv('MQTT_TOPIC', ''),
        "mqtt_username": os.getenv('MQTT_USERNAME', ''),
        "mqtt_password": os.getenv('MQTT_PASSWORD', ''),
        "mqtt_keepalive": int(os.getenv('MQTT_KEEPALIVE', '60')),
        
        # DAPNET API settings
        "dapnet_api_url": os.getenv('DAPNET_API_URL', ''),
        "dapnet_user": os.getenv('DAPNET_USER', ''),
        "dapnet_password": os.getenv('DAPNET_PASSWORD', ''),
        "callsign": os.getenv('CALLSIGN', ''),
        "transmitter_group": os.getenv('TRANSMITTER_GROUP', ''),
        
        # Application settings
        "max_retries": int(os.getenv('MAX_RETRIES', '5')),
        "retry_delay": int(os.getenv('RETRY_DELAY', '5')),
        "api_timeout": int(os.getenv('API_TIMEOUT', '30')),
        
        # Database settings
        "database_file": os.getenv('DATABASE_FILE', 'meshtastic.db'),
        
        # Logging settings
        "log_file": os.getenv('LOG_FILE', 'meshtastic_debug.log'),
        "log_level": os.getenv('LOG_LEVEL', 'INFO').upper()
    }
    
    logging.info("Configuration loaded from environment variables")
    return config

# Load configuration
CONFIG = load_config_from_env()

def validate_config():
    """Validate configuration parameters"""
    try:
        logging.info("Validating configuration...")
        
        required_fields = [
            ("encryption_key", "ENCRYPTION_KEY"),
            ("mqtt_broker", "MQTT_BROKER"),
            ("mqtt_username", "MQTT_USERNAME"),
            ("mqtt_password", "MQTT_PASSWORD"),
            ("dapnet_user", "DAPNET_USER"),
            ("dapnet_password", "DAPNET_PASSWORD"),
            ("callsign", "CALLSIGN"),
            ("dapnet_api_url", "DAPNET_API_URL"),
            ("transmitter_group", "TRANSMITTER_GROUP")
        ]
        
        missing_fields = []
        for field, env_var in required_fields:
            if not CONFIG.get(field):
                missing_fields.append(env_var)
        
        if missing_fields:
            raise ValueError(f"Missing required environment variables: {', '.join(missing_fields)}")
        
        # Validate encryption key
        try:
            base64.b64decode(CONFIG["encryption_key"])
        except Exception as e:
            raise ValueError(f"Invalid encryption key format in ENCRYPTION_KEY: {e}")
        
        # Validate numeric values
        if CONFIG["mqtt_port"] <= 0 or CONFIG["mqtt_port"] > 65535:
            raise ValueError("MQTT_PORT must be between 1 and 65535")
        
        if CONFIG["max_retries"] <= 0:
            raise ValueError("MAX_RETRIES must be greater than 0")
        
        if CONFIG["retry_delay"] <= 0:
            raise ValueError("RETRY_DELAY must be greater than 0")
        
        if CONFIG["api_timeout"] <= 0:
            raise ValueError("API_TIMEOUT must be greater than 0")
        
        logging.info("Configuration validation successful")
        return True
        
    except Exception as e:
        logging.critical(f"Configuration validation failed: {e}")
        logging.critical("Please check your .env file and ensure all required variables are set")
        return False

def prepare_encryption_key():
    """Prepare and validate encryption key"""
    try:
        key = CONFIG["encryption_key"]
        padded_key = key.ljust(len(key) + ((4 - (len(key) % 4)) % 4), "=")
        replaced_key = padded_key.replace("-", "+").replace("_", "/")
        
        # Test key validity
        key_bytes = base64.b64decode(replaced_key.encode("ascii"))
        logging.info(f"Encryption key prepared successfully (length: {len(key_bytes)} bytes)")
        
        return replaced_key
    except Exception as e:
        logging.error(f"Failed to prepare encryption key: {e}")
        raise

def setup_database():
    """Setup database with proper error handling"""
    global db_connection
    
    try:
        logging.info("Setting up database connection...")
        
        # Create connection with timeout and error handling
        db_connection = sqlite3.connect(
            CONFIG["database_file"], 
            timeout=30.0,
            check_same_thread=False
        )
        
        # Enable WAL mode for better concurrency
        db_connection.execute("PRAGMA journal_mode=WAL")
        db_connection.execute("PRAGMA synchronous=NORMAL")
        
        cursor = db_connection.cursor()
        
        create_table_query = f"""CREATE TABLE IF NOT EXISTS {CONFIG['channel']} (
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
        db_connection.commit()
        
        logging.info(f"Database setup completed successfully: {CONFIG['database_file']}")
        return True
        
    except sqlite3.Error as e:
        logging.error(f"Database setup failed: {e}")
        return False
    except Exception as e:
        logging.error(f"Unexpected error during database setup: {e}")
        return False

def decode_encrypted(message_packet, encryption_key):
    """Decode encrypted message with enhanced error handling"""
    try:
        logging.debug("Starting message decryption...")
        
        # Validate input
        if not hasattr(message_packet, 'encrypted') or not message_packet.encrypted:
            logging.warning("No encrypted data found in message packet")
            return False
        
        if not hasattr(message_packet, 'id') or not hasattr(message_packet, 'from'):
            logging.warning("Missing required fields for decryption")
            return False
        
        # Prepare decryption
        key_bytes = base64.b64decode(encryption_key.encode("ascii"))
        
        nonce_packet_id = getattr(message_packet, "id").to_bytes(8, "little")
        nonce_from_node = getattr(message_packet, "from").to_bytes(8, "little")
        nonce = nonce_packet_id + nonce_from_node
        
        logging.debug(f"Decryption nonce prepared (length: {len(nonce)})")
        
        # Decrypt
        cipher = Cipher(
            algorithms.AES(key_bytes), 
            modes.CTR(nonce), 
            backend=default_backend()
        )
        decryptor = cipher.decryptor()
        decrypted_bytes = (
            decryptor.update(getattr(message_packet, "encrypted"))
            + decryptor.finalize()
        )
        
        # Parse decrypted data
        data = mesh_pb2.Data()
        data.ParseFromString(decrypted_bytes)
        message_packet.decoded.CopyFrom(data)
        
        client_id = create_node_id(getattr(message_packet, "from", None))
        logging.info(f"Successfully decoded message from: {client_id}")
        
        # Process text messages
        if message_packet.decoded.portnum == portnums_pb2.TEXT_MESSAGE_APP:
            try:
                text_payload = message_packet.decoded.payload.decode("utf-8")
                logging.info(f"Text message content: {text_payload[:100]}...")  # Log first 100 chars
                
                # Send to DAPNET with retry
                send_to_dapnet_pocsag(text_payload, client_id)
                
            except UnicodeDecodeError as e:
                logging.error(f"Failed to decode text payload: {e}")
            except Exception as e:
                logging.error(f"Error processing text message: {e}")
        
        return True
        
    except Exception as e:
        logging.error(f"Decryption failed: {e}")
        logging.debug(f"Message packet details: {message_packet}")
        return False

def send_to_dapnet_pocsag(text_payload, client_id, max_retries=None):
    """Send message to DAPNET with retry mechanism"""
    if max_retries is None:
        max_retries = CONFIG["max_retries"]
    
    payload = {
        "text": text_payload,
        "callSignNames": [CONFIG["callsign"]],
        "transmitterGroupNames": [CONFIG["transmitter_group"]],
        "emergency": False
    }
    
    for attempt in range(max_retries):
        try:
            logging.info(f"Sending to DAPNET (attempt {attempt + 1}/{max_retries}): {text_payload[:50]}... from {client_id}")
            
            auth = (CONFIG["callsign"], CONFIG["dapnet_password"])
            headers = {"Content-Type": "application/json"}
            
            response = requests.post(
                CONFIG["dapnet_api_url"], 
                auth=auth, 
                headers=headers, 
                json=payload,
                timeout=CONFIG["api_timeout"]
            )
            
            if response.status_code == 200:
                logging.info("Message sent to DAPNET successfully")
                return True
            else:
                logging.warning(f"DAPNET API returned status {response.status_code}: {response.text}")
                
        except requests.exceptions.Timeout:
            logging.warning(f"DAPNET API timeout (attempt {attempt + 1})")
        except requests.exceptions.ConnectionError:
            logging.warning(f"DAPNET API connection error (attempt {attempt + 1})")
        except requests.exceptions.RequestException as e:
            logging.warning(f"DAPNET API request error (attempt {attempt + 1}): {e}")
        except Exception as e:
            logging.error(f"Unexpected error sending to DAPNET (attempt {attempt + 1}): {e}")
        
        if attempt < max_retries - 1:
            sleep_time = CONFIG["retry_delay"] * (2 ** attempt)  # Exponential backoff
            logging.info(f"Retrying in {sleep_time} seconds...")
            time.sleep(sleep_time)
    
    logging.error(f"Failed to send message to DAPNET after {max_retries} attempts")
    return False

def create_node_id(node_number):
    """Create node ID with validation"""
    try:
        if node_number is None:
            return "!unknown"
        return f"!{hex(node_number)[2:]}"
    except Exception as e:
        logging.error(f"Error creating node ID: {e}")
        return "!error"

def on_connect(client, userdata, flags, rc, properties=None):
    """Enhanced MQTT connection callback - compatible with both v1 and v2 API"""
    try:
        if rc == 0:
            logging.info(f"Successfully connected to MQTT broker: {CONFIG['mqtt_broker']}")
            
            # Subscribe to topic
            subscribe_topic = f"{CONFIG['root_topic']}{CONFIG['channel']}/#"
            result = client.subscribe(subscribe_topic, 0)
            
            if result[0] == mqtt.MQTT_ERR_SUCCESS:
                logging.info(f"Successfully subscribed to topic: {subscribe_topic}")
            else:
                logging.error(f"Failed to subscribe to topic: {subscribe_topic}")
                
        else:
            error_messages = {
                1: "Connection refused - incorrect protocol version",
                2: "Connection refused - invalid client identifier",
                3: "Connection refused - server unavailable",
                4: "Connection refused - bad username or password",
                5: "Connection refused - not authorised"
            }
            error_msg = error_messages.get(rc, f"Unknown error code: {rc}")
            logging.error(f"MQTT connection failed: {error_msg}")
            
    except Exception as e:
        logging.error(f"Error in on_connect callback: {e}")

def on_disconnect(client, userdata, rc, properties=None, reasonCode=None):
    """Enhanced MQTT disconnection callback - compatible with both v1 and v2 API"""
    try:
        # Handle both old and new callback signatures
        if rc != 0:
            logging.warning(f"Unexpected MQTT disconnection. Reason code: {rc}")
            if reasonCode is not None:
                logging.warning(f"Disconnect reason: {reasonCode}")
        else:
            logging.info("MQTT disconnected gracefully")
    except Exception as e:
        logging.error(f"Error in on_disconnect callback: {e}")

def on_message(client, userdata, msg):
    """Enhanced message processing with comprehensive error handling"""
    try:
        logging.debug(f"Received MQTT message on topic: {msg.topic}")
        
        # Parse service envelope
        service_envelope = mqtt_pb2.ServiceEnvelope()
        try:
            service_envelope.ParseFromString(msg.payload)
        except Exception as e:
            logging.error(f"Failed to parse ServiceEnvelope: {e}")
            return
        
        # Extract message packet
        if not hasattr(service_envelope, 'packet'):
            logging.warning("ServiceEnvelope missing packet field")
            return
            
        message_packet = service_envelope.packet
        logging.debug(f"Message packet from node: {getattr(message_packet, 'from', 'unknown')}")
        
        # Process broadcast messages
        if hasattr(message_packet, 'to') and message_packet.to == BROADCAST_NUM:
            logging.debug("Processing broadcast message")
            
            if (hasattr(message_packet, 'encrypted') and 
                message_packet.HasField("encrypted") and 
                not message_packet.HasField("decoded")):
                
                logging.debug("Processing encrypted message")
                decode_encrypted(message_packet, encryption_key)
            else:
                logging.debug("Received non-encrypted broadcast message")
        else:
            logging.debug("Ignoring non-broadcast message")
            
    except Exception as e:
        logging.error(f"Critical error in message processing: {e}")
        logging.debug(f"Message details - Topic: {msg.topic}, Payload length: {len(msg.payload)}")

def setup_mqtt_client():
    """Setup MQTT client with enhanced error handling"""
    global mqtt_client
    
    try:
        logging.info("Setting up MQTT client...")
        
        # Create client with callback API version 2 for better compatibility
        mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        
        # Set credentials
        mqtt_client.username_pw_set(
            username=CONFIG["mqtt_username"], 
            password=CONFIG["mqtt_password"]
        )
        
        # Set callbacks
        mqtt_client.on_connect = on_connect
        mqtt_client.on_disconnect = on_disconnect
        mqtt_client.on_message = on_message
        
        # Configure client options
        mqtt_client.reconnect_delay_set(min_delay=1, max_delay=120)
        
        logging.info("MQTT client setup completed")
        return True
        
    except Exception as e:
        logging.error(f"Failed to setup MQTT client: {e}")
        return False

def connect_mqtt_with_retry():
    """Connect to MQTT broker with retry mechanism"""
    global mqtt_client
    
    max_retries = CONFIG["max_retries"]
    
    for attempt in range(max_retries):
        try:
            logging.info(f"Attempting MQTT connection (attempt {attempt + 1}/{max_retries})...")
            
            result = mqtt_client.connect(
                CONFIG["mqtt_broker"], 
                CONFIG["mqtt_port"], 
                CONFIG["mqtt_keepalive"]
            )
            
            if result == mqtt.MQTT_ERR_SUCCESS:
                logging.info("MQTT connection initiated successfully")
                return True
            else:
                logging.warning(f"MQTT connection failed with code: {result}")
                
        except Exception as e:
            logging.error(f"MQTT connection attempt {attempt + 1} failed: {e}")
        
        if attempt < max_retries - 1:
            sleep_time = CONFIG["retry_delay"] * (2 ** attempt)
            logging.info(f"Retrying MQTT connection in {sleep_time} seconds...")
            time.sleep(sleep_time)
    
    logging.critical(f"Failed to connect to MQTT broker after {max_retries} attempts")
    return False

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    logging.info(f"Received signal {signum}, initiating graceful shutdown...")
    shutdown_event.set()

def cleanup_resources():
    """Clean up resources before exit"""
    global mqtt_client, db_connection
    
    try:
        logging.info("Cleaning up resources...")
        
        # Disconnect MQTT client
        if mqtt_client:
            try:
                mqtt_client.disconnect()
                mqtt_client.loop_stop()
                logging.info("MQTT client disconnected")
            except Exception as e:
                logging.error(f"Error disconnecting MQTT client: {e}")
        
        # Close database connection
        if db_connection:
            try:
                db_connection.close()
                logging.info("Database connection closed")
            except Exception as e:
                logging.error(f"Error closing database connection: {e}")
        
        logging.info("Resource cleanup completed")
        
    except Exception as e:
        logging.error(f"Error during resource cleanup: {e}")

def main_loop():
    """Main application loop with enhanced error handling"""
    global mqtt_client
    
    try:
        logging.info("Starting main application loop...")
        
        # Start MQTT loop in background
        mqtt_client.loop_start()
        
        # Main monitoring loop
        while not shutdown_event.is_set():
            try:
                # Check MQTT client status
                if not mqtt_client.is_connected():
                    logging.warning("MQTT client disconnected, attempting reconnection...")
                    if not connect_mqtt_with_retry():
                        logging.critical("Failed to reconnect to MQTT broker")
                        break
                
                # Health check interval
                time.sleep(10)
                
            except KeyboardInterrupt:
                logging.info("Keyboard interrupt received")
                break
            except Exception as e:
                logging.error(f"Error in main loop: {e}")
                time.sleep(5)  # Brief pause before continuing
        
        logging.info("Main loop ended")
        
    except Exception as e:
        logging.critical(f"Critical error in main loop: {e}")
    finally:
        cleanup_resources()

def print_config_summary():
    """Print configuration summary (without sensitive data)"""
    logging.info("Configuration Summary:")
    logging.info(f"  MQTT Broker: {CONFIG['mqtt_broker']}:{CONFIG['mqtt_port']}")
    logging.info(f"  MQTT Topic: {CONFIG['root_topic']}{CONFIG['channel']}")
    logging.info(f"  MQTT Username: {CONFIG['mqtt_username']}")
    logging.info(f"  DAPNET API: {CONFIG['dapnet_api_url']}")
    logging.info(f"  Callsign: {CONFIG['callsign']}")
    logging.info(f"  Transmitter Group: {CONFIG['transmitter_group']}")
    logging.info(f"  Database File: {CONFIG['database_file']}")
    logging.info(f"  Log File: {CONFIG['log_file']}")
    logging.info(f"  Max Retries: {CONFIG['max_retries']}")
    logging.info(f"  Retry Delay: {CONFIG['retry_delay']}s")
    logging.info(f"  API Timeout: {CONFIG['api_timeout']}s")

def main():
    """Main function with comprehensive initialization"""
    global encryption_key
    
    try:
        # Setup signal handlers
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        logging.info("Starting application initialization...")
        
        # Check if .env file exists
        if os.path.exists('.env'):
            logging.info("Found .env file, loading configuration...")
        else:
            logging.warning("No .env file found, using environment variables only")
        
        # Print configuration summary
        print_config_summary()
        
        # Validate configuration
        if not validate_config():
            logging.critical("Configuration validation failed, exiting")
            logging.critical("Please check your .env file or environment variables")
            sys.exit(1)
        
        # Prepare encryption key
        encryption_key = prepare_encryption_key()
        
        # Setup database
        if not setup_database():
            logging.critical("Database setup failed, exiting")
            sys.exit(1)
        
        # Setup MQTT client
        if not setup_mqtt_client():
            logging.critical("MQTT client setup failed, exiting")
            sys.exit(1)
        
        # Connect to MQTT broker
        if not connect_mqtt_with_retry():
            logging.critical("Failed to connect to MQTT broker, exiting")
            sys.exit(1)
        
        logging.info("Initialization completed successfully")
        logging.info("Application is now running. Press Ctrl+C to stop.")
        
        # Start main loop
        main_loop()
        
    except KeyboardInterrupt:
        logging.info("Application interrupted by user")
    except Exception as e:
        logging.critical(f"Critical application error: {e}")
        sys.exit(1)
    finally:
        logging.info("Application shutdown completed")

if __name__ == "__main__":
    main()
