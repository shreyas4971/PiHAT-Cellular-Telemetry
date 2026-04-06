import serial
import RPi.GPIO as GPIO
import time
import os
import smbus2

# ================= HARDWARE CONFIGURATION =================
# GPIO Pin Definitions (BCM Mode)
EN_PIN = 22       
PWR_PIN = 17      
RST_PIN = 27
SIM_SEL_PIN = 26  

# USB & AT Command Configuration
BAUD_RATE = 115200

# DS3231 RTC Configuration
I2C_BUS = 1
DS3231_ADDR = 0x68

# ================= RTC FUNCTIONS =================
def bcd_to_dec(bcd):
    """Converts Binary-Coded Decimal to standard Decimal."""
    return (bcd // 16 * 10) + (bcd % 16)

def get_hardware_time():
    """Reads raw registers from the DS3231 and formats a timestamp."""
    try:
        bus = smbus2.SMBus(I2C_BUS)
        # Read 7 bytes starting from register 0x00
        data = bus.read_i2c_block_data(DS3231_ADDR, 0x00, 7)
        bus.close()

        # Decode the raw bytes
        second = bcd_to_dec(data[0])
        minute = bcd_to_dec(data[1])
        hour = bcd_to_dec(data[2] & 0x3F) # Bitmask to handle 24hr format
        day = bcd_to_dec(data[4])
        month = bcd_to_dec(data[5] & 0x1F) # Bitmask for century bit
        year = bcd_to_dec(data[6]) + 2000

        return f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"
    
    except Exception as e:
        print(f"⚠️ [RTC] Failed to read DS3231: {e}")
        # Fallback to system time if the I2C wire gets disconnected
        return time.strftime('%Y-%m-%d %H:%M:%S')

# ================= MODEM HARDWARE INITIALIZATION =================
def setup_hardware():
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    
    for pin in [EN_PIN, PWR_PIN, RST_PIN, SIM_SEL_PIN]:
        GPIO.setup(pin, GPIO.OUT)
        GPIO.output(pin, GPIO.LOW)
    
    print("⚡ Powering on the EC200U module...")
    GPIO.output(EN_PIN, GPIO.HIGH)
    time.sleep(1)
    
    GPIO.output(SIM_SEL_PIN, GPIO.LOW)
    
    GPIO.output(PWR_PIN, GPIO.HIGH)
    time.sleep(1.2)
    GPIO.output(PWR_PIN, GPIO.LOW)
    
    print("⏳ Waiting 15 seconds for the modem to fully boot up...")
    time.sleep(15)

def auto_find_and_open_port():
    """Sweeps ports automatically and strictly verifies the AT command line."""
    print("🔍 Scanning for active Type-C AT command port...")
    
    while True:
        ports_to_test = [f"/dev/ttyUSB{i}" for i in range(7)]
        
        if not any(os.path.exists(p) for p in ports_to_test):
            time.sleep(2)
            continue
            
        for port in ports_to_test:
            if os.path.exists(port):
                try:
                    with serial.Serial(port, BAUD_RATE, timeout=1) as ser:
                        
                        # 1. Flush Linux OS Buffers
                        ser.reset_input_buffer()
                        ser.reset_output_buffer()
                        
                        # 2. Auto-Baud Sync Sequence
                        for _ in range(3):
                            ser.write(b"AT\r\n")
                            time.sleep(0.1)
                        
                        response = ""
                        while ser.in_waiting > 0:
                            response += ser.read(ser.in_waiting).decode(errors='ignore')
                            time.sleep(0.05)
                        
                        # THE FIX: Split the response into a list of exact words.
                        # This prevents false positives from massive diagnostic logs.
                        if "OK" in response.split():
                            print(f"✅ AT COMMAND PORT SECURED & SYNCED: {port}")
                            time.sleep(1) 
                            
                            final_ser = serial.Serial(port, BAUD_RATE, timeout=1)
                            final_ser.reset_input_buffer()
                            return final_ser, port
                            
                except serial.SerialException:
                    pass 
                    
        print("⚠️ Waiting for modem to synchronize AT queries...")
        time.sleep(3)

def send_at(ser, command, wait_time=1.0):
    try:
        print(f"[USB] --> {command}")
        ser.write((command + '\r\n').encode('utf-8'))
        time.sleep(wait_time)

        response = ""
        while ser.in_waiting > 0:
            response += ser.read(ser.in_waiting).decode(errors='ignore')
            time.sleep(0.1)

        print(f"[USB] <-- {response.strip()}\n")
        return response
    except OSError as e:
        raise serial.SerialException(f"Hardware abruptly disconnected: {e}")

# ================= SEQUENTIAL BLOCKING CHECKS =================
def ensure_sim(ser):
    print("🔍 [USB] Checking SIM card status...")
    while True:
        resp = send_at(ser, "AT+CPIN?", 2)
        if "READY" in resp:
            print("✅ [USB] SIM is READY!")
            break
        print("⚠️ [USB] SIM not ready. Retrying in 2 seconds...")
        time.sleep(2)

def ensure_network(ser):
    print("🔍 [USB] Checking auto-negotiated network registration...")
    while True:
        send_at(ser, "AT+CSQ", 1) 
        resp = send_at(ser, "AT+CREG?", 2)
        
        if ",1" in resp or ",5" in resp or ",6" in resp:
            print("✅ [USB] Network registered!")
            send_at(ser, "AT+COPS?", 1)
            break
            
        print("⚠️ [USB] Not registered. Retrying in 3 seconds...")
        time.sleep(3)

def ensure_pdp(ser):
    while True:
        resp = send_at(ser, "AT+QIACT?", 2)
        if "1,1,1" in resp or ('1,1' in resp and '"' in resp):
            print("✅ [USB] PDP Context Active!")
            break
            
        print("🌐 [USB] Activating PDP Context...")
        cops_resp = send_at(ser, "AT+COPS?", 2).lower()
        apn = "internet" 
        
        if "jio" in cops_resp:
            apn = "jionet"
        elif "airtel" in cops_resp:
            apn = "airtelgprs.com"
        elif "vi" in cops_resp or "vodafone" in cops_resp or "idea" in cops_resp:
            apn = "www"
        elif "bsnl" in cops_resp:
            apn = "bsnlnet"
            
        print(f"⚙️ [USB] Auto-selected APN: {apn}")
        send_at(ser, f'AT+QICSGP=1,1,"{apn}","","",1', 2)
        send_at(ser, "AT+QIACT=1", 5)
        time.sleep(3)

def ensure_mqtt(ser):
    while True:
        resp = send_at(ser, "AT+QMTCONN?", 2)
        if ",3" in resp:
            print("✅ [USB] MQTT Connected!")
            break
            
        print("🔌 [USB] Connecting MQTT...")
        send_at(ser, 'AT+QMTOPEN=0,"broker.emqx.io",1883', 5)
        
        client_id = f"pi_hat_{int(time.time())}"
        send_at(ser, f'AT+QMTCONN=0,"{client_id}"', 4)
        time.sleep(3)

# ================= MAIN LOOP =================
def main():
    setup_hardware()
    ser_usb = None
    current_usb_port = None

    try:
        ser_usb, current_usb_port = auto_find_and_open_port()
        msg_count = 1

        while True:
            print("\n====== LOOP START ======")

            try:
                # 1. ZOMBIE PORT CHECK
                resp = send_at(ser_usb, "AT", 1)
                if not resp:
                    raise serial.SerialException("Modem stopped responding (Zombie Port).")

                # 2. Safety Check: Did the dynamic port vanish?
                if not os.path.exists(current_usb_port):
                    raise OSError("Port physically deleted by OS.")

                # 3. The Staircase
                ensure_sim(ser_usb)
                ensure_network(ser_usb)
                ensure_pdp(ser_usb)
                ensure_mqtt(ser_usb)

                # 4. Fetch precise hardware time from DS3231
                rtc_timestamp = get_hardware_time()

                # 5. Publish
                payload = f"[{rtc_timestamp}] EC200 Auto-Detect OK #{msg_count}"
                topic = "pihat1"

                print(f"📡 [USB] Publishing message #{msg_count}...")
                cmd = f'AT+QMTPUB=0,0,0,0,"{topic}","{payload}"'
                resp = send_at(ser_usb, cmd, 2)

                if "ERROR" in resp:
                    print("⚠️ [USB] Publish failed → forcing socket disconnect")
                    send_at(ser_usb, "AT+QMTDISC=0", 2)
                else:
                    msg_count += 1

                time.sleep(5)

            # --- THE RECOVERY BLOCK ---
            except (serial.SerialException, OSError) as e:
                print(f"\n❌ [CRITICAL ERROR] Connection lost! ({e})")
                print("🔄 Releasing dead port and waiting for modem to reboot...")
                
                if ser_usb:
                    try:
                        ser_usb.close()
                    except:
                        pass
                
                if current_usb_port:
                    while os.path.exists(current_usb_port):
                        time.sleep(1)
                else:
                    time.sleep(5)
                
                print("✅ Port cleared! Hunting for new AT connection...")
                ser_usb, current_usb_port = auto_find_and_open_port()

    except KeyboardInterrupt:
        print("\nStopped by user")

    finally:
        if 'ser_usb' in locals() and ser_usb and getattr(ser_usb, 'is_open', False):
            print("Cleaning up USB data connections...")
            try:
                send_at(ser_usb, "AT+QMTDISC=0", 2)
                send_at(ser_usb, "AT+QIDEACT=1", 2)
            except:
                pass
            ser_usb.close()

        GPIO.cleanup()
        print("Cleanup done")

if __name__ == '__main__':
    main()
