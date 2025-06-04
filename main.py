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
from telethon.tl.types import (ChannelParticipantsSearch, InputPeerUser, User, Chat, Channel) # Added User, Chat, Channel
from telethon import errors as telethon_errors # For specific error handling in /get_id
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

import settings_manager # For settings commands
import delay # For accessing delay values
import dest  # For accessing dest value
import importlib # For reloading modules if settings are changed by bot

from database import pickledb
from config import MAX_ACCOUNTS_PER_API, BOT_TOKEN, ADMIN_ID as ADMIN_ID_STR, LOG_CHANNEL_ID # Load ADMIN_ID as string
from aiogram.utils.markdown import hcode # For formatting in /settings

logging.basicConfig(level=logging.ERROR, format='%(asctime)s - %(levelname)s - %(message)s')

ADMIN_ID = int(ADMIN_ID_STR) # Convert ADMIN_ID to int for use in decorators

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
    if message.from_user.id == ADMIN_ID: # Use integer ADMIN_ID
        start_message = (
            "👋 **Привет, Администратор!**\n\n"
            "🤖 Я ваш бот-помощник для управления приглашениями пользователей в Telegram-группы.\n\n"
            "**Что я умею:**\n"
            "✅ Автоматически приглашать пользователей из списка в целевую группу.\n"
            "✅ Использовать несколько аккаунтов Telegram для обхода ограничений.\n"
            "✅ Работать с прокси для повышения анонимности и стабильности.\n"
            "✅ Вести подробные логи происходящего как в консоли, так и в специальном Telegram-канале.\n"
            "✅ Управляться командами через этот чат (например: /settings, /set_delay, /add_user, /get_id и другие).\n\n"
            "🚀 Для начала работы, убедитесь, что `BOT_TOKEN` и `ADMIN_ID` заданы в `settings/config.py`.\n"
            "🛠️ Остальные параметры (прокси, API ключи, список пользователей, задержки, целевая группа) можно настроить через команды бота.\n"
            "📄 Для полного списка команд и их использования обратитесь к `RUN_GUIDE.md`."
        )
        await message.reply(start_message)

@dp.message_handler(commands=['settings', 'config'], user_id=ADMIN_ID)
async def show_settings(message: types.Message):
    # Delay Settings
    delay_settings_text = "<b>⏱️ Задержки (секунды):</b>\n"
    delay_vars = {k: v for k, v in vars(delay).items() if k.startswith("delay_")}
    if delay_vars:
        for name, val in delay_vars.items():
            delay_settings_text += f"  - {name.replace('_', ' ').capitalize()}: {hcode(str(val))}\n"
    else:
        delay_settings_text += "  <code>Настройки задержек не найдены.</code>\n"

    # Destination Setting
    dest_setting_text = f"<b>🎯 Целевая группа/канал:</b> {hcode(dest.dest if hasattr(dest, 'dest') else 'Не задано')}\n"

    # API Keys
    api_keys_list = settings_manager.get_api_keys_list(mask_hashes=True)
    api_keys_text = "<b>🔑 API Ключи:</b>\n"
    if not api_keys_list:
        api_keys_text += f"  <code>Нет ключей</code> (<code>{settings_manager.API_KEYS_FILE}</code>)\n"
    else:
        api_keys_text += f"  Количество: {hcode(str(len(api_keys_list)))}\n"
        if len(api_keys_list) <= 10:
            for key in api_keys_list:
                api_keys_text += f"  - {hcode(key)}\n"
        else:
            api_keys_text += "  (Используйте /list_apikeys для полного списка)\n"

    # Usernames
    usernames_list = settings_manager.get_usernames()
    usernames_text = "<b>👤 Юзернеймы:</b>\n"
    if not usernames_list:
        usernames_text += f"  <code>Нет юзернеймов</code> (<code>{settings_manager.USERNAMES_FILE}</code>)\n"
    else:
        usernames_text += f"  Количество: {hcode(str(len(usernames_list)))}\n"
        if len(usernames_list) <= 10:
            for i, user in enumerate(usernames_list[:5]):
                usernames_text += f"  - {hcode(user)}\n"
            if len(usernames_list) > 5:
                usernames_text += "  ...\n"
        else:
            usernames_text += f"  (Показаны первые 5, используйте /list_users для большего)\n"

    # Proxies
    proxies_list = settings_manager.get_proxies()
    proxies_text = "<b>🌐 Прокси:</b>\n"
    if not proxies_list:
        proxies_text += f"  <code>Нет прокси</code> (<code>{settings_manager.PROXY_FILE_SM}</code>)\n"
    else:
        proxies_text += f"  Количество: {hcode(str(len(proxies_list)))}\n"
        if len(proxies_list) <= 10:
            for proxy_str in proxies_list[:5]: # Corrected variable name
                proxies_text += f"  - {hcode(settings_manager.mask_proxy_string(proxy_str))}\n"
            if len(proxies_list) > 5:
                proxies_text += "  ...\n"
        else:
            proxies_text += f"  (Показаны первые 5, используйте /list_proxies для большего)\n"

    # Other Settings
    other_settings_text = "<b>⚙️ Другие настройки:</b>\n"
    other_settings_text += f"  - Макс. аккаунтов на API: {hcode(str(MAX_ACCOUNTS_PER_API))}\n"
    other_settings_text += f"  - ID Администратора: {hcode(ADMIN_ID_STR)}\n" # Show the string version from config
    other_settings_text += f"  - ID Канала логов: {hcode(str(LOG_CHANNEL_ID))}\n"

    full_text = (
        "<b>⚙️ Текущие настройки бота ⚙️</b>\n\n"
        f"{dest_setting_text}\n"
        f"{delay_settings_text}\n"
        f"{api_keys_text}\n"
        f"{usernames_text}\n"
        f"{proxies_text}\n"
        f"{other_settings_text}"
    )
    await message.reply(full_text, parse_mode=types.ParseMode.HTML)

@dp.message_handler(commands=['get_id'], user_id=ADMIN_ID)
async def get_id_command(message: types.Message):
    args = message.get_args().strip()
    if not args:
        await message.reply("ℹ️ Please provide a Telegram link or username after the command.\n"
                            "Example: `/get_id @username` or `/get_id t.me/joinchat/link` or `/get_id https://t.me/publicchannel`")
        return

    identifier = args

    active_client = None
    # accounts is global and should be populated by main() before bot polling starts
    if not accounts:
        await message.reply("🔴 No client accounts seem to be loaded. Cannot perform ID lookup.")
        return

    for client_session in accounts.values():
        try:
            if client_session and client_session.is_connected() and await client_session.is_user_authorized():
                 active_client = client_session
                 break
        except Exception: # Ignore problematic clients
            continue

    if not active_client:
        if accounts: active_client = next(iter(accounts.values()), None)

    if not active_client:
        await message.reply("🔴 No active or available client sessions to perform ID lookup. Please ensure accounts are loaded and authorized.")
        return

    try:
        client_username_display = "N/A"
        # Ensure active_client and its 'me' attribute are valid before accessing username
        if hasattr(active_client, 'me') and active_client.me:
            client_username_display = active_client.me.username if active_client.me.username else f"ID:{active_client.me.id}"

        colored_print(f"ℹ️ Admin requested ID for: {identifier} using client {client_username_display}", YELLOW)
        entity = await active_client.get_entity(identifier)

        entity_type = "Unknown"
        if isinstance(entity, User): entity_type = "User"
        elif isinstance(entity, Chat): entity_type = "Chat"
        elif isinstance(entity, Channel):
            entity_type = "Channel"
            if entity.megagroup: entity_type = "Megagroup (Supergroup)"

        title = getattr(entity, 'title', None) or \
                getattr(entity, 'username', None) or \
                (f"{getattr(entity, 'first_name', '')} {getattr(entity, 'last_name', '')}".strip())
        title = title if title else "N/A"


        response_text = (
            f"✅ **Entity Found!**\n\n"
            f"🆔 **ID:** <code>{entity.id}</code>\n"
            f"👤 **Type:** <code>{entity_type}</code>\n"
            f"📝 **Title/Name/Username:** <code>{title}</code>"
        )
        await message.reply(response_text, parse_mode=types.ParseMode.HTML)

    except ValueError as e:
        await message.reply(f"🔴 Could not resolve identifier: <code>{identifier}</code>.\n"
                            f"Error: <code>{e}</code>\n"
                            f"ℹ️ Ensure the link/username is correct and accessible by one of the bot's client accounts.", parse_mode=types.ParseMode.HTML)
    except telethon_errors.rpcerrorlist.UsernameInvalidError as e:
        await message.reply(f"🔴 Invalid username or not found: <code>{identifier}</code>.\nError: <code>{e}</code>", parse_mode=types.ParseMode.HTML)
    except telethon_errors.RPCError as e:
        await message.reply(f"🔴 Telegram API error while resolving <code>{identifier}</code>:\n<code>{e}</code>", parse_mode=types.ParseMode.HTML)
    except Exception as e:
        colored_print(f"🔴 Unexpected error in /get_id for {identifier}: {type(e).__name__}: {e}", RED)
        await message.reply(f"🔴 An unexpected error occurred: <code>{type(e).__name__}: {e}</code>", parse_mode=types.ParseMode.HTML)

async def main():
    global last_account_switch_time, session_names, inviting_paused, db

    colored_print("🚀 Bot Starting! Initializing setup... ✨", GREEN)
    await send_log_to_telegram("🚀 Bot Starting! Initializing setup... ✨")

    try:
        with open(API_KEYS_FILE, 'r') as f:
            api_keys = [line.strip().split(':') for line in f if line.strip()]
    except FileNotFoundError:
        colored_print(f"  🔴 Critical: {API_KEYS_FILE} not found. Please create it and add API keys (API_ID:API_HASH per line).", RED)
        await send_log_to_telegram(f"🔴 Critical: <code>{API_KEYS_FILE}</code> not found. Please create it and add API keys (API_ID:API_HASH per line).")
        sys.exit(1)

    if not api_keys:
        colored_print(f"  🔴 Critical: {API_KEYS_FILE} is empty. Please add API keys (API_ID:API_HASH per line).", RED)
        await send_log_to_telegram(f"🔴 Critical: <code>{API_KEYS_FILE}</code> is empty. Please add API keys (API_ID:API_HASH per line).")
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
                dests[session_name] = await account.get_entity(dest)
                valid_session_names.append(session_name)
                accounts[session_name] = account
                colored_print(f'    ✅ Account {session_name} authorized successfully! Ready to work. ✨', GREEN)
                await send_log_to_telegram(f"✅ Account <b>{session_name}</b> authorized successfully! Ready. ✨")

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
            colored_print('  🏁🎉 All users processed! Script finished successfully! 🎉🏁', GREEN)
            await send_log_to_telegram("🏁🎉 All users processed! Script finished successfully! 🎉🏁")
            break

        username = usernames[ind]

        current_time = time.time()
        if current_time - last_account_switch_time < delay_between_clients:
            remaining_delay = delay_between_clients - (current_time - last_account_switch_time)
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
                await asyncio.sleep(delay_between_accounts)

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
                    await asyncio.sleep(delay_after_join)
                except errors.rpcerrorlist.ImportBotAuthorizationRequiredError:
                    colored_print(f"    ⛔ Аккаунт {rd_session_name} - бот 🤖. Боты не могут вступать в группы.", RED)
                    await send_log_to_telegram(f"⚠ Аккаунт <b>{rd_session_name}</b> - бот 🤖.")
                    joined_group[rd_session_name] = True
                    await asyncio.sleep(delay_after_error)
                    continue
                except Exception as e:
                    colored_print(f"    ⛔ Ошибка при вступлении аккаунта {rd_session_name} в целевую группу: {e}", RED)
                    await send_log_to_telegram(f"⚠ Ошибка при вступлении аккаунта <b>{rd_session_name}</b> в целевую группу: {e}")
                    await asyncio.sleep(delay_after_error)
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
                    colored_print(f"    ✅ User @{username} successfully added! (Inviter: {rd_session_name}) [{ind + 1}/{len(usernames)}]", GREEN)
                    await send_log_to_telegram(f"✅ User <b>@{username}</b> successfully added! (Inviter: <b>{rd_session_name}</b>) [{ind+1}/{len(usernames)}]")
                    with open(USED_FILE, 'a') as used_file:
                        used_file.write(f"{username}\n")
                else: # This case might mean user was invited but not yet in participant list, or invite failed silently.
                    colored_print(f"    🟡 User @{username} invited, but not confirmed in group yet (Inviter: {rd_session_name}) [{ind + 1}/{len(usernames)}]. May require manual check.", YELLOW)
                    await send_log_to_telegram(f"🟡 User <b>@{username}</b> invited, but not confirmed in group yet (Inviter: <b>{rd_session_name}</b>) [{ind+1}/{len(usernames)}]. May require manual check.")
            else:
                colored_print(f"    ℹ️ @{username} is not a user (channel/bot?) [{ind + 1}/{len(usernames)}]. Skipping.", YELLOW)
                await send_log_to_telegram(f"⚠ Нельзя пригласить @{username}, это не пользователь 🤷.")
                await asyncio.sleep(delay_after_error)
                continue

        except ValueError:
            colored_print(f'      ⚠ Пользователь @{username} не найден 🤷.', YELLOW)