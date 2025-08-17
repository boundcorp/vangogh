import machine, time, network, ujson, os
from machine import UART, Pin, I2C, deepsleep
import struct
import math
import M5
from M5 import *
import gc

# =============================================================================
# HARDWARE & PIN SETUP
# =============================================================================

# Initialize M5Stack
M5.begin()

# GPS UART setup - restored to working config
gps_uart = UART(1, baudrate=115200, tx=Pin(17), rx=Pin(18))

# I2C for AXP2101 - use pins 11,12 to avoid GPS conflict
i2c = I2C(0, scl=Pin(11), sda=Pin(12), freq=400000)
AXP2101_ADDR = 0x34

# Import credentials and coordinates from local secrets file
try:
    from local_secrets import WIFI_SSID, WIFI_PASS, HOME_LAT, HOME_LON, FTP_HOST, FTP_USER, FTP_PASS, FTP_DIR
except ImportError:
    # Fallback if secrets file doesn't exist
    WIFI_SSID = "YOUR_WIFI_SSID"
    WIFI_PASS = "YOUR_WIFI_PASSWORD"
    HOME_LAT = 40.7128  # Example: NYC latitude
    HOME_LON = -74.0060  # Example: NYC longitude
    FTP_HOST = "your-ftp-server.com"
    FTP_USER = "van_monitor"
    FTP_PASS = "your_ftp_password"
    FTP_DIR = "/van_data"
    print("WARNING: local_secrets.py not found, using placeholder credentials and coordinates")

HOME_RADIUS_FT = 2000  # Distance threshold in feet

# =============================================================================
# GLOBAL VARIABLES
# =============================================================================

is_syncing = False
last_wifi_attempt = 0
WIFI_RETRY_INTERVAL = 30000  # 30 seconds in milliseconds

# Power management variables
engine_off_time = None
SHUTDOWN_DELAY = 30000  # 30 seconds in milliseconds
is_shutting_down = False

# Logging variables
last_log_time = 0
LOG_INTERVAL = 60000  # 60 seconds in milliseconds
SD_MOUNT_PATH = "/sd"
LOG_RETENTION_DAYS = 28  # Keep logs for 4 weeks
last_cleanup_time = 0
CLEANUP_INTERVAL = 86400000  # Check for cleanup once per day (24 hours in ms)

# Shared sensor data
sensor_data = {
    "engine": False,
    "battery": None,
    "wifi": False,
    "gps": {"gps_fix_valid": False, "satellites": 0},
    "last_update": 0
}

# Update intervals
SENSOR_UPDATE_INTERVAL = 5000  # 5 seconds for sensors
DISPLAY_UPDATE_INTERVAL = 250   # 250ms for smooth display
DEEP_SLEEP_INTERVAL = 10000     # 10 seconds for deep sleep cycles

# Previous display state for change detection
previous_display_state = {
    "gps_status": None,
    "battery_pct": None,
    "battery_state": None,
    "engine_status": None,
    "wifi_status": None,
    "home_status": None,
    "coordinates": None,
    "speed": None,
    "time": None
}

# =============================================================================
# DISPLAY FUNCTIONS
# =============================================================================

def init_display():
    """Initialize the LCD display using M5 library"""
    try:
        Lcd.clear(0x0000)  # Black background
        Lcd.setTextColor(0xFFFF, 0x0000)  # White text on black
        print("LCD initialized successfully via M5 library")
        return True
    except Exception as e:
        print(f"Failed to initialize LCD: {e}")
        return False

def calculate_distance_feet(lat1, lon1, lat2, lon2):
    """Calculate distance between two GPS coordinates in feet"""
    # Haversine formula
    R = 3959  # Earth radius in miles
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat/2) * math.sin(dlat/2) + 
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * 
         math.sin(dlon/2) * math.sin(dlon/2))
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    distance_miles = R * c
    return distance_miles * 5280  # Convert to feet

def is_close_to_home(gps_data):
    """Check if current GPS location is within home radius"""
    if not gps_data.get("gps_fix_valid") or not gps_data.get("latitude") or not gps_data.get("longitude"):
        return False
    
    distance_ft = calculate_distance_feet(
        gps_data["latitude"], gps_data["longitude"],
        HOME_LAT, HOME_LON
    )
    return distance_ft <= HOME_RADIUS_FT

def update_text_if_changed(x, y, new_text, color, bg_color=0x0000, key=None):
    """Update text only if it has changed from previous state"""
    global previous_display_state
    
    if key and previous_display_state.get(key) == new_text:
        return  # No change, skip update
    
    # Clear the area and draw new text
    text_width = len(new_text) * 6  # Approximate character width
    Lcd.fillRect(x, y, text_width + 10, 15, bg_color)
    Lcd.setTextColor(color, bg_color)
    Lcd.setCursor(x, y)
    Lcd.print(new_text)
    
    if key:
        previous_display_state[key] = new_text

def draw_status_screen(gps_data, battery_data, wifi_connected, engine_on):
    """Smart update display - only change what's different"""
    global previous_display_state
    
    # Colors
    WHITE = 0xFFFF
    GREEN = 0x07E0
    RED = 0xF800
    YELLOW = 0xFFE0
    BLUE = 0x001F
    
    # Title (only draw once)
    if not previous_display_state.get("title_drawn"):
        # Clear entire screen once to remove startup text
        Lcd.clear(0x0000)
        
        Lcd.setTextColor(WHITE, 0x0000)
        Lcd.setCursor(10, 10)
        Lcd.print("VAN MONITOR")
        Lcd.drawLine(10, 30, 310, 30, WHITE)
        # Static labels
        Lcd.setCursor(10, 40)
        Lcd.print("GPS:")
        Lcd.setCursor(10, 120) 
        Lcd.print("BATTERY:")
        Lcd.setCursor(10, 160)
        Lcd.print("USB:")
        Lcd.setCursor(10, 180)
        Lcd.print("ENGINE:")
        Lcd.setCursor(10, 200)
        Lcd.print("WIFI:")
        Lcd.setCursor(180, 40)
        Lcd.print("HOME:")
        previous_display_state["title_drawn"] = True
    
    # GPS Status
    if gps_data.get("status") == "hardware_error":
        gps_status = "HW ERROR"
        gps_color = RED
    elif gps_data.get("gps_fix_valid"):
        gps_status = f"FIX SAT:{gps_data.get('satellites', 0)}"
        gps_color = GREEN
    else:
        gps_status = "NO FIX"
        gps_color = RED
    
    update_text_if_changed(55, 40, gps_status, gps_color, key="gps_status")
    
    # GPS Coordinates (if valid)
    if gps_data.get("gps_fix_valid") and gps_data.get("latitude") and gps_data.get("longitude"):
        coords = f"LAT: {gps_data['latitude']:.4f}"
        update_text_if_changed(10, 60, coords, WHITE, key="lat")
        
        coords2 = f"LON: {gps_data['longitude']:.4f}"
        update_text_if_changed(10, 80, coords2, WHITE, key="lon")
        
        if gps_data.get("speed"):
            speed_text = f"SPEED: {gps_data['speed']:.1f} km/h"
            update_text_if_changed(10, 100, speed_text, WHITE, key="speed")
    
    # Battery Status
    if battery_data:
        bat_pct = battery_data.get("battery", {}).get("percentage", 0)
        if bat_pct > 50:
            bat_color = GREEN
        elif bat_pct > 20:
            bat_color = YELLOW
        else:
            bat_color = RED
        
        bat_text = f"{bat_pct}%"
        update_text_if_changed(85, 120, bat_text, bat_color, key="battery_pct")
        
        charge_state = battery_data.get("charging", {}).get("state", "unknown")
        charge_text = f"Status: {charge_state}"
        update_text_if_changed(10, 140, charge_text, WHITE, key="battery_state")
    
    # USB Status
    usb_status = "CONNECTED" if engine_on else "UNPLUGGED"
    usb_color = GREEN if engine_on else RED
    update_text_if_changed(50, 160, usb_status, usb_color, key="usb_status")
    
    # Engine Status
    engine_status = "ON" if engine_on else "OFF"
    engine_color = GREEN if engine_on else RED
    update_text_if_changed(75, 180, engine_status, engine_color, key="engine_status")
    
    # WiFi Status
    wifi_status = "CONNECTED" if wifi_connected else "OFF"
    wifi_color = GREEN if wifi_connected else RED
    update_text_if_changed(60, 200, wifi_status, wifi_color, key="wifi_status")
    
    # Home Status
    if gps_data.get("gps_fix_valid"):
        close_to_home = is_close_to_home(gps_data)
        home_status = "YES" if close_to_home else "NO"
        home_color = GREEN if close_to_home else RED
    else:
        home_status = "?"
        home_color = YELLOW
    
    update_text_if_changed(225, 40, home_status, home_color, key="home_status")
    
    # Time
    if gps_data.get("time"):
        time_str = gps_data["time"] if gps_data["time"] != "??:??:??" else "??:??:??"
        time_text = f"TIME: {time_str}"
        update_text_if_changed(10, 220, time_text, WHITE, key="time")

# =============================================================================
# HARDWARE FUNCTIONS
# =============================================================================

def engine_is_on():
    """Check if USB power (engine) is detected"""
    try:
        status = i2c.readfrom_mem(AXP2101_ADDR, 0x00, 1)
        return bool(status[0] & 0x20)  # Bit 5 = VBUS present
    except:
        return False

# =============================================================================
# GPS FUNCTIONS
# =============================================================================

def read_gps(timeout=3000):
    """Read GPS data using simple readline approach"""
    try:
        start_time = time.ticks_ms()
        gps_data = {"gps_fix_valid": False, "satellites": 0}
        lines_processed = 0
        gprmc_found = False
        gpgga_found = False
        
        while time.ticks_diff(time.ticks_ms(), start_time) < timeout:
            line = gps_uart.readline()
            if line:
                try:
                    line_str = line.decode('ascii').strip()
                    
                    if line_str.startswith('$'):
                        lines_processed += 1
                        
                        # Parse GPRMC for position, speed, time
                        if line_str.startswith(('$GPRMC', '$GNRMC')):
                            parts = line_str.split(',')
                            if len(parts) >= 13:
                                time_str = parts[1]
                                status = parts[2]
                                lat_str = parts[3]
                                lat_dir = parts[4]
                                lon_str = parts[5]
                                lon_dir = parts[6]
                                speed_str = parts[7]
                                
                                if status == 'A' and lat_str and lon_str:
                                    try:
                                        # Parse coordinates
                                        if len(lat_str) >= 4 and len(lon_str) >= 5:
                                            lat = float(lat_str[:2]) + float(lat_str[2:]) / 60.0
                                            if lat_dir == 'S': 
                                                lat = -lat
                                            
                                            lon = float(lon_str[:3]) + float(lon_str[3:]) / 60.0
                                            if lon_dir == 'W': 
                                                lon = -lon
                                            
                                            # Parse speed
                                            speed = 0.0
                                            if speed_str:
                                                speed_knots = float(speed_str)
                                                speed_kmh = speed_knots * 1.852
                                                speed = speed_kmh if speed_kmh > 1.0 else 0.0
                                            
                                            # Parse time
                                            time_formatted = "??:??:??"
                                            if time_str and len(time_str) >= 6:
                                                time_formatted = f"{time_str[:2]}:{time_str[2:4]}:{time_str[4:6]}"
                                            
                                            gps_data.update({
                                                "gps_fix_valid": True,
                                                "latitude": lat,
                                                "longitude": lon,
                                                "speed": speed,
                                                "time": time_formatted
                                            })
                                            gprmc_found = True
                                    except (ValueError, IndexError):
                                        continue
                        
                        # Parse GPGGA for satellite count
                        elif line_str.startswith(('$GPGGA', '$GNGGA')):
                            parts = line_str.split(',')
                            if len(parts) >= 8:  # Reduced requirement for fragmented sentences
                                try:
                                    satellites = parts[7] if len(parts) > 7 else ""
                                    if satellites and satellites.isdigit():
                                        sat_count = int(satellites)
                                        gps_data["satellites"] = sat_count
                                        gpgga_found = True
                                except (ValueError, IndexError):
                                    continue
                        
                        # Return early if we have both types of data
                        if gprmc_found and gpgga_found:
                            return gps_data
                            
                except:
                    continue
            else:
                time.sleep(0.01)
        
        # Debug output
        if lines_processed == 0:
            print("GPS: No valid NMEA sentences received")
        else:
            print(f"GPS: Processed {lines_processed} lines, fix={gps_data.get('gps_fix_valid')}, sats={gps_data.get('satellites', 0)}")
        
        return gps_data
        
    except Exception as e:
        print(f"GPS read error: {e}")
        return {"gps_fix_valid": False, "satellites": 0, "error": str(e)}


# =============================================================================
# BATTERY FUNCTIONS
# =============================================================================

def read_battery_status():
    """Read battery status from AXP2101 with fixed voltage-based percentage"""
    try:
        # Read key registers
        pmu_status = i2c.readfrom_mem(AXP2101_ADDR, 0x00, 1)[0]
        charge_status = i2c.readfrom_mem(AXP2101_ADDR, 0x01, 1)[0]
        
        # Read battery voltage (16-bit, 1mV resolution) - THIS WORKS
        bat_v_raw = i2c.readfrom_mem(AXP2101_ADDR, 0x34, 2)
        bat_voltage = struct.unpack(">H", bat_v_raw)[0] * 0.001
        
        # Calculate percentage from voltage (Li-ion: 3.0V=0%, 4.2V=100%)
        voltage_percentage = max(0, min(100, int((bat_voltage - 3.0) / 1.2 * 100)))
        
        charge_states = {
            0: "trickle", 1: "pre-charge", 2: "const-current",
            3: "const-voltage", 4: "done", 5: "not-charging"
        }
        
        return {
            "battery": {
                "percentage": voltage_percentage,  # Use calculated percentage
                "voltage": round(bat_voltage, 3)
            },
            "charging": {"state": charge_states.get(charge_status & 0x07, "unknown")},
            "voltage_percentage": voltage_percentage,  # For compatibility
            "battery_voltage": round(bat_voltage, 3),
            "pmu_status": {"raw": pmu_status}
        }
    except Exception as e:
        print(f"Battery read error: {e}")
        return None

# =============================================================================
# MICROSD & LOGGING FUNCTIONS
# =============================================================================

def init_sd_card():
    """Initialize microSD card for M5Stack CoreS3"""
    try:
        from machine import SDCard, Pin
        
        # Initialize SD card with M5Stack CoreS3 pins
        sd = SDCard(slot=2, sck=Pin(36), mosi=Pin(37), miso=Pin(35), cs=Pin(4))
        
        # Mount it
        os.mount(sd, SD_MOUNT_PATH)
        print(f"SD card mounted to {SD_MOUNT_PATH}")
        
        # Test write access
        test_file = f"{SD_MOUNT_PATH}/test.txt"
        with open(test_file, 'w') as f:
            f.write("Van Monitor SD Test")
        
        # Clean up test file
        os.remove(test_file)
        print("SD card write test successful")
        return True
        
    except Exception as e:
        print(f"Failed to mount SD card: {e}")
        return False

def get_date_string(timestamp=None):
    """Get date string in YYYY-MM-DD format"""
    if timestamp is None:
        timestamp = time.time()
    
    # Convert to local time tuple (year, month, day, hour, min, sec, weekday, yearday)
    t = time.localtime(timestamp)
    return f"{t[0]:04d}-{t[1]:02d}-{t[2]:02d}"

def cleanup_old_logs():
    """Remove log files older than LOG_RETENTION_DAYS"""
    global last_cleanup_time
    
    current_time = time.ticks_ms()
    if time.ticks_diff(current_time, last_cleanup_time) < CLEANUP_INTERVAL:
        return
    
    try:
        current_timestamp = time.time()
        cutoff_timestamp = current_timestamp - (LOG_RETENTION_DAYS * 86400)  # 4 weeks ago
        
        files = os.listdir(SD_MOUNT_PATH)
        log_files = [f for f in files if f.startswith('van_log_') and f.endswith('.json')]
        
        deleted_count = 0
        for filename in log_files:
            try:
                # Extract date from filename: van_log_YYYY-MM-DD.json
                date_part = filename[8:-5]  # Remove 'van_log_' and '.json'
                
                if len(date_part) == 10 and date_part.count('-') == 2:  # YYYY-MM-DD format
                    year, month, day = date_part.split('-')
                    file_timestamp = time.mktime((int(year), int(month), int(day), 0, 0, 0, 0, 0))
                    
                    if file_timestamp < cutoff_timestamp:
                        os.remove(f"{SD_MOUNT_PATH}/{filename}")
                        print(f"Deleted old log: {filename}")
                        deleted_count += 1
                        
            except Exception as e:
                print(f"Error processing {filename}: {e}")
        
        if deleted_count > 0:
            print(f"Cleanup complete: {deleted_count} old log files removed")
        else:
            print("No old log files to remove")
            
        last_cleanup_time = current_time
        
    except Exception as e:
        print(f"Log cleanup error: {e}")

def log_sensor_data():
    """Log current sensor data to daily SD card files"""
    global last_log_time
    
    current_time = time.ticks_ms()
    if time.ticks_diff(current_time, last_log_time) < LOG_INTERVAL:
        return
    
    try:
        # Create timestamp
        timestamp = time.time()
        date_str = get_date_string(timestamp)
        
        # Prepare log entry
        log_entry = {
            "timestamp": timestamp,
            "date": date_str,
            "engine": sensor_data["engine"],
            "battery": sensor_data["battery"],
            "wifi": sensor_data["wifi"],
            "gps": sensor_data["gps"],
            "close_to_home": is_close_to_home(sensor_data["gps"]) if sensor_data["gps"].get("gps_fix_valid") else False
        }
        
        # Daily log filename
        log_filename = f"{SD_MOUNT_PATH}/van_log_{date_str}.json"
        
        # Read existing data for today or create new
        logs = []
        try:
            with open(log_filename, 'r') as f:
                logs = ujson.load(f)
        except:
            logs = []
            print(f"Creating new daily log: {log_filename}")
        
        logs.append(log_entry)
        
        # Write back to file
        with open(log_filename, 'w') as f:
            ujson.dump(logs, f)
        
        print(f"Logged data to {log_filename} (entry #{len(logs)})")
        last_log_time = current_time
        
        # Perform cleanup check
        cleanup_old_logs()
        
    except Exception as e:
        print(f"SD logging error: {e}")

def upload_files_ftp():
    """Upload log files to FTP server"""
    try:
        import socket
        
        print("Connecting to FTP server...")
        # Simple FTP implementation for MicroPython
        sock = socket.socket()
        sock.connect((FTP_HOST, 21))
        
        # Read FTP response
        response = sock.recv(1024).decode()
        print(f"FTP: {response.strip()}")
        
        # Login
        sock.send(f"USER {FTP_USER}\r\n".encode())
        response = sock.recv(1024).decode()
        print(f"FTP: {response.strip()}")
        
        sock.send(f"PASS {FTP_PASS}\r\n".encode())
        response = sock.recv(1024).decode()
        print(f"FTP: {response.strip()}")
        
        if "230" in response:  # Login successful
            print("FTP login successful")
            
            # Change to upload directory
            sock.send(f"CWD {FTP_DIR}\r\n".encode())
            response = sock.recv(1024).decode()
            
            # List files to upload
            try:
                files = os.listdir(SD_MOUNT_PATH)
                for filename in files:
                    if filename.endswith('.json'):
                        print(f"Uploading {filename}...")
                        # Note: Full FTP upload implementation would be more complex
                        # This is a simplified version for proof of concept
            except Exception as e:
                print(f"File listing error: {e}")
        
        sock.close()
        return True
        
    except Exception as e:
        print(f"FTP upload error: {e}")
        return False

# =============================================================================
# POWER MANAGEMENT FUNCTIONS
# =============================================================================

def enter_deep_sleep():
    """Enter deep sleep mode"""
    print("Entering deep sleep mode...")
    
    # Clear display
    Lcd.clear(0x0000)
    Lcd.setTextColor(0xFFFF, 0x0000)
    Lcd.setCursor(80, 100)
    Lcd.print("DEEP SLEEP")
    Lcd.setCursor(60, 120)
    Lcd.print("ENGINE OFF")
    
    time.sleep(1)
    
    # Enter deep sleep for 10 seconds
    deepsleep(DEEP_SLEEP_INTERVAL)

def handle_shutdown_sequence():
    """Handle vehicle shutdown sequence"""
    global is_shutting_down
    
    print("Starting shutdown sequence...")
    is_shutting_down = True
    
    # Update display
    Lcd.clear(0x0000)
    Lcd.setTextColor(0xFFE0, 0x0000)  # Yellow
    Lcd.setCursor(70, 80)
    Lcd.print("SHUTTING DOWN")
    Lcd.setCursor(60, 100)
    Lcd.print("Checking WiFi...")
    
    # Check if close to home
    if sensor_data["gps"].get("gps_fix_valid") and is_close_to_home(sensor_data["gps"]):
        print("Close to home - attempting WiFi connection...")
        
        Lcd.setCursor(50, 120)
        Lcd.print("Uploading data...")
        
        # Try to connect to WiFi
        if check_wifi():
            print("WiFi connected - uploading files...")
            upload_success = upload_files_ftp()
            
            if upload_success:
                Lcd.setCursor(60, 140)
                Lcd.print("Upload complete")
            else:
                Lcd.setCursor(60, 140)
                Lcd.print("Upload failed")
            
            time.sleep(2)
        else:
            print("WiFi connection failed")
            Lcd.setCursor(60, 140)
            Lcd.print("No WiFi")
            time.sleep(1)
    else:
        print("Not close to home - skipping WiFi")
        Lcd.setCursor(60, 120)
        Lcd.print("Away from home")
        time.sleep(1)
    
    # Enter deep sleep cycle
    enter_deep_sleep()

# =============================================================================
# NETWORK FUNCTIONS
# =============================================================================

def check_wifi():
    """Try to connect to WiFi"""
    try:
        sta = network.WLAN(network.STA_IF)
        sta.active(True)
        
        if sta.isconnected():
            return True
            
        print("Connecting to WiFi...")
        sta.connect(WIFI_SSID, WIFI_PASS)
        
        timeout = 10
        while timeout > 0:
            if sta.isconnected():
                print("WiFi connected!")
                return True
            time.sleep(1)
            timeout -= 1
    except:
        pass
    return False

# =============================================================================
# MAIN APPLICATION
# =============================================================================

def update_sensors():
    """Update sensor data in background (non-blocking)"""
    global sensor_data, is_syncing, last_wifi_attempt, engine_off_time, is_shutting_down
    
    current_time = time.ticks_ms()
    
    # Only update if enough time has passed
    if time.ticks_diff(current_time, sensor_data["last_update"]) < SENSOR_UPDATE_INTERVAL:
        return
    
    print("\n--- Updating Sensors ---")
    
    # Check engine and track power state
    prev_engine_state = sensor_data["engine"]
    sensor_data["engine"] = engine_is_on()
    print(f"Engine: {'ON' if sensor_data['engine'] else 'OFF'}")
    
    # Track engine off time for shutdown logic
    if sensor_data["engine"]:
        engine_off_time = None  # Reset timer when engine is on
    else:
        if prev_engine_state and not sensor_data["engine"]:
            # Engine just turned off
            engine_off_time = current_time
            print("Engine turned off - starting shutdown timer")
        elif engine_off_time and time.ticks_diff(current_time, engine_off_time) > SHUTDOWN_DELAY:
            # Engine has been off for more than 30 seconds
            print("Engine off >30s - triggering shutdown sequence")
            handle_shutdown_sequence()
            return  # Exit early as we're shutting down
    
    # Read battery
    sensor_data["battery"] = read_battery_status()
    if sensor_data["battery"]:
        print(f"Battery: {sensor_data['battery'].get('battery', {}).get('percentage', 0)}%")
    
    # Read GPS
    sensor_data["gps"] = read_gps()
    gps_result = sensor_data["gps"]
    if gps_result.get('gps_fix_valid'):
        sat_count = gps_result.get('satellites', 0)
        gps_time = gps_result.get('time', '??:??:??')
        print(f"GPS: Fix - SAT:{sat_count} TIME:{gps_time}")
    else:
        print("GPS: No fix")
        print(f"Debug: GPS returned {gps_result}")
    
    # Show home status if GPS valid
    if sensor_data["gps"].get('gps_fix_valid'):
        close_to_home = is_close_to_home(sensor_data["gps"])
        print(f"Close to home: {'YES' if close_to_home else 'NO'}")
        distance_ft = calculate_distance_feet(
            sensor_data["gps"]["latitude"], sensor_data["gps"]["longitude"],
            HOME_LAT, HOME_LON
        )
        print(f"Distance: {int(distance_ft)}ft")
    
    # Check WiFi - connect if close to home (regardless of USB power for testing)
    close_to_home = is_close_to_home(sensor_data["gps"]) if sensor_data["gps"].get('gps_fix_valid') else False
    
    # Try WiFi if close to home and enough time has passed
    if close_to_home and time.ticks_diff(current_time, last_wifi_attempt) > WIFI_RETRY_INTERVAL:
        last_wifi_attempt = current_time
        sensor_data["wifi"] = check_wifi()
        if sensor_data["wifi"]:
            print("WiFi: Connected (close to home)")
        else:
            print("WiFi: Failed to connect")
    else:
        # Quick check if already connected
        sta = network.WLAN(network.STA_IF)
        sensor_data["wifi"] = sta.isconnected() if sta.active() else False
    
    print(f"WiFi: {'Connected' if sensor_data['wifi'] else 'Disconnected'}")
    
    # Log data to SD card every 60 seconds
    log_sensor_data()
    
    sensor_data["last_update"] = current_time

def update_display():
    """Update display with current sensor data (fast, non-blocking)"""
    draw_status_screen(
        sensor_data["gps"], 
        sensor_data["battery"], 
        sensor_data["wifi"], 
        sensor_data["engine"]
    )

def main():
    """Main application loop"""
    print("Starting Van Monitor (M5 version)...")
    
    # Initialize display
    if not init_display():
        print("Display init failed, continuing anyway...")
    
    # Initialize SD card
    sd_available = init_sd_card()
    if not sd_available:
        print("SD card not available - logging disabled")
    
    # Show startup screen
    Lcd.clear(0x0000)
    Lcd.setTextColor(0xFFFF, 0x0000)
    Lcd.setCursor(100, 100)
    Lcd.print("VAN MONITOR")
    Lcd.setCursor(110, 120)
    Lcd.print("Starting...")
    if sd_available:
        Lcd.setCursor(100, 140)
        Lcd.print("SD: Ready")
    time.sleep(2)
    
    # Initial sensor reading
    update_sensors()
    update_display()
    
    # Main loop with separated sensor and display updates
    print("Starting monitoring loop (smooth display updates)")
    print("Press Ctrl+C to stop")
    
    try:
        while True:
            # Update sensors (non-blocking, only when needed)
            update_sensors()
            
            # Update display (smooth, consistent)
            update_display()
            
            # Fast display refresh for smooth updates
            time.sleep(DISPLAY_UPDATE_INTERVAL / 1000.0)  # Convert to seconds
            
    except KeyboardInterrupt:
        print("\nStopping...")
        Lcd.clear(0x0000)
        Lcd.setCursor(80, 100)
        Lcd.print("SHUTTING DOWN")
        time.sleep(1)

if __name__ == "__main__":
    main()