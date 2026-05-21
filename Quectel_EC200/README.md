# EC200U Master Scripts

---

1. **QCTL_EC200_MASTER(4G).py* The core telemetry and control script for standard Physical SIM deployments. It features a user-selectable communication interface, allowing seamless switching between high-speed USB Type-C (ideal for full 4G MQTT sessions) and GPIO-based UART (for lightweight AT command testing without tying up USB ports). It includes automatic PDP context activation, strict AT command throttling to prevent baseband crashes, and automatic initialization of the onboard DS3231 Real-Time Clock (RTC) to ensure all generated telemetry payloads maintain accurate timestamping even during network outages.

2. **QCTL_EC200_MASTER(4G+GNSS).py** An extended framework that combines robust cellular telemetry with active location tracking. In addition to the standard 4G networking features, this script automatically initializes the EC200's internal GNSS engine. It handles hardware-driven satellite polling, seamlessly injecting real-time coordinate data (Latitude, Longitude, Altitude) directly into the RTC-timestamped MQTT payloads for precise asset tracking.

**3. QCTL_EC200_MASTER(eSIM).py** A specialized production script engineered explicitly for onboard eSIM operations. It utilizes hardware-level GPIO multiplexing to physically route the EC200's baseband processor directly to the embedded STMicro eSIM chip, bypassing the physical plastic SIM slot entirely. It includes tailored routing logic for M2M cellular profiles (such as Truphone) while maintaining the robust UART/USB selection and resilient, self-healing MQTT pipelines found in the primary scripts.
