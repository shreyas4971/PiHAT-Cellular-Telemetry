import serial
import RPi.GPIO as GPIO
import time
import os
import smbus2

# ================= HARDWARE CONFIGURATION =================
EN_PIN = 22      
PWR_PIN = 17      
RST_PIN = 27      
SIM_SEL_PIN = 5  

BAUD_RATE = 115200

# DS3231 RTC Configuration
I2C_BUS = 1
DS3231_ADDR = 0x68

# ================= RTC FUNCTIONS =================
def bcd_to_dec(bcd):
    return (bcd // 16 * 10) + (bcd % 16)

def get_hardware_time():
    """Reads registers from DS3231 with safety 'with' block."""
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
                print(f"⚠️ [RTC] Failed to read DS3231: {e}")
                return time.strftime('%Y-%m-%d %H:%M:%S')

# ================= MODEM HARDWARE INITIALIZATION =================
def setup_hardware():
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)

    for pin in [EN_PIN, PWR_PIN, SIM_SEL_PIN]:
        GPIO.setup(pin, GPIO.OUT)
        GPIO.output(pin, GPIO.LOW)

    # Initialize RST to HIGH (Active-LOW circuit)
    GPIO.setup(RST_PIN, GPIO.OUT)
    GPIO.output(RST_PIN, GPIO.HIGH)

    print("🧹 Forcing an Active-LOW hardware reset on the Simcom module...")
   
    # 1. Main power ON
    GPIO.output(EN_PIN, GPIO.HIGH)
    time.sleep(1)
   
    # 2. Select Physical SIM
    GPIO.output(SIM_SEL_PIN, GPIO.LOW)  

    # 3. Active-LOW Reset Pulse
    print("⚡ Pulling RST_PIN (GPIO 27) LOW to simulate button press...")
    GPIO.output(RST_PIN, GPIO.LOW)
    time.sleep(0.5)
    GPIO.output(RST_PIN, GPIO.HIGH)
   
    time.sleep(1)

    # 4. Power-On Pulse
    print("⚡ Pulsing PWR_PIN (GPIO 17) to boot...")
    GPIO.output(PWR_PIN, GPIO.HIGH)
    time.sleep(1.2)
    GPIO.output(PWR_PIN, GPIO.LOW)

    print("⏳ Waiting 25 seconds for modem to fully boot...")
    time.sleep(25)

def auto_find_and_open_port():
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
                            print(f"✅ AT COMMAND PORT SECURED: {port}")
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

def send_mqtt_payload(ser, command, text):
    try:
        print(f"[USB] --> {command} (Waiting for prompt...)")
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
            print(f"[USB] --> Sending payload: {text}")
            ser.write((text + '\r\n').encode('utf-8'))
            time.sleep(1.0)
           
            while ser.in_waiting > 0:
                response += ser.read(ser.in_waiting).decode(errors='ignore')
                time.sleep(0.1)
        else:
            print("⚠️ [USB] Timeout waiting for '>' prompt from modem!")
           
        print(f"[USB] <-- {response.strip()}\n")
        return response
    except OSError as e:
         raise serial.SerialException(f"Hardware abruptly disconnected: {e}")

# ================= GNSS FUNCTIONS FOR A7672S =================
def ensure_gnss(ser):
    print("🛰️ [USB] Initializing A7672S GNSS Engine...")
   
    # Correct command for A7672S is AT+CGNSSPWR
    resp = send_at(ser, "AT+CGNSSPWR?", 1)
    if "+CGNSSPWR: 1" not in resp:
        # Expected response: OK and eventually +CGNSSPWR: READY!
        send_at(ser, "AT+CGNSSPWR=1", 2)
    else:
        print("✅ [USB] GNSS is already active.")

def get_gps_location(ser):
    # Correct command for A7672S is AT+CGNSSINFO
    resp = send_at(ser, "AT+CGNSSINFO", 1)
   
    # Typical A7672S response: +CGNSSINFO: <mode>,<gps_sv>,<glo_sv>,...,<lat>,<N/S>,<lon>,<E/W>...
    # Mode 2 (2D) or 3 (3D) indicates a valid fix.
    if "+CGNSSINFO: 2," in resp or "+CGNSSINFO: 3," in resp:
        try:
            # Split and clean the response
            raw_data = resp.split("+CGNSSINFO: ")[1].split(",")
            # Fields for A7672S: Lat is at index 5, Lon at index 7, Alt at index 11
            lat = raw_data[5]
            lat_dir = raw_data[6]
            lon = raw_data[7]
            lon_dir = raw_data[8]
            alt = raw_data[11]
            return f"Lat:{lat}{lat_dir}, Lon:{lon}{lon_dir}, Alt:{alt}m"
        except (IndexError, ValueError):
            pass
           
    return "Searching for Satellites..."

# ================= SEQUENTIAL BLOCKING CHECKS =================
def ensure_sim(ser):
    print("🔍 [USB] Checking SIM card status...")
    blank_count = 0
   
    while True:
        resp = send_at(ser, "AT+CPIN?", 2)
        if "READY" in resp:
            print("✅ [USB] SIM is READY!")
            break
           
        if not resp.strip():
            blank_count += 1
            if blank_count >= 10:
                raise serial.SerialException("Baseband unresponsive for 20 seconds. Forcing hardware reboot.")
        else:
            blank_count = 0
           
        print(f"⚠️ [USB] SIM not ready (Blank responses: {blank_count}/10). Retrying in 2 seconds...")
        time.sleep(2)

def ensure_network(ser):
    print("🔍 [USB] Checking network registration...")
    blank_count = 0
   
    while True:
        send_at(ser, "AT+CSQ", 1)
        resp = send_at(ser, "AT+CREG?", 2)
       
        if ",1" in resp or ",5" in resp or ",6" in resp:
            print("✅ [USB] Network registered!")
            send_at(ser, "AT+COPS?", 1)
            break
           
        if not resp.strip():
            blank_count += 1
            if blank_count >= 10:
                raise serial.SerialException("Baseband went silent during network check. Forcing hardware reboot.")
        else:
            blank_count = 0
           
        print(f"⚠️ [USB] Not registered (Blank responses: {blank_count}/10). Retrying in 3 seconds...")
        time.sleep(3)

def ensure_pdp(ser):
    print("🔍 [USB] Checking PDP Context...")
    blank_count = 0
   
    while True:
        resp = send_at(ser, "AT+CGACT?", 2)
        if "1,1" in resp:
            print("✅ [USB] PDP Context Active!")
            break
           
        if not resp.strip():
            blank_count += 1
            if blank_count >= 10:
                raise serial.SerialException("Baseband went silent during PDP check. Forcing hardware reboot.")

        else:
            blank_count = 0
           
        print("🌐 [USB] Activating PDP Context...")
        send_at(ser, 'AT+CGDCONT=1,"IP","internet"', 2)
        send_at(ser, "AT+CGACT=1,1", 5)
        time.sleep(3)

def build_mqtt_from_scratch(ser):
    print("🧹 [USB] Wiping previous MQTT states and building fresh session...")
    send_at(ser, "AT+CMQTTDISC=0,60", 1)
    send_at(ser, "AT+CMQTTREL=0", 1)
    send_at(ser, "AT+CMQTTSTOP", 1)
    send_at(ser, "AT+CMQTTSTART", 2)
   
    client_id = f"pi_hat_{int(time.time())}"
    send_at(ser, f'AT+CMQTTACCQ=0,"{client_id}"', 2)
   
    print("🔌 [USB] Connecting MQTT...")
    while True:
        conn_resp = send_at(ser, 'AT+CMQTTCONNECT=0,"tcp://broker.emqx.io:1883",60,1', 5)
        if "ERROR" in conn_resp:
            print("⚠️ [USB] MQTT Connection failed. Retrying in 3 seconds...")
            time.sleep(3)
        else:
            print("✅ [USB] MQTT Connected!")
            break

def ensure_mqtt(ser, force_rebuild=False):
    if force_rebuild:
        build_mqtt_from_scratch(ser)
        return

    resp = send_at(ser, "AT+CMQTTCONNECT?", 2)
   
    # THE FIX: We now look for the "tcp://" string to verify a real, active connection
    # If the modem doesn't reply with the tcp link, THEN we rebuild.
    if "tcp://" not in resp:
        print("⚠️ [USB] MQTT disconnected unexpectedly! Rebuilding session...")
        build_mqtt_from_scratch(ser)
    else:
        print("✅ [USB] MQTT Connection stable.")
       
# ================= MAIN LOOP =================
def main():
    setup_hardware()
    ser_usb = None
    current_usb_port = None

    try:
        ser_usb, current_usb_port = auto_find_and_open_port()
        msg_count = 1
        is_first_loop = True

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
                ensure_gnss(ser_usb) # <--- Activate GPS
               
                # 4. Smart MQTT Routing
                ensure_mqtt(ser_usb, force_rebuild=is_first_loop)
                is_first_loop = False

                # 5. Fetch Data
                rtc_timestamp = get_hardware_time()
                gps_info = get_gps_location(ser_usb)

                # 6. Publish
                payload = f"[{rtc_timestamp}] SIMCom GPS: {gps_info} | Msg:{msg_count}"
                topic = "pihat1"

                print(f"📡 [USB] Publishing message #{msg_count}...")
               
                topic_cmd = f"AT+CMQTTTOPIC=0,{len(topic)}"
                send_mqtt_payload(ser_usb, topic_cmd, topic)
               
                payload_cmd = f"AT+CMQTTPAYLOAD=0,{len(payload)}"
                send_mqtt_payload(ser_usb, payload_cmd, payload)
               
                resp = send_at(ser_usb, "AT+CMQTTPUB=0,0,60", 3)

                if "ERROR" in resp:
                    print("⚠️ [USB] Publish failed → forcing socket disconnect")
                    send_at(ser_usb, "AT+CMQTTDISC=0,60", 2)
                    send_at(ser_usb, "AT+CMQTTREL=0", 2)
                    send_at(ser_usb, "AT+CMQTTSTOP", 2)
                else:
                    msg_count += 1

                time.sleep(5)

            # --- THE RECOVERY BLOCK ---
            except (serial.SerialException, OSError) as e:
                print(f"\n❌ [CRITICAL ERROR] Connection lost! ({e})")
                print("🔄 Forcing a physical hardware reboot to clear zombie state...")
               
                if ser_usb:
                    try:
                        ser_usb.close()
                    except:
                        pass
               
                setup_hardware()
               
                print("✅ Modem rebooted! Hunting for new AT connection...")
                ser_usb, current_usb_port = auto_find_and_open_port()
               
                is_first_loop = True

    except KeyboardInterrupt:
        print("\nStopped by user")

    finally:
        if 'ser_usb' in locals() and ser_usb and getattr(ser_usb, 'is_open', False):
            print("Cleaning up USB data connections...")
            try:
                send_at(ser_usb, "AT+CGPS=0", 2) # Turn off GPS to save power
                send_at(ser_usb, "AT+CMQTTDISC=0,60", 2)
                send_at(ser_usb, "AT+CMQTTREL=0", 2)
                send_at(ser_usb, "AT+CMQTTSTOP", 2)
                send_at(ser_usb, "AT+CGACT=0,1", 2)
            except:
                pass
            ser_usb.close()

        GPIO.cleanup()
        print("Cleanup done")

if __name__ == '__main__':
    main()
