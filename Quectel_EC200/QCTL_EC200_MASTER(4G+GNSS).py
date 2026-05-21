# ==========================================================
# QUECTEL EC200U "FULL AUTO WITH GNSS ENABLED" FRAMEWORK
# ==========================================================
# Features:
# - Physical SIM MUX Routing (GPIO 26 locked LOW)
# - GNSS (GPS) Engine Initialization & Parsing
# - UART / Type-C USB selection menu
# - Automatic SIM & network detection
# - PDP auto activation (Dynamic APN)
# - MQTT communication
# - RTC timestamp integration (DS3231)
# - Hardware reset recovery
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
SIM_SEL_PIN = 26

# ==========================================================
# SERIAL & I2C CONFIGURATION
# ==========================================================

UART_PORT = '/dev/serial0'
BAUD_RATE = 115200

I2C_BUS = 1
DS3231_ADDR = 0x68

MQTT_BROKER = "broker.emqx.io"
MQTT_PORT = 1883
MQTT_TOPIC = "pihat1"

# ==========================================================
# RTC FUNCTIONS
# ==========================================================

def dec_to_bcd(val): 
    return (val // 10 << 4) + (val % 10)

def bcd_to_dec(val): 
    return ((val >> 4) * 10) + (val & 0x0F)

def rtc_get_time():
    try:
        bus = smbus2.SMBus(I2C_BUS)
        data = bus.read_i2c_block_data(DS3231_ADDR, 0x00, 7)
        bus.close()
        year = bcd_to_dec(data[6]) + 2000
        month = bcd_to_dec(data[5] & 0x1F)
        day = bcd_to_dec(data[4])
        hour = bcd_to_dec(data[2] & 0x3F)
        minute = bcd_to_dec(data[1])
        second = bcd_to_dec(data[0])
        return f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"
    except: 
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
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)
        
    GPIO.output(RST_PIN, GPIO.HIGH)
    GPIO.output(SIM_SEL_PIN, GPIO.LOW) # Force Physical SIM
    time.sleep(0.5)

def power_on_modem():
    print("⚡ Powering on EC200U module (GNSS Mode)...")
    GPIO.output(EN_PIN, GPIO.HIGH)
    time.sleep(1)
    
    print("🔘 Sending PWRKEY pulse...")
    GPIO.output(PWR_PIN, GPIO.HIGH)
    time.sleep(1.2)
    GPIO.output(PWR_PIN, GPIO.LOW)
    
    print("⏳ Waiting for modem boot sequence (15s)...")
    time.sleep(15)
    print("✅ MODEM INITIALIZATION COMPLETE\n")

def hardware_reset(choice):
    print("\n🔄 ENTERING FULL MODEM REBOOT STATE")
    try:
        if 'ser' in globals(): ser.close()
    except: pass
    
    print("⚡ Resetting EC200U module...")
    GPIO.output(RST_PIN, GPIO.LOW)
    time.sleep(0.8)
    GPIO.output(RST_PIN, GPIO.HIGH)
    
    print("⏳ Waiting for modem reboot (25s)...")
    time.sleep(25)
    
    GPIO.output(SIM_SEL_PIN, GPIO.LOW)
    print("✅ MODEM REBOOT COMPLETE\n")

# ==========================================================
# SERIAL COMMUNICATION
# ==========================================================

def send_at(ser, command, timeout=3):
    try:
        print(f"[MODEM] --> {command}")
        ser.reset_input_buffer()
        ser.write((command + '\r\n').encode())
        
        response = ""
        start = time.time()
        
        while time.time() - start < timeout:
            if ser.in_waiting:
                response += ser.read(ser.in_waiting).decode(errors='ignore')
                if "OK" in response or "ERROR" in response: 
                    break
            time.sleep(0.1)
            
        print(f"[MODEM] <-- {response.strip()}\n")
        
        # --- THE MASTER READING FIX ---
        time.sleep(2.0) # Increased to standard 2.0s to match global Quectel cooldown
        
        return response
    except Exception as e: 
        raise serial.SerialException(e)

def auto_find_usb_port():
    print("🔍 Detecting EC200 USB AT port...")
    while True:
        for i in range(7):
            port = f"/dev/ttyUSB{i}"
            if os.path.exists(port):
                try:
                    with serial.Serial(port, BAUD_RATE, timeout=1) as s:
                        s.write(b"AT\r\n")
                        time.sleep(0.5)
                        if "OK" in s.read_all().decode(errors='ignore'):
                            print(f"✅ EC200 AT PORT LOCKED: {port}")
                            time.sleep(2)
                            return serial.Serial(port, BAUD_RATE, timeout=1), port
                except: pass
        print("⚠️ Waiting for USB ports to appear...")
        time.sleep(2)

# ==========================================================
# STATE FUNCTIONS
# ==========================================================

def ensure_gnss(ser):
    print("🛰️ Powering on Quectel GNSS Engine...")
    send_at(ser, "AT+QGPS=1", 3)
    return True

def get_quectel_gps(ser):
    print("🛰️ Polling Satellite Data...")
    resp = send_at(ser, "AT+QGPSLOC?", 3)
    
    if "+QGPSLOC:" in resp:
        try:
            p = resp.split("+QGPSLOC:")[1].split(",")
            return f"Lat:{p[1]}, Lon:{p[2]}, Alt:{p[3]}m"
        except: pass
        
    return "GPS Searching..."

def ensure_pdp(ser):
    print("🌐 Preparing PDP context...")
    if "+CGATT: 1" not in send_at(ser, "AT+CGATT?", 3): 
        print("⚠️ LTE attach not ready")
        return False
        
    cops = send_at(ser, "AT+COPS?", 3).lower()
    
    apn = "internet"
    if "jio" in cops: apn = "jionet"
    elif "airtel" in cops: apn = "airtelgprs.com"
    elif "vi" in cops: apn = "www"
    
    print(f"⚙️ Selected APN: {apn}")
    
    send_at(ser, "AT+QIDEACT=1", 5)
    time.sleep(3) # ADDED DELAY: Let deactivate finish
    
    send_at(ser, f'AT+QICSGP=1,1,"{apn}","","",1', 5)
    
    print("🚀 Activating PDP context...")
    if "OK" in send_at(ser, "AT+QIACT=1", 15):
        print("⏳ Settling LTE stack...")
        time.sleep(10) # ADDED DELAY: Let LTE stack stabilize
        return True
        
    print("❌ PDP activation failed")
    return False

def ensure_mqtt(ser):
    print("🔌 Connecting MQTT...")
    send_at(ser, "AT+QMTDISC=0", 2)
    time.sleep(3) # ADDED DELAY: Clear old sockets
    
    send_at(ser, f'AT+QMTOPEN=0,"{MQTT_BROKER}",{MQTT_PORT}', 5)
    
    print("⏳ Waiting for TCP socket to open...")
    time.sleep(4) # ADDED DELAY: TCP establish
    
    client_id = f"pi_gps_{int(time.time())}"
    if "OK" in send_at(ser, f'AT+QMTCONN=0,"{client_id}"', 5):
        print("⏳ Waiting for MQTT negotiation...")
        time.sleep(5) # ADDED DELAY: MQTT Handshake
        return True
        
    return False

# ==========================================================
# MAIN APPLICATION
# ==========================================================

def main():
    setup_hardware()
    power_on_modem()
    
    print("\nSelect Communication Mode:")
    print("1. UART")
    print("2. Type-C USB")
    choice = input("\nEnter option: ").strip()
    
    if choice == "1":
        print("🔗 Initializing UART...")
        ser = serial.Serial(UART_PORT, BAUD_RATE, timeout=1)
    else:
        print("🔗 Initializing Type-C USB...")
        ser, current_port = auto_find_usb_port()
        
    state = "SIM"
    msg_count = 1
    
    while True:
        try:
            print("\n====== MAIN LOOP ======")
            if "OK" not in send_at(ser, "AT", 1): 
                raise serial.SerialException("Dead Link")
                
            if state == "SIM":
                print("🔍 Checking SIM status...")
                if "+CPIN: READY" in send_at(ser, "AT+CPIN?", 2): 
                    print("✅ SIM READY")
                    state = "NETWORK"
                    
            elif state == "NETWORK":
                print("📶 Checking network registration...")
                resp = send_at(ser, "AT+CREG?", 3)
                if ",1" in resp or ",5" in resp: 
                    print("✅ Network registered")
                    state = "GNSS"
                    
            elif state == "GNSS":
                if ensure_gnss(ser): 
                    state = "PDP"
                    
            elif state == "PDP":
                if ensure_pdp(ser): 
                    state = "MQTT"
                    
            elif state == "MQTT":
                if ensure_mqtt(ser): 
                    state = "PUBLISH"
                    
            elif state == "PUBLISH":
                ts = rtc_get_time()
                coords = get_quectel_gps(ser)
                
                payload = f"[{ts}] EC200U GPS | {coords} | Msg #{msg_count}"
                
                print(f"📡 Publishing MQTT message #{msg_count}")
                resp = send_at(ser, f'AT+QMTPUB=0,0,0,0,"{MQTT_TOPIC}","{payload}"', 5)
                
                if "ERROR" in resp or not resp: 
                    print("⚠️ MQTT publish failed")
                    state = "MQTT"
                else: 
                    print("✅ MQTT publish successful")
                    msg_count += 1
                    
                time.sleep(10) # Loop stability delay
                
        except Exception as e:
            print(f"\n❌ CRITICAL ERROR: {e}")
            hardware_reset(choice)
            
            if choice == "1": 
                ser = serial.Serial(UART_PORT, BAUD_RATE, timeout=1)
            else: 
                ser, current_port = auto_find_usb_port()
                
            state = "SIM"

if __name__ == "__main__": 
    try:
        main()
    except KeyboardInterrupt:
        print("\n🛑 Stopped by user")
    finally:
        GPIO.cleanup()
        print("🧹 Cleanup complete")
