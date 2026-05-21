# =====================================================================
# SIMCOM A7672S "FULL AUTO WITH ESIM" FRAMEWORK
# =====================================================================
# Features:
# - STMicro eSIM MUX Routing (GPIO 5 locked HIGH)
# - UART / Type-C USB selection menu
# - Active-LOW Hardware Reset Recovery
# - Heavy AT command throttling to prevent buffer overflow
# - Auto APN Toggling
# - MQTT communication (CMQTT state rebuilds)
# - RTC timestamp integration (DS3231)
# - Step-wise state machine architecture
# =====================================================================

import serial
import RPi.GPIO as GPIO
import time
import os
from datetime import datetime

try:
    import smbus2
except ImportError:
    pass # smbus2 not installed

# ==========================================================
# GPIO CONFIGURATION (BCM MODE)
# ==========================================================

EN_PIN = 22
PWR_PIN = 17
RST_PIN = 27
SIM_SEL_PIN = 5  # MUX pin

# ==========================================================
# SERIAL & I2C CONFIGURATION
# ==========================================================

BAUD_RATE = 115200
I2C_BUS = 1
DS3231_ADDR = 0x68

MQTT_BROKER = "tcp://broker.emqx.io:1883"
MQTT_TOPIC = "pihat1"

# ==========================================================
# RTC FUNCTIONS
# ==========================================================

def dec_to_bcd(val):
    return (val // 10 << 4) + (val % 10)

def bcd_to_dec(val):
    return ((val >> 4) * 10) + (val & 0x0F)

def rtc_get_time():
    for attempt in range(1, 4):
        try:
            with smbus2.SMBus(I2C_BUS) as bus:
                data = bus.read_i2c_block_data(DS3231_ADDR, 0x00, 7)
            second = bcd_to_dec(data[0])
            minute = bcd_to_dec(data[1])
            hour = bcd_to_dec(data[2] & 0x3F)
            day = bcd_to_dec(data[4])
            month = bcd_to_dec(data[5] & 0x1F)
            year = bcd_to_dec(data[6]) + 2000
            return f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"
        except Exception:
            if attempt < 3:
                time.sleep(1)
            else:
                return time.strftime('%Y-%m-%d %H:%M:%S')

# ==========================================================
# STARTUP & HARDWARE CONTROL
# ==========================================================

def initialize_startup_sequence():
    """Handles RTC sync, physical hardware power-on, and comm mode selection."""
    
    # 1. RTC Check
    try:
        bus = smbus2.SMBus(I2C_BUS)
        bus.read_byte(DS3231_ADDR)
        print("🕒 RTC synchronization successful.")
    except Exception as e:
        print(f"⚠️ RTC synchronization failed: {e}")
    
    # 2. Hardware Power-On
    print("\n⚡ Powering on A7672S module...")
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    
    for pin in [EN_PIN, PWR_PIN, RST_PIN, SIM_SEL_PIN]:
        GPIO.setup(pin, GPIO.OUT)
    
    # Force MUX to STMicro eSIM (HIGH)
    GPIO.output(SIM_SEL_PIN, GPIO.HIGH)
    GPIO.output(EN_PIN, GPIO.HIGH)
    GPIO.output(RST_PIN, GPIO.HIGH) # Reset is active low
    
    print("🔘 Sending PWRKEY pulse...")
    GPIO.output(PWR_PIN, GPIO.HIGH)
    time.sleep(2)
    GPIO.output(PWR_PIN, GPIO.LOW)
    
    print("⏳ Waiting for modem boot sequence (20s)...")
    time.sleep(20)
    print("✅ MODEM INITIALIZATION COMPLETE\n")
    
    # 3. Communication Menu
    print("Select Communication Mode:")
    print("1. UART")
    print("2. Type-C USB")
    
    while True:
        choice = input("\nEnter option: ").strip()
        if choice == '1':
            print("🔗 Initializing UART on /dev/serial0...")
            return "/dev/serial0", "1"
        elif choice == '2':
            print("🔗 Initializing Type-C USB...")
            return None, "2"
        else:
            print("❌ Invalid option. Please enter 1 or 2.")

def hardware_reset(choice):
    print("\n🔄 ENTERING FULL MODEM REBOOT STATE")
    GPIO.output(RST_PIN, GPIO.LOW)
    time.sleep(1)
    GPIO.output(RST_PIN, GPIO.HIGH)
    print("⏳ Waiting for modem reboot (25s)...")
    time.sleep(25)
    print("✅ MODEM REBOOT COMPLETE\n")

# ==========================================================
# SERIAL COMMUNICATION
# ==========================================================

def send_at(ser, command, timeout=5):
    try:
        print(f"[MODEM] --> {command}")
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        ser.write((command + '\r\n').encode())

        response = ""
        start_time = time.time()

        while time.time() - start_time < timeout:
            if ser.in_waiting:
                chunk = ser.read(ser.in_waiting).decode(errors='ignore')
                response += chunk
                if ("OK" in response or "ERROR" in response or "+CME ERROR" in response):
                    break
            time.sleep(0.1)

        print(f"[MODEM] <-- {response.strip()}\n")
        
        # GLOBAL THROTTLE: Force a 2-second breath between EVERY command
        time.sleep(2) 
        
        return response

    except Exception as e:
        raise serial.SerialException(f"Connection lost: {e}")

def send_mqtt_payload(ser, command, text):
    try:
        print(f"[MODEM] --> {command} (Waiting for prompt...)")
        ser.write((command + '\r\n').encode('utf-8'))
        
        response = ""
        timeout = time.time() + 5.0 
        
        while time.time() < timeout:
            if ser.in_waiting > 0:
                response += ser.read(ser.in_waiting).decode(errors='ignore')
            if ">" in response:
                break
            time.sleep(0.1)
            
        if ">" in response:
            print(f"[MODEM] --> Sending payload string...")
            ser.write((text + '\r\n').encode('utf-8'))
            time.sleep(1.0)
            
            while ser.in_waiting > 0:
                response += ser.read(ser.in_waiting).decode(errors='ignore')
                time.sleep(0.1)
        else:
            print("⚠️ [MODEM] Timeout waiting for '>' prompt!")
            
        print(f"[MODEM] <-- {response.strip()}\n")
        time.sleep(2) # Mandatory breather
        return response

    except Exception as e:
         raise serial.SerialException(f"Connection lost: {e}")

def auto_find_usb_port():
    print("🔍 Detecting SIMCom USB AT port...")
    while True:
        ports_to_test = ["/dev/ttyUSB2", "/dev/ttyUSB1", "/dev/ttyUSB3", "/dev/ttyUSB0"]
        for port in ports_to_test:
            if os.path.exists(port):
                try:
                    with serial.Serial(port, BAUD_RATE, timeout=1) as ser:
                        ser.write(b"AT\r\n")
                        time.sleep(0.5)
                        response = ser.read(ser.in_waiting).decode(errors='ignore')
                        if "OK" in response:
                            print(f"✅ SIMCOM AT PORT LOCKED: {port}")
                            time.sleep(2)
                            return serial.Serial(port, BAUD_RATE, timeout=1)
                except serial.SerialException:
                    pass 
        print("⚠️ Waiting for USB ports to appear...")
        time.sleep(5)

# ==========================================================
# STATE FUNCTIONS
# ==========================================================

def ensure_sim(ser):
    print("🔍 Checking SIM status...")
    resp = send_at(ser, "AT+CPIN?", 3)
    if "READY" in resp:
        print("✅ SIM READY")
        return True
    
    print("⚠️ SIM not ready. Waiting 10 seconds before retry...")
    time.sleep(10)
    return False

def ensure_network(ser):
    print("📶 Checking network registration...")
    resp = send_at(ser, "AT+CREG?", 3)
    if ",1" in resp or ",5" in resp:
        send_at(ser, "AT+COPS?", 3)
        print("✅ Network registered")
        return True
        
    print("⏳ Network not ready yet. Waiting 10 seconds...")
    time.sleep(10)
    return False

def ensure_pdp(ser, retry_count=[0]):
    print("🌐 Checking PDP context...")
    resp = send_at(ser, "AT+CGACT?", 3)
    if "1,1" in resp:
        print("✅ PDP already active")
        return True

    print("🚀 Activating PDP context...")
    cops = send_at(ser, "AT+COPS?", 3).lower()
    
    apn = "internet"
    if "airtel" in cops:
        apn = "airtelgprs.com" if retry_count[0] % 2 == 0 else "iot.airtel.com"
        retry_count[0] += 1
    elif "jio" in cops:
        apn = "jionet"

    print(f"⚙️ Selected APN: {apn}")
    send_at(ser, f'AT+CGDCONT=1,"IP","{apn}"', 3)
    
    act_resp = send_at(ser, "AT+CGACT=1,1", 10) # Heavy timeout
    if "OK" in act_resp:
        print("⏳ Settling LTE stack (10s)...")
        time.sleep(10)
        return True
        
    print("❌ PDP activation failed. Waiting 10 seconds...")
    time.sleep(10)
    return False

def build_mqtt_from_scratch(ser):
    print("🧹 Wiping previous MQTT states...")
    send_at(ser, "AT+CMQTTDISC=0,60", 3)
    send_at(ser, "AT+CMQTTREL=0", 3)
    send_at(ser, "AT+CMQTTSTOP", 3)
    
    print("🔌 Booting MQTT Service (Wait 5s)...")
    send_at(ser, "AT+CMQTTSTART", 3)
    time.sleep(5)
    
    client_id = f"pi_hat_{int(time.time())}"
    print(f"🆔 Acquiring Client ID: {client_id} (Wait 5s)...")
    send_at(ser, f'AT+CMQTTACCQ=0,"{client_id}"', 3)
    time.sleep(5)
    
    print("⏳ Connecting to Broker (Timeout 60s)...")
    conn_resp = send_at(ser, f'AT+CMQTTCONNECT=0,"{MQTT_BROKER}",60,1', 15)
    
    if "ERROR" in conn_resp:
        print("❌ Connection to broker failed. Cooling down...")
        time.sleep(10)
        return False
    return True

def ensure_mqtt(ser, force_rebuild=False):
    if force_rebuild:
        return build_mqtt_from_scratch(ser)

    resp = send_at(ser, "AT+CMQTTCONNECT?", 3)
    if "+CMQTTCONNECT: 0\r" in resp or "ERROR" in resp:
        print("⚠️ MQTT disconnected! Rebuilding session...")
        return build_mqtt_from_scratch(ser)
        
    print("✅ MQTT Connection stable")
    return True

# ==========================================================
# MAIN APPLICATION
# ==========================================================

def main():
    port_name, choice = initialize_startup_sequence()

    if choice == "1":
        try:
            ser = serial.Serial(port_name, BAUD_RATE, timeout=1)
        except serial.SerialException as e:
            print(f"❌ UART open failed: {e}")
            return
    elif choice == "2":
        ser = auto_find_usb_port()

    # Pre-flight MQTT wipe to guarantee a clean start
    send_at(ser, "AT+CMQTTDISC=0,60", 3)
    send_at(ser, "AT+CMQTTREL=0", 3)
    send_at(ser, "AT+CMQTTSTOP", 3)

    state = "SIM"
    msg_count = 1
    force_mqtt_rebuild = True

    while True:
        try:
            print("\n====== MAIN LOOP ======")
            
            # Simple heartbeat check
            resp = send_at(ser, "AT", 2)
            if not resp:
                raise serial.SerialException("AT response timeout. Modem unresponsive.")

            if state == "SIM":
                if ensure_sim(ser): state = "NETWORK"

            elif state == "NETWORK":
                if ensure_network(ser): state = "PDP"

            elif state == "PDP":
                if ensure_pdp(ser): state = "MQTT"

            elif state == "MQTT":
                if ensure_mqtt(ser, force_rebuild=force_mqtt_rebuild):
                    force_mqtt_rebuild = False
                    state = "PUBLISH"
                else:
                    force_mqtt_rebuild = True
                    time.sleep(5)

            elif state == "PUBLISH":
                timestamp = rtc_get_time()
                payload = f"[{timestamp}] SIMCom A7672S (eSIM) | Msg #{msg_count}"
                
                print(f"📡 Publishing MQTT message #{msg_count}")
                topic_cmd = f"AT+CMQTTTOPIC=0,{len(MQTT_TOPIC)}"
                send_mqtt_payload(ser, topic_cmd, MQTT_TOPIC)
                
                payload_cmd = f"AT+CMQTTPAYLOAD=0,{len(payload)}"
                send_mqtt_payload(ser, payload_cmd, payload)
                
                resp = send_at(ser, "AT+CMQTTPUB=0,0,60", 5)

                if "ERROR" in resp:
                    print("⚠️ MQTT publish failed. Flagging for rebuild.")
                    force_mqtt_rebuild = True
                    state = "MQTT"
                else:
                    print("✅ MQTT publish successful")
                    msg_count += 1

                print("⏳ Resting for 15 seconds before next cycle...")
                time.sleep(15)

        except Exception as e:
            print(f"\n❌ CRITICAL ERROR: {e}")
            try: ser.close()
            except: pass
            
            hardware_reset(choice)
            
            if choice == "1":
                while True:
                    try:
                        ser = serial.Serial(port_name, BAUD_RATE, timeout=1)
                        print("✅ UART reconnected")
                        break
                    except Exception:
                        time.sleep(5)
            else:
                ser = auto_find_usb_port()

            state = "SIM"
            force_mqtt_rebuild = True

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n🛑 Stopped by user")
    finally:
        GPIO.cleanup()
        print("🧹 Cleanup complete")
