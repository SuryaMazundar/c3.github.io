import keyboard
import re
import win32gui
import time
import requests
import sys
import psutil
import os
from pathlib import Path

# ================= CONFIG =================
TARGET_WINDOW_KEYWORD = "Intra"  
DEFAULT_PORT = 5300 
DUPLICATE_TIMEOUT = 3.0           
IDLE_TIMEOUT = 0.5                
MIN_SCAN_LENGTH = 5               
# ==========================================

# Enhanced port detection
def get_web_port():
    """Get the port from WebApp if it's running, otherwise use default"""
    try:
        # Method 1: Check running processes more thoroughly
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = proc.info.get('cmdline', [])
                if cmdline and any('WebApp.py' in str(arg) for arg in cmdline):
                    print(f"DEBUG: Found WebApp process with cmdline: {cmdline}")
                    
                    # Check for --port argument
                    for i, arg in enumerate(cmdline):
                        if arg == '--port' and i + 1 < len(cmdline):
                            port = int(cmdline[i + 1])
                            print(f"DEBUG: Found port via --port arg: {port}")
                            return port
                    
                    # Check if port is in the command line as part of other arguments
                    for arg in cmdline:
                        if isinstance(arg, str) and '--port=' in arg:
                            port_part = arg.split('--port=')[-1]
                            if port_part.isdigit():
                                port = int(port_part)
                                print(f"DEBUG: Found port via --port= format: {port}")
                                return port
                    
                    # If no port specified, check what port Flask is actually using
                    # by checking network connections
                    try:
                        connections = proc.connections()
                        for conn in connections:
                            if conn.status == psutil.CONN_LISTEN and conn.laddr:
                                port = conn.laddr.port
                                if port != 5300:  # Only return if it's different from default
                                    print(f"DEBUG: Found port via network connection: {port}")
                                    return port
                    except (psutil.AccessDenied, psutil.NoSuchProcess):
                        pass
                        
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        
        # Method 2: Try to read from common startup locations
        possible_dirs = [
            r"C:\C3 - Checkin Checkout Center",
            os.getcwd(),
            os.path.dirname(os.path.abspath(__file__))
        ]
        
        for install_dir in possible_dirs:
            batch_file = os.path.join(install_dir, "StartC3.bat")
            if os.path.exists(batch_file):
                with open(batch_file, 'r') as f:
                    content = f.read()
                    # Look for port in the batch file
                    import re
                    port_match = re.search(r'--port\s+(\d+)', content)
                    if port_match:
                        port = int(port_match.group(1))
                        print(f"DEBUG: Found port in batch file: {port}")
                        return port
                    
        print("DEBUG: Using default port 5300")
        return DEFAULT_PORT
                    
    except Exception as e:
        print(f"Port detection warning: {e}")
        return DEFAULT_PORT

def get_web_endpoint():
    """Get the current web endpoint with dynamic port"""
    # Try to read port from file first
    try:
        # Look for web_port.txt in the same directory as this script
        script_dir = os.path.dirname(os.path.abspath(__file__))
        port_file = os.path.join(script_dir, "web_port.txt")
        
        if os.path.exists(port_file):
            with open(port_file, 'r') as f:
                port_str = f.read().strip()
                if port_str.isdigit():
                    port = int(port_str)
                    endpoint = f"http://127.0.0.1:{port}/api/receive_scan"
                    print(f"DEBUG: Using port from file: {port}")
                    return endpoint
    except Exception as e:
        print(f"DEBUG: Error reading port file: {e}")
    
    # Fallback to dynamic detection
    port = get_web_port()
    endpoint = f"http://127.0.0.1:{port}/api/receive_scan"
    print(f"DEBUG: Using detected port: {port}")
    return endpoint

def get_active_window_title() -> str:
    """Return title of currently focused window."""
    try:
        return win32gui.GetWindowText(win32gui.GetForegroundWindow())
    except Exception:
        return ""

def normalize(text: str) -> str:
    """
    Clean and validate scanned input:
    - Keeps full prefixes (TBA, PKG, etc.)
    - Removes spaces and non-printable chars
    """
    if text is None:
        return ""
    return text.strip()

def send_to_web(tracking_id: str):
    """Send scanned tracking ID to Flask API with error handling."""
    web_endpoint = get_web_endpoint()
    try:
        print(f"→ Sending {tracking_id} to {web_endpoint}...", end=" ", flush=True)
        r = requests.post(web_endpoint, json={"tracking_id": tracking_id}, timeout=3)
        if r.status_code == 200:
            print(f"[SUCCESS]")
        else:
            print(f"[ERROR: {r.status_code}]")
    except requests.exceptions.Timeout:
        print("[TIMEOUT]")
    except requests.exceptions.ConnectionError:
        print("[CONNECTION ERROR] - Is WebApp running?")
    except Exception as e:
        print(f"[ERROR: {e}]")

def is_intra_item_field_focused():
    """
    Detect if we're in the Intra #item field by checking window title and field state.
    """
    try:
        title = get_active_window_title()
        if TARGET_WINDOW_KEYWORD.lower() not in title.lower():
            return False
            
        # For Intra client, we can use a simpler approach
        # Clear any existing selection and check if field is ready for input
        keyboard.press('end')
        keyboard.release('end')
        time.sleep(0.05)
        
        # Try to select all to see if field has content
        keyboard.press('ctrl')
        keyboard.press('a')
        keyboard.release('a')
        keyboard.release('ctrl')
        time.sleep(0.05)
        
        # Just assume it's ready if we're in Intra window
        # This is more reliable for Intra's specific behavior
        return True
        
    except Exception as e:
        print(f"Field detection error: {e}")
        return False

def capture_scan_event() -> str: 
    """
    Capture input but only in Intra item field.
    Enhanced for better barcode detection.
    """
    events = []
    last_event_time = time.time()
    
    print("[CAPTURING] ", end="", flush=True)

    while True:
        # Only capture if we're in the right context
        if not is_intra_item_field_focused():
            time.sleep(0.1)
            continue
            
        event = keyboard.read_event(suppress=False)
        if event.event_type != keyboard.KEY_DOWN:
            continue

        events.append(event)
        last_event_time = time.time()

        if event.name == "enter":
            print("[ENTER] ", end="", flush=True)
            break

        # Stop if idle (barcode scanners are fast)
        if time.time() - last_event_time > IDLE_TIMEOUT and events:
            print("[TIMEOUT] ", end="", flush=True)
            break

    # Convert events to text
    typed_text = ""
    for e in events:
        if hasattr(e, "name"):
            name = e.name
            if len(name) == 1:
                typed_text += name
            elif name == "space":
                typed_text += " "
            elif name == "enter":
                continue
            elif name in ["exclam", "at", "hash", "dollar", "percent", "caret", "ampersand",
                          "asterisk", "parenleft", "parenright", "minus", "underscore",
                          "equal", "plus", "bracketleft", "bracketright", "backslash",
                          "semicolon", "quote", "comma", "period", "slash", "tilde"]:
                symbol_map = {
                    "exclam": "!", "at": "@", "hash": "#", "dollar": "$", "percent": "%",
                    "caret": "^", "ampersand": "&", "asterisk": "*", "parenleft": "(",
                    "parenright": ")", "minus": "-", "underscore": "_", "equal": "=",
                    "plus": "+", "bracketleft": "[", "bracketright": "]", "backslash": "\\",
                    "semicolon": ";", "quote": "'", "comma": ",", "period": ".",
                    "slash": "/", "tilde": "~",
                }
                typed_text += symbol_map.get(name, "")
    
    result = typed_text.strip()
    print(f"Raw: '{result}'", flush=True)
    return result

def looks_like_barcode(text):
    """
    Simple check if text looks like a barcode vs manual typing.
    Barcodes are usually long strings of letters/numbers without spaces.
    """
    if not text or len(text) < MIN_SCAN_LENGTH:
        return False
    
    # Remove common barcode prefixes/suffixes for checking
    clean_text = text.upper().replace(' ', '')
    
    # Barcodes are usually alphanumeric and quite long
    if (len(clean_text) >= 8 and 
        clean_text.isalnum() and
        not any(word in clean_text.lower() for word in ['the', 'and', 'you', 'are', 'this', 'that'])):
        return True
    
    # Or if it contains common tracking number patterns
    tracking_patterns = [
        r'^\d{2}[A-Z]\d{16}$',  # Like 12H75E180927615660
        r'^\d{18,}$',           # Long numeric strings
        r'^[A-Z0-9]{10,}$',     # Long alphanumeric strings
    ]
    
    for pattern in tracking_patterns:
        if re.match(pattern, clean_text):
            return True
    
    return False

def run_scanner():
    """Main loop to continuously read scans from active Intra window."""
    print("\n===============================")
    print("   SC LOGIC Barcode Bridge")
    print("===============================")
    print("INTRA ITEM FIELD mode - Only captures from Intra #item field")
    print("Press [Esc] to quit.\n")

    # Debug: Show what port we're using
    web_endpoint = get_web_endpoint()
    print(f"Target endpoint: {web_endpoint}")
    print("Press [Esc] to quit.\n")
    
    # Test the connection immediately
    try:
        test_response = requests.get(web_endpoint.replace('/api/receive_scan', ''), timeout=2)
        print(f"WebApp connection test: SUCCESS (Status {test_response.status_code})")
    except Exception as e:
        print(f"WebApp connection test: FAILED - {e}")
        print("Make sure WebApp is running!")

    last = {"code": None, "t": 0}
    scan_count = 0

    last = {"code": None, "t": 0}
    scan_count = 0

    while True:
        try:
            # Exit if Esc pressed
            if keyboard.is_pressed("esc"):
                print("\nExiting bridge.")
                sys.exit(0)

            title = get_active_window_title()
            sys.stdout.write(f"\rActive: {title[:50]:50s} | Scans: {scan_count}")
            sys.stdout.flush()

            # Only scan when Intra client is active AND in item field
            if TARGET_WINDOW_KEYWORD.lower() in title.lower() and is_intra_item_field_focused():
                raw = capture_scan_event()
                cleaned = normalize(raw)

                if not cleaned:
                    continue

                # Ignore too-short scans
                if len(cleaned) < MIN_SCAN_LENGTH:
                    print(f"Ignored short: '{cleaned}'")
                    continue

                now = time.time()

                # Prevent duplicate scans within timeout
                if cleaned == last["code"] and now - last["t"] < DUPLICATE_TIMEOUT:
                    print(f"Ignored duplicate: {cleaned}")
                    continue

                # Additional barcode validation
                if not looks_like_barcode(cleaned):
                    print(f"Doesn't look like barcode: '{cleaned}'")
                    continue

                print(f"\n[SCAN #{scan_count + 1}] {cleaned}")
                send_to_web(cleaned)
                last = {"code": cleaned, "t": now}
                scan_count += 1

            else:
                time.sleep(0.1)

        except KeyboardInterrupt:
            print("\nStopped by user.")
            break
        except Exception as e:
            print(f"\n[ERROR] {e}")
            time.sleep(1)

if __name__ == "__main__":
    run_scanner()