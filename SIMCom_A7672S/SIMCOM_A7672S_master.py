# ==========================================================
# SIMCOM A7672S "FULL AUTO" FRAMEWORK
# ==========================================================
# Features:
# - UART / Type-C USB selection
# - Active-LOW Hardware Reset Recovery
# - Baseband Zombie Tripwire (Auto-Reboots on Silent Modem)
# - Dynamic Airtel APN Toggling
# - GNSS (GPS) Engine Initialization & Parsing
# - MQTT communication (CMQTT state rebuilds)
# - RTC timestamp integration (DS3231)
# - Step-wise state machine architecture
# ==========================================================

import serial
import RPi.GPIO as GPIO
import time
import os
import smbus2
from datetime import datetime

# ==========================================================
# GPIO CONFIGURATION (BCM MODE)
# ==========================================================

EN_PIN = 22
PWR_PIN = 17
RST_PIN = 27  
SIM_SEL_PIN = 5

# ==========================================================
# SERIAL & I2C CONFIGURATION
# ==========================================================

UART_PORT = '/dev/serial0'
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

def rtc_set_system_time():
    try:
        with smbus2.SMBus(I2C_BUS) as bus:
            now = datetime.now()
            rtc_data = [
                dec_to_bcd(now.second),
                dec_to_bcd(now.minute),
                dec_to_bcd(now.hour),
                dec_to_bcd(now.isoweekday()),
                dec_to_bcd(now.day),
                dec_to_bcd(now.month),
                dec_to_bcd(now.year - 2000)
            ]
            bus.write_i2c_block_data(DS3231_ADDR, 0x00, rtc_data)
        print("🕒 RTC synchronized with system time")
    except Exception as e:
        print(f"⚠️ RTC synchronization failed: {e}")

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
        except Exception as e:
            if attempt < 3:
                time.sleep(0.5)
            else:
                return time.strftime('%Y-%m-%d %H:%M:%S')

# ==========================================================
# HARDWARE CONTROL
# ==========================================================

def setup_hardware():
    GPIO.cleanup()
    time.sleep(0.5)
    
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)

    for pin in [EN_PIN, PWR_PIN, RST_PIN, SIM_SEL_PIN]:
        GPIO.setup(pin, GPIO.OUT)
    
    GPIO.output(EN_PIN, GPIO.LOW)
    GPIO.output(PWR_PIN, GPIO.LOW)
    GPIO.output(SIM_SEL_PIN, GPIO.LOW) # Keep High for eSIM

    # SIMCOM A7672S RESET is Active-LOW
    GPIO.output(RST_PIN, GPIO.HIGH)

def power_on_modem():
    print("⚡ Powering on A7672S module...")
    GPIO.output(EN_PIN, GPIO.HIGH)
    time.sleep(2)

    print("🔘 Sending PWRKEY pulse...")
    GPIO.output(PWR_PIN, GPIO.HIGH)
    time.sleep(2)
    # GPIO.output(PWR_PIN, GPIO.LOW)

    print("⏳ Waiting for modem boot sequence (20s)...")
    time.sleep(10)
    print("✅ MODEM INITIALIZATION COMPLETE\n")

def hardware_reset(choice):
    print("\n🔄 ENTERING FULL MODEM REBOOT STATE")
    try:
        if 'ser' in globals():
            ser.close()
    except:
        pass

    print("⚡ Pulling RST_PIN LOW...")
    GPIO.output(RST_PIN, GPIO.LOW)
    time.sleep(1)
    GPIO.output(RST_PIN, GPIO.HIGH)

    print("⏳ Waiting for modem reboot (25s)...")
    time.sleep(25)

    if choice == "2":
        print("⏳ Stabilizing USB interfaces...")
        time.sleep(5)
    else:
        print("⏳ Stabilizing UART interface...")
        time.sleep(2)
        
    print("✅ MODEM REBOOT COMPLETE\n")

# ==========================================================
# SERIAL COMMUNICATION
# ==========================================================

def send_at(ser, command, timeout=3):
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
        return response

    except Exception as e:
        raise serial.SerialException(f"Connection lost: {e}")

def send_mqtt_payload(ser, command, text):
    try:
        print(f"[MODEM] --> {command} (Waiting for prompt...)")
        ser.write((command + '\r\n').encode('utf-8'))
        
        response = ""
        timeout = time.time() + 3.0 
        
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
        return response

    except Exception as e:
         raise serial.SerialException(f"Connection lost: {e}")

def auto_find_usb_port():
    print("🔍 Detecting SIMCom USB AT port...")
    while True:
        ports_to_test = [
            "/dev/ttyUSB2", "/dev/ttyUSB1", "/dev/ttyUSB3", 
            "/dev/ttyUSB4", "/dev/ttyUSB5", "/dev/ttyUSB6", "/dev/ttyUSB0"
        ]
        
        if not any(os.path.exists(p) for p in ports_to_test):
            print("⚠️ No ttyUSB modem ports detected")
            time.sleep(2)
            continue
            
        for port in ports_to_test:
            if os.path.exists(port):
                try:
                    with serial.Serial(port, BAUD_RATE, timeout=1) as ser:
                        ser.reset_input_buffer()
                        ser.reset_output_buffer()
                        
                        for _ in range(3):
                            ser.write(b"AT\r\n")
                            time.sleep(0.1)
                        
                        response = ""
                        while ser.in_waiting > 0:
                            response += ser.read(ser.in_waiting).decode(errors='ignore')
                            time.sleep(0.05)
                        
                        if "OK" in response.split():
                            print(f"✅ SIMCOM AT PORT LOCKED: {port}")
                            time.sleep(2) 
                            
                            final_ser = serial.Serial(port, BAUD_RATE, timeout=1)
                            final_ser.reset_input_buffer()
                            return final_ser, port
                            
                except serial.SerialException:
                    pass 
                    
        time.sleep(3)

# ==========================================================
# STATE FUNCTIONS (WITH SIMCOM TRIPWIRES)
# ==========================================================

def ensure_sim(ser):
    print("🔍 Checking SIM status...")
    blank_count = 0
    
    for _ in range(5):
        resp = send_at(ser, "AT+CPIN?", 2)

        if "READY" in resp:
            print("✅ SIM READY")
            return True

        if not resp.strip():
            blank_count += 1
            if blank_count >= 4:
                raise serial.SerialException("Baseband silent (SIM). Forcing reboot.")
        else:
            blank_count = 0

        print("⚠️ SIM not ready")
        time.sleep(10)
        
    return False

def ensure_network(ser):
    print("📶 Checking network registration...")
    blank_count = 0
    csq = send_at(ser, "AT+CSQ", 2)
    
    for attempt in range(5):
        print(f"📡 Registration check {attempt + 1}/5")
        resp = send_at(ser, "AT+CREG?", 3)

        if ",1" in resp or ",5" in resp or ",6" in resp:
            send_at(ser, "AT+COPS?", 2)
            print("✅ Network registered")
            return True

        if not resp.strip():
            blank_count += 1
            if blank_count >= 4:
                raise serial.SerialException("Baseband silent (Network). Forcing reboot.")
        else:
            blank_count = 0

        print("⏳ Network not ready yet")
        time.sleep(10)

    return False

def ensure_pdp(ser, retry_count=[0]):
    print("🌐 Checking PDP context...")
    blank_count = 0
    
    for _ in range(3):
        resp = send_at(ser, "AT+CGACT?", 3)
        if "1,1" in resp:
            print("✅ PDP already active")
            return True

        if not resp.strip():
            blank_count += 1
            if blank_count >= 3:
                raise serial.SerialException("Baseband silent (PDP). Forcing reboot.")
        else:
            blank_count = 0

        print("🚀 Activating PDP context...")
        cops = send_at(ser, "AT+COPS?", 3).lower()
        
        apn = "internet"
        if "jio" in cops:
            apn = "jionet"
        elif "airtel" in cops:
            apn = "airtelgprs.com" if retry_count[0] % 2 == 0 else "iot.airtel.com"
            retry_count[0] += 1
            print(f"🔄 Airtel APN Toggler active. Attempt {retry_count[0]}")
        elif "vi" in cops or "vodafone" in cops:
            apn = "www"
        elif "bsnl" in cops:
            apn = "bsnlnet"

        print(f"⚙️ Selected APN: {apn}")
        send_at(ser, f'AT+CGDCONT=1,"IP","{apn}"', 2)
        
        act_resp = send_at(ser, "AT+CGACT=1,1", 8)

        if "ERROR" not in act_resp and act_resp.strip():
            print("⏳ Settling LTE stack...")
            time.sleep(5)
            return True
            
        time.sleep(10)
        
    print("❌ PDP activation failed")
    return False

def ensure_gnss(ser):
    print("🛰️ Verifying GNSS (GPS) Engine...")
    resp = send_at(ser, "AT+CGNSSPWR?", 2)
    
    if "+CGNSSPWR: 1" not in resp:
        print("⚙️ Booting up GNSS Receiver...")
        send_at(ser, "AT+CGNSSPWR=1", 2)
        time.sleep(5)
    else:
        print("✅ GNSS Engine is active")
    return True

def get_gps_location(ser):
    resp = send_at(ser, "AT+CGNSSINFO", 2)
    
    if "+CGNSSINFO: 2," in resp or "+CGNSSINFO: 3," in resp:
        try:
            raw_data = resp.split("+CGNSSINFO: ")[1].split(",")
            lat = raw_data[5]
            lat_dir = raw_data[6]
            lon = raw_data[7]
            lon_dir = raw_data[8]
            alt = raw_data[11]
            return f"Lat:{lat}{lat_dir}, Lon:{lon}{lon_dir}, Alt:{alt}m"
        except Exception:
            pass
            
    return "Searching for Satellites..."

def build_mqtt_from_scratch(ser):
    print("🧹 Wiping previous MQTT states...")
    send_at(ser, "AT+CMQTTDISC=0,60", 1)
    time.sleep(5)
    send_at(ser, "AT+CMQTTREL=0", 1)
    time.sleep(5)
    send_at(ser, "AT+CMQTTSTOP", 1)
    time.sleep(5)

    print("🔌 Booting MQTT Service...")
    send_at(ser, "AT+CMQTTSTART", 2)
    
    client_id = f"pi_hat_{int(time.time())}"
    send_at(ser, f'AT+CMQTTACCQ=0,"{client_id}"', 2)
    
    print("⏳ Connecting to Broker...")
    conn_resp = send_at(ser, f'AT+CMQTTCONNECT=0,"{MQTT_BROKER}",60,1', 5)
    
    if "ERROR" in conn_resp:
        return False
    return True

def ensure_mqtt(ser, force_rebuild=False):
    if force_rebuild:
        return build_mqtt_from_scratch(ser)

    resp = send_at(ser, "AT+CMQTTCONNECT?", 2)
    
    if "+CMQTTCONNECT: 0\r" in resp or "+CMQTTCONNECT: 0\n" in resp or "ERROR" in resp:
        print("⚠️ MQTT disconnected! Rebuilding session...")
        return build_mqtt_from_scratch(ser)
        
    print("✅ MQTT Connection stable")
    return True

# ==========================================================
# MAIN APPLICATION
# ==========================================================

def main():
    setup_hardware()
    rtc_set_system_time()
    power_on_modem()

    print("\nSelect Communication Mode:")
    print("1. UART")
    print("2. Type-C USB")

    choice = input("\nEnter option: ").strip()

    if choice == "1":
        print("🔗 Using UART mode")
        try:
            ser = serial.Serial(UART_PORT, BAUD_RATE, timeout=1)
        except serial.SerialException as e:
            print(f"❌ UART open failed: {e}")
            return
    elif choice == "2":
        print("🔗 Using Type-C USB mode")
        ser, current_port = auto_find_usb_port()
    else:
        print("❌ Invalid option")
        return

    send_at(ser, "AT+CMQTTDISC=0,60", 1)
    time.sleep(5)
    send_at(ser, "AT+CMQTTREL=0", 1)
    time.sleep(5)
    send_at(ser, "AT+CMQTTSTOP", 1)
    time.sleep(5)

    state = "SIM"
    msg_count = 1
    force_mqtt_rebuild = True

    while True:
        try:
            print("\n====== MAIN LOOP ======")

            resp = send_at(ser, "AT", 1)
            if not resp:
                raise serial.SerialException("AT response timeout. Modem unresponsive.")

            # STATE MACHINE
            if state == "SIM":
                if ensure_sim(ser):
                    state = "NETWORK"

            elif state == "NETWORK":
                if ensure_network(ser):
                    state = "PDP"

            elif state == "PDP":
                if ensure_pdp(ser):
                    state = "GNSS"

            elif state == "GNSS":
                if ensure_gnss(ser):
                    state = "MQTT"

            elif state == "MQTT":
                if ensure_mqtt(ser, force_rebuild=force_mqtt_rebuild):
                    force_mqtt_rebuild = False
                    state = "PUBLISH"
                else:
                    force_mqtt_rebuild = True
                    time.sleep(3)

            elif state == "PUBLISH":
                timestamp = rtc_get_time()
                gps_data = get_gps_location(ser)
                
                payload = f"[{timestamp}] SIMCom A7672S | {gps_data} | Msg #{msg_count}"
                
                print(f"📡 Publishing MQTT message #{msg_count}")
                
                topic_cmd = f"AT+CMQTTTOPIC=0,{len(MQTT_TOPIC)}"
                send_mqtt_payload(ser, topic_cmd, MQTT_TOPIC)
                
                payload_cmd = f"AT+CMQTTPAYLOAD=0,{len(payload)}"
                send_mqtt_payload(ser, payload_cmd, payload)
                
                resp = send_at(ser, "AT+CMQTTPUB=0,0,60", 3)

                if "ERROR" in resp:
                    print("⚠️ MQTT publish failed")
                    force_mqtt_rebuild = True
                    state = "MQTT"
                else:
                    print("✅ MQTT publish successful")
                    msg_count += 1

                time.sleep(10)

        # ==================================================
        # HARDWARE RECOVERY BLOCK
        # ==================================================
        except Exception as e:
            print(f"\n❌ CRITICAL ERROR: {e}")
            try:
                ser.close()
            except:
                pass

            if choice == "2":
                print("🔌 Initiating Hardware Recovery...")
                hardware_reset(choice)
            else:
                print("⏳ Waiting for UART recovery...")
                time.sleep(5)

            if choice == "1":
                try:
                    ser = serial.Serial(UART_PORT, BAUD_RATE, timeout=1)
                    print("✅ UART reconnected")
                except serial.SerialException as err:
                    print(f"❌ UART reconnect failed: {err}")
                    time.sleep(5)
                    continue
            else:
                ser, current_port = auto_find_usb_port()

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
