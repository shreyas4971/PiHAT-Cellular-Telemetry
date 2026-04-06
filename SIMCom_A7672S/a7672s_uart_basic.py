import serial
import RPi.GPIO as GPIO
import time

# GPIO Pin Definitions (Simcom HAT)
EN_PIN = 22
PWR_PIN = 17
RST_PIN = 21
SIM_SEL_PIN = 5  # Simcom physical SIM mux pin

SERIAL_PORT = '/dev/serial0'
BAUD_RATE = 115200

def setup_hardware():
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)

    for pin in [EN_PIN, PWR_PIN, RST_PIN, SIM_SEL_PIN]:
        GPIO.setup(pin, GPIO.OUT)
        GPIO.output(pin, GPIO.LOW)

    print("Powering on the Simcom module...")
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

def send_mqtt_payload(ser, command, text):
    """Helper for Simcom's multi-step topic/payload input waiting for '>' prompt"""
    print(f"--> {command} (Waiting for prompt...)")
    ser.write((command + '\r\n').encode('utf-8'))
    time.sleep(0.5)
    
    response = ser.read(ser.in_waiting).decode(errors='ignore')
    if ">" in response:
        print(f"--> Sending: {text}")
        ser.write((text + '\r\n').encode('utf-8'))
        time.sleep(1.0)
        response += ser.read(ser.in_waiting).decode(errors='ignore')
    
    print(f"<-- {response.strip()}\n")
    return response

# ================= STATUS CHECKS =================

def is_network_registered(ser):
    resp = send_at(ser, "AT+CREG?", 2)
    # 1 = Home, 5 = Roaming, 6 = SMS only (Common for data-only IoT SIMs on LTE)
    return ",1" in resp or ",5" in resp or ",6" in resp

def is_pdp_active(ser):
    resp = send_at(ser, "AT+CGACT?", 2)
    return "1,1" in resp

# ================= RECOVERY LOOPS =================

def ensure_network(ser):
    print("🔍 Checking network registration...")
    while not is_network_registered(ser):
        print("⚠️ Not registered. Retrying in 3 seconds...")
        time.sleep(3)
    send_at(ser, "AT+COPS?", 1)
    print("✅ Network registered!")

def ensure_pdp(ser, retry_count=[0]):
    while not is_pdp_active(ser):
        print("🌐 Activating PDP Context...")
        
        cops_resp = send_at(ser, "AT+COPS?", 2).lower()
        apn = "internet" 
        
        if "jio" in cops_resp:
            apn = "jionet"
        elif "airtel" in cops_resp:
            apn = "airtelgprs.com" if retry_count[0] % 2 == 0 else "iot.airtel.com"
            retry_count[0] += 1
        elif "vi" in cops_resp or "vodafone" in cops_resp or "idea" in cops_resp:
            apn = "www"
        elif "bsnl" in cops_resp:
            apn = "bsnlnet"
            
        print(f"⚙️ Auto-selected APN: {apn}")
        
        send_at(ser, f'AT+CGDCONT=1,"IP","{apn}"', 2)
        resp = send_at(ser, "AT+CGACT=1,1", 5)
        
        if "ERROR" in resp:
            print("⚠️ PDP Activation failed. Retrying in 3 seconds...")
            time.sleep(3)
        else:
            print("✅ PDP Context Active!")
            retry_count[0] = 0 

def ensure_mqtt(ser):
    """Initializes and connects the Simcom MQTT service."""
    send_at(ser, "AT+CMQTTSTART", 2)
    
    client_id = f"pi_hat_{int(time.time())}"
    accq_resp = send_at(ser, f'AT+CMQTTACCQ=0,"{client_id}"', 2)
    
    if "ERROR" not in accq_resp:
        print("🔌 Connecting MQTT...")
        send_at(ser, 'AT+CMQTTCONNECT=0,"tcp://broker.emqx.io:1883",60,1', 5)

# ================= MAIN LOOP =================

def main():
    setup_hardware()

    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
        print("Serial opened\n")

        send_at(ser, "AT")
        send_at(ser, "AT+CPIN?", 2)

        
        msg_count = 1

        while True:
            print("\n====== LOOP START ======")

            ensure_network(ser)
            ensure_pdp(ser)
            ensure_mqtt(ser)

            payload = f"Universal Simcom HAT OK #{msg_count}"
            topic = "pihat1"

            print(f"📡 Publishing message #{msg_count}...")
            
            topic_cmd = f"AT+CMQTTTOPIC=0,{len(topic)}"
            send_mqtt_payload(ser, topic_cmd, topic)
            
            payload_cmd = f"AT+CMQTTPAYLOAD=0,{len(payload)}"
            send_mqtt_payload(ser, payload_cmd, payload)
            
            resp = send_at(ser, "AT+CMQTTPUB=0,0,60", 3)

            if "ERROR" in resp:
                print("⚠️ Publish failed → forcing socket disconnect to reset state")
                send_at(ser, "AT+CMQTTDISC=0,60", 2)
                send_at(ser, "AT+CMQTTREL=0", 2)
                send_at(ser, "AT+CMQTTSTOP", 2)
            else:
                msg_count += 1

            time.sleep(5)

    except KeyboardInterrupt:
        print("\nStopped by user")

    finally:
        if 'ser' in locals() and ser.is_open:
            print("Cleaning up connections...")
            send_at(ser, "AT+CMQTTDISC=0,60", 2)
            send_at(ser, "AT+CMQTTREL=0", 2)
            send_at(ser, "AT+CMQTTSTOP", 2)
            send_at(ser, "AT+CGACT=0,1", 2) 
            ser.close()

        GPIO.cleanup()
        print("Cleanup done")

if __name__ == '__main__':
    main()
