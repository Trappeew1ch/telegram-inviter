```markdown
# Telegram Inviter Bot: Step-by-Step Guide

This guide will walk you through setting up and running the Telegram Inviter Bot.

## 1. Prerequisites

*   **Python 3.x:** Ensure you have Python version 3.x installed on your system. You can download it from [python.org](https://www.python.org/).
*   **pip:** Python's package installer, `pip`, is required to install dependencies. It usually comes with Python 3.x.

## 2. Installation of Dependencies

The bot relies on several Python libraries. Install them using pip:

```bash
pip install telethon aiogram
```
*(Note: This bot uses Telethon for interacting with Telegram client APIs and Aiogram for the control bot interface.)*

## 3. Initial Configuration (Critical)

Before running the bot for the first time, you **MUST** configure the following:

### 3.1. `settings/config.py`

This file contains essential credentials for the bot to operate.

*   **`BOT_TOKEN`**: **MUST be updated.** This is the token for your Telegram bot (obtained from BotFather). This bot is used to control the inviter script.
    *   Example: `BOT_TOKEN = "1234567890:ABCdefGhIJKLmnopQRSTuvwxyz123456789"`
*   **`ADMIN_ID`**: **MUST be updated.** This is your personal Telegram User ID. Only this user will be ableto control the bot. You can get your ID from bots like `@userinfobot`.
    *   Example: `ADMIN_ID = "123456789"`
*   **`LOG_CHANNEL_ID`**: (Recommended) The ID of a private Telegram channel or group where the bot will send its operational logs. The control bot (identified by `BOT_TOKEN`) must be an administrator in this channel/group.
    *   Example: `LOG_CHANNEL_ID = -1001234567890`
*   **`MAX_ACCOUNTS_PER_API`**: (Optional) Maximum number of user accounts to associate with a single API key. Default is `10`.

**Once the above settings in `settings/config.py` are configured, you can manage most other settings through bot commands after the first run.**

### 3.2. Initial Setup for Other Files (Optional - Can be managed by bot commands later)

These files store data used by the inviter accounts. You can create them manually before the first run, or use bot commands to populate them later.

*   **`api/api_keys.txt`**:
    *   Stores API credentials for your Telegram user accounts (clients, not bots).
    *   Format: `api_id:api_hash` (one per line). Obtain these from [my.telegram.org](https://my.telegram.org).
    *   Example:
        ```
        1234567:abcdef1234567890abcdef12345678
        ```
    *   **Bot Command Alternative:** `/add_apikey`, `/remove_apikey`, `/list_apikeys`.

*   **`sessions/` Directory**:
    *   This directory will store `.session` files for your Telegram user accounts. These are generated automatically by Telethon when an account successfully logs in via an API key. Ensure this directory is writable.

*   **`username/usernames.txt`**:
    *   List of target usernames (without `@`) to invite, one per line.
    *   **Bot Command Alternative:** `/add_user`, `/remove_user`, `/clear_users`, `/list_users`, or by uploading a `usernames.txt` file to the bot.
    *   If this file is empty at runtime, the bot will prompt in the console to scrape users from a source chat.

*   **`settings/dest.py`**:
    *   Specifies the target group/channel where users will be invited.
    *   Format: `dest = '@your_target_group'` or `dest = -1001234567890` (for private chats, use the ID).
    *   **Bot Command Alternative:** `/set_dest`, `/view_dest`.

*   **`settings/delay.py`**:
    *   Controls delays (in seconds) between various actions. Default values are provided.
    *   **Bot Command Alternative:** `/set_delay`, `/view_delays`.

*   **`settings/proxy.txt`**:
    *   Optional file for configuring proxies for client accounts.
    *   Format examples:
        *   MTProto: `mtproto:server:port:secret`
        *   HTTP/SOCKS5: `http:host:port:username:password` (user/pass are optional)
    *   **Bot Command Alternative:** `/add_proxy`, `/remove_proxy`, `/list_proxies`, or by uploading a `proxy.txt` file.

## 4. Running the Bot

1.  Navigate to the project's root directory in your terminal or command prompt.
2.  Run the bot using the command:
    ```bash
    python main.py
    ```
3.  Monitor the console output for real-time status updates, progress, and any error messages.
4.  Interact with your control bot on Telegram using the commands listed below. Check the Telegram log channel (if configured) for detailed operational logs.

## 5. Bot Commands

Once the bot is running and you have configured `BOT_TOKEN` and `ADMIN_ID` in `settings/config.py`, you can manage most aspects of the bot via commands sent to your control bot on Telegram:

### General:
*   `/start`: Activates the bot and shows a welcome message.
*   `/settings` or `/config`: View a summary of all current settings.
*   `/get_id <link_or_username>`: Fetches the Telegram ID of a given username, channel link, or joinchat link.
    *   Example: `/get_id @someuser` or `/get_id t.me/publicgroup`

### Delay Management:
*   `/view_delays`: View current delay settings from `settings/delay.py`.
*   `/set_delay <delay_name> <value>`: Set a specific delay.
    *   `<delay_name>` can be: `join`, `invite`, `error`, `accounts`, `clients`.
    *   `<value>` is in seconds (e.g., `/set_delay invite 15`).
    *   *Note: A script restart might be needed for changes to fully apply to active processes.*

### Destination Management:
*   `/view_dest`: View the current target destination group/channel from `settings/dest.py`.
*   `/set_dest <target>`: Set the destination group/channel.
    *   `<target>` can be a username (e.g., `@mygroup`) or a chat ID (e.g., `-100123456789`).
    *   *Note: A script restart is recommended for changes to reliably apply to all client accounts.*

### API Key Management (`api/api_keys.txt`):
*   `/list_apikeys`: List configured API keys (hashes are masked).
*   `/add_apikey <api_id> <api_hash>`: Add a new API key.
*   `/remove_apikey <api_id>`: Remove an API key by its ID.
    *   *Note: A script restart is needed for the main inviting process to use new/removed API keys.*

### Username List Management (`username/usernames.txt`):
*   `/list_users`: List usernames currently in `usernames.txt` (shows a preview if long).
*   `/add_user <username>`: Add a single username to the list.
*   `/remove_user <username>`: Remove a username from the list.
*   `/clear_users`: Clear all usernames from `usernames.txt`.
*   **File Upload**: You can upload a plain text file (e.g., `usernames.txt`) directly to the bot. The bot will process it and overwrite the existing list.
    *   *Note: Changes to the username list require the main inviting process to be restarted or to complete its current cycle to take effect.*

### Proxy Management (`settings/proxy.txt`):
*   `/list_proxies`: List configured proxies (sensitive parts are masked).
*   `/add_proxy <proxy_string>`: Add a new proxy.
    *   Example: `/add_proxy http:proxy.example.com:8080:user:pass`
*   `/remove_proxy <proxy_string>`: Remove an existing proxy (must be an exact match).
*   **File Upload**: You can upload a plain text file (e.g., `proxy.txt`) containing a list of proxies (one per line) to the bot. This will overwrite the existing proxy list.
    *   *Note: A script restart is needed for new proxy configurations to be used by new client account initializations.*

**Important Note on Configuration Changes:** Many settings, especially those related to client initialization (API keys, proxies) or data lists used by the main inviting loop (usernames, destination), may require the main script (`python main.py`) to be restarted for changes to take full effect. The bot will reload module configurations where possible (delays, destination), but cached data or active loops might not reflect changes immediately.

## 6. Important Security Notes

*   **`settings/config.py` is Critical:** Ensure `BOT_TOKEN` and `ADMIN_ID` are correctly and securely set. This file is paramount for bot security and control.
*   **Log Channel Privacy:** Keep your Telegram log channel private.
*   **Responsible Use:** Use this bot responsibly and in accordance with Telegram's Terms of Service. Automation can lead to account restrictions if not used carefully.
*   **Session File Security:** Your `.session` files in the `sessions/` directory are active login tokens. Protect them like passwords.

---

This guide should help you get the bot up and running. If you encounter issues, carefully review your configuration and the bot's log output for clues.
```
