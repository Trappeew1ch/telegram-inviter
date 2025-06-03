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

from database import pickledb

from delay import (delay_after_join, delay_after_invite, delay_after_error,
                   delay_between_accounts, delay_between_clients)

from dest import dest
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
            colored_print(f"  🔴 Сессия {session_name} не авторизована. Пожалуйста, авторизуйте сессию и перезапустите скрипт. Пропускаем...", RED)
            await send_log_to_telegram(f"🔴 Сессия <b>{session_name}</b> не авторизована. Пожалуйста, авторизуйте сессию и перезапустите скрипт.")
            return None

        try:
            me = await account.get_me()
            if not me.first_name:
                raise ValueError("У аккаунта отсутствует имя.")
        except ValueError as e:
            colored_print(f"  🔴 Ошибка в сессии {session_name}: {e}. Пропускаем...", RED)
            await send_log_to_telegram(f"🔴 Ошибка в сессии <b>{session_name}</b>: отсутствует имя. Пожалуйста, проверьте аккаунт.")
            return None
        except Exception as e:
            colored_print(f"  🔴 Не удалось получить данные аккаунта {session_name}: {e}. Пропускаем...", RED)
            await send_log_to_telegram(f"🔴 Ошибка в сессии <b>{session_name}</b>: не удалось получить данные аккаунта. Проверьте валидность сессии.")
            return None

    except Exception as e:
        colored_print(f"  🔴 Не удалось создать аккаунт {session_name}: {e}. Пропускаем...", RED)
        await send_log_to_telegram(f"🔴 Ошибка при создании аккаунта <b>{session_name}</b>: {e}")
        return None

    return account


async def send_log_to_telegram(message):
    try:
        await bot.send_message(LOG_CHANNEL_ID, message)
    except Exception as e:
        colored_print(f"  🔴 CRITICAL: Ошибка при отправке сообщения в лог-канал: {e} 💔", RED)
        # Попытка отправить урезанное сообщение, если полное не проходит
        try:
            await bot.send_message(LOG_CHANNEL_ID, f"🔴 CRITICAL: Ошибка отправки лога. Детали: {str(e)[:1000]}")
        except Exception as final_e:
            colored_print(f"  🔴 CRITICAL: Не удалось отправить даже урезанное сообщение в лог-канал: {final_e} 💔💔", RED)


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
            "✅ Управляться командами через этот чат (например, /pause, /resume, /status).\n\n"
            "🚀 Для начала работы, убедитесь, что все конфигурационные файлы (api_keys.txt, usernames.txt, dest.py, и т.д.) настроены правильно.\n\n"
            "🛠️ Если возникнут проблемы, я сообщу об этом в логах с соответствующими эмодзи и инструкциями.\n\n"
            "📡 Ожидаю ваших команд!"
        )
        await message.reply(start_message)


async def main():
    global last_account_switch_time, session_names, inviting_paused, db

    colored_print("🚀🚀🚀 Запуск скрипта! Начинаем работу! ✨✨✨", GREEN)
    await send_log_to_telegram("🚀🚀🚀 <b>Запуск скрипта!</b> Начинаем работу! ✨✨✨")

    try:
        with open(API_KEYS_FILE, 'r') as f:
            api_keys = [line.strip().split(':') for line in f if line.strip()]
    except FileNotFoundError:
        colored_print(f"  🔴 Файл {API_KEYS_FILE} не найден. Пожалуйста, создайте файл и добавьте API ключи в формате 'API_ID:API_HASH'.", RED)
        await send_log_to_telegram(f"🔴 Файл <code>{API_KEYS_FILE}</code> не найден. Пожалуйста, создайте файл и добавьте API ключи в формате 'API_ID:API_HASH'.")
        sys.exit(1)

    if not api_keys:
        colored_print(f"  🔴 Файл {API_KEYS_FILE} пустой. Необходимо добавить API ключи в формате 'API_ID:API_HASH'.", RED)
        await send_log_to_telegram(f"🔴 Файл <code>{API_KEYS_FILE}</code> пустой. Необходимо добавить API ключи в формате 'API_ID:API_HASH'.")
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
                            colored_print(f"  🟡 Предупреждение: Ошибка при чтении прокси: {e}. Строка: {line}. Пропускаем...", YELLOW)
                            await send_log_to_telegram(f"🟡 Предупреждение: Ошибка при чтении прокси: {e}. Строка: <code>{line}</code>. Пропускаем...")
                            continue


        except FileNotFoundError:
            colored_print(f"  🟡 Файл {PROXY_FILE} не найден. Работаем без прокси. 🌐", YELLOW)
            await send_log_to_telegram(f"🟡 Файл <code>{PROXY_FILE}</code> не найден. Работаем без прокси. 🌐")
        except Exception as e:
            colored_print(f"  🔴 Ошибка при чтении файла {PROXY_FILE}: {e}. Продолжаем работу без прокси. 🌐", RED)
            await send_log_to_telegram(f"🔴 Ошибка при чтении файла <code>{PROXY_FILE}</code>: {e}. Продолжаем работу без прокси. 🌐")
    else:
        colored_print(f"  🟡 Файл {PROXY_FILE} не найден. Работаем без прокси. 🌐", YELLOW)
        await send_log_to_telegram(f"🟡 Файл <code>{PROXY_FILE}</code> не найден. Работаем без прокси. 🌐")

    proxy_cycle = itertools.cycle(proxies) if proxies else None

    session_names = [f for f in os.listdir(SESSION_DIR) if f.endswith('.session')]
    if not session_names:
        colored_print(f"  🔴 Папка {SESSION_DIR} пустая. Пожалуйста, добавьте файлы сессий Telegram (.session).", RED)
        await send_log_to_telegram(f"🔴 Папка <code>{SESSION_DIR}</code> пустая. Пожалуйста, добавьте файлы сессий Telegram (.session).")
        sys.exit(1)

    api_key_counts = {}
    valid_session_names = []
    for session_name in session_names:
        colored_print(f'  🔄 Пробуем авторизовать аккаунт {session_name}...', GREEN)
        api_id, api_hash = next(api_key_cycle)
        proxy = next(proxy_cycle) if proxy_cycle else None

        account = await create_telegram_account(session_name, api_id, api_hash, proxy)

        if account:
            try:
                dests[session_name] = await account.get_entity(dest)
                valid_session_names.append(session_name)
                accounts[session_name] = account
                colored_print(f'    ✅ Аккаунт {session_name} успешно авторизован и готов к работе! ✨', GREEN)
                await send_log_to_telegram(f"✅ Аккаунт <b>{session_name}</b> успешно авторизован и готов к работе! ✨")

                if (api_id, api_hash) not in api_key_counts:
                    api_key_counts[(api_id, api_hash)] = 0
                api_key_counts[(api_id, api_hash)] += 1
                if api_key_counts[(api_id, api_hash)] > MAX_ACCOUNTS_PER_API:
                    colored_print(f"  🔴 Превышено максимальное количество аккаунтов ({MAX_ACCOUNTS_PER_API}) на одном ключе API {api_id}:{api_hash}. Проверьте настройки.", RED)
                    await send_log_to_telegram(f"🔴 Превышено максимальное количество аккаунтов ({MAX_ACCOUNTS_PER_API}) на API: <b>{api_id}:{api_hash}</b>. Проверьте настройки.")
                    sys.exit(1)
            except Exception as e:
                colored_print(f"  🔴 Не удалось получить информацию о целевой группе для аккаунта {session_name}: {e}. Пропускаем.", RED)
                await send_log_to_telegram(f"🔴 Ошибка в сессии <b>{session_name}</b>: Не удалось получить информацию о целевой группе (<code>{dest}</code>). Проверьте, существует ли группа/канал и доступен ли он этому аккаунту.")
                continue
        else:
            # Сообщение об ошибке авторизации уже отправлено из create_telegram_account
            pass


    if not valid_session_names:
        colored_print("  🔴 Нет активных аккаунтов для работы. Пожалуйста, проверьте сессии. Завершаем работу. 💔", RED)
        await send_log_to_telegram("🔴 Нет активных аккаунтов для работы. Пожалуйста, проверьте сессии. Завершаем работу. 💔")
        sys.exit(1)

    session_names = valid_session_names
    colored_print('🏁 Все доступные аккаунты авторизованы. Скрипт запущен и готов к работе! 💪🎉', GREEN)
    await send_log_to_telegram("🏁 Все доступные аккаунты авторизованы. Скрипт запущен и готов к работе! 💪🎉")

    db = pickledb.load(DB_FILE, True)


    current_usernames_hash = get_file_hash(USERNAMES_FILE)
    previous_usernames_hash = db.get('usernames_hash')

    if current_usernames_hash != previous_usernames_hash:
        colored_print("  ℹ️ Файл со списком пользователей изменился. Начинаем обработку с начала списка. 📄", GREEN)
        await send_log_to_telegram("ℹ️ Файл со списком пользователей (<code>usernames.txt</code>) изменился. Начинаем обработку с начала списка. 📄")
        db.set('start', 0)
        db.set('usernames_hash', current_usernames_hash)
        ind = 0
    else:
        ind = int(db.get('start')) if db.get('start') is not None else 0
        # Ensure usernames_list is loaded before trying to access it
        try:
            with open(USERNAMES_FILE, 'r') as f:
                usernames_list = [line.strip() for line in f]
            start_username = usernames_list[ind] if ind < len(usernames_list) else "конец списка"
            colored_print(f'  ▶️ Продолжаем работу с пользователя {start_username} (номер {ind} в списке).', GREEN)
            await send_log_to_telegram(f"▶️ Продолжаем работу с пользователя <b>{start_username}</b> (номер <b>{ind}</b> в списке).")
        except FileNotFoundError:
            colored_print(f"  🔴 Файл {USERNAMES_FILE} не найден, хотя хэш совпадает. Это странно. Начинаем с начала.", RED)
            await send_log_to_telegram(f"🔴 Файл <code>{USERNAMES_FILE}</code> не найден, хотя хэш совпадает. Начинаем с начала.")
            ind = 0 # Reset index as file is missing
            usernames_list = [] # Ensure usernames_list is empty
            db.set('start', 0) # Reset start for next run if file appears
            db.set('usernames_hash', None) # Reset hash


    joined_group = {session_name: False for session_name in session_names}

    try:
        # usernames_list might be already loaded if continuing, otherwise load it here
        if 'usernames_list' not in locals() or not usernames_list:
            with open(USERNAMES_FILE, 'r') as f:
                usernames = [line.strip() for line in f]
        else:
            usernames = usernames_list
    except FileNotFoundError:
        colored_print(f"  🔴 Файл {USERNAMES_FILE} не найден. Пожалуйста, создайте файл и добавьте имена пользователей.", RED)
        await send_log_to_telegram(f"🔴 Файл <code>{USERNAMES_FILE}</code> не найден. Пожалуйста, создайте файл и добавьте имена пользователей.")
        usernames = []

    if not usernames: # Handles both file not found and empty file after attempting to load
        colored_print(f"  🟡 Файл {USERNAMES_FILE} пуст или не найден. Пожалуйста, укажите источник пользователей.", YELLOW)
        await send_log_to_telegram(f"🟡 Файл <code>{USERNAMES_FILE}</code> пуст или не найден. Пожалуйста, укажите источник пользователей.")
        while True:
            source_chat_url = input(f"  📝 Введите ссылку на публичный чат для парсинга пользователей (или 'exit' для выхода): ")
            if source_chat_url.lower() == 'exit':
                colored_print("🚪 Выход из скрипта по команде пользователя.", YELLOW)
                await send_log_to_telegram("🚪 Выход из скрипта по команде пользователя.")
                sys.exit(0)
            try:
                if not valid_session_names: # Should not happen due to earlier check, but good for safety
                    colored_print("  🔴 Нет активных аккаунтов для парсинга. Завершаем работу. 💔", RED)
                    await send_log_to_telegram("🔴 Нет активных аккаунтов для парсинга. Завершаем работу. 💔")
                    sys.exit(1)

                account = accounts[valid_session_names[0]] # Use the first valid account for parsing
                colored_print(f"  ⏳ Пытаемся получить доступ к чату: {source_chat_url}...", YELLOW)
                await send_log_to_telegram(f"⏳ Пытаемся получить доступ к чату: <code>{source_chat_url}</code>...")
                source_chat = await account.get_entity(source_chat_url)
                colored_print(f"  ✅ Доступ к чату {source_chat_url} получен. Начинаем сбор участников...", GREEN)
                await send_log_to_telegram(f"✅ Доступ к чату <code>{source_chat_url}</code> получен. Начинаем сбор участников...")
                break
            except ValueError:
                colored_print(f"  🔴 Некорректная ссылка: {source_chat_url}. Попробуйте ещё раз или введите 'exit'.", RED)
                await send_log_to_telegram(f"🔴 Некорректная ссылка: <code>{source_chat_url}</code>. Попробуйте ещё раз или введите 'exit'.")
            except Exception as e:
                colored_print(f"  🔴 Не удалось получить информацию о чате {source_chat_url}: {e}. Попробуйте другую ссылку или 'exit'.", RED)
                await send_log_to_telegram(f"🔴 Не удалось получить информацию о чате <code>{source_chat_url}</code>: {e}. Попробуйте другую ссылку или 'exit'.")
                # Do not break here, let the user try again or exit

        try:
            participants = await account(GetParticipantsRequest(
                source_chat,
                ChannelParticipantsSearch(''), # Get all participants
                0, 10000, # You might want to handle pagination for very large chats
                hash=0
            ))
            usernames = [user.username for user in participants.users if user.username]
            if usernames:
                with open(USERNAMES_FILE, 'w') as f:
                    for username in usernames:
                        f.write(f"{username}\n")
                colored_print(f"  👥 Успешно собрано {len(usernames)} пользователей из чата {source_chat_url} и сохранено в {USERNAMES_FILE}.", GREEN)
                await send_log_to_telegram(f"👥 Успешно собрано {len(usernames)} пользователей из чата <code>{source_chat_url}</code> и сохранено в <code>{USERNAMES_FILE}</code>. Начинаем инвайтинг! 🚀")
                # Update hash after fetching and saving new usernames
                current_usernames_hash = get_file_hash(USERNAMES_FILE)
                db.set('usernames_hash', current_usernames_hash)
                db.set('start', 0) # Start from the beginning of the new list
                ind = 0
            else:
                colored_print(f"  🟡 В чате {source_chat_url} не найдено пользователей с username. Пожалуйста, проверьте чат или укажите другой.", YELLOW)
                await send_log_to_telegram(f"🟡 В чате <code>{source_chat_url}</code> не найдено пользователей с username. Пожалуйста, проверьте чат или укажите другой.")
                sys.exit(1)

        except Exception as e:
            colored_print(f"  🔴 Не удалось получить список участников чата {source_chat_url}: {e}", RED)
            await send_log_to_telegram(f"🔴 Не удалось получить список участников чата <code>{source_chat_url}</code>: {e}")
            sys.exit(1)


    while True:
        if bot_db.get('inviting_paused'):
            colored_print("  ⏸️ Приглашения приостановлены администратором. Ожидаем возобновления...", YELLOW)
            # Send periodic log message about paused state if needed, but avoid spamming
            await asyncio.sleep(15) # Check every 15 seconds
            continue

        if ind >= len(usernames):
            colored_print('  🏁🎉🎉 Все пользователи из списка обработаны! Завершение работы. 🎉🎉🎉', GREEN)
            await send_log_to_telegram("🏁🎉🎉 Все пользователи из списка обработаны! Завершение работы. 🎉🎉🎉")
            db.set('start', 0) # Reset for next run with potentially new users
            break

        username = usernames[ind]

        current_time = time.time()
        if current_time - last_account_switch_time < delay_between_clients:
            remaining_delay = delay_between_clients - (current_time - last_account_switch_time)
            colored_print(f"  ⏳ Ожидаем {remaining_delay:.2f} сек. перед сменой аккаунта (общая задержка между клиентами)...", YELLOW)
            await asyncio.sleep(remaining_delay)

        rd_session_name = random.choice(session_names) # Select a random available session
        colored_print(f'  ➡️  Выбран аккаунт: {rd_session_name} для @{username}', GREEN)
        last_account_switch_time = time.time()

        account = accounts[rd_session_name]
        dest_entity = dests[rd_session_name]

        if rd_session_name in accounts_on_cooldown:
            remaining_cooldown = accounts_on_cooldown[rd_session_name] - time.time()
            if remaining_cooldown > 0:
                colored_print(f'  ⏳ Аккаунт {rd_session_name} на перерыве ещё {remaining_cooldown:.2f} сек. 😴 Пропускаем...', YELLOW)
                await asyncio.sleep(delay_between_accounts) # Wait before trying another account or same account again

                all_on_cooldown = True
                active_sessions_available = False
                for s_name in session_names:
                    if s_name not in accounts_on_cooldown or accounts_on_cooldown[s_name] <= time.time():
                        active_sessions_available = True
                        break
                if not active_sessions_available: # Corrected logic
                    colored_print("  🔴 Все аккаунты на временном перерыве (cooldown). Ожидаем истечения перерыва или завершаем, если это надолго.", RED)
                    await send_log_to_telegram("🔴 Все аккаунты на временном перерыве (cooldown). Скрипт приостановит работу до истечения самого короткого перерыва.")
                    # Optional: Find the minimum cooldown and sleep for that duration, then continue
                    # For now, let it cycle and wait with delay_between_accounts
                    # sys.exit(1) # Or implement a smarter wait
                continue # Try next available account or wait
            else:
                del accounts_on_cooldown[rd_session_name] # Cooldown expired

        try:
            user = await account.get_input_entity(username)

            if not joined_group[rd_session_name]:
                try:
                    colored_print(f"    ➕ Аккаунт {rd_session_name} вступает в целевую группу: {dest}...", YELLOW)
                    await send_log_to_telegram(f"➕ Аккаунт <b>{rd_session_name}</b> вступает в целевую группу: <code>{dest}</code>...")
                    await account(JoinChannelRequest(dest_entity))
                    colored_print(f"    ✅ Аккаунт {rd_session_name} успешно вступил в целевую группу!", GREEN)
                    await send_log_to_telegram(f"✅ Аккаунт <b>{rd_session_name}</b> успешно вступил в целевую группу!")
                    joined_group[rd_session_name] = True
                    colored_print(f"    ⏳ Пауза {delay_after_join} сек. после вступления в группу...", YELLOW)
                    await asyncio.sleep(delay_after_join)
                except errors.rpcerrorlist.ImportBotAuthorizationRequiredError: # Specific error for bots
                    colored_print(f"    ℹ️ Аккаунт {rd_session_name} является ботом 🤖 и не может вступать в группы. Приглашения будут осуществляться без вступления.", YELLOW)
                    await send_log_to_telegram(f"ℹ️ Аккаунт <b>{rd_session_name}</b> является ботом 🤖 и не может вступать в группы. Приглашения будут осуществляться без вступления.")
                    joined_group[rd_session_name] = True # Mark as "joined" to skip this step next time
                    # No delay_after_error needed here as it's not a real error for this operation
                except UserChannelsTooMuchError:
                    colored_print(f"    🔴 Аккаунт {rd_session_name} уже состоит в слишком большом количестве каналов/групп и не может вступить в целевую. Пропускаем аккаунт.", RED)
                    await send_log_to_telegram(f"🔴 Аккаунт <b>{rd_session_name}</b> уже состоит в слишком большом количестве каналов/групп и не может вступить в целевую. Пропускаем аккаунт.")
                    session_names.remove(rd_session_name) # Remove from active list for this run
                    if not session_names:
                        colored_print("  🔴 Все доступные аккаунты не смогли вступить в группу (слишком много каналов). Завершение работы.", RED)
                        await send_log_to_telegram("🔴 Все доступные аккаунты не смогли вступить в группу (слишком много каналов). Завершение работы.")
                        sys.exit(1)
                    continue # Try with next account
                except Exception as e:
                    colored_print(f"    🔴 Ошибка при вступлении аккаунта {rd_session_name} в целевую группу: {e}. Пауза {delay_after_error} сек.", RED)
                    await send_log_to_telegram(f"🔴 Ошибка при вступлении аккаунта <b>{rd_session_name}</b> в целевую группу (<code>{dest}</code>): {e}. Пауза {delay_after_error} сек.")
                    await asyncio.sleep(delay_after_error)
                    continue # Try with next user or account after delay

            if isinstance(user, InputPeerUser):
                colored_print(f"    📧 {ind + 1}/{len(usernames)}: Приглашаем @{username} в группу с аккаунта {rd_session_name}...", GREEN)
                await account(InviteToChannelRequest(dest_entity, [user]))
                # Verification of addition is tricky and often unreliable immediately.
                # Telegram might delay actual addition or not provide instant feedback.
                # The log below is optimistic. For critical tasks, verify separately or later.
                colored_print(f"    ✅ {ind + 1}/{len(usernames)}: Приглашение для @{username} отправлено с аккаунта {rd_session_name}. (Проверка фактического добавления не производится).", GREEN)
                await send_log_to_telegram(f"✅ [{ind+1}/{len(usernames)}] Приглашение для <b>@{username}</b> отправлено. (Аккаунт: <b>{rd_session_name}</b>)")
                with open(USED_FILE, 'a') as used_file:
                    used_file.write(f"{username}\n") # Log attempt
            else:
                colored_print(f"    ℹ️ {ind + 1}/{len(usernames)}: Нельзя пригласить @{username}, так как это не пользователь (возможно, канал, удаленный аккаунт или бот). Пропускаем.", YELLOW)
                await send_log_to_telegram(f"ℹ️ [{ind+1}/{len(usernames)}] Нельзя пригласить @{username}, так как это не пользователь (возможно, канал, удаленный аккаунт или бот). Пропускаем.")
                # No need for delay_after_error here, it's not an error, just a skip
                # However, if this happens often, it might indicate a bad username list

        except ValueError: # User not found
            colored_print(f'      🟡 {ind + 1}/{len(usernames)}: Пользователь @{username} не найден аккаунтом {rd_session_name}. Пропускаем. 🤷', YELLOW)
            await send_log_to_telegram(f"🟡 [{ind+1}/{len(usernames)}] Пользователь <b>@{username}</b> не найден аккаунтом {rd_session_name}. Пропускаем. 🤷")
            # No specific delay needed for not found, but general delay_after_error might be too long.
            # Consider a shorter, specific delay or rely on delay_between_clients.
            await asyncio.sleep(random.uniform(1,3)) # Short random delay
        except FloodWaitError as e:
            flood_seconds = e.seconds
            colored_print(f'      🌊 FloodWaitError для аккаунта {rd_session_name}: необходимо подождать {flood_seconds} секунд. Аккаунт будет на перерыве. ⏳', RED)
            await send_log_to_telegram(f"🌊 FloodWaitError для аккаунта <b>{rd_session_name}</b>: необходимо подождать {flood_seconds} секунд. Аккаунт будет на перерыве. ⏳")
            accounts_on_cooldown[rd_session_name] = time.time() + flood_seconds
            # No need to sleep here, the main loop will handle cooldown and switch accounts.
            # If this was the only account, the script would pause effectively.
            ind -=1 # Retry current user with a different account after this account's cooldown
        except UserChannelsTooMuchError: # This error was already handled during JoinChannelRequest, but can happen here too if not joined
            colored_print(f"    🔴 Аккаунт {rd_session_name} уже состоит в слишком большом количестве каналов/групп. 🤯 Пропускаем аккаунт.", RED)
            await send_log_to_telegram(f"🔴 Аккаунт <b>{rd_session_name}</b> уже состоит в слишком большом количестве каналов/групп. 🤯 Пропускаем аккаунт.")
            if rd_session_name in session_names: session_names.remove(rd_session_name)
            if not session_names:
                colored_print("  🔴 Все доступные аккаунты были удалены (слишком много каналов). Завершение работы. 💔", RED)
                await send_log_to_telegram("🔴 Все доступные аккаунты были удалены (слишком много каналов). Завершение работы. 💔")
                sys.exit(1)
            ind -=1 # Retry current user with a different account
        except errors.UserPrivacyRestrictedError:
            colored_print(f"    🛡️ {ind + 1}/{len(usernames)}: Пользователь @{username} имеет настройки приватности, не позволяющие его пригласить с аккаунта {rd_session_name}. Пропускаем. 😔", YELLOW)
            await send_log_to_telegram(f"🛡️ [{ind+1}/{len(usernames)}] Пользователь <b>@{username}</b> имеет настройки приватности (аккаунт <b>{rd_session_name}</b>). Пропускаем. 😔")
            # Log to used.txt to avoid retrying this user again and again if privacy settings are permanent
            with open(USED_FILE, 'a') as used_file:
                used_file.write(f"{username} #PrivacyRestricted\n")
            await asyncio.sleep(delay_after_invite) # Standard delay after an attempt
        except errors.ChatAdminRequiredError: # Should ideally not happen if bot is admin or invites are open
            colored_print(f"    👮 Аккаунт {rd_session_name} не имеет прав администратора в целевой группе для приглашения (или приглашения закрыты). Проверьте права. Пропускаем аккаунт. 😔", RED)
            await send_log_to_telegram(f"👮 Аккаунт <b>{rd_session_name}</b> не имеет прав администратора в целевой группе <code>{dest}</code> (или приглашения закрыты). Проверьте права. Пропускаем аккаунт. 😔")
            if rd_session_name in session_names: session_names.remove(rd_session_name)
            if not session_names:
                colored_print("  🔴 Все доступные аккаунты не имеют прав администратора. Завершение работы. 💔", RED)
                await send_log_to_telegram("🔴 Все доступные аккаунты не имеют прав администратора. Завершение работы. 💔")
                sys.exit(1)
            ind -=1 # Retry current user with a different account
        except errors.UserNotMutualContactError: # Rare for group invites, more common for adding friends
            colored_print(f"    🤝 {ind + 1}/{len(usernames)}: Пользователь @{username} не является взаимным контактом для аккаунта {rd_session_name} и не может быть приглашен (нетипично для групп). Пропускаем. 😔", YELLOW)
            await send_log_to_telegram(f"🤝 [{ind+1}/{len(usernames)}] Пользователь <b>@{username}</b> не является взаимным контактом для <b>{rd_session_name}</b> (нетипично для групп). Пропускаем. 😔")
            await asyncio.sleep(delay_after_invite)
        except errors.rpcerrorlist.BotGroupsBlockedError: # If the inviting account is a bot and is blocked
            colored_print(f"    🚫 Бот {rd_session_name} заблокирован в группе {dest} или не может писать сообщения. Пропускаем аккаунт.", RED)
            await send_log_to_telegram(f"🚫 Бот <b>{rd_session_name}</b> заблокирован в группе <code>{dest}</code> или не может писать сообщения. Пропускаем аккаунт.")
            if rd_session_name in session_names: session_names.remove(rd_session_name)
            if not session_names:
                colored_print("  🔴 Все доступные аккаунты-боты заблокированы. Завершение работы. 💔", RED)
                await send_log_to_telegram("🔴 Все доступные аккаунты-боты заблокированы. Завершение работы. 💔")
                sys.exit(1)
            ind -=1 # Retry current user
        except errors.rpcerrorlist.UserKickedError: # User was kicked and cannot be re-invited
            colored_print(f"    🚫 {ind + 1}/{len(usernames)}: Пользователь @{username} был ранее удален из группы и не может быть приглашен снова. Пропускаем. 😔", YELLOW)
            await send_log_to_telegram(f"🚫 [{ind+1}/{len(usernames)}] Пользователь <b>@{username}</b> был ранее удален из группы и не может быть приглашен снова. Пропускаем. 😔")
            with open(USED_FILE, 'a') as used_file:
                used_file.write(f"{username} #Kicked\n")
            await asyncio.sleep(delay_after_invite)
        except errors.rpcerrorlist.ChatWriteForbiddenError: # General write permission issue
            colored_print(f"    ✍️ Аккаунт {rd_session_name} не имеет права писать/приглашать в целевую группу {dest} (возможно, группа только для чтения или аккаунт ограничен). Пропускаем аккаунт.", RED)
            await send_log_to_telegram(f"✍️ Аккаунт <b>{rd_session_name}</b> не имеет права писать/приглашать в целевую группу <code>{dest}</code>. Пропускаем аккаунт.")
            if rd_session_name in session_names: session_names.remove(rd_session_name)
            if not session_names:
                colored_print("  🔴 Все доступные аккаунты не могут писать/приглашать в целевую группу. Завершение работы. 💔", RED)
                await send_log_to_telegram("🔴 Все доступные аккаунты не могут писать/приглашать в целевую группу. Завершение работы. 💔")
                sys.exit(1)
            ind -=1 # Retry current user
        except errors.rpcerrorlist.UsersTooMuchError: # Account has hit its daily invite limit
            colored_print(f"    📈 Аккаунт {rd_session_name} достиг лимита приглашений на сегодня. Аккаунт будет на перерыве до завтра. ⏳ Пропускаем аккаунт.", RED)
            await send_log_to_telegram(f"📈 Аккаунт <b>{rd_session_name}</b> достиг лимита приглашений. Аккаунт будет на перерыве до завтра. ⏳ Пропускаем аккаунт.")
            accounts_on_cooldown[rd_session_name] = time.time() + 86400 # Cooldown for 24 hours
            if rd_session_name in session_names: session_names.remove(rd_session_name) # Effectively remove for this run or manage cooldowns centrally
            if not session_names and not any(accounts_on_cooldown[s] < time.time() + 86000 for s in accounts_on_cooldown): # Check if any account will be available soon
                 colored_print("  🔴 Все доступные аккаунты достигли суточного лимита приглашений. Завершение работы. 💔", RED)
                 await send_log_to_telegram("🔴 Все доступные аккаунты достигли суточного лимита приглашений. Завершение работы. 💔")
                 sys.exit(1)
            ind -=1 # Retry current user
        except Exception as e:
            colored_print(f'      🔴 {ind + 1}/{len(usernames)}: Непредвиденная ошибка при работе с @{username} через аккаунт {rd_session_name}: {type(e).__name__}: {e} 😥. Пропускаем пользователя и ждём {delay_after_error} сек.', RED)
            await send_log_to_telegram(f"🔴 Непредвиденная ошибка при работе с <b>@{username}</b> через <b>{rd_session_name}</b>: {type(e).__name__}: {e} 😥. Пропускаем, ждём {delay_after_error} сек.")
            await asyncio.sleep(delay_after_error)
            # Consider not skipping user but putting account on short cooldown for unexpected errors.
            # For now, user is skipped.

        ind += 1
        db.set('start', str(ind)) # Save progress
        db.dump() # Ensure data is written to disk
        colored_print(f"    ⏳ Пауза {delay_after_invite:.2f} сек. после попытки приглашения...", YELLOW)
        await asyncio.sleep(delay_after_invite)


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    try:
        async def on_startup(_):
            colored_print("🤖 Телеграм-бот для управления командами успешно запущен! Готов принимать команды от администратора. 📡", GREEN)
            await send_log_to_telegram("🤖 Телеграм-бот для управления командами успешно запущен! Готов принимать команды от администратора. 📡")

        executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        colored_print("\n🚪 Прерывание пользователем (Ctrl+C). Завершение работы...", YELLOW)
        # Attempt to send a log message, but it might not always succeed during shutdown
        try:
            # Create a new loop for this final message if the main one is closing
            asyncio.run(send_log_to_telegram("🚪 Прерывание пользователем (Ctrl+C). Завершение работы..."))
        except RuntimeError: # Loop might be closed already
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
        except RuntimeError: # Loop might be closed already
            pass
        if loop.is_running() and not loop.is_closed():
            loop.close()