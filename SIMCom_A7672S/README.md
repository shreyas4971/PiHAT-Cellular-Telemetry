Scripts optimized for the PiHAT A7672S (LASE variant), featuring aggressive auto-recovery logic and software-based Location-Based Services (LBS) triangulation.

**1. a7672s_uart_basic.py:** Description: Configures the Raspberry Pi's hardware serial pins to communicate with the SIMCom A7672S. Provides a stable UART pipeline for initial AT command verification and basic MQTT payload testing.

**2. a7672s_typec_master.py:** Description: The bulletproof USB Type-C master script. Includes absolute attempt-counters for baseband lockups, hardware-level auto-reboot sequences, and a custom Python LBS (Cell ID) fallback to provide location data when the module is indoors or lacks GPS silicon.

**3. a7672s_rtc_setup.py:** Description: Configures the I2C communication for the DS3231 RTC on the SIMCom HAT. Guarantees that all telemetry data is stamped with accurate hardware time before being passed to the SIMCom modem for transmission.
