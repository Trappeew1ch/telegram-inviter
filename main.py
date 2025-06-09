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
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.dispatcher import FSMContext
from aiogram.types import (ReplyKeyboardMarkup, KeyboardButton,
                           InlineKeyboardMarkup, InlineKeyboardButton)
from aiogram.utils.exceptions import MessageNotModified


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

DB_FILE = os.path.join(DATABASE_DIR, "clientbot_test.db") # For user processing progress
BOT_DB_FILE = os.path.join(DATABASE_DIR, "bot_data.db") # For bot settings

sys.path.append(SETTINGS_DIR)
sys.path.append(DATABASE_DIR)

from database import pickledb

# Import default values with aliases
from delay import (delay_after_join as default_delay_after_join,
                   delay_after_invite as default_delay_after_invite,
                   delay_after_error as default_delay_after_error,
                   delay_between_accounts as default_delay_between_accounts,
                   delay_between_clients as default_delay_between_clients)

from dest import dest as default_dest
from config import (MAX_ACCOUNTS_PER_API as default_MAX_ACCOUNTS_PER_API,
                    BOT_TOKEN, ADMIN_ID,
                    LOG_CHANNEL_ID as default_LOG_CHANNEL_ID)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

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

# Global variables for Telethon client sessions and their destination entities
accounts = {}
dests = {}
accounts_on_cooldown = {}
session_names = []
last_account_switch_time = 0


# Global variables for inviting process state
inviting_task = None
current_inviting_user = "N/A"
processed_in_current_run = 0

bot = Bot(token=BOT_TOKEN, parse_mode=types.ParseMode.HTML)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

bot_db = pickledb.load(BOT_DB_FILE, True)
if not bot_db.exists('inviting_paused'):
    bot_db.set('inviting_paused', True)
db = None # Will be loaded in main_setup

# --- Settings Management Utilities ---
def get_setting(key, default_value=None):
    return bot_db.get(key) if bot_db.exists(key) else default_value

def set_setting(key, value):
    bot_db.set(key, value)

# --- File Hash Utility ---
def get_file_hash(filepath):
    hasher = hashlib.sha256()
    if not os.path.exists(filepath): return None
    with open(filepath, 'rb') as file:
        while True:
            chunk = file.read(4096)
            if not chunk: break
            hasher.update(chunk)
    return hasher.hexdigest()

# --- Telethon Account Creation ---
async def create_telegram_account(session_name, api_id, api_hash, proxy=None):
    session_path = os.path.join(SESSION_DIR, session_name)
    try:
        client_args = {'connection': ConnectionTcpFull}
        if proxy:
            proxy_type, addr, port, username, secret_or_password = proxy
            if proxy_type.lower() == 'mtproto':
                client_args['connection'] = ConnectionTcpMTProxy
                client_args['proxy'] = (addr, int(port), secret_or_password)
            else:
                client_args['proxy'] = (proxy_type, addr, int(port), True, username, secret_or_password)

        account = TelegramClient(session_path, api_id, api_hash, **client_args)
        await account.connect()
        if not await account.is_user_authorized():
            await send_log_to_telegram(f"❌ Сессия <b>{session_name}</b> не авторизована.")
            return None
        me = await account.get_me()
        if not me or not me.first_name:
             await send_log_to_telegram(f"❌ Ошибка в сессии <b>{session_name}</b>: отсутствует имя или не удалось получить данные.")
             return None
        return account
    except Exception as e:
        await send_log_to_telegram(f"❌ Ошибка создания аккаунта <b>{session_name}</b>: {e}")
        return None

# --- Telegram Logging ---
async def send_log_to_telegram(message):
    try:
        log_channel_id = get_setting('LOG_CHANNEL_ID', default_LOG_CHANNEL_ID)
        if log_channel_id:
            await bot.send_message(log_channel_id, message)
        else:
            colored_print(f"  ERROR LOG_CHANNEL_ID not set. Cannot send log: {message}", RED)
    except Exception as e:
        colored_print(f"  ERROR sending log to Telegram: {e}. Message: {message}", RED)

# --- Bot UI Handlers ---
@dp.message_handler(commands=['start'])
async def start_bot_command(message: types.Message):
    if str(message.from_user.id) == ADMIN_ID:
        await send_main_menu(message.chat.id)

async def send_main_menu(chat_id, message_id=None):
    text = "Добро пожаловать в панель управления ботом! 🤖\nВыберите действие:"
    is_paused = get_setting('inviting_paused', True)
    start_stop_text = "▶️ Запустить инвайтинг" if is_paused else "⏸️ Остановить инвайтинг"
    keyboard = InlineKeyboardMarkup(row_width=1).add(
        InlineKeyboardButton(text=start_stop_text, callback_data="toggle_inviting"),
        InlineKeyboardButton(text="⚙️ Settings", callback_data="settings_menu"),
        InlineKeyboardButton(text="📊 View Status", callback_data="view_status")
    )
    if message_id:
        try: await bot.edit_message_text(text, chat_id, message_id, reply_markup=keyboard)
        except MessageNotModified: pass
        except Exception as e: logging.warning(f"Error editing main menu: {e}")
    else: await bot.send_message(chat_id, text, reply_markup=keyboard)

@dp.callback_query_handler(lambda c: c.data == 'settings_menu')
async def process_settings_menu_button(callback_query: types.CallbackQuery):
    if str(callback_query.from_user.id) != ADMIN_ID: return await callback_query.answer("Доступ запрещен.", show_alert=True)
    # This function (send_settings_menu) and other specific menu handlers (send_general_settings_menu, etc.)
    # should be defined as they were in previous steps. For brevity, they are omitted here but are part of the full code.
    # Example:
    # await send_settings_menu(callback_query.from_user.id, callback_query.message.message_id)
    await callback_query.answer("Меню настроек (не реализовано в этом сокращенном примере).")


@dp.callback_query_handler(lambda c: c.data == 'toggle_inviting')
async def process_toggle_inviting(callback_query: types.CallbackQuery):
    global inviting_task
    if str(callback_query.from_user.id) != ADMIN_ID:
        return await callback_query.answer("Доступ запрещен.", show_alert=True)

    currently_paused = get_setting('inviting_paused', True)
    alert_message = ""

    if currently_paused:
        set_setting('inviting_paused', False)
        if inviting_task is None or inviting_task.done():
            await send_log_to_telegram("▶️ Запуск процесса инвайтинга...")
            inviting_task = asyncio.create_task(run_inviting_process())
            alert_message = "▶️ Процесс инвайтинга запускается..."
        else:
            alert_message = "ℹ️ Процесс инвайтинга уже активен."
    else:
        set_setting('inviting_paused', True)
        await send_log_to_telegram("🛑 Процесс инвайтинга останавливается...")
        alert_message = "🛑 Процесс инвайтинга останавливается... (завершит текущую операцию)"

    await callback_query.answer(alert_message, show_alert=True)
    await send_main_menu(callback_query.from_user.id, callback_query.message.message_id)


@dp.callback_query_handler(lambda c: c.data == 'view_status')
async def process_view_status(callback_query: types.CallbackQuery):
    global inviting_task, current_inviting_user, processed_in_current_run
    if str(callback_query.from_user.id) != ADMIN_ID:
        return await callback_query.answer("Доступ запрещен.", show_alert=True)

    is_paused_setting = get_setting('inviting_paused', True)
    status_text_setting = "ОСТАНОВЛЕН (по настройке) ⏸️" if is_paused_setting else "ЗАПУЩЕН (по настройке) ✅"

    num_loaded_users = 0
    if os.path.exists(USERNAMES_FILE):
        try:
            with open(USERNAMES_FILE, 'r', encoding='utf-8') as f:
                num_loaded_users = len([line for line in f if line.strip()])
        except Exception: pass

    task_actual_status = "НЕ АКТИВНА"
    if inviting_task and not inviting_task.done():
        task_actual_status = "АКТИВНА (в процессе)"
    elif inviting_task and inviting_task.done():
        try:
            if inviting_task.exception(): task_actual_status = f"ЗАВЕРШЕНА С ОШИБКОЙ: {str(inviting_task.exception())[:100]}"
            else: task_actual_status = "ЗАВЕРШЕНА"
        except asyncio.CancelledError: task_actual_status = "ОТМЕНЕНА"
        except Exception as e: task_actual_status = f"ЗАВЕРШЕНА С НЕИЗВ. ОШИБКОЙ: {str(e)[:100]}"

    db_start_index = db.get('start', "N/A") if db else "N/A" # Ensure db is initialized
    status_msg = (
        f"<b>📊 Текущий статус:</b>\n\n"
        f"Состояние (настройка): <b>{status_text_setting}</b>\n"
        f"Фактический статус задачи: <b>{task_actual_status}</b>\n"
        f"Активных сессий: <code>{len(accounts)}</code>\n"
        f"Загружено username: <code>{num_loaded_users}</code>\n"
        f"Индекс обработки: <code>{db_start_index}</code>\n"
        f"Обработано в этом запуске: <code>{processed_in_current_run}</code>\n"
        f"Текущий пользователь: <code>{current_inviting_user}</code>"
    )
    await bot.send_message(callback_query.from_user.id, status_msg)
    await callback_query.answer()

# --- Main Setup Function ---
async def main_setup():
    global accounts, dests, session_names, db, api_key_cycle, proxy_cycle

    default_settings_init_map = {
        'inviting_paused': True, 'delay_after_join': default_delay_after_join,
        'delay_after_invite': default_delay_after_invite, 'delay_after_error': default_delay_after_error,
        'delay_between_accounts': default_delay_between_accounts, 'delay_between_clients': default_delay_between_clients,
        'dest': default_dest, 'LOG_CHANNEL_ID': default_LOG_CHANNEL_ID,
        'MAX_ACCOUNTS_PER_API': default_MAX_ACCOUNTS_PER_API,
    }
    for key, value in default_settings_init_map.items():
        if not bot_db.exists(key): set_setting(key, value)

    db = pickledb.load(DB_FILE, True)

    initial_dest_target_link = get_setting('dest', default_dest)
    current_max_accounts_per_api = get_setting('MAX_ACCOUNTS_PER_API', default_MAX_ACCOUNTS_PER_API)

    await send_log_to_telegram("🚀 <b>Инициализация бота...</b> Загрузка сессий Telegram...")
    try:
        with open(API_KEYS_FILE, 'r') as f: api_keys = [line.strip().split(':') for line in f if line.strip()]
    except FileNotFoundError: await send_log_to_telegram(f"❌ Файл <code>{API_KEYS_FILE}</code> не найден."); sys.exit(1)
    if not api_keys: await send_log_to_telegram(f"❌ Файл <code>{API_KEYS_FILE}</code> пуст."); sys.exit(1)
    api_key_cycle = itertools.cycle(api_keys)

    proxies = []
    if os.path.exists(PROXY_FILE):
        try:
            with open(PROXY_FILE, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'): continue
                    try:
                        parts = line.split(':')
                        proxy_type_val = parts[0].lower()
                        if proxy_type_val == 'mtproto' and len(parts) == 4:
                            proxies.append((parts[0], parts[1], int(parts[2]), parts[3], None))
                        elif proxy_type_val in ['http', 'socks5', 'socks4'] and len(parts) >= 3:
                            p_user, p_pass = (parts[3], parts[4]) if len(parts) == 5 else (None, None)
                            proxies.append((parts[0], parts[1], int(parts[2]), p_user, p_pass))
                        else: raise ValueError("Формат не распознан")
                    except ValueError: colored_print(f"  ⚠ Неверный формат прокси: {line}. Пропуск.", YELLOW)
        except Exception as e: await send_log_to_telegram(f"❌ Ошибка чтения прокси: {e}.")
    proxy_cycle = itertools.cycle(proxies) if proxies else None

    available_session_files = [f for f in os.listdir(SESSION_DIR) if f.endswith('.session')]
    if not available_session_files: await send_log_to_telegram(f"❌ Папка <code>{SESSION_DIR}</code> пустая."); sys.exit(1)

    session_names = available_session_files
    api_key_counts = {}
    accounts.clear(); dests.clear()
    valid_session_names_this_run = []

    for session_filename in session_names:
        current_api_id, current_api_hash = next(api_key_cycle)
        current_proxy = next(proxy_cycle) if proxy_cycle else None
        account = await create_telegram_account(session_filename, current_api_id, current_api_hash, current_proxy)
        if account:
            try:
                dests[session_filename] = await account.get_entity(initial_dest_target_link)
                valid_session_names_this_run.append(session_filename)
                accounts[session_filename] = account
                await send_log_to_telegram(f"✅ Аккаунт <b>{session_filename}</b> авторизован.")
                api_key_tuple = (current_api_id, current_api_hash)
                api_key_counts[api_key_tuple] = api_key_counts.get(api_key_tuple, 0) + 1
                if api_key_counts[api_key_tuple] > current_max_accounts_per_api:
                    await send_log_to_telegram(f"❌ Превышено на API: <b>{current_api_id}:{current_api_hash}</b>."); sys.exit(1)
            except Exception as e:
                await send_log_to_telegram(f"❌ Ошибка сессии <b>{session_filename}</b> (цель: {initial_dest_target_link}): {str(e)[:100]}")
                if account: await account.disconnect()

    if not accounts: await send_log_to_telegram("❌ Нет активных аккаунтов."); sys.exit(1)
    session_names = valid_session_names_this_run
    await send_log_to_telegram(f"🏁 <b>Бот инициализирован. Активных сессий: {len(accounts)}.</b> Инвайтинг не запущен.")

# --- Inviting Process ---
async def run_inviting_process():
    global last_account_switch_time, db, accounts, dests, session_names
    global current_inviting_user, processed_in_current_run

    delay_after_join_val = get_setting('delay_after_join', default_delay_after_join)
    delay_after_invite_val = get_setting('delay_after_invite', default_delay_after_invite)
    delay_after_error_val = get_setting('delay_after_error', default_delay_after_error)
    delay_between_accounts_val = get_setting('delay_between_accounts', default_delay_between_accounts)
    delay_between_clients_val = get_setting('delay_between_clients', default_delay_between_clients)
    dest_target_link_val = get_setting('dest', default_dest)

    processed_in_current_run = 0
    current_inviting_user = "N/A"
    dests_for_run = {}

    try:
        current_run_session_names = list(session_names)
        if not current_run_session_names:
            await send_log_to_telegram("⚠️ Нет сконфигурированных сессий для инвайтинга."); set_setting('inviting_paused', True); return

        for s_name in list(current_run_session_names):
            acc = accounts.get(s_name)
            if acc and acc.is_connected() and await acc.is_user_authorized():
                try: dests_for_run[s_name] = await acc.get_entity(dest_target_link_val)
                except Exception as e:
                    await send_log_to_telegram(f"❌ Ошибка цели для <b>{s_name}</b> ({dest_target_link_val}): {str(e)[:100]}. Исключен.")
                    current_run_session_names.remove(s_name)
            else:
                await send_log_to_telegram(f"ℹ️ Сессия <b>{s_name}</b> не активна. Исключена.")
                if s_name in current_run_session_names: current_run_session_names.remove(s_name)

        if not dests_for_run or not current_run_session_names:
            await send_log_to_telegram(f"⚠️ Не удалось получить инфо о <code>{dest_target_link_val}</code> или нет сессий. Инвайтинг остановлен."); set_setting('inviting_paused', True); return

        await send_log_to_telegram(f"▶️ <b>Цикл инвайтинга!</b> Цель: <code>{dest_target_link_val}</code>. Сессий: {len(current_run_session_names)}")

        current_usernames_hash = get_file_hash(USERNAMES_FILE)
        previous_usernames_hash = db.get('usernames_hash')
        ind = 0
        if current_usernames_hash == previous_usernames_hash and db.exists('start'): ind = int(db.get('start'))
        else: db.set('start', 0); db.set('usernames_hash', current_usernames_hash)

        joined_group_this_run = {name: False for name in current_run_session_names}

        usernames_to_process = []
        try:
            with open(USERNAMES_FILE, 'r', encoding='utf-8') as f: usernames_to_process = [line.strip() for line in f if line.strip()]
        except FileNotFoundError: await send_log_to_telegram(f"❌ Файл <code>{USERNAMES_FILE}</code> не найден."); set_setting('inviting_paused', True); return
        if not usernames_to_process: await send_log_to_telegram(f"❌ Файл <code>{USERNAMES_FILE}</code> пуст."); set_setting('inviting_paused', True); return

        while True:
            if get_setting('inviting_paused', True): await send_log_to_telegram("ℹ️ Инвайтинг остановлен."); return
            if ind >= len(usernames_to_process): await send_log_to_telegram("🎉 Все пользователи обработаны!"); break
            username_to_process = usernames_to_process[ind]; current_inviting_user = username_to_process
            if not current_run_session_names: await send_log_to_telegram("❌ Нет аккаунтов для продолжения."); break

            rd_session_name = random.choice(current_run_session_names)
            account_client = accounts[rd_session_name]; dest_entity_to_use = dests_for_run[rd_session_name]

            current_time_loop = time.time()
            if current_time_loop - last_account_switch_time < delay_between_clients_val:
                wait_delay = delay_between_clients_val - (current_time_loop - last_account_switch_time)
                if wait_delay > 0: await asyncio.sleep(wait_delay)
            last_account_switch_time = time.time()

            if rd_session_name in accounts_on_cooldown: # Check global cooldown
                rem_cooldown = accounts_on_cooldown[rd_session_name] - time.time()
                if rem_cooldown > 0:
                    await asyncio.sleep(delay_between_accounts_val if delay_between_accounts_val > 1 else 1)
                    if all(s_n in accounts_on_cooldown and accounts_on_cooldown[s_n] > time.time() for s_n in current_run_session_names):
                        await send_log_to_telegram("⚠ Все аккаунты на перерыве."); return
                    continue
                else: del accounts_on_cooldown[rd_session_name]

            try:
                user_to_invite = await account_client.get_input_entity(username_to_process)
                if not joined_group_this_run.get(rd_session_name):
                    try:
                        await account_client(JoinChannelRequest(dest_entity_to_use))
                        await send_log_to_telegram(f"➕ <b>{rd_session_name}</b> вступил в {str(dest_entity_to_use.id)[:15]}..."); joined_group_this_run[rd_session_name] = True; await asyncio.sleep(delay_after_join_val)
                    except Exception as e_join:
                        err_msg = str(e_join)[:100]
                        await send_log_to_telegram(f"⚠ Ошибка вступления <b>{rd_session_name}</b>: {err_msg}")
                        if isinstance(e_join, (UserChannelsTooMuchError, errors.rpcerrorlist.ImportBotAuthorizationRequiredError, errors.rpcerrorlist.ChatAdminRequiredError, errors.rpcerrorlist.ChannelPrivateError)):
                            if rd_session_name in current_run_session_names: current_run_session_names.remove(rd_session_name)
                        await asyncio.sleep(delay_after_error_val); ind-=1; processed_in_current_run-=1; continue # Retry user with different account

                if isinstance(user_to_invite, InputPeerUser):
                    await account_client(InviteToChannelRequest(dest_entity_to_use, [user_to_invite]))
                    await send_log_to_telegram(f"✉️ [{ind+1}/{len(usernames_to_process)}] @{username_to_process} приглашен (<b>{rd_session_name}</b>)")
                    with open(USED_FILE, 'a', encoding='utf-8') as f: f.write(f"{username_to_process}\n")
                    await asyncio.sleep(delay_after_invite_val)
                else: await send_log_to_telegram(f"⚠ @{username_to_process} не пользователь.")
            except ValueError: await send_log_to_telegram(f"❓ @{username_to_process} не найден ({rd_session_name}).")
            except (UserChannelsTooMuchError, errors.rpcerrorlist.UsersTooMuchError, errors.rpcerrorlist.ChatAdminRequiredError, errors.rpcerrorlist.UserPrivacyRestrictedError, errors.rpcerrorlist.UserNotMutualContactError, errors.rpcerrorlist.BotGroupsBlockedError, errors.rpcerrorlist.ChannelPrivateError, errors.rpcerrorlist.GroupcallForbiddenError) as e_telethon:
                await send_log_to_telegram(f"⛔ Telethon ({type(e_telethon).__name__}) для {rd_session_name} с @{username_to_process}: {str(e_telethon)[:100]}")
                if type(e_telethon) in [UserChannelsTooMuchError, errors.rpcerrorlist.UsersTooMuchError, errors.rpcerrorlist.ChatAdminRequiredError, errors.rpcerrorlist.ChannelPrivateError]:
                    accounts_on_cooldown[rd_session_name] = time.time() + 3600
                    if rd_session_name in current_run_session_names: current_run_session_names.remove(rd_session_name)
                ind-=1; processed_in_current_run-=1 # Retry
            except FloodWaitError as e_flood:
                await send_log_to_telegram(f"🌊 FloodWait: {rd_session_name} ({e_flood.seconds} сек).")
                accounts_on_cooldown[rd_session_name] = time.time() + e_flood.seconds + delay_between_accounts_val
                await asyncio.sleep(e_flood.seconds + 5); ind-=1; processed_in_current_run-=1 # Retry
            except Exception as e_general:
                await send_log_to_telegram(f"❌ Ошибка @{username_to_process} ({rd_session_name}): {str(e_general)[:100]}")
                await asyncio.sleep(delay_after_error_val) # General error, proceed to next user

            processed_in_current_run += 1; ind += 1; db.set('start', ind)

    except Exception as e_outer_critical:
        await send_log_to_telegram(f"❌ Крит. ошибка инвайтинга: {str(e_outer_critical)[:200]}."); logging.error("Critical error in run_inviting_process:", exc_info=True)
    finally:
        set_setting('inviting_paused', True); current_inviting_user = "N/A"
        await send_log_to_telegram("ℹ️ Процесс инвайтинга завершен/остановлен.")
        colored_print("ℹ️ Процесс инвайтинга завершен/остановлен.", YELLOW)

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.create_task(main_setup())
    executor.start_polling(dp, skip_updates=True)
