#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import asyncio
import itertools
import random
import logging
import time
import hashlib

from telethon import TelegramClient, errors
from telethon.errors import (SessionPasswordNeededError, FloodWaitError,
                            UserChannelsTooMuchError)
from telethon.tl.functions.messages import AddChatUserRequest
from telethon.tl.functions.channels import (InviteToChannelRequest,
                                            JoinChannelRequest,
                                            GetParticipantsRequest)
from telethon.tl.types import (ChannelParticipantsSearch, InputPeerUser)
from telethon.network import ConnectionTcpMTProxy
from telethon.connection import ConnectionTcpFull


from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage

SETTINGS_DIR = "settings"
USERNAME_DIR = "username"
API_DIR = "api"
DATABASE_DIR = "database"
SESSION_DIR = "session"

DELAY_FILE = os.path.join(SETTINGS_DIR, "delay.py")
CONFIG_FILE = os.path.join(SETTINGS_DIR, "config.py")
DEST_FILE = os.path.join(SETTINGS_DIR, "dest.py")
PROXY_FILE = os.path.join(SETTINGS_DIR, "proxy.txt")

USERNAMES_FILE = os.path.join(USERNAME_DIR, "usernames.txt")
USED_FILE = os.path.join(USERNAME_DIR, "used.txt")

API_KEYS_FILE = os.path.join(API_DIR, "api_keys.txt")

DB_FILE = os.path.join(DATABASE_DIR, "clientbot_test.db")
BOT_DB_FILE = os.path.join(DATABASE_DIR, "bot_data.db")

sys.path.append(SETTINGS_DIR)
sys.path.append(DATABASE_DIR)

import settings_manager
import importlib
import delay
import dest # Import the module itself to allow reloading

from database import pickledb

# from dest import dest # Replaced by `import dest`
from config import MAX_ACCOUNTS_PER_API, BOT_TOKEN, ADMIN_ID, LOG_CHANNEL_ID

logging.basicConfig(level=logging.ERROR, format='%(asctime)s - %(levelname)s - %(message)s')

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

def colored_print(message, color):
    print(f"{color}{message}{RESET}")

os.makedirs(SETTINGS_DIR, exist_ok=True)
os.makedirs(USERNAME_DIR, exist_ok=True)
os.makedirs(API_DIR, exist_ok=True)
os.makedirs(DATABASE_DIR, exist_ok=True)
os.makedirs(SESSION_DIR, exist_ok=True)

accounts = {}
dests = {}
accounts_on_cooldown = {}
session_names = []
last_account_switch_time = 0
inviting_paused = False

bot = Bot(token=BOT_TOKEN, parse_mode=types.ParseMode.HTML)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

bot_db = pickledb.load(BOT_DB_FILE, False)
if not bot_db.get('inviting_paused'):
    bot_db.set('inviting_paused', False)
    bot_db.dump()

def get_file_hash(filepath):
    hasher = hashlib.sha256()
    if not os.path.exists(filepath):
        return None
    with open(filepath, 'rb') as file:
        while True:
            chunk = file.read(4096)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()

async def create_telegram_account(session_name, api_id, api_hash, proxy=None):
    session_path = os.path.join(SESSION_DIR, session_name)
    try:
        if proxy:
            proxy_type, addr, port, username, password = proxy

            if proxy_type.lower() == 'mtproto':
                account = TelegramClient(session_path, api_id, api_hash,
                                         connection=ConnectionTcpMTProxy,
                                         proxy=(addr, port, username))
            else:
                account = TelegramClient(session_path, api_id, api_hash,
                                         connection=ConnectionTcpFull,
                                         proxy=(proxy_type, addr, port, username, password))
        else:
            account = TelegramClient(session_path, api_id, api_hash,
                                     connection=ConnectionTcpFull)

        await account.connect()

        if not await account.is_user_authorized():
            colored_print(f"  ⛔ Сессия {session_name} не авторизована 😔. Пропускаем...", RED)
            await send_log_to_telegram(f"❌ Сессия <b>{session_name}</b> не авторизована 😔.")
            return None

        try:
            me = await account.get_me()
            if not me.first_name:
                raise ValueError("У аккаунта отсутствует имя.")
        except ValueError as e:
            colored_print(f"  ⛔ Ошибка: {e}. Пропускаем сессию {session_name}", RED)
            await send_log_to_telegram(f"❌ Ошибка в сессии <b>{session_name}</b>: отсутствует имя 😢.")
            return None
        except Exception as e:
            colored_print(f"  ⛔ Не удалось получить данные аккаунта {session_name}: {e}. Пропускаем.", RED)
            await send_log_to_telegram(f"❌ Ошибка в сессии <b>{session_name}</b>: не удалось получить данные аккаунта.")
            return None

    except Exception as e:
        colored_print(f"  ⛔ Не удалось создать аккаунт {session_name}: {e}. Пропускаем...", RED)
        await send_log_to_telegram(f"❌ Ошибка при создании аккаунта <b>{session_name}</b>: {e}")
        return None

    return account


async def send_log_to_telegram(message):
    try:
        await bot.send_message(LOG_CHANNEL_ID, message)
    except Exception as e:
        colored_print(f"  ERROR Ошибка при отправке сообщения в лог-канал: {e} 😢", RED)
        await bot.send_message(LOG_CHANNEL_ID, message)


@dp.message_handler(commands=['start'])
async def start_bot(message: types.Message):
    if str(message.from_user.id) == ADMIN_ID:
        start_message = (
            "👋 **Привет, Администратор!**\n\n"
            "🤖 Я ваш бот-помощник для управления приглашениями пользователей в Telegram-группы.\n\n"
            "**Что я умею:**\n"
            "✅ Автоматически приглашать пользователей из списка в целевую группу.\n"
            "✅ Использовать несколько аккаунтов Telegram для обхода ограничений.\n"
            "✅ Работать с прокси для повышения анонимности и стабильности.\n"
            "✅ Вести подробные логи происходящего как в консоли, так и в специальном Telegram-канале.\n"
            "✅ Управляться командами через этот чат (например, /pause, /resume, /status, /settings, /view_delays, /set_delay).\n\n"
            "🚀 Для начала работы, убедитесь, что все конфигурационные файлы (api_keys.txt, usernames.txt, dest.py, и т.д.) настроены правильно.\n\n"
            "🛠️ Если возникнут проблемы, я сообщу об этом в логах с соответствующими эмодзи и инструкциями.\n\n"
            "📡 Ожидаю ваших команд!"
        ) # Updated start message to include new commands
        await message.reply(start_message)

# Helper functions for /settings command (get_proxies_summary removed as it's replaced)

@dp.message_handler(commands=['settings', 'config'])
async def show_settings(message: types.Message):
    if str(message.from_user.id) != ADMIN_ID:
        return await message.reply("❌ This command is only for the admin.")

    # Use the new get_api_keys_list from settings_manager for API keys info
    api_keys_list = settings_manager.get_api_keys_list(mask_hashes=True)
    api_keys_summary_message = f"Configured Keys: <code>{len(api_keys_list)}</code>"
    if api_keys_list:
        if len(api_keys_list) <= 10: # Show details if list is short
             api_keys_summary_message += "\n" + "\n".join([f"- <code>{key}</code>" for key in api_keys_list])
        else:
            api_keys_summary_message += " (List too long to display here, use /list_apikeys)"

    # Use new settings_manager.get_usernames() for usernames info
    current_usernames = settings_manager.get_usernames()
    usernames_count = len(current_usernames)
    usernames_status_message = f"Found: <code>{usernames_count}</code> users."
    if usernames_count > 0 and usernames_count <= 10: # Show a few examples if the list is short
        usernames_status_message += "\nExamples:\n" + "\n".join([f"- <code>{u}</code>" for u in current_usernames[:3]]) # Show first 3
        if usernames_count > 3:
            usernames_status_message += "\n- ..."
    elif usernames_count == 0:
        usernames_status_message = "File found (empty) or not found."

    # Use new settings_manager.get_proxies() for proxy info
    current_proxies = settings_manager.get_proxies()
    proxies_count = len(current_proxies)
    proxies_status_message = f"Found: <code>{proxies_count}</code> proxies."
    if proxies_count > 0 and proxies_count <= 5: # Show a few examples if the list is short, masked
        proxies_status_message += "\nExamples (masked):\n" + "\n".join([f"- <code>{settings_manager.mask_proxy_string(p)}</code>" for p in current_proxies[:3]]) # Show first 3 masked
        if proxies_count > 3:
            proxies_status_message += "\n- ..."
    elif proxies_count == 0:
        proxies_status_message = "File found (empty) or not found / Not used."

    # Update /start command help text if it's not already reflecting all new commands
    start_command_help_update_needed = False # Placeholder, assume it's updated or handle separately

    settings_message = (
        "⚙️ **Current Bot Settings** ⚙️\n\n"
        f"**📜 Destination (`settings/dest.py`):**\n"
        f"- Target: <code>{dest.dest if hasattr(dest, 'dest') else 'Not Set'}</code>\n\n"
        f"**⏱️ Delays (`settings/delay.py` - seconds):**\n"
        f"- After Join: <code>{delay.delay_after_join}</code>\n"
        f"- After Invite: <code>{delay.delay_after_invite}</code>\n"
        f"- After Error: <code>{delay.delay_after_error}</code>\n"
        f"- Between Accounts: <code>{delay.delay_between_accounts}</code>\n"
        f"- Between Clients: <code>{delay.delay_between_clients}</code>\n\n"
        f"**🔑 API Keys (`{settings_manager.API_KEYS_FILE}`):**\n{api_keys_summary_message}\n\n"
        f"**👤 Usernames (`{settings_manager.USERNAMES_FILE}`):**\n{usernames_status_message}\n\n"
        f"**🌐 Proxies (`{settings_manager.PROXY_FILE_SM}`):**\n{proxies_status_message}\n\n"
        f"**🤖 Other Config (`settings/config.py`):**\n"
        f"- Max Accounts per API: <code>{MAX_ACCOUNTS_PER_API}</code>\n"
        f"- Admin ID: <code>{ADMIN_ID}</code>\n"
        f"- Log Channel ID: <code>{LOG_CHANNEL_ID}</code>"
    )
    await message.reply(settings_message, parse_mode=types.ParseMode.HTML)

@dp.message_handler(commands=['view_delays'])
async def view_delays_command(message: types.Message):
    if str(message.from_user.id) != ADMIN_ID:
        return await message.reply("❌ This command is only for the admin.")

    current_delays = settings_manager.get_all_delay_settings()
    if not current_delays:
        # Fallback if file reading failed but module is loaded
        current_delays = {k: v for k, v in vars(delay).items() if k.startswith("delay_")}
        if not current_delays:
            return await message.reply("🔴 Could not read delay settings. Ensure `settings/delay.py` exists and is readable.")

    delay_message_parts = ["⏱️ **Current Delay Settings (from `settings/delay.py`)** ⏱️\n"]
    for key, value in current_delays.items():
        readable_key = key.replace('_', ' ').replace('delay ', '').capitalize()
        delay_message_parts.append(f"- {readable_key}: <code>{value}</code> seconds")

    await message.reply("\n".join(delay_message_parts), parse_mode=types.ParseMode.HTML)


@dp.message_handler(commands=['set_delay'])
async def set_delay_command(message: types.Message):
    if str(message.from_user.id) != ADMIN_ID:
        return await message.reply("❌ This command is only for the admin.")

    args = message.get_args().split()
    if len(args) != 2:
        return await message.reply("ℹ️ Usage: `/set_delay <delay_name> <value>`\n"
                                   "Example: `/set_delay join 7`\n"
                                   "Valid names: `join`, `invite`, `error`, `accounts`, `clients`")

    delay_short_name = args[0].lower()
    try:
        new_value = int(args[1])
        if new_value < 0:
            raise ValueError("Delay value must be a non-negative integer.")
    except ValueError:
        return await message.reply("🔴 Invalid value. Delay must be a non-negative integer.")

    delay_key_map = {
        "join": "delay_after_join",
        "invite": "delay_after_invite",
        "error": "delay_after_error",
        "accounts": "delay_between_accounts",
        "clients": "delay_between_clients"
    }

    if delay_short_name not in delay_key_map:
        valid_names = ", ".join([f"<code>{name}</code>" for name in delay_key_map.keys()])
        return await message.reply(f"🔴 Invalid delay name '<code>{delay_short_name}</code>'.\n"
                                   f"Valid names are: {valid_names}", parse_mode=types.ParseMode.HTML)

    full_delay_key = delay_key_map[delay_short_name]
    success = settings_manager.update_delay_setting(full_delay_key, new_value)

    if success:
        try:
            importlib.reload(delay)
            await message.reply(f"✅ Delay '<code>{delay_short_name}</code>' (<code>{full_delay_key}</code>) updated to <code>{new_value}</code> in <code>settings/delay.py</code>.\n"
                                "The <code>delay</code> module has been reloaded.\n"
                                "🔁 For changes to reliably affect an active inviting session, please restart the script.",
                                parse_mode=types.ParseMode.HTML)
        except Exception as e:
            await message.reply(f"🔴 Delay setting saved to file, but an error occurred while reloading the module: {e}")
    else:
        await message.reply(f"🔴 Failed to update '<code>{full_delay_key}</code>'. Key might be missing in <code>settings/delay.py</code> or file is not writable.")

@dp.message_handler(commands=['view_dest'])
async def view_dest_command(message: types.Message):
    if str(message.from_user.id) != ADMIN_ID:
        return await message.reply("❌ This command is only for the admin.")

    current_dest = settings_manager.get_dest_setting()
    if not current_dest and hasattr(dest, 'dest'): # Fallback to loaded module if file read fails
        current_dest = dest.dest

    if current_dest:
        await message.reply(f"📜 **Current Destination Target:**\n<code>{current_dest}</code>", parse_mode=types.ParseMode.HTML)
    else:
        await message.reply("🔴 Destination not set or could not be read from `settings/dest.py`.")

@dp.message_handler(commands=['set_dest'])
async def set_dest_command(message: types.Message):
    if str(message.from_user.id) != ADMIN_ID:
        return await message.reply("❌ This command is only for the admin.")

    args = message.get_args().split()
    if not args:
        return await message.reply("ℹ️ Usage: `/set_dest <target_username_or_id>`\n"
                                   "Example: `/set_dest @mygroup` or `/set_dest -100123456789`")

    new_target_dest = args[0]

    if not new_target_dest: # Should be caught by split() check but good for safety
         return await message.reply("🔴 Target destination cannot be empty.")

    success = settings_manager.update_dest_setting(new_target_dest)

    if success:
        try:
            importlib.reload(dest)
            global dests # Access the global dests dictionary
            dests.clear() # Clear cached entities

            # Update the global dest variable in main.py's scope
            # This is if other parts of main directly reference `dest` after its initial import.
            # However, the critical part is that `create_telegram_account` uses `dest.dest`.
            # Rebinding the global `dest` name in `main.py`'s scope to the module is good practice.
            # This was changed from `from dest import dest` to `import dest`
            # So, direct access should now be `dest.dest`

            await message.reply(f"✅ Destination updated to <code>{new_target_dest}</code> in <code>settings/dest.py</code>.\n"
                                "The <code>dest</code> module has been reloaded and target entity cache cleared.\n"
                                "🔁 Changes will apply to new account initializations. For active operations on already initialized accounts, a script restart might be safest.",
                                parse_mode=types.ParseMode.HTML)
        except Exception as e:
            await message.reply(f"🔴 Destination setting saved to file, but an error occurred: {e}")
    else:
        await message.reply(f"🔴 Failed to update destination. Ensure <code>settings/dest.py</code> is writable.")

@dp.message_handler(commands=['add_apikey'])
async def add_apikey_command(message: types.Message):
    if str(message.from_user.id) != ADMIN_ID:
        return await message.reply("❌ This command is only for the admin.")

    args = message.get_args().split()
    if len(args) != 2:
        return await message.reply("ℹ️ Usage: `/add_apikey <api_id> <api_hash>`")

    api_id, api_hash = args[0], args[1]

    # Basic validation (can be more sophisticated)
    if not api_id.isdigit():
        return await message.reply("🔴 Invalid API ID: Must be a number.")
    if not api_hash or len(api_hash) < 30: # Basic check for hash length/presence
        return await message.reply("🔴 Invalid API Hash: Appears too short or empty.")

    if settings_manager.add_api_key(api_id, api_hash):
        await message.reply(f"✅ API Key <code>{api_id}:{'*' * len(api_hash)}</code> added successfully.\n"
                            "🔁 Restart the script's main inviting process for the new key to be used in account cycling.",
                            parse_mode=types.ParseMode.HTML)
    else:
        await message.reply(f"🔴 Failed to add API Key. It might be a duplicate or an error occurred.", parse_mode=types.ParseMode.HTML)

@dp.message_handler(commands=['remove_apikey'])
async def remove_apikey_command(message: types.Message):
    if str(message.from_user.id) != ADMIN_ID:
        return await message.reply("❌ This command is only for the admin.")

    args = message.get_args().split()
    if len(args) != 1:
        return await message.reply("ℹ️ Usage: `/remove_apikey <api_id>`")

    api_id_to_remove = args[0]
    if not api_id_to_remove.isdigit():
        return await message.reply("🔴 Invalid API ID: Must be a number.")

    if settings_manager.remove_api_key(api_id_to_remove):
        await message.reply(f"✅ API Key with ID <code>{api_id_to_remove}</code> removed successfully (if it existed).\n"
                            "🔁 Restart the script's main inviting process to stop using this key.",
                            parse_mode=types.ParseMode.HTML)
    else:
        await message.reply(f"🔴 Failed to remove API Key with ID <code>{api_id_to_remove}</code> (it might not exist or an error occurred).",
                            parse_mode=types.ParseMode.HTML)

@dp.message_handler(commands=['list_apikeys'])
async def list_apikeys_command(message: types.Message):
    if str(message.from_user.id) != ADMIN_ID:
        return await message.reply("❌ This command is only for the admin.")

    keys = settings_manager.get_api_keys_list(mask_hashes=True)
    if not keys:
        return await message.reply(f"ℹ️ No API keys found in `{settings_manager.API_KEYS_FILE}`.")

    response_message = "🔑 **Configured API Keys:**\n"
    for key_entry in keys:
        response_message += f"- <code>{key_entry}</code>\n"

    await message.reply(response_message, parse_mode=types.ParseMode.HTML)

@dp.message_handler(commands=['add_user'])
async def add_user_command(message: types.Message):
    if str(message.from_user.id) != ADMIN_ID:
        return await message.reply("❌ This command is only for the admin.")

    args = message.get_args().split()
    if not args:
        return await message.reply("ℹ️ Usage: `/add_user <username>` (e.g., `/add_user someuser`)")

    username_to_add = args[0].lstrip('@')
    if not username_to_add:
        return await message.reply("🔴 Username cannot be empty.")

    if settings_manager.add_username_to_file(username_to_add):
        await message.reply(f"✅ Username <code>{username_to_add}</code> added to <code>{settings_manager.USERNAMES_FILE}</code>.\n"
                            "🔁 Restart the main inviting process for this change to take effect in the current session.",
                            parse_mode=types.ParseMode.HTML)
    else:
        await message.reply(f"🔴 Failed to add username <code>{username_to_add}</code>. It might already exist or an error occurred.",
                            parse_mode=types.ParseMode.HTML)

@dp.message_handler(commands=['remove_user'])
async def remove_user_command(message: types.Message):
    if str(message.from_user.id) != ADMIN_ID:
        return await message.reply("❌ This command is only for the admin.")

    args = message.get_args().split()
    if not args:
        return await message.reply("ℹ️ Usage: `/remove_user <username>`")

    username_to_remove = args[0].lstrip('@')
    if not username_to_remove:
        return await message.reply("🔴 Username cannot be empty.")

    if settings_manager.remove_username_from_file(username_to_remove):
        await message.reply(f"✅ Username <code>{username_to_remove}</code> removed from <code>{settings_manager.USERNAMES_FILE}</code> (if it existed).\n"
                            "🔁 Restart the main inviting process for this change to take effect.",
                            parse_mode=types.ParseMode.HTML)
    else:
        await message.reply(f"🔴 Failed to remove username <code>{username_to_remove}</code>. It might not exist or an error occurred.",
                            parse_mode=types.ParseMode.HTML)

@dp.message_handler(commands=['clear_users'])
async def clear_users_command(message: types.Message):
    if str(message.from_user.id) != ADMIN_ID:
        return await message.reply("❌ This command is only for the admin.")

    if settings_manager.clear_usernames_file():
        await message.reply(f"✅ All usernames cleared from <code>{settings_manager.USERNAMES_FILE}</code>.\n"
                            "🔁 Restart the main inviting process for this change to take effect.",
                            parse_mode=types.ParseMode.HTML)
    else:
        await message.reply(f"🔴 Failed to clear usernames from <code>{settings_manager.USERNAMES_FILE}</code>.",
                            parse_mode=types.ParseMode.HTML)

@dp.message_handler(commands=['list_users'])
async def list_users_command(message: types.Message):
    if str(message.from_user.id) != ADMIN_ID:
        return await message.reply("❌ This command is only for the admin.")

    usernames = settings_manager.get_usernames()
    if not usernames:
        return await message.reply(f"ℹ️ No usernames found in <code>{settings_manager.USERNAMES_FILE}</code>.",
                                   parse_mode=types.ParseMode.HTML)

    response_message = f"👥 **Usernames in <code>{settings_manager.USERNAMES_FILE}</code> ({len(usernames)} total):**\n"

    # Display a limited number of usernames to avoid message length limits
    display_limit = 20
    for i, uname in enumerate(usernames):
        if i < display_limit:
            response_message += f"- <code>{uname}</code>\n"
        else:
            response_message += f"\n...and {len(usernames) - display_limit} more."
            break

    await message.reply(response_message, parse_mode=types.ParseMode.HTML)

@dp.message_handler(content_types=types.ContentType.DOCUMENT)
async def handle_document_upload(message: types.Message):
    if str(message.from_user.id) != ADMIN_ID:
        # Optionally, inform non-admins they can't do this, or just ignore.
        return

    # Handle usernames.txt upload
    if message.document and (message.document.file_name.lower() == 'usernames.txt' or \
                              (message.document.mime_type == 'text/plain' and 'user' in message.caption.lower() if message.caption else False)):
        try:
            file_info = await bot.get_file(message.document.file_id)
            downloaded_file = await bot.download_file(file_info.file_path)
            content = downloaded_file.read().decode('utf-8')
            new_usernames = [line.strip() for line in content.splitlines() if line.strip() and not line.strip().startswith('#')]

            if settings_manager.overwrite_usernames_file(new_usernames):
                await message.reply(f"✅ Successfully processed uploaded usernames file <code>{message.document.file_name}</code>.\n"
                                    f"<code>{len(new_usernames)}</code> usernames loaded into <code>{settings_manager.USERNAMES_FILE}</code>.\n"
                                    "🔁 Restart the main inviting process for these changes to take effect.",
                                    parse_mode=types.ParseMode.HTML)
            else:
                await message.reply(f"🔴 Failed to overwrite usernames from uploaded file.", parse_mode=types.ParseMode.HTML)
        except Exception as e:
            await message.reply(f"🔴 Error processing uploaded usernames file: {e}", parse_mode=types.ParseMode.HTML)
            logging.error(f"Error processing uploaded usernames.txt: {e}")
        return # Processed as usernames.txt

    # Handle proxy.txt upload
    if message.document and (message.document.file_name.lower() == 'proxy.txt' or \
                              (message.document.mime_type == 'text/plain' and 'proxy' in message.caption.lower() if message.caption else False) ):
        try:
            file_info = await bot.get_file(message.document.file_id)
            downloaded_file = await bot.download_file(file_info.file_path)
            content = downloaded_file.read().decode('utf-8')
            new_proxies = [line.strip() for line in content.splitlines() if line.strip() and not line.strip().startswith('#')]

            if settings_manager.overwrite_proxies_file(new_proxies):
                await message.reply(f"✅ Successfully processed uploaded proxy file <code>{message.document.file_name}</code>.\n"
                                    f"<code>{len(new_proxies)}</code> proxies loaded into <code>{settings_manager.PROXY_FILE_SM}</code>.\n"
                                    "🔁 Restart the main inviting process for these changes to take effect for new account initializations.",
                                    parse_mode=types.ParseMode.HTML)
            else:
                await message.reply(f"🔴 Failed to overwrite proxies from uploaded file.", parse_mode=types.ParseMode.HTML)
        except Exception as e:
            await message.reply(f"🔴 Error processing uploaded proxy file: {e}", parse_mode=types.ParseMode.HTML)
            logging.error(f"Error processing uploaded proxy.txt: {e}")
        return # Processed as proxy.txt

    elif message.document: # If it's another document type not caught above
        await message.reply("ℹ️ Unrecognized document. If you're trying to update settings, please upload a correctly named `.txt` file (e.g., `usernames.txt`, `proxy.txt`) or use the specific commands.")


@dp.message_handler(commands=['add_proxy'])
async def add_proxy_command(message: types.Message):
    if str(message.from_user.id) != ADMIN_ID:
        return await message.reply("❌ This command is only for the admin.")

    proxy_string = message.get_args().strip()
    if not proxy_string:
        return await message.reply("ℹ️ Usage: `/add_proxy <proxy_string>` (e.g., `/add_proxy http:host:port:user:pass`)")

    if settings_manager.add_proxy_to_file(proxy_string):
        await message.reply(f"✅ Proxy <code>{settings_manager.mask_proxy_string(proxy_string)}</code> added to <code>{settings_manager.PROXY_FILE_SM}</code>.\n"
                            "🔁 Restart the main inviting process for this change to be used.",
                            parse_mode=types.ParseMode.HTML)
    else:
        await message.reply(f"🔴 Failed to add proxy. It might already exist or an error occurred.",
                            parse_mode=types.ParseMode.HTML)

@dp.message_handler(commands=['remove_proxy'])
async def remove_proxy_command(message: types.Message):
    if str(message.from_user.id) != ADMIN_ID:
        return await message.reply("❌ This command is only for the admin.")

    proxy_string = message.get_args().strip()
    if not proxy_string:
        return await message.reply("ℹ️ Usage: `/remove_proxy <exact_proxy_string_to_remove>`")

    if settings_manager.remove_proxy_from_file(proxy_string):
        await message.reply(f"✅ Proxy <code>{settings_manager.mask_proxy_string(proxy_string)}</code> removed from <code>{settings_manager.PROXY_FILE_SM}</code> (if it existed).\n"
                            "🔁 Restart the main inviting process.",
                            parse_mode=types.ParseMode.HTML)
    else:
        await message.reply(f"🔴 Failed to remove proxy. It might not exist or an error occurred.",
                            parse_mode=types.ParseMode.HTML)

@dp.message_handler(commands=['list_proxies'])
async def list_proxies_command(message: types.Message):
    if str(message.from_user.id) != ADMIN_ID:
        return await message.reply("❌ This command is only for the admin.")

    proxies = settings_manager.get_proxies()
    if not proxies:
        return await message.reply(f"ℹ️ No proxies found in <code>{settings_manager.PROXY_FILE_SM}</code>.",
                                   parse_mode=types.ParseMode.HTML)

    response_message = f"🌐 **Configured Proxies ({len(proxies)} total):**\n"
    display_limit = 15
    for i, p_str in enumerate(proxies):
        if i < display_limit:
            response_message += f"- <code>{settings_manager.mask_proxy_string(p_str)}</code>\n"
        else:
            response_message += f"\n...and {len(proxies) - display_limit} more."
            break

    await message.reply(response_message, parse_mode=types.ParseMode.HTML)

@dp.message_handler(commands=['get_id'])
async def get_id_command(message: types.Message):
    if str(message.from_user.id) != ADMIN_ID:
        return await message.reply("❌ This command is only for the admin.")

    args = message.get_args().strip()
    if not args:
        await message.reply("ℹ️ Please provide a Telegram link or username after the command.\n"
                            "Example: `/get_id @username` or `/get_id t.me/joinchat/link` or `/get_id https://t.me/publicchannel`")
        return

    identifier = args

    active_client = None
    if not accounts: # Ensure accounts is populated
        await message.reply("🔴 No client accounts loaded. Cannot perform ID lookup.")
        return

    for client_session in accounts.values():
        try: # Check if client is connected and authorized
            if client_session.is_connected() and await client_session.is_user_authorized():
                active_client = client_session
                break
        except Exception: # Catch potential errors if a session is bad
            continue

    if not active_client: # Fallback if no connected and authorized client found
        active_client = next(iter(accounts.values()), None)

    if not active_client:
        await message.reply("🔴 No active or available client sessions to perform ID lookup. Please ensure accounts are loaded and authorized.")
        return

    try:
        client_username = "N/A"
        if hasattr(active_client, 'get_me') and callable(active_client.get_me):
            me_info = await active_client.get_me()
            if me_info and hasattr(me_info, 'username'):
                client_username = me_info.username

        colored_print(f"ℹ️ Admin requested ID for: {identifier} using client {client_username}", YELLOW)

        entity = await active_client.get_entity(identifier)

        entity_type = "Unknown"
        if isinstance(entity, User):
            entity_type = "User"
        elif isinstance(entity, Chat):
            entity_type = "Chat"
        elif isinstance(entity, Channel):
            entity_type = "Channel"
            if entity.megagroup: # Distinguish between broadcast channel and supergroup/megagroup
                entity_type = "Megagroup (Channel)"

        title = getattr(entity, 'title', None) or \
                getattr(entity, 'username', None) or \
                (f"{getattr(entity, 'first_name', '')} {getattr(entity, 'last_name', '')}".strip()) or \
                "N/A"
        if not title or title == "N/A": # If title is still N/A, try to construct from first/last name for users
             if isinstance(entity, User) and (entity.first_name or entity.last_name):
                 title = f"{entity.first_name or ''} {entity.last_name or ''}".strip()


        response_text = (
            f"✅ **Entity Found!**\n\n"
            f"🆔 **ID:** `{entity.id}`\n"
            f"👤 **Type:** `{entity_type}`\n"
            f"📝 **Title/Name/Username:** `{title}`"
        )
        # Use HTML for consistency with other commands
        await message.reply(response_text, parse_mode=types.ParseMode.HTML.replace("**", "b").replace("`", "code"))


    except ValueError as e:
        await message.reply(f"🔴 Could not resolve identifier: <code>{identifier}</code>.\n"
                            f"Error: <code>{e}</code>\n"
                            f"ℹ️ Ensure the link/username is correct and accessible by one of the bot's client accounts.", parse_mode=types.ParseMode.HTML)
    except errors.rpcerrorlist.UsernameInvalidError as e:
        await message.reply(f"🔴 Invalid username or not found: <code>{identifier}</code>.\nError: <code>{e}</code>", parse_mode=types.ParseMode.HTML)
    except errors.RPCError as e:
        await message.reply(f"🔴 Telegram API error while resolving <code>{identifier}</code>:\n<code>{e}</code>", parse_mode=types.ParseMode.HTML)
    except Exception as e:
        colored_print(f"🔴 Unexpected error in /get_id for {identifier}: {e}", RED)
        await message.reply(f"🔴 An unexpected error occurred: <code>{e}</code>", parse_mode=types.ParseMode.HTML)


async def main():
    global last_account_switch_time, session_names, inviting_paused, db, dests # Added dests to global

    colored_print("🚀🚀🚀 Запуск скрипта!  Начинаем работу! ✨✨✨", GREEN)
    await send_log_to_telegram("🚀🚀🚀 <b>Запуск скрипта!</b> Начинаем работу! ✨✨✨")

    try:
        with open(API_KEYS_FILE, 'r') as f:
            api_keys = [line.strip().split(':') for line in f if line.strip()]
    except FileNotFoundError:
        colored_print(f"  ⛔ Файл {API_KEYS_FILE} не найден 😢. Пожалуйста, добавьте файл с API ключами.", RED)
        await send_log_to_telegram(f"❌ Файл <code>{API_KEYS_FILE}</code> не найден 😢. Пожалуйста, добавьте файл с API ключами.")
        sys.exit(1)

    if not api_keys:
        colored_print(f"  ⛔ Файл {API_KEYS_FILE} пустой 🫥. Необходимо добавить API ключи.", RED)
        await send_log_to_telegram(f"❌ Файл <code>{API_KEYS_FILE}</code> пустой 🫥. Необходимо добавить API ключи.")
        sys.exit(1)

    api_key_cycle = itertools.cycle(api_keys)

    proxies = []
    if os.path.exists(PROXY_FILE):
        try:
            with open(PROXY_FILE, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            parts = line.split(':')
                            if len(parts) == 3:
                                proxy_type = 'mtproto'
                                addr, port, secret = parts
                                proxies.append((proxy_type, addr, int(port), secret, None))
                            elif len(parts) >= 2:
                                if len(parts) == 4:
                                      proxy_type, addr, port, userpass = parts
                                      username, password = userpass.split(':', 1)
                                elif len(parts) == 2:
                                    addr, port = parts
                                    proxy_type = 'http'
                                    username, password = None, None
                                elif len(parts) == 3:
                                    try:
                                        proxy_type, addr, port = parts
                                        username, password = None, None
                                    except ValueError:
                                        addr, port, userpass = parts
                                        username, password = userpass.split(':', 1)
                                        proxy_type = 'http'
                                proxies.append((proxy_type, addr, int(port), username, password))
                            else:
                                 raise ValueError(f"Неверный формат прокси в строке: {line}")
                        except ValueError as e:
                            colored_print(f"  ⚠ Ошибка при чтении прокси: {e}. Строка: {line}. Пропускаем...", YELLOW)
                            continue


        except FileNotFoundError:
            colored_print(f"  ⚠ Файл {PROXY_FILE} не найден. Работаем без прокси.", YELLOW)
            await send_log_to_telegram("⚠ Файл с прокси не найден. Работаем без прокси.")
        except Exception as e:
            colored_print(f"  ⛔ Ошибка при чтении файла {PROXY_FILE}: {e}. Продолжаем работу без прокси.", RED)
            await send_log_to_telegram(f"❌ Ошибка при чтении файла с прокси: {e}. Продолжаем работу без прокси.")
    else:
        colored_print(f"  ⚠ Файл {PROXY_FILE} не найден. Работаем без прокси.", YELLOW)
        await send_log_to_telegram("⚠ Файл с прокси не найден. Работаем без прокси.")

    proxy_cycle = itertools.cycle(proxies) if proxies else None

    session_names = [f for f in os.listdir(SESSION_DIR) if f.endswith('.session')]
    if not session_names:
        colored_print(f"  ⛔ Папка {SESSION_DIR} пустая 🫥. Пожалуйста, добавьте файлы сессий Telegram.", RED)
        await send_log_to_telegram(f"❌ Папка <code>{SESSION_DIR}</code> пустая 🫥. Пожалуйста, добавьте файлы сессий Telegram.")
        sys.exit(1)

    api_key_counts = {}
    valid_session_names = []
    for session_name in session_names:
        colored_print(f'  ✅ Пробуем авторизовать аккаунт {session_name} 💫', GREEN)
        api_id, api_hash = next(api_key_cycle)
        proxy = next(proxy_cycle) if proxy_cycle else None

        account = await create_telegram_account(session_name, api_id, api_hash, proxy)

        if account:
            try:
                # Ensure dests is a dictionary
                if not isinstance(dests, dict): # Should be initialized as {} globally
                    dests = {}
                dests[session_name] = await account.get_entity(dest.dest) # Use dest.dest
                valid_session_names.append(session_name)
                accounts[session_name] = account
                colored_print(f'    🟢 Аккаунт {session_name} успешно авторизован! ✨', GREEN)
                await send_log_to_telegram(f"✅ Аккаунт <b>{session_name}</b> успешно авторизован! ✨")

                if (api_id, api_hash) not in api_key_counts:
                    api_key_counts[(api_id, api_hash)] = 0
                api_key_counts[(api_id, api_hash)] += 1
                if api_key_counts[(api_id, api_hash)] > MAX_ACCOUNTS_PER_API:
                    colored_print(f"  ⛔ Превышено максимальное количество аккаунтов ({MAX_ACCOUNTS_PER_API}) на одном ключе API {api_id}:{api_hash}. 🤯", RED)
                    await send_log_to_telegram(f"❌ Превышено максимальное количество аккаунтов на API: <b>{api_id}:{api_hash}</b>. 🤯")
                    sys.exit(1)
            except Exception as e:
                colored_print(f"  ⛔ Не удалось получить информацию о целевой группе для аккаунта {session_name}: {e}. Пропускаем.", RED)
                await send_log_to_telegram(f"❌ Ошибка в сессии <b>{session_name}</b>: Не удалось получить информацию о целевой группе 😔.")
                continue
        else:
            pass


    if not valid_session_names:
        colored_print("  ⛔ Нет активных аккаунтов 😭. Завершаем работу.", RED)
        await send_log_to_telegram("❌ Нет активных аккаунтов 😭. Завершаем работу.")
        sys.exit(1)

    session_names = valid_session_names
    colored_print('🏁 Скрипт запущен и готов к работе! 💪🎉', GREEN)
    await send_log_to_telegram("🏁 Скрипт запущен и готов к работе! 💪🎉")

    db = pickledb.load(DB_FILE, True)


    current_usernames_hash = get_file_hash(USERNAMES_FILE)
    previous_usernames_hash = db.get('usernames_hash')

    if current_usernames_hash != previous_usernames_hash:
        colored_print("  🔄 Файл со списком пользователей изменился 🔄. Начинаем с начала 😉.", GREEN)
        await send_log_to_telegram("🔄 Файл со списком пользователей изменился. Начинаем с начала 😉.")
        db.set('start', 0)
        db.set('usernames_hash', current_usernames_hash)
        ind = 0
    else:
        ind = int(db.get('start')) if db.get('start') is not None else 0
        with open(USERNAMES_FILE, 'r') as f:
            usernames_list = [line.strip() for line in f]
        start_username = usernames_list[ind] if ind < len(usernames_list) else "неизвестен 🤷"

        colored_print(f'  🔄 Продолжаем с пользователя {start_username} (номер {ind}) 🔄', GREEN)
        await send_log_to_telegram(f"🔄 Продолжаем с пользователя <b>{start_username}</b> (номер <b>{ind}</b>) 🔄")

    joined_group = {session_name: False for session_name in session_names}

    try:
        with open(USERNAMES_FILE, 'r') as f:
            usernames = [line.strip() for line in f]
    except FileNotFoundError:
        colored_print(f"  ⛔ Файл {USERNAMES_FILE} не найден 😢.", RED)
        await send_log_to_telegram(f"❌ Файл <code>{USERNAMES_FILE}</code> не найден 😢.")
        usernames = []

    if not usernames:
        while True:
            source_chat_url = input(f"  📝 Файл {USERNAMES_FILE} пустой 🫥. Введите ссылку на чат, откуда брать пользователей: ")
            try:
                if valid_session_names:
                    account = accounts[valid_session_names[0]]
                    source_chat = await account.get_entity(source_chat_url)
                    break
                else:
                    colored_print("  ⛔ Нет активных аккаунтов для получения информации о чате 😭. Завершаем работу.", RED)
                    await send_log_to_telegram("❌ Нет активных аккаунтов для получения информации о чате 😭. Завершаем работу.")
                    sys.exit(1)
            except ValueError:
                colored_print("  ⚠ Некорректная ссылка 🤕. Попробуйте ещё раз.", RED)
                await send_log_to_telegram(f"⚠ Некорректная ссылка 🤕: <code>{source_chat_url}</code>")
            except Exception as e:
                colored_print(f"  ⛔ Не удалось получить информацию о чате: {e}", RED)
                await send_log_to_telegram(f"❌ Не удалось получить информацию о чате: <code>{source_chat_url}</code>: {e}")
                break

        try:
            participants = await account(GetParticipantsRequest(
                source_chat,
                ChannelParticipantsSearch(''),
                0, 1000,
                hash=0
            ))
            usernames = [user.username for user in participants.users if user.username]
            await send_log_to_telegram(f"✅ Получили список пользователей из чата <code>{source_chat_url}</code> ({len(usernames)} человек) 🎉.  Начинаем работу! 🤝")

        except Exception as e:
            colored_print(f"  ⛔ Не удалось получить список участников чата: {e}", RED)
            await send_log_to_telegram(f"❌ Не удалось получить список участников чата: {e}")
            sys.exit(1)


    while True:
        if bot_db.get('inviting_paused'):
            await asyncio.sleep(5)
            continue

        if ind >= len(usernames):
            colored_print('  🎉🎉🎉 Все пользователи обработаны! Успешное завершение! 🎉🎉🎉', GREEN)
            await send_log_to_telegram("🎉🎉🎉 Все пользователи обработаны! Успешное завершение! 🎉🎉🎉")
            break

        username = usernames[ind]

        current_time = time.time()
        if current_time - last_account_switch_time < delay.delay_between_clients:
            remaining_delay = delay.delay_between_clients - (current_time - last_account_switch_time)
            colored_print(f"  ⏳ Ждём {remaining_delay:.2f} секунд перед сменой аккаунта ⏱", YELLOW)
            await asyncio.sleep(remaining_delay)

        rd_session_name = random.choice(session_names)
        colored_print(f'  🔄 Переключаемся на аккаунт {rd_session_name} ⚙️', GREEN)
        last_account_switch_time = time.time()

        account = accounts[rd_session_name]
        dest_entity = dests[rd_session_name]

        if rd_session_name in accounts_on_cooldown:
            remaining_cooldown = accounts_on_cooldown[rd_session_name] - time.time()
            if remaining_cooldown > 0:
                colored_print(f'  ⏰ Аккаунт {rd_session_name} на перерыве ещё {remaining_cooldown:.2f} секунд 🛌. Пропускаем...', RED)
                await asyncio.sleep(delay.delay_between_accounts)

                all_on_cooldown = True
                for s_name in session_names:
                    if s_name not in accounts_on_cooldown or accounts_on_cooldown[s_name] <= time.time():
                        all_on_cooldown = False
                        break
                if all_on_cooldown:
                    colored_print("  ⚠ Все аккаунты на перерыве 😴. Завершаем работу.", RED)
                    await send_log_to_telegram("⚠ Все аккаунты на перерыве 😴. Завершаем работу.")
                    sys.exit(1)

                continue
            else:
                del accounts_on_cooldown[rd_session_name]

        try:
            user = await account.get_input_entity(username)

            if not joined_group[rd_session_name]:
                try:
                    await account(JoinChannelRequest(dest_entity))
                    colored_print(f"    ➕ Аккаунт {rd_session_name} вступил в целевую группу ✅", GREEN)
                    await send_log_to_telegram(f"➕ Аккаунт <b>{rd_session_name}</b> вступил в целевую группу ✅")
                    joined_group[rd_session_name] = True
                    await asyncio.sleep(delay.delay_after_join)
                except errors.rpcerrorlist.ImportBotAuthorizationRequiredError:
                    colored_print(f"    ⛔ Аккаунт {rd_session_name} - бот 🤖. Боты не могут вступать в группы.", RED)
                    await send_log_to_telegram(f"⚠ Аккаунт <b>{rd_session_name}</b> - бот 🤖.")
                    joined_group[rd_session_name] = True
                    await asyncio.sleep(delay.delay_after_error)
                    continue
                except Exception as e:
                    colored_print(f"    ⛔ Ошибка при вступлении аккаунта {rd_session_name} в целевую группу: {e}", RED)
                    await send_log_to_telegram(f"⚠ Ошибка при вступлении аккаунта <b>{rd_session_name}</b> в целевую группу: {e}")
                    await asyncio.sleep(delay.delay_after_error)
                    continue

            if isinstance(user, InputPeerUser):
                await account(InviteToChannelRequest(dest_entity, [user]))
                colored_print(f"    ✉ {ind + 1}: Пользователю @{username} отправлено приглашение 📨", GREEN)

                participants = await account(GetParticipantsRequest(
                    dest_entity,
                    ChannelParticipantsSearch(username),
                    0, 1,
                    hash=0
                ))
                if participants.users:
                    colored_print(f"    ✅ {ind + 1}: Пользователь @{username} успешно добавлен! 🎉", GREEN)
                    await send_log_to_telegram(f"✅ [{ind+1}/{len(usernames)}] Пользователь <b>@{username}</b> успешно добавлен! 🎉 (аккаунт <b>{rd_session_name}</b>)")
                    with open(USED_FILE, 'a') as used_file:
                        used_file.write(f"{username}\n")
                else:
                    colored_print(f"    ⚠ {ind + 1}: Не удалось добавить пользователя @{username} 😔", RED)
            else:
                colored_print(f"    ⚠ {ind + 1}: Нельзя пригласить @{username}, это не пользователь 🤷 (возможно, канал, чат или бот).", RED)
                await send_log_to_telegram(f"⚠ Нельзя пригласить @{username}, это не пользователь 🤷.")
                await asyncio.sleep(delay.delay_after_error) # Using delay.
                continue

        except ValueError: # User not found
            colored_print(f'      🟡 {ind + 1}/{len(usernames)}: Пользователь @{username} не найден аккаунтом {rd_session_name}. Пропускаем. 🤷', YELLOW)
            await send_log_to_telegram(f"🟡 [{ind+1}/{len(usernames)}] Пользователь <b>@{username}</b> не найден аккаунтом {rd_session_name}. Пропускаем. 🤷")
            await asyncio.sleep(random.uniform(1,3)) # Short random delay
        except FloodWaitError as e:
            flood_seconds = e.seconds
            colored_print(f'      🌊 FloodWaitError для аккаунта {rd_session_name}: необходимо подождать {flood_seconds} секунд. Аккаунт будет на перерыве. ⏳', RED)
            await send_log_to_telegram(f"🌊 FloodWaitError для аккаунта <b>{rd_session_name}</b>: необходимо подождать {flood_seconds} секунд. Аккаунт будет на перерыве. ⏳")
            accounts_on_cooldown[rd_session_name] = time.time() + flood_seconds
            ind -=1
        except UserChannelsTooMuchError:
            colored_print(f"    🔴 Аккаунт {rd_session_name} уже состоит в слишком большом количестве каналов/групп. 🤯 Пропускаем аккаунт.", RED)
            await send_log_to_telegram(f"🔴 Аккаунт <b>{rd_session_name}</b> уже состоит в слишком большом количестве каналов/групп. 🤯 Пропускаем аккаунт.")
            if rd_session_name in session_names: session_names.remove(rd_session_name)
            if not session_names:
                colored_print("  🔴 Все доступные аккаунты были удалены (слишком много каналов). Завершение работы. 💔", RED)
                await send_log_to_telegram("🔴 Все доступные аккаунты были удалены (слишком много каналов). Завершение работы. 💔")
                sys.exit(1)
            ind -=1
        except errors.UserPrivacyRestrictedError:
            colored_print(f"    🛡️ {ind + 1}/{len(usernames)}: Пользователь @{username} имеет настройки приватности, не позволяющие его пригласить с аккаунта {rd_session_name}. Пропускаем. 😔", YELLOW)
            await send_log_to_telegram(f"🛡️ [{ind+1}/{len(usernames)}] Пользователь <b>@{username}</b> имеет настройки приватности (аккаунт <b>{rd_session_name}</b>). Пропускаем. 😔")
            with open(USED_FILE, 'a') as used_file:
                used_file.write(f"{username} #PrivacyRestricted\n")
            await asyncio.sleep(delay.delay_after_invite)
        except errors.ChatAdminRequiredError:
            colored_print(f"    👮 Аккаунт {rd_session_name} не имеет прав администратора в целевой группе для приглашения (или приглашения закрыты). Проверьте права. Пропускаем аккаунт. 😔", RED)
            await send_log_to_telegram(f"👮 Аккаунт <b>{rd_session_name}</b> не имеет прав администратора в целевой группе <code>{dest}</code> (или приглашения закрыты). Проверьте права. Пропускаем аккаунт. 😔")
            if rd_session_name in session_names: session_names.remove(rd_session_name)
            if not session_names:
                colored_print("  🔴 Все доступные аккаунты не имеют прав администратора. Завершение работы. 💔", RED)
                await send_log_to_telegram("🔴 Все доступные аккаунты не имеют прав администратора. Завершение работы. 💔")
                sys.exit(1)
            ind -=1
        except errors.UserNotMutualContactError:
            colored_print(f"    🤝 {ind + 1}/{len(usernames)}: Пользователь @{username} не является взаимным контактом для аккаунта {rd_session_name} и не может быть приглашен (нетипично для групп). Пропускаем. 😔", YELLOW)
            await send_log_to_telegram(f"🤝 [{ind+1}/{len(usernames)}] Пользователь <b>@{username}</b> не является взаимным контактом для <b>{rd_session_name}</b> (нетипично для групп). Пропускаем. 😔")
            await asyncio.sleep(delay.delay_after_invite)
        except errors.rpcerrorlist.BotGroupsBlockedError:
            colored_print(f"    🚫 Бот {rd_session_name} заблокирован в группе {dest} или не может писать сообщения. Пропускаем аккаунт.", RED)
            await send_log_to_telegram(f"🚫 Бот <b>{rd_session_name}</b> заблокирован в группе <code>{dest}</code> или не может писать сообщения. Пропускаем аккаунт.")
            if rd_session_name in session_names: session_names.remove(rd_session_name)
            if not session_names:
                colored_print("  🔴 Все доступные аккаунты-боты заблокированы. Завершение работы. 💔", RED)
                await send_log_to_telegram("🔴 Все доступные аккаунты-боты заблокированы. Завершение работы. 💔")
                sys.exit(1)
            ind -=1
        except errors.rpcerrorlist.UserKickedError:
            colored_print(f"    🚫 {ind + 1}/{len(usernames)}: Пользователь @{username} был ранее удален из группы и не может быть приглашен снова. Пропускаем. 😔", YELLOW)
            await send_log_to_telegram(f"🚫 [{ind+1}/{len(usernames)}] Пользователь <b>@{username}</b> был ранее удален из группы и не может быть приглашен снова. Пропускаем. 😔")
            with open(USED_FILE, 'a') as used_file:
                used_file.write(f"{username} #Kicked\n")
            await asyncio.sleep(delay.delay_after_invite)
        except errors.rpcerrorlist.ChatWriteForbiddenError:
            colored_print(f"    ✍️ Аккаунт {rd_session_name} не имеет права писать/приглашать в целевую группу {dest} (возможно, группа только для чтения или аккаунт ограничен). Пропускаем аккаунт.", RED)
            await send_log_to_telegram(f"✍️ Аккаунт <b>{rd_session_name}</b> не имеет права писать/приглашать в целевую группу <code>{dest}</code>. Пропускаем аккаунт.")
            if rd_session_name in session_names: session_names.remove(rd_session_name)
            if not session_names:
                colored_print("  🔴 Все доступные аккаунты не могут писать/приглашать в целевую группу. Завершение работы. 💔", RED)
                await send_log_to_telegram("🔴 Все доступные аккаунты не могут писать/приглашать в целевую группу. Завершение работы. 💔")
                sys.exit(1)
            ind -=1
        except errors.rpcerrorlist.UsersTooMuchError:
            colored_print(f"    📈 Аккаунт {rd_session_name} достиг лимита приглашений на сегодня. Аккаунт будет на перерыве до завтра. ⏳ Пропускаем аккаунт.", RED)
            await send_log_to_telegram(f"📈 Аккаунт <b>{rd_session_name}</b> достиг лимита приглашений. Аккаунт будет на перерыве до завтра. ⏳ Пропускаем аккаунт.")
            accounts_on_cooldown[rd_session_name] = time.time() + 86400
            if rd_session_name in session_names: session_names.remove(rd_session_name)
            if not session_names and not any(accounts_on_cooldown[s] < time.time() + 86000 for s in accounts_on_cooldown):
                 colored_print("  🔴 Все доступные аккаунты достигли суточного лимита приглашений. Завершение работы. 💔", RED)
                 await send_log_to_telegram("🔴 Все доступные аккаунты достигли суточного лимита приглашений. Завершение работы. 💔")
                 sys.exit(1)
            ind -=1
        except Exception as e:
            colored_print(f'      🔴 {ind + 1}/{len(usernames)}: Непредвиденная ошибка при работе с @{username} через аккаунт {rd_session_name}: {type(e).__name__}: {e} 😥. Пропускаем пользователя и ждём {delay.delay_after_error} сек.', RED)
            await send_log_to_telegram(f"🔴 Непредвиденная ошибка при работе с <b>@{username}</b> через <b>{rd_session_name}</b>: {type(e).__name__}: {e} 😥. Пропускаем, ждём {delay.delay_after_error} сек.")
            await asyncio.sleep(delay.delay_after_error) # Using delay.

        ind += 1
        db.set('start', str(ind)) # Save progress
        db.dump() # Ensure data is written to disk
        colored_print(f"    ⏳ Пауза {delay.delay_after_invite:.2f} сек. после попытки приглашения...", YELLOW)
        await asyncio.sleep(delay.delay_after_invite) # Using delay.


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    try:
        async def on_startup(_):
            # Apply UI/logging enhancements from previous steps to this message too
            colored_print("🤖 Телеграм-бот для управления командами успешно запущен! Готов принимать команды от администратора. 📡", GREEN)
            await send_log_to_telegram("🤖 Телеграм-бот для управления командами успешно запущен! Готов принимать команды от администратора. 📡")

        executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        colored_print("\n🚪 Прерывание пользователем (Ctrl+C). Завершение работы...", YELLOW)
        try:
            asyncio.run(send_log_to_telegram("🚪 Прерывание пользователем (Ctrl+C). Завершение работы..."))
        except RuntimeError:
            pass
    except Exception as глобальная_ошибка:
        colored_print(f"💥 Глобальная непредвиденная ошибка в скрипте: {глобальная_ошибка}", RED)
        try:
            asyncio.run(send_log_to_telegram(f"💥 Глобальная непредвиденная ошибка в скрипте: {глобальная_ошибка}. Требуется вмешательство!"))
        except RuntimeError:
            pass
    finally:
        colored_print("🛑 Скрипт завершил свою работу.", YELLOW)
        try:
            asyncio.run(send_log_to_telegram("🛑 Скрипт завершил свою работу."))
        except RuntimeError:
            pass
        if loop.is_running() and not loop.is_closed():