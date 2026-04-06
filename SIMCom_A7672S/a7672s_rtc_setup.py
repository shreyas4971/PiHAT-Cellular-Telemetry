import smbus2
from datetime import datetime

def dec_to_bcd(val):
    """Converts standard Decimal to Binary-Coded Decimal for the hardware chip."""
    return (val // 10 * 16) + (val % 10)

def main():
    try:
        # Open I2C Bus 1
        bus = smbus2.SMBus(1)
        
        # Grab the perfect current time from the Raspberry Pi's OS
        now = datetime.now()

        # Encode the current time into the DS3231's required BCD format
        data = [
            dec_to_bcd(now.second),
            dec_to_bcd(now.minute),
            dec_to_bcd(now.hour),
            1, # Day of the week (1-7, mostly ignored for basic timestamps)
            dec_to_bcd(now.day),
            dec_to_bcd(now.month),
            dec_to_bcd(now.year - 2000) # Year is stored as an offset from 2000
        ]

        # Blast the encoded data into the DS3231 starting at memory register 0x00
        bus.write_i2c_block_data(0x68, 0x00, data)
        bus.close()

        print(f"✅ SUCCESS! DS3231 RTC hardware clock is permanently set to: {now.strftime('%Y-%m-%d %H:%M:%S')}")
        print("As long as the coin cell battery is installed, it will never forget this time!")

    except Exception as e:
        print(f"❌ Error setting RTC: {e}")
        print("Make sure the I2C wires are connected securely.")

if __name__ == "__main__":
    main()

