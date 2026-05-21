# ==========================================================
# QUECTEL EC200U "FULL AUTO WITH ESIM" FRAMEWORK
# ==========================================================
# Features:
# - STMicro eSIM MUX Routing (GPIO 26 locked HIGH)
# - UART / Type-C USB selection
# - Automatic SIM & network detection
# - PDP auto activation (Truphone APN)
# - MQTT communication
# - RTC timestamp integration (DS3231)
# - USB reconnect recovery
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

def rtc_set_system_time():
    try:
        bus = smbus2.SMBus(I2C_BUS)
        now = datetime.now()
        rtc_data = [
            dec_to_bcd(now.second), dec_to_bcd(now.minute), dec_to_bcd(now.hour),
            dec_to_bcd(now.isoweekday()), dec_to_bcd(now.day), dec_to_bcd(now.month),
            dec_to_bcd(now.year - 2000)
        ]
        bus.write_i2c_block_data(DS3231_ADDR, 0x00, rtc_data)
        bus.close()
        print("🕒 RTC synchronized with system time")
    except Exception as e:
        print(f"⚠️ RTC synchronization failed: {e}")

def rtc_get_time():
    try:
        bus = smbus2.SMBus(I2C_BUS)
        data = bus.read_i2c_block_data(DS3231_ADDR, 0x00, 7)
        bus.close()
        second = bcd_to_dec(data[0])
        minute = bcd_to_dec(data[1])
        hour = bcd_to_dec(data[2] & 0x3F)
        day = bcd_to_dec(data[4])
        month = bcd_to_dec(data[5] & 0x1F)
        year = bcd_to_dec(data[6]) + 2000
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

    GPIO.setup(EN_PIN, GPIO.OUT)
    GPIO.setup(PWR_PIN, GPIO.OUT)
    GPIO.setup(RST_PIN, GPIO.OUT)
    GPIO.setup(SIM_SEL_PIN, GPIO.OUT)

    GPIO.output(EN_PIN, GPIO.LOW)
    GPIO.output(PWR_PIN, GPIO.LOW)
    GPIO.output(RST_PIN, GPIO.HIGH)
    
    # HARDCODED: Select internal eSIM pathway directly before boot
    GPIO.output(SIM_SEL_PIN, GPIO.HIGH)
    time.sleep(0.5)

def power_on_modem():
    print("⚡ Powering on EC200U module (eSIM)...")
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
    
    print("⏳ Waiting for modem reboot (20s)...")
    time.sleep(20)
    
    GPIO.output(SIM_SEL_PIN, GPIO.HIGH) # Re-assert eSIM MUX
    print("⏳ Stabilizing interfaces...")
    time.sleep(5)
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
                if "OK" in response or "ERROR" in response or "+CME ERROR" in response: 
                    break
            time.sleep(0.1)
            
        print(f"[MODEM] <-- {response.strip()}\n")
        
        time.sleep(2.0) # ADDED DELAY: Mandatory buffer cooldown after every AT command
        
        return response
    except Exception as e:
        raise serial.SerialException(f"Connection lost: {e}")

def auto_find_usb_port():
    print("🔍 Detecting EC200 USB AT port...")
    while True:
        ports_to_test = [f"/dev/ttyUSB{i}" for i in range(7)]
        for port in ports_to_test:
            if os.path.exists(port):
                try:
                    with serial.Serial(port, BAUD_RATE, timeout=1) as ser:
                        ser.write(b"AT\r\n")
                        time.sleep(0.5)
                        if "OK" in ser.read_all().decode(errors='ignore'):
                            print(f"✅ EC200 AT PORT LOCKED: {port}")
                            time.sleep(2)
                            final_ser = serial.Serial(port, BAUD_RATE, timeout=1)
                            return final_ser, port
                except: pass
        print("⚠️ Waiting for USB ports to appear...")
        time.sleep(3)

# ==========================================================
# STATE FUNCTIONS
# ==========================================================

def ensure_sim(ser):
    print("🔍 Checking eSIM status...")
    while True:
        resp = send_at(ser, "AT+CPIN?", 2)
        if "+CPIN: READY" in resp: 
            print("✅ eSIM READY")
            return True
        print("⚠️ eSIM not ready. Waiting...")
        time.sleep(3)

def ensure_network(ser):
    print("📶 Checking network registration...")
    for attempt in range(10):
        print(f"📡 Registration check {attempt + 1}/10")
        resp = send_at(ser, "AT+CREG?", 5)
        if ",1" in resp or ",5" in resp: 
            print("✅ Network registered")
            return True
        time.sleep(5)
    print("❌ Network registration timeout")
    return False

def ensure_pdp(ser):
    print("🌐 Preparing PDP context (eSIM)...")
    if "+CGATT: 1" not in send_at(ser, "AT+CGATT?", 3): 
        print("⚠️ LTE attach not ready")
        return False
        
    send_at(ser, "AT+QIDEACT=1", 5)
    time.sleep(3) # ADDED DELAY: Let deactivate finish
    
    print("⚙️ Setting Truphone APN...")
    send_at(ser, 'AT+QICSGP=1,1,"iot.truphone.com","","",1', 5)
    
    print("🚀 Activating PDP context...")
    if "OK" not in send_at(ser, "AT+QIACT=1", 15): 
        print("❌ PDP activation failed")
        return False
        
    print("⏳ Settling LTE stack...")
    time.sleep(10) # ADDED DELAY: Let LTE stack stabilize
    
    is_active = "+QIACT:" in send_at(ser, "AT+QIACT?", 5)
    if is_active: print("✅ PDP context active")
    return is_active

def ensure_mqtt(ser):
    while True:
        resp = send_at(ser, "AT+QMTCONN?", 3)
        if ",3" in resp or "0,0,0" in resp: 
            print("✅ MQTT connected")
            return True
            
        print("🔌 Connecting MQTT...")
        send_at(ser, "AT+QMTDISC=0", 2)
        send_at(ser, "AT+QMTCLOSE=0", 2)
        time.sleep(3) # ADDED DELAY: Clear old sockets
        
        send_at(ser, 'AT+QMTCFG="recv/mode",0,0,1', 1)
        send_at(ser, f'AT+QMTOPEN=0,"{MQTT_BROKER}",{MQTT_PORT}', 5)
        
        print("⏳ Waiting for TCP socket to open...")
        time.sleep(4) # ADDED DELAY: TCP establish
        
        client_id = f"pi_esim_{int(time.time())}"
        send_at(ser, f'AT+QMTCONN=0,"{client_id}"', 5)
        
        print("⏳ Waiting for MQTT negotiation...")
        time.sleep(5)

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
                raise serial.SerialException("AT timeout")

            if state == "SIM" and ensure_sim(ser): 
                state = "NETWORK"
            elif state == "NETWORK" and ensure_network(ser): 
                state = "PDP"
            elif state == "PDP" and ensure_pdp(ser): 
                state = "MQTT"
            elif state == "MQTT" and ensure_mqtt(ser): 
                state = "PUBLISH"
            elif state == "PUBLISH":
                timestamp = rtc_get_time()
                payload = f"[{timestamp}] EC200U eSIM | Msg #{msg_count}"
                
                print(f"📡 Publishing MQTT message #{msg_count}")
                resp = send_at(ser, f'AT+QMTPUB=0,0,0,0,"{MQTT_TOPIC}","{payload}"', 5)
                
                if "ERROR" in resp or not resp: 
                    print("⚠️ MQTT publish failed")
                    state = "MQTT"
                else: 
                    print("✅ MQTT publish successful")
                    msg_count += 1
                    
                time.sleep(10) # ADDED DELAY: Loop stability
                
        except Exception as e:
            print(f"\n❌ CRITICAL ERROR: {e}")
            try: ser.close()
            except: pass
            
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
