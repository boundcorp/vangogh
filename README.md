# Van Monitoring System

A comprehensive MicroPython application for real-time vehicle monitoring using the M5Stack CoreS3 SE.

## Features

### 🚗 Vehicle Monitoring
- **GPS Tracking**: Real-time location with satellite count and speed
- **Battery Management**: Voltage monitoring with percentage calculation
- **Engine Detection**: USB power detection for engine status
- **Home Proximity**: Automatic detection when within configurable radius

### 📱 Display & Interface
- **Smooth LCD Updates**: Flicker-free display with state tracking
- **Real-time Data**: GPS coordinates, battery status, WiFi connectivity
- **Visual Status**: Color-coded indicators (green/yellow/red)

### 🌐 Connectivity
- **WiFi Auto-Connect**: Automatic connection when close to home
- **Secure Credentials**: Local secrets management (not committed to git)

## Hardware Requirements

- **M5Stack CoreS3 SE** with ESP32-S3
- **GPS Module** connected to UART1 (TX=17, RX=18)
- **AXP2101** battery management unit (I2C pins 11,12)
- **ST7789 LCD** (via M5 library)

## Quick Start

### 1. Hardware Setup
Connect your GPS module to pins 17 (TX) and 18 (RX) at 115200 baud.

### 2. Credentials Setup
Create a `local_secrets.py` file (this file is gitignored):

```python
# WiFi configuration
WIFI_SSID = "your_wifi_ssid"
WIFI_PASS = "your_wifi_password"

# Home location coordinates
HOME_LAT = 40.7128  # Your home latitude
HOME_LON = -74.0060  # Your home longitude
```

### 3. Installation
```bash
# Copy files to your M5Stack CoreS3
mpremote cp main.py :
mpremote cp local_secrets.py :

# Run the application
mpremote run main.py
```

### 4. Configuration
- Update `HOME_LAT` and `HOME_LON` in `local_secrets.py` with your actual coordinates
- Adjust `HOME_RADIUS_FT` in `main.py` if needed (default: 2000 feet)

## System Architecture

### Sensor Collection (5-second intervals)
- GPS position and satellite data via NMEA parsing
- Battery voltage and charging status
- USB power detection
- WiFi connectivity management

### Display Updates (250ms intervals)
- Smart rendering that only updates changed values
- Prevents flicker with state tracking
- Smooth, responsive interface

### Data Processing
- **GPRMC sentences**: Position, speed, time data
- **GPGGA sentences**: Satellite count and altitude
- **Haversine formula**: Distance calculation for home proximity
- **Voltage-based**: Battery percentage calculation

## Display Layout

```
VAN MONITOR
─────────────────────────────────
GPS: FIX SAT:8          HOME: YES
LAT: 40.7128
LON: -74.0060
SPEED: 0.0 km/h

BATTERY: 65%
Status: not-charging

USB: CONNECTED
ENGINE: ON
WIFI: CONNECTED

TIME: 14:30:25
```

## Technical Details

### GPS Configuration
- **Baudrate**: 115200
- **Pins**: TX=17, RX=18
- **Sentences**: GPRMC (position), GPGGA (satellites)
- **Timeout**: 3 seconds per reading

### Battery Monitoring
- **I2C Address**: 0x34 (AXP2101)
- **Voltage Range**: 3.0V - 4.2V (Li-ion)
- **Percentage**: Calculated from voltage readings
- **Pins**: SCL=11, SDA=12

### WiFi Management
- **Auto-retry**: Every 30 seconds when close to home
- **Connection timeout**: 10 seconds
- **Power-aware**: Manages connection based on location

## File Structure

```
├── main.py              # Main application
├── local_secrets.py     # Credentials (gitignored)
├── .gitignore          # Git ignore rules
├── README.md           # This file
└── CLAUDE.md           # Development notes
```

## Development

### Adding Features
The modular design makes it easy to extend:
- Sensor data is collected in `update_sensors()`
- Display updates in `draw_status_screen()`
- Hardware interfaces are clearly separated

### Debugging
Enable debug output by monitoring the serial console:
```bash
mpremote run main.py
```

### Testing Components
Individual hardware components can be tested separately by importing specific functions.

## Security

- **No credentials in git**: All sensitive data in `local_secrets.py`
- **Gitignored secrets**: Prevents accidental commits
- **Clean history**: Repository contains no sensitive information

## Troubleshooting

### GPS Issues
- Verify wiring: TX=17, RX=18
- Check baudrate: 115200
- Ensure antenna has clear sky view
- Monitor NMEA sentences in debug output

### Battery Reading Issues
- Verify I2C connection: SCL=11, SDA=12
- Check AXP2101 address: 0x34
- Ensure battery is connected

### Display Problems
- Verify M5 library import: `from M5 import *`
- Check display initialization
- Monitor for error messages

### WiFi Connection Issues
- Verify credentials in `local_secrets.py`
- Check signal strength
- Ensure proximity to home location

## License

This project was developed with assistance from Claude Code.

## Contributing

1. Fork the repository
2. Create your feature branch
3. Make changes (ensure `local_secrets.py` is not committed)
4. Test thoroughly
5. Submit a pull request

---

🤖 *Generated with [Claude Code](https://claude.ai/code)*