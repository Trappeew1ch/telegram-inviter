import os
import re

# This script is intended to be in the root directory,
# and 'settings' is a subdirectory.
SETTINGS_DIR = "settings"
API_DIR = "api"
USERNAME_DIR = "username"

DELAY_FILE = os.path.join(SETTINGS_DIR, "delay.py")
DEST_FILE = os.path.join(SETTINGS_DIR, "dest.py")
API_KEYS_FILE = os.path.join(API_DIR, "api_keys.txt")
USERNAMES_FILE = os.path.join(USERNAME_DIR, "usernames.txt")
PROXY_FILE_SM = os.path.join(SETTINGS_DIR, "proxy.txt") # Path for proxy settings


def update_delay_setting(key_to_update: str, new_value: int) -> bool:
    if not os.path.exists(DELAY_FILE):
        print(f"Error: {DELAY_FILE} not found.")
        return False

    lines = []
    updated = False
    pattern = re.compile(rf"^(\s*{re.escape(key_to_update)}\s*=\s*)(\d+)(\s*#.*)?$")

    try:
        with open(DELAY_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                match = pattern.match(line)
                if match:
                    comment = match.group(3) if match.group(3) is not None else ''
                    lines.append(f"{match.group(1)}{new_value}{comment}\n")
                    updated = True
                else:
                    lines.append(line)
    except IOError as e:
        print(f"Error reading {DELAY_FILE}: {e}")
        return False

    if updated:
        try:
            with open(DELAY_FILE, 'w', encoding='utf-8') as f:
                f.writelines(lines)
            return True
        except IOError as e:
            print(f"Error writing to {DELAY_FILE}: {e}")
            return False
    else:
        print(f"Key '{key_to_update}' not found in {DELAY_FILE}.")
        return False

def get_all_delay_settings() -> dict:
    delays = {}
    if not os.path.exists(DELAY_FILE):
        print(f"Warning: {DELAY_FILE} not found when trying to get settings.")
        return delays
    try:
        with open(DELAY_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                match = re.match(r"(\w+)\s*=\s*(\d+)", line)
                if match:
                    key, value = match.groups()
                    delays[key] = int(value)
    except IOError as e:
        print(f"Error reading {DELAY_FILE} for getting settings: {e}")
    except ValueError as e:
        print(f"Error parsing value in {DELAY_FILE}: {e}")
    return delays

def update_dest_setting(new_dest_value: str) -> bool:
    if not os.path.exists(DEST_FILE):
        try:
            with open(DEST_FILE, 'w', encoding='utf-8') as f:
                if not (new_dest_value.startswith("'") and new_dest_value.endswith("'")) and \
                   not (new_dest_value.startswith('"') and new_dest_value.endswith('"')):
                    f.write(f'dest = "{new_dest_value.replace("\"", "\\\"")}"\n')
                else:
                    f.write(f'dest = {new_dest_value}\n')
            return True
        except IOError as e:
            print(f"Error creating/writing {DEST_FILE}: {e}")
            return False

    lines = []
    updated = False
    if not (new_dest_value.startswith("'") and new_dest_value.endswith("'")) and \
       not (new_dest_value.startswith('"') and new_dest_value.endswith('"')):
        processed_dest_value = f'"{new_dest_value.replace("\"", "\\\"")}"'
    else:
        processed_dest_value = new_dest_value

    pattern = re.compile(r"^\s*dest\s*=\s*.*")

    try:
        with open(DEST_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                if pattern.match(line) and not updated:
                    lines.append(f"dest = {processed_dest_value}\n")
                    updated = True
                elif pattern.match(line) and updated:
                    lines.append(f"# {line.strip()} (commented out duplicate)\n")
                else:
                    lines.append(line)

        if not updated:
            lines.append(f"dest = {processed_dest_value}\n")

        with open(DEST_FILE, 'w', encoding='utf-8') as f:
            f.writelines(lines)
        return True
    except IOError as e:
        print(f"Error reading/writing {DEST_FILE}: {e}")
        return False

def get_dest_setting() -> str:
    if not os.path.exists(DEST_FILE):
        print(f"Warning: {DEST_FILE} not found when trying to get destination.")
        return ""
    try:
        with open(DEST_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith("dest"):
                    match = re.match(r"^\s*dest\s*=\s*(['\"])(.*?)\1", line)
                    if match:
                        return match.group(2)
        print(f"Warning: 'dest' variable not found or malformed in {DEST_FILE}.")
        return ""
    except IOError as e:
        print(f"Error reading {DEST_FILE} for getting destination: {e}")
        return ""
    except ValueError as e:
        print(f"Error parsing value in {DEST_FILE}: {e}")
        return ""

def get_api_keys_list(mask_hashes: bool = True) -> list[str]:
    keys = []
    if not os.path.exists(API_KEYS_FILE):
        print(f"Warning: {API_KEYS_FILE} not found.")
        return keys
    try:
        with open(API_KEYS_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if mask_hashes:
                    parts = line.split(':', 1)
                    if len(parts) == 2:
                        keys.append(f"{parts[0]}:{'*' * len(parts[1])}")
                    else:
                        keys.append(line)
                else:
                    keys.append(line)
    except IOError as e:
        print(f"Error reading {API_KEYS_FILE}: {e}")
    return keys

def add_api_key(api_id: str, api_hash: str) -> bool:
    os.makedirs(API_DIR, exist_ok=True)
    current_keys = get_api_keys_list(mask_hashes=False)
    for key_line in current_keys:
        if key_line.startswith(api_id + ":"):
            print(f"Error: API ID {api_id} already exists.")
            return False
    try:
        with open(API_KEYS_FILE, 'a', encoding='utf-8') as f:
            f.write(f"{api_id}:{api_hash}\n")
        return True
    except IOError as e:
        print(f"Error writing to {API_KEYS_FILE}: {e}")
        return False

def remove_api_key(api_id_to_remove: str) -> bool:
    if not os.path.exists(API_KEYS_FILE):
        print(f"Error: {API_KEYS_FILE} not found.")
        return False
    lines = []
    removed = False
    try:
        with open(API_KEYS_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line_stripped = line.strip()
                if not line_stripped or line_stripped.startswith('#'):
                    lines.append(line)
                    continue
                if line_stripped.startswith(api_id_to_remove + ":"):
                    removed = True
                else:
                    lines.append(line)
        if removed:
            with open(API_KEYS_FILE, 'w', encoding='utf-8') as f:
                f.writelines(lines)
        else:
            print(f"API ID {api_id_to_remove} not found for removal.")
        return removed
    except IOError as e:
        print(f"Error reading/writing {API_KEYS_FILE}: {e}")
        return False

def get_usernames() -> list[str]:
    usernames = []
    os.makedirs(USERNAME_DIR, exist_ok=True)
    if not os.path.exists(USERNAMES_FILE):
        print(f"Info: {USERNAMES_FILE} not found, returning empty list.")
        return usernames
    try:
        with open(USERNAMES_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    usernames.append(line.lstrip('@'))
    except IOError as e:
        print(f"Error reading {USERNAMES_FILE}: {e}")
    return usernames

def add_username_to_file(username: str) -> bool:
    os.makedirs(USERNAME_DIR, exist_ok=True)
    username_to_add = username.lstrip('@').strip()
    if not username_to_add:
        print("Error: Attempted to add an empty username.")
        return False
    current_usernames = get_usernames()
    if username_to_add in current_usernames:
        print(f"Info: Username '{username_to_add}' already exists in {USERNAMES_FILE}.")
        return False
    try:
        with open(USERNAMES_FILE, 'a', encoding='utf-8') as f:
            f.write(f"{username_to_add}\n")
        print(f"Info: Username '{username_to_add}' added to {USERNAMES_FILE}.")
        return True
    except IOError as e:
        print(f"Error writing to {USERNAMES_FILE}: {e}")
        return False

def remove_username_from_file(username: str) -> bool:
    os.makedirs(USERNAME_DIR, exist_ok=True)
    if not os.path.exists(USERNAMES_FILE):
        print(f"Error: {USERNAMES_FILE} not found for username removal.")
        return False
    username_to_remove = username.lstrip('@').strip()
    if not username_to_remove:
        print("Error: Attempted to remove an empty username.")
        return False
    current_usernames = get_usernames()
    if username_to_remove not in current_usernames:
        print(f"Info: Username '{username_to_remove}' not found in {USERNAMES_FILE}.")
        return False
    new_usernames = [u for u in current_usernames if u != username_to_remove]
    return overwrite_usernames_file(new_usernames)

def clear_usernames_file() -> bool:
    os.makedirs(USERNAME_DIR, exist_ok=True)
    try:
        with open(USERNAMES_FILE, 'w', encoding='utf-8') as f:
            f.write("")
        print(f"Info: {USERNAMES_FILE} has been cleared.")
        return True
    except IOError as e:
        print(f"Error clearing {USERNAMES_FILE}: {e}")
        return False

def overwrite_usernames_file(new_usernames: list[str]) -> bool:
    os.makedirs(USERNAME_DIR, exist_ok=True)
    try:
        with open(USERNAMES_FILE, 'w', encoding='utf-8') as f:
            for uname in new_usernames:
                uname_clean = uname.lstrip('@').strip()
                if uname_clean and not uname_clean.startswith('#'):
                    f.write(f"{uname_clean}\n")
        print(f"Info: {USERNAMES_FILE} has been overwritten.")
        return True
    except IOError as e:
        print(f"Error overwriting {USERNAMES_FILE}: {e}")
        return False

# --- Proxy Management Functions ---

def get_proxies() -> list[str]:
    """Reads proxy.txt and returns a list of proxy strings."""
    proxies = []
    os.makedirs(SETTINGS_DIR, exist_ok=True) # Ensure settings dir exists
    if not os.path.exists(PROXY_FILE_SM):
        print(f"Info: {PROXY_FILE_SM} not found, returning empty list.")
        return proxies
    try:
        with open(PROXY_FILE_SM, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'): # Ignore empty lines and comments
                    proxies.append(line)
    except IOError as e:
        print(f"Error reading {PROXY_FILE_SM}: {e}")
    return proxies

def add_proxy_to_file(proxy_string: str) -> bool:
    """Adds a proxy string to proxy.txt if it's not already there."""
    os.makedirs(SETTINGS_DIR, exist_ok=True)
    proxy_to_add = proxy_string.strip()
    if not proxy_to_add:
        print("Error: Attempted to add an empty proxy string.")
        return False

    current_proxies = get_proxies()
    if proxy_to_add in current_proxies:
        print(f"Info: Proxy '{proxy_to_add}' already exists in {PROXY_FILE_SM}.")
        return False

    try:
        with open(PROXY_FILE_SM, 'a', encoding='utf-8') as f:
            f.write(f"{proxy_to_add}\n")
        print(f"Info: Proxy '{proxy_to_add}' added to {PROXY_FILE_SM}.")
        return True
    except IOError as e:
        print(f"Error writing to {PROXY_FILE_SM}: {e}")
        return False

def remove_proxy_from_file(proxy_string: str) -> bool:
    """Removes an exact matching proxy string from proxy.txt."""
    os.makedirs(SETTINGS_DIR, exist_ok=True)
    if not os.path.exists(PROXY_FILE_SM):
        print(f"Error: {PROXY_FILE_SM} not found for proxy removal.")
        return False

    proxy_to_remove = proxy_string.strip()
    if not proxy_to_remove:
        print("Error: Attempted to remove an empty proxy string.")
        return False

    current_proxies = get_proxies()
    if proxy_to_remove not in current_proxies:
        print(f"Info: Proxy '{proxy_to_remove}' not found in {PROXY_FILE_SM}.")
        return False

    new_proxies = [p for p in current_proxies if p != proxy_to_remove]
    return overwrite_proxies_file(new_proxies)

def overwrite_proxies_file(new_proxies: list[str]) -> bool:
    """Overwrites proxy.txt with the provided list of proxy strings."""
    os.makedirs(SETTINGS_DIR, exist_ok=True)
    try:
        with open(PROXY_FILE_SM, 'w', encoding='utf-8') as f:
            for p_str in new_proxies:
                p_clean = p_str.strip()
                if p_clean and not p_clean.startswith('#'):
                    f.write(f"{p_clean}\n")
        print(f"Info: {PROXY_FILE_SM} has been overwritten.")
        return True
    except IOError as e:
        print(f"Error overwriting {PROXY_FILE_SM}: {e}")
        return False

def mask_proxy_string(proxy_str: str) -> str:
    """Masks sensitive parts of a proxy string for display."""
    parts = proxy_str.split(':')
    if len(parts) > 2:  # type:host:port...
        if len(parts) == 3 and '@' not in parts[1] : # Likely host:port:secret (e.g. MTProto)
            parts[2] = "****"
        elif len(parts) == 4: # Likely type:host:port:user or host:port:user:pass (if type is missing)
             # If format is type:host:port:secret (e.g. some MTProto tools)
            if parts[0].lower() == "mtproto" or parts[0].lower() == "mtproxy":
                 parts[3] = "****" # Mask secret
            else: # type:host:port:user
                 parts[3] = "****" # Mask user
        elif len(parts) >= 5: # Likely type:host:port:user:pass
            parts[-1] = "****" # Mask password
            parts[-2] = "****" # Mask username
    return ":".join(parts)
