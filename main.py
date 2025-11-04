import os
import json
import base64
import logging
from datetime import datetime

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from aiogram import Bot, Dispatcher, executor, types

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")
if not SPREADSHEET_ID:
    raise RuntimeError("SPREADSHEET_ID is missing")

# --- GOOGLE AUTH ---
b64 = os.getenv("GOOGLE_SERVICE_ACCOUNT_B64")
if not b64:
    raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_B64 is missing")

try:
    service_json = base64.b64decode(b64).decode("utf-8")
    service_account_info = json.loads(service_json)
except Exception as e:
    raise RuntimeError("Failed to decode GOOGLE_SERVICE_ACCOUNT_B64: " + str(e))

scope = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

creds = ServiceAccountCredentials.from_json_keyfile_dict(service_account_info, scope)
client = gspread.authorize(creds)

batches = client.open_by_key(SPREADSHEET_ID).worksheet("Batches")
sales = client.open_by_key(SPREADSHEET_ID).worksheet("Sales")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

# Временное хранилище выбора партии по пользователю
user_state = {}


def get_available_batches():
    data = batches.get_all_records()
    result = []
    for row in data:
        try:
            rem = int(row["Remaining"])
        except:
            rem = 0
        if rem > 0:
            result.append(
                f'{row["BatchID"]}: {row["Cheese"]} ({row["MilkType"]}) от {row["Date"]} — осталось {rem}'
            )
    return result


@dp.message_handler(commands=["start"])
async def start(message: types.Message):
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add("Списать сыр ✅")
    await message.answer("Привет! Что делаем?", reply_markup=keyboard)


@dp.message_handler(lambda msg: msg.text == "Списать сыр ✅")
async def choose_batch(message: types.Message):
    options = get_available_batches()
    if not options:
        return await message.answer("Сыров для списания нет ✅")

    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for opt in options:
        keyboard.add(opt)
    await message.answer("Выбери партию:", reply_markup=keyboard)


@dp.message_handler(lambda msg: ":" in msg.text and "осталось" in msg.text)
async def ask_quantity(message: types.Message):
    batch_id = message.text.split(":")[0].strip()
    user_state[message.from_user.id] = batch_id
    await message.answer("Сколько головок списать? ✏️")


@dp.message_handler(lambda msg: msg.text.isdigit())
async def record_sale(message: types.Message):
    user_id = message.from_user.id

    if user_id not in user_state:
        return  # Это не часть операции списания

    qty = int(message.text)
    batch_id = user_state[user_id]

    records = batches.get_all_records()
    for idx, row in enumerate(records):
        if str(row["BatchID"]) == str(batch_id):
            new_remaining = int(row["Remaining"]) - qty
            if new_remaining < 0:
                return await message.answer("Ошибка: списываем больше, чем есть ❌")

            # Update Remaining (column F = 6)
            batches.update_cell(idx + 2, 6, new_remaining)
            break

    sales.append_row([
        datetime.now().strftime("%Y-%m-%d"),
        batch_id,
        qty,
        "",  # Customer optional
        message.from_user.username or message.from_user.full_name,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ])

    del user_state[user_id]  # очищаем контекст

    await message.answer("✅ Списание записано.\nОстатки обновлены.")


if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)



if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)

