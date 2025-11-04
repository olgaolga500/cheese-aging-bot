import os
import logging
from datetime import datetime
from aiogram import Bot, Dispatcher, executor, types
import gspread
from oauth2client.service_account import ServiceAccountCredentials

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")

# Авторизация Google Sheets
scope = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
creds = ServiceAccountCredentials.from_json_keyfile_dict(
    eval(os.getenv("GOOGLE_SERVICE_ACCOUNT")), scope
)
client = gspread.authorize(creds)

batches = client.open_by_key(SPREADSHEET_ID).worksheet("Batches")
sales = client.open_by_key(SPREADSHEET_ID).worksheet("Sales")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)


def get_available_batches():
    data = batches.get_all_records()
    result = []
    for row in data:
        if row["Remaining"] and int(row["Remaining"]) > 0:
            result.append(
                f'{row["BatchID"]}: {row["Cheese"]} ({row["MilkType"]}) от {row["Date"]} — осталось {row["Remaining"]}'
            )
    return result


@dp.message_handler(commands=["start"])
async def start(message: types.Message):
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add("Списать сыр (продажа)")
    await message.answer("Привет! Что делаем?", reply_markup=keyboard)


@dp.message_handler(lambda msg: msg.text == "Списать сыр (продажа)")
async def choose_batch(message: types.Message):
    options = get_available_batches()
    if not options:
        return await message.answer("Сыров для списания нет.")
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for opt in options:
        keyboard.add(opt)
    await message.answer("Выбери партию:", reply_markup=keyboard)


@dp.message_handler(lambda msg: ":" in msg.text and "осталось" in msg.text)
async def ask_quantity(message: types.Message):
    batch_id = message.text.split(":")[0].strip()
    message.chat_data = {"batch_id": batch_id}
    await message.answer("Сколько головок списать? (в штуках)")


@dp.message_handler(lambda msg: msg.text.isdigit())
async def record_sale(message: types.Message):
    qty = int(message.text)
    batch_id = message.reply_to_message.chat_data["batch_id"]

    records = batches.get_all_records()
    for idx, row in enumerate(records):
        if str(row["BatchID"]) == str(batch_id):
            new_remaining = int(row["Remaining"]) - qty
            if new_remaining < 0:
                return await message.answer("Ошибка: списываем больше, чем есть.")
            batches.update_cell(idx + 2, 6, new_remaining)  # Remaining column
            break

    sales.append_row([
        datetime.now().strftime("%Y-%m-%d"),
        batch_id,
        qty,
        "",  # Customer пустой по умолчанию
        message.from_user.username or message.from_user.full_name,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ])

    await message.answer("✅ Списание записано.\nОстатки обновлены.")


if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)

