# Hardware

The reference target is an ESP32-S3 N16R8. Both sensors share a 100 kHz I2C
bus. Use 3.3 V-compatible breakout boards and a common ground. The ADS1115 ADDR
pin must select 0x48 and MPU6050 AD0 must select 0x68.

| Signal | Connection |
|---|---|
| I2C SDA | ESP32-S3 GPIO8 |
| I2C SCL | ESP32-S3 GPIO9 |
| ADS1115 AIN0 | AD8232 OUT |
| ADS1115 reference | GND, single-ended AIN0–GND |
| AD8232 LO− | ESP32-S3 GPIO4 |
| AD8232 LO+ | ESP32-S3 GPIO5 |

Expected visual documentation is listed in docs/images/README.md. The
repository intentionally contains no invented schematic or electrode-placement
image.
