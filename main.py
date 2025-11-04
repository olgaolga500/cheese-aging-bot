import os
import logging
from datetime import datetime

from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    ConversationHandler,
    CallbackContext,
)

import gspread
from oauth2client.service_account import ServiceAccountCredentials
import base64
import json

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === Google Sheets Auth ===
google_creds_b64 = os.getenv("GOOGLE_SERVICE_ACCOUNT_B64")
creds_json = json.loads(base64.b64decode(google_creds_b64))
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_json, scope)
client = gspread.authorize(creds)

SHEET_NAME = "Cheese"  # ← поменяй если нужно
sheet = client.open(SHEET_NAME).sheet1

(
    RECORD_NAME,
    RECORD_MILKTYPE,
    RECORD_SIZE,
    RECORD_COUNT,
    WRITE_NAME,
    WRITE_SIZE,
    WRITE_COUNT
) = range(7)


def start(update: Update, context: CallbackContext):
    keyboard = [
        ["📥 Записать сыр"],
        ["📤 Списать сыр"]
    ]
    update.message.reply_text(
        "Выберите действие:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )


# === ЗАПИСЬ СЫРА ===
def record_start(update: Update, context: CallbackContext):
    update.message.reply_text("Введите название сыра:")
    return RECORD_NAME


def record_name(update: Update, context: CallbackContext):
    context.user_data["name"] = update.message.text

    keyboard = [["коровье", "козье"], ["буйволиное", "смесь"]]
    update.message.reply_text(
        "Выберите тип молока:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    return RECORD_MILKTYPE


def record_milktype(update: Update, context: CallbackContext):
    context.user_data["milktype"] = update.message.text

    keyboard = [["большая", "маленькая"]]
    update.message.reply_text(
        "Размер головки:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    return RECORD_SIZE


def record_size(update: Update, context: CallbackContext):
    context.user_data["size"] = update.message.text
    update.message.reply_text("Количество (шт):")
    return RECORD_COUNT


def record_count(update: Update, context: CallbackContext):
    count = update.message.text.strip()
    name = context.user_data["name"]
    milktype = context.user_data["milktype"]
    size = context.user_data["size"]
    date = datetime.now().strftime("%d.%m.%Y")

    sheet.append_row([date, name, milktype, size, count, "записано"])

    update.message.reply_text(f"✅ Записано: {name}, {size}, {count} шт.")
    return ConversationHandler.END


# === СПИСАНИЕ ===
def write_start(update: Update, context: CallbackContext):
    update.message.reply_text("Введите название сыра:")
    return WRITE_NAME


def write_name(update: Update, context: CallbackContext):
    context.user_data["name"] = update.message.text

    keyboard = [["большая", "маленькая"]]
    update.message.reply_text(
        "Размер головки:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    return WRITE_SIZE


def write_size(update: Update, context: CallbackContext):
    context.user_data["size"] = update.message.text
    update.message.reply_text("Количество (шт):")
    return WRITE_COUNT


def write_count(update: Update, context: CallbackContext):
    count = update.message.text.strip()
    name = context.user_data["name"]
    size = context.user_data["size"]
    date = datetime.now().strftime("%d.%m.%Y")

    sheet.append_row([date, name, "-", size, count, "списано"])

    update.message.reply_text(f"📤 Списано: {name}, {size}, {count} шт.")
    return ConversationHandler.END


def cancel(update: Update, context: CallbackContext):
    update.message.reply_text("Отменено.")
    return ConversationHandler.END


def main():
    updater = Updater(os.getenv("BOT_TOKEN"), use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))

    dp.add_handler(ConversationHandler(
        entry_points=[MessageHandler(Filters.regex("📥 Записать сыр"), record_start)],
        states={
            RECORD_NAME: [MessageHandler(Filters.text & ~Filters.command, record_name)],
            RECORD_MILKTYPE: [MessageHandler(Filters.text & ~Filters.command, record_milktype)],
            RECORD_SIZE: [MessageHandler(Filters.text & ~Filters.command, record_size)],
            RECORD_COUNT: [MessageHandler(Filters.text & ~Filters.command, record_count)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    ))

    dp.add_handler(ConversationHandler(
        entry_points=[MessageHandler(Filters.regex("📤 Списать сыр"), write_start)],
        states={
            WRITE_NAME: [MessageHandler(Filters.text & ~Filters.command, write_name)],
            WRITE_SIZE: [MessageHandler(Filters.text & ~Filters.command, write_size)],
            WRITE_COUNT: [MessageHandler(Filters.text & ~Filters.command, write_count)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    ))

    updater.start_polling()
    updater.idle()


if __name__ == "__main__":
    main()
