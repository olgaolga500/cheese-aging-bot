import os
import json
import base64
import logging
from datetime import datetime, date, time, timedelta

import gspread
from oauth2client.service_account import ServiceAccountCredentials
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")

# Google Sheets auth
scope = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

b64 = os.getenv("GOOGLE_SERVICE_ACCOUNT_B64")
if not b64:
    raise RuntimeError("Environment variable GOOGLE_SERVICE_ACCOUNT_B64 not found")

service_json = base64.b64decode(b64).decode("utf-8")
service_account_info = json.loads(service_json)
creds = ServiceAccountCredentials.from_json_keyfile_dict(service_account_info, scope)
client = gspread.authorize(creds)

# Sheets
batches = client.open_by_key(SPREADSHEET_ID).worksheet("Batches")
actions = client.open_by_key(SPREADSHEET_ID).worksheet("Actions")
subscribers = client.open_by_key(SPREADSHEET_ID).worksheet("Subscribers")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = ReplyKeyboardMarkup([["Списать сыр", "Мои задачи на сегодня"]], resize_keyboard=True)
    await update.message.reply_text("Привет! Что делаем?", reply_markup=keyboard)


async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user = update.message.from_user.username or update.message.from_user.full_name

    existing = subscribers.col_values(1)
    if str(chat_id) not in existing:
        subscribers.append_row([chat_id, user])

    await update.message.reply_text("✅ Ты подписан на ежедневные напоминания.")


def get_todays_actions():
    data = actions.get_all_records()
    today = date.today().strftime("%Y-%m-%d")
    result = []
    for idx, row in enumerate(data, start=2):
        if str(row["ActionDate"]) == today and not row["Done"]:
            result.append((idx, row))
    return result


async def send_daily_notifications(context: ContextTypes.DEFAULT_TYPE):
    task_list = get_todays_actions()
    if not task_list:
        return

    subs = subscribers.get_all_records()
    for sub in subs:
        chat_id = sub["ChatID"]
        for row_index, row in task_list:
            text = f"{row['Cheese']} — {row['Action']}"
            button = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Done", callback_data=f"done:{row_index}")]
            ])
            try:
                await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=button)
            except:
                pass


async def today_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task_list = get_todays_actions()
    if not task_list:
        await update.message.reply_text("На сегодня нет задач 🎉")
        return

    for row_index, row in task_list:
        text = f"{row['Cheese']} — {row['Action']}"
        button = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Done", callback_data=f"done:{row_index}")]
        ])
        await update.message.reply_text(text, reply_markup=button)


async def mark_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    row_index = int(query.data.split(":")[1])
    username = query.from_user.username or query.from_user.full_name
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    actions.update_cell(row_index, 4, True)  # Done
    actions.update_cell(row_index, 5, username)
    actions.update_cell(row_index, 6, timestamp)

    await query.edit_message_text(f"✅ Выполнено ({username})")


async def write_batch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Внесение партии скоро добавим 😉")


def main():
    app = ApplicationBuilder().token(TG_TOKEN).build()

    # Команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))

    # Планировщик
    job_queue = app.job_queue
    job_queue.run_daily(send_daily_notifications, time=time(7, 0))  # 7:00 UTC → ~09:00 Черногория

    print("Bot started")
    app.run_polling()


