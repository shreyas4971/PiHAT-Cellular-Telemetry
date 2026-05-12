# ==========================================================
# EC200U MULTI-INTERFACE TEST FRAMEWORK
# ==========================================================
# Features:
# - UART / Type-C USB selection
# - Automatic SIM & network detection
# - PDP auto activation
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
# SERIAL CONFIGURATION
# ==========================================================

UART_PORT = '/dev/serial0'
BAUD_RATE = 115200

# ==========================================================
# RTC CONFIGURATION
# ==========================================================

I2C_BUS = 1
DS3231_ADDR = 0x68

# ==========================================================
# MQTT CONFIGURATION
# ==========================================================

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
            dec_to_bcd(now.second),
            dec_to_bcd(now.minute),
            dec_to_bcd(now.hour),
            dec_to_bcd(now.isoweekday()),
            dec_to_bcd(now.day),
            dec_to_bcd(now.month),
            dec_to_bcd(now.year - 2000)
        ]

        bus.write_i2c_block_data(
            DS3231_ADDR,
            0x00,
            rtc_data
        )

        bus.close()

        print("🕒 RTC synchronized with system time")

    except Exception as e:

        print(f"⚠️ RTC synchronization failed: {e}")

def rtc_get_time():

    try:

        bus = smbus2.SMBus(I2C_BUS)

        data = bus.read_i2c_block_data(
            DS3231_ADDR,
            0x00,
            7
        )

        bus.close()

        second = bcd_to_dec(data[0])
        minute = bcd_to_dec(data[1])
        hour = bcd_to_dec(data[2] & 0x3F)

        day = bcd_to_dec(data[4])
        month = bcd_to_dec(data[5] & 0x1F)
        year = bcd_to_dec(data[6]) + 2000

        return (
            f"{year:04d}-{month:02d}-{day:02d} "
            f"{hour:02d}:{minute:02d}:{second:02d}"
        )

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

    # RESET_N idle state
    GPIO.output(RST_PIN, GPIO.HIGH)

    # Physical SIM selected
    GPIO.output(SIM_SEL_PIN, GPIO.LOW)

def power_on_modem():

    print("⚡ Powering on EC200U module...")

    # ------------------------------------------------------
    # ENABLE MAIN POWER RAIL
    # ------------------------------------------------------

    GPIO.output(EN_PIN, GPIO.HIGH)

    time.sleep(1)

    # ------------------------------------------------------
    # PWRKEY BOOT PULSE
    # ------------------------------------------------------

    print("🔘 Sending PWRKEY pulse...")

    GPIO.output(PWR_PIN, GPIO.HIGH)

    time.sleep(1.2)

    GPIO.output(PWR_PIN, GPIO.LOW)

    # ------------------------------------------------------
    # ACTIVE BOOT DETECTION
    # ------------------------------------------------------

    print("⏳ Waiting for modem boot sequence...")

    boot_ok = False

    boot_start = time.time()

    while time.time() - boot_start < 60:

        # --------------------------------------------------
        # UART CHECK
        # --------------------------------------------------

        if os.path.exists(UART_PORT):

            try:

                ser_test = serial.Serial(
                    UART_PORT,
                    BAUD_RATE,
                    timeout=1
                )

                ser_test.reset_input_buffer()

                ser_test.reset_output_buffer()

                ser_test.write(b"AT\r\n")

                time.sleep(1)

                resp = ser_test.read_all().decode(
                    errors='ignore'
                )

                ser_test.close()

                if "OK" in resp:

                    print(
                        "✅ UART modem boot complete"
                    )

                    boot_ok = True

                    break

            except:

                pass

        # --------------------------------------------------
        # USB CHECK
        # --------------------------------------------------

        for i in range(7):

            port = f"/dev/ttyUSB{i}"

            if os.path.exists(port):

                try:

                    ser_test = serial.Serial(
                        port,
                        BAUD_RATE,
                        timeout=1
                    )

                    ser_test.reset_input_buffer()

                    ser_test.reset_output_buffer()

                    ser_test.write(b"AT\r\n")

                    time.sleep(1)

                    resp = ser_test.read_all().decode(
                        errors='ignore'
                    )

                    ser_test.close()

                    if "OK" in resp:

                        print(
                            f"✅ USB modem boot complete "
                            f"({port})"
                        )

                        boot_ok = True

                        break

                except:

                    pass

        if boot_ok:

            break

        print("⏳ Modem still booting...")

        time.sleep(2)

    # ------------------------------------------------------
    # FINAL STATUS
    # ------------------------------------------------------

    if not boot_ok:

        print(
            "⚠️ Modem boot timeout exceeded"
        )

    else:

        print(
            "✅ MODEM INITIALIZATION COMPLETE"
        )

    print()

def hardware_reset(choice):

    print("\n🔄 ENTERING FULL MODEM REBOOT STATE")

    # ------------------------------------------------------
    # CLOSE OLD SERIAL
    # ------------------------------------------------------

    try:

        if 'ser' in globals():
            ser.close()

    except:
        pass

    # ------------------------------------------------------
    # ASSERT RESET
    # ------------------------------------------------------

    print("⚡ Resetting EC200U module...")

    GPIO.output(RST_PIN, GPIO.HIGH)

    time.sleep(0.1)

    # RESET_N active LOW
    GPIO.output(RST_PIN, GPIO.LOW)

    time.sleep(0.8)

    GPIO.output(RST_PIN, GPIO.HIGH)

    # ------------------------------------------------------
    # WAIT FOR MODEM SHUTDOWN
    # ------------------------------------------------------

    print("⏳ Waiting for modem shutdown...")

    time.sleep(5)

    # ------------------------------------------------------
    # WAIT FOR MODEM BOOT
    # ------------------------------------------------------

    print("⏳ Waiting for modem boot sequence...")

    time.sleep(20)

    # ------------------------------------------------------
    # USB ENUMERATION WAIT
    # ------------------------------------------------------

    if choice == "2":

        print("🔍 Waiting for USB enumeration...")

        usb_found = False

        start = time.time()

        while time.time() - start < 30:

            existing_ports = any(
                os.path.exists(f"/dev/ttyUSB{i}")
                for i in range(7)
            )

            if existing_ports:

                usb_found = True

                print(
                    "✅ USB modem interfaces detected"
                )

                break

            time.sleep(1)

        if not usb_found:

            print(
                "⚠️ USB enumeration timeout"
            )

        # Extra stabilization delay
        print("⏳ Stabilizing modem interfaces...")

        time.sleep(5)

    # ------------------------------------------------------
    # UART STABILIZATION
    # ------------------------------------------------------

    else:

        print("⏳ Stabilizing UART interface...")

        time.sleep(5)

    print("✅ MODEM REBOOT COMPLETE\n")

# ==========================================================
# SERIAL COMMUNICATION
# ==========================================================

def send_at(ser, command, timeout=3):

    try:

        print(f"[MODEM] --> {command}")

        # Clear old garbage
        ser.reset_input_buffer()
        ser.reset_output_buffer()

        # Send command
        ser.write((command + '\r\n').encode())

        response = ""
        start_time = time.time()

        # WAIT FOR FULL RESPONSE
        while time.time() - start_time < timeout:

            if ser.in_waiting:

                chunk = ser.read(
                    ser.in_waiting
                ).decode(
                    errors='ignore'
                )

                response += chunk

                # RESPONSE COMPLETE
                if (
                    "OK" in response
                    or "ERROR" in response
                    or "+CME ERROR" in response
                ):
                    break

            time.sleep(0.1)

        print(f"[MODEM] <-- {response.strip()}\n")

        return response

    except Exception as e:

        raise serial.SerialException(
            f"Connection lost: {e}"
        )

# ==========================================================
# USB AUTO DETECTION
# ==========================================================

def auto_find_usb_port():

    print("🔍 Detecting EC200 USB AT port...")

    while True:

        ports_to_test = [f"/dev/ttyUSB{i}" for i in range(7)]
        
        if not any(os.path.exists(p) for p in ports_to_test):
            print("⚠️ No ttyUSB modem ports detected")
            time.sleep(2)
            continue
            
        for port in ports_to_test:

            if os.path.exists(port):

                try:

                    # Use 'with' to safely open and auto-close the test port
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
                        
                        # THE MASTER FIX: Exact word match prevents false positives
                        if "OK" in response.split():
                            print(f"✅ EC200 AT PORT LOCKED: {port}")
                            print("⏳ Stabilizing modem interfaces...")
                            
                            # Keep the 8s stabilization required by the rest of 1.py
                            time.sleep(8) 
                            
                            # Re-open permanently to return to the main loop
                            final_ser = serial.Serial(port, BAUD_RATE, timeout=1)
                            final_ser.reset_input_buffer()
                            return final_ser, port
                            
                except serial.SerialException:
                    pass 
                    
        print("⚠️ Waiting for modem to synchronize AT queries...")
        time.sleep(3)

# ==========================================================
# STATE FUNCTIONS
# ==========================================================

def ensure_sim(ser):

    while True:

        print("🔍 Checking SIM status...")

        resp = send_at(ser, "AT+CPIN?", 2)

        if "+CPIN: READY" in resp:

            print("✅ SIM READY")

            return True

        print("⚠️ SIM not ready")

        time.sleep(3)

def ensure_network(ser):

    print("📶 Checking network registration...")

    csq = send_at(ser, "AT+CSQ", 3)

    if not csq:
        print("⚠️ No CSQ response")
        time.sleep(3)
        return False

    for attempt in range(5):

        print(f"📡 Registration check {attempt + 1}/5")
        resp = send_at(ser, "AT+CREG?", 5)

        # REGISTERED HOME (Handles formats 0, 1, and 2)
        if "+CREG: 0,1" in resp or "+CREG: 1,1" in resp or "+CREG: 2,1" in resp:
            print("✅ Network registered (home)")
            return True

        # REGISTERED ROAMING (Handles formats 0, 1, and 2)
        if "+CREG: 0,5" in resp or "+CREG: 1,5" in resp or "+CREG: 2,5" in resp:
            print("✅ Network registered (roaming)")
            return True

        if not resp:
            print("⚠️ CREG timeout")
        else:
            print("⏳ Network not ready yet")

        time.sleep(5)

    print("❌ Network registration timeout")
    return False

def ensure_pdp(ser):

    print("🌐 Preparing PDP context...")

    # --------------------------------------------------
    # CHECK LTE ATTACH STATUS
    # --------------------------------------------------

    attach_resp = send_at(
        ser,
        "AT+CGATT?",
        3
    )

    if "+CGATT: 1" not in attach_resp:

        print("⚠️ LTE attach not ready yet...")
        time.sleep(5)
        return False

    # --------------------------------------------------
    # IMPORTANT:
    # LET EC200 LTE STACK STABILIZE
    # AFTER RESET / USB RECOVERY
    # --------------------------------------------------

    print("⏳ Allowing LTE stack stabilization...")

    time.sleep(10)

    # --------------------------------------------------
    # CHECK IF PDP ALREADY ACTIVE
    # --------------------------------------------------

    resp = send_at(
        ser,
        "AT+QIACT?",
        5
    )

    if "+QIACT:" in resp:

        print("✅ PDP already active")

        return True

    # --------------------------------------------------
    # PDP QUERY FAILED
    # --------------------------------------------------

    if not resp:

        print("⚠️ PDP response timeout")

    # --------------------------------------------------
    # GET NETWORK NAME
    # --------------------------------------------------

    cops = send_at(
        ser,
        "AT+COPS?",
        3
    )

    apn = "internet"

    if "airtel" in cops.lower():

        apn = "airtelgprs.com"

    elif "jio" in cops.lower():

        apn = "jionet"

    elif "vi" in cops.lower() or "vodafone" in cops.lower():

        apn = "www"

    print(f"⚙️ Selected APN: {apn}")

    # --------------------------------------------------
    # DEACTIVATE OLD PDP SESSION
    # --------------------------------------------------

    send_at(
        ser,
        "AT+QIDEACT=1",
        5
    )

    time.sleep(3)

    # --------------------------------------------------
    # CONFIGURE PDP
    # --------------------------------------------------

    cmd = (
        f'AT+QICSGP=1,1,"{apn}","","",1'
    )

    resp = send_at(
        ser,
        cmd,
        5
    )

    if "OK" not in resp:

        print("❌ Failed to configure PDP")

        return False

    # --------------------------------------------------
    # ACTIVATE PDP
    # --------------------------------------------------

    print("🚀 Activating PDP context...")

    resp = send_at(
        ser,
        "AT+QIACT=1",
        15
    )

    if "OK" not in resp:

        print("❌ PDP activation failed")

        return False

    # --------------------------------------------------
    # LET LTE/IP STACK FULLY STABILIZE
    # --------------------------------------------------

    print("⏳ Settling LTE stack...")
    time.sleep(10)

    # Single health check after waiting
    at_resp = send_at(
        ser,
        "AT",
        2
    )

    if "OK" not in at_resp:
        print("❌ LTE stack unresponsive after PDP activation")
        return False

    # --------------------------------------------------
    # VERIFY PDP MULTIPLE TIMES
    # --------------------------------------------------

    for i in range(5):

        print(
            f"🔍 PDP verification "
            f"{i+1}/5"
        )

        resp = send_at(
            ser,
            "AT+QIACT?",
            5
        )

        if "+QIACT:" in resp:

            print("✅ PDP context active")

            return True

        time.sleep(2)

    print("❌ PDP verification failed")

    return False

def ensure_mqtt(ser):

    while True:

        resp = send_at(ser, "AT+QMTCONN?", 3)

        if ",3" in resp or "+QMTCONN: 0,0,0" in resp or "+QMTCONN: 0,3" in resp:
            print("✅ MQTT connected")
            return True

        print("🔌 Connecting MQTT...")

        send_at(ser, "AT+QMTDISC=0", 2)
        send_at(ser, "AT+QMTCLOSE=0", 2)
        
        # Modem needs a moment to clear old sockets
        time.sleep(2)

        send_at(ser, 'AT+QMTCFG="recv/mode",0,0,1', 1)
        send_at(ser, f'AT+QMTOPEN=0,"{MQTT_BROKER}",{MQTT_PORT}', 5)

        # THE CRUCIAL DELAY: Let TCP establish before trying to send MQTT credentials
        print("⏳ Waiting for TCP socket to open...")
        time.sleep(4)

        client_id = f"pi_hat_{int(time.time())}"
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

    # ------------------------------------------------------
    # UART MODE
    # ------------------------------------------------------

    if choice == "1":

        print("🔗 Using UART mode")

        try:

            ser = serial.Serial(
                UART_PORT,
                BAUD_RATE,
                timeout=1
            )

        except serial.SerialException as e:

            print(f"❌ UART open failed: {e}")

            return

    # ------------------------------------------------------
    # USB MODE
    # ------------------------------------------------------

    elif choice == "2":

        print("🔗 Using Type-C USB mode")

        ser, current_port = auto_find_usb_port()

    else:

        print("❌ Invalid option")

        return

    # ------------------------------------------------------
    # CLEANUP PREVIOUS SESSIONS
    # ------------------------------------------------------

    send_at(ser, "AT+QMTDISC=0", 2)

    send_at(ser, "AT+QMTCLOSE=0", 2)

    send_at(ser, "AT+QIDEACT=1", 5)

    state = "SIM"

    msg_count = 1

    # ------------------------------------------------------
    # MAIN LOOP
    # ------------------------------------------------------

    while True:

        try:

            print("\n====== MAIN LOOP ======")

            # --------------------------------------------------
            # MODEM HEALTH CHECK
            # --------------------------------------------------

            at_ok = False

            for _ in range(3):

                resp = send_at(ser, "AT", 1)

                if "OK" in resp:

                    at_ok = True

                    break

                time.sleep(1)

            if not at_ok:

                raise serial.SerialException(
                    "AT response timeout"
                )

            # --------------------------------------------------
            # STATE MACHINE
            # --------------------------------------------------

            if state == "SIM":

                if ensure_sim(ser):

                    state = "NETWORK"

            elif state == "NETWORK":

                if ensure_network(ser):

                    state = "PDP"

            elif state == "PDP":

                if ensure_pdp(ser):

                    state = "MQTT"

            elif state == "MQTT":

                if ensure_mqtt(ser):

                    state = "PUBLISH"

            elif state == "PUBLISH":

                timestamp = rtc_get_time()

                payload = (
                    f"[{timestamp}] "
                    f"EC200 Test Message #{msg_count}"
                )

                print(
                    f"📡 Publishing MQTT "
                    f"message #{msg_count}"
                )

                cmd = (
                    f'AT+QMTPUB=0,0,0,0,'
                    f'"{MQTT_TOPIC}","{payload}"'
                )

                resp = send_at(
                    ser,
                    cmd,
                    3
                )

                if (
                    "ERROR" in resp
                    or not resp
                ):

                    print(
                        "⚠️ MQTT publish failed"
                    )

                    state = "MQTT"

                else:

                    print(
                        "✅ MQTT publish successful"
                    )

                    msg_count += 1

                time.sleep(5)

        # ------------------------------------------------------
        # RECOVERY BLOCK
        # ------------------------------------------------------

        except Exception as e:

            print(f"\n❌ CRITICAL ERROR: {e}")

            try:

                ser.close()

            except:

                pass

            # --------------------------------------------------
            # USB RECOVERY HANDLING
            # --------------------------------------------------

            if choice == "2":

                print(
                    "🔌 USB recovery handling..."
                )

                # Full reset only for dead AT interface
                if "AT response timeout" in str(e):

                    hardware_reset(choice)

                else:

                    print(
                        "⏳ Waiting for modem "
                        "USB recovery..."
                    )

                    time.sleep(5)

            # --------------------------------------------------
            # RECONNECT INTERFACE
            # --------------------------------------------------

            if choice == "1":

                try:

                    ser = serial.Serial(
                        UART_PORT,
                        BAUD_RATE,
                        timeout=1
                    )

                    print(
                        "✅ UART reconnected"
                    )

                except serial.SerialException as err:

                    print(
                        f"❌ UART reconnect "
                        f"failed: {err}"
                    )

                    time.sleep(5)

                    continue

            else:

                ser, current_port = (
                    auto_find_usb_port()
                )

            # --------------------------------------------------
            # RESET STATE MACHINE
            # --------------------------------------------------

            state = "SIM"

# ==========================================================
# ENTRY POINT
# ==========================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        print("\n🛑 Stopped by user")

    finally:

        GPIO.cleanup()

        print("🧹 Cleanup complete")
