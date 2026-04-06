import serial
import RPi.GPIO as GPIO
import time

# GPIO Pin Definitions
EN_PIN = 22
PWR_PIN = 17
RST_PIN = 27

SIM_SEL_PIN = 26

SERIAL_PORT = '/dev/serial0'
BAUD_RATE = 115200

def setup_hardware():
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)

    for pin in [EN_PIN, PWR_PIN, RST_PIN, SIM_SEL_PIN]:
        GPIO.setup(pin, GPIO.OUT)
        GPIO.output(pin, GPIO.LOW)

    print("Powering on the EC200U module...")
    GPIO.output(EN_PIN, GPIO.HIGH)
    time.sleep(1)

    GPIO.output(SIM_SEL_PIN, GPIO.LOW)  # Physical SIM

    GPIO.output(PWR_PIN, GPIO.HIGH)
    time.sleep(1.2)
    GPIO.output(PWR_PIN, GPIO.LOW)

    print("Waiting 15 seconds for modem boot...")
    time.sleep(15)

def send_at(ser, command, wait_time=1.0):
    print(f"--> {command}")
    ser.write((command + '\r\n').encode('utf-8'))
    time.sleep(wait_time)

    response = ""
    while ser.in_waiting:
        response += ser.read(ser.in_waiting).decode(errors='ignore')
        time.sleep(0.1)

    print(f"<-- {response.strip()}\n")
    return response

# ================= STATUS CHECKS =================

def is_network_registered(ser):
    resp = send_at(ser, "AT+CREG?", 2)
    return ",1" in resp or ",5" in resp

def is_pdp_active(ser):
    resp = send_at(ser, "AT+QIACT?", 2)
    return "1,1,1" in resp or ('1,1' in resp and '"' in resp)

def is_mqtt_connected(ser):
    resp = send_at(ser, "AT+QMTCONN?", 2)
    return ",3" in resp

# ================= RECOVERY LOOPS =================

def ensure_network(ser):
    """Blocks until cellular network is registered."""
    print("🔍 Checking network registration...")
    while not is_network_registered(ser):
        print("⚠️ Not registered. Retrying in 3 seconds...")
        time.sleep(3)
    
    # Verify the access technology (We want to see a 7 at the end for LTE)
    send_at(ser, "AT+COPS?", 1)
    print("✅ Network registered!")

def ensure_pdp(ser, retry_count=[0]):
    """Blocks until PDP data context is active, auto-detecting the APN."""
    while not is_pdp_active(ser):
        print("🌐 Activating PDP Context...")
        
        cops_resp = send_at(ser, "AT+COPS?", 2).lower()
        apn = "internet" # Default fallback
        
        if "jio" in cops_resp:
            apn = "jionet"
        elif "airtel" in cops_resp:
            # Alternate APNs if the first one fails
            apn = "airtelgprs.com" if retry_count[0] % 2 == 0 else "iot.airtel.com"
            retry_count[0] += 1
        elif "vi" in cops_resp or "vodafone" in cops_resp or "idea" in cops_resp:
            apn = "www"
        elif "bsnl" in cops_resp:
            apn = "bsnlnet"
            
        print(f"⚙️ Auto-selected APN: {apn}")
        
        send_at(ser, f'AT+QICSGP=1,1,"{apn}","","",1', 2)
        resp = send_at(ser, "AT+QIACT=1", 5)
        
        if "ERROR" in resp:
            print("⚠️ PDP Activation failed. Retrying in 3 seconds...")
            time.sleep(3)
        else:
            print("✅ PDP Context Active!")
            retry_count[0] = 0 # Reset counter on success

def ensure_mqtt(ser):
    """Blocks until MQTT broker is connected."""
    while not is_mqtt_connected(ser):
        print("🔌 Connecting MQTT...")
        
        send_at(ser, 'AT+QMTOPEN=0,"broker.emqx.io",1883', 5)
        
        client_id = f"pi_hat_{int(time.time())}"
        send_at(ser, f'AT+QMTCONN=0,"{client_id}"', 4)
        
        if not is_mqtt_connected(ser):
             print("⚠️ MQTT Connection failed. Retrying in 3 seconds...")
             time.sleep(3)
        else:
             print("✅ MQTT Connected!")

# ================= MAIN LOOP =================

def main():
    setup_hardware()

    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
        print("Serial opened\n")

        send_at(ser, "AT")
        send_at(ser, "AT+CPIN?", 2)

        # ==========================================
        # HARDWARE CONFIG: FORCE 4G LTE ONLY
        # 3 = LTE Only, 1 = Apply Immediately
        # ==========================================
        print("⚙️ Forcing Quectel module to strictly use 4G LTE...")
        send_at(ser, 'AT+QCFG="nwscanmode",3,1', 3)

        msg_count = 1

        while True:
            print("\n====== LOOP START ======")

            ensure_network(ser)
            ensure_pdp(ser)
            ensure_mqtt(ser)

            payload = f"Universal Pi HAT OK #{msg_count}"
            topic = "pihat1"

            print(f"📡 Publishing message #{msg_count}...")
            cmd = f'AT+QMTPUB=0,0,0,0,"{topic}","{payload}"'
            resp = send_at(ser, cmd, 2)

            if "ERROR" in resp:
                print("⚠️ Publish failed → forcing socket disconnect to reset state")
                send_at(ser, "AT+QMTDISC=0", 2)
            else:
                msg_count += 1

            time.sleep(5)

    except KeyboardInterrupt:
        print("\nStopped by user")

    finally:
        if 'ser' in locals() and ser.is_open:
            print("Cleaning up connections...")
            send_at(ser, "AT+QMTDISC=0", 2)
            send_at(ser, "AT+QIDEACT=1", 2)
            ser.close()

        GPIO.cleanup()
        print("Cleanup done")

if __name__ == '__main__':
    main()
