#PiHAT-LTE (Industrial Telemetry & GNSS)

This repository contains the official Python driver scripts, test utilities, and automation frameworks for the WIN-PiHAT 4G LTE series. Designed to provide robust, cloud-connected cellular backhauls for industrial environments, these HATs specifically target the Quectel EC200U and SIMCom A7672S basebands.

The codebase provides heavily throttled, deployable examples for UART and auto-detecting USB Type-C communication, Real-Time Clock (RTC) synchronization, and resilient MQTT payload delivery. It features dedicated deployment architectures tailored for Physical SIM configurations, onboard eSIM routing (via GPIO multiplexing), and active-antenna GNSS (GPS) tracking.

To maintain stable IoT operations in volatile field deployments, the scripts include advanced hardware-recovery tripwires, dynamic APN toggling, strict AT command buffer management to prevent baseband crashes, and automated MQTT socket rebuilds.

---

### **Core Framework Features:**
* **Hardware Variants:** Dedicated scripts for Physical SIM, STMicro eSIM, and GNSS/GPS tracking modes.
* **Baseband Protection:** Mandatory AT command throttling and buffer cooldowns to prevent memory overflow and baseband crashes.
* **Self-Healing MQTT:** Automated detection of dropped TCP sockets with full QMT and CMQTT teardown/rebuild sequences.
* **Zombie Tripwires:** Active-LOW hardware reset recovery (via RST_PIN) if the internal baseband processor goes silent or unresponsive.
* **Interface Agnostic:** Seamless runtime selection between /dev/serial0 (UART) and auto-enumerated /dev/ttyUSB ports.
