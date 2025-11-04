import logging
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, ForceReply
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

logging.basicConfig(level=logging.INFO)

# Авторизация Google Sheets
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
import json
from oauth2client.service_account import ServiceAccountCredentials
import os

scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

# Читаем JSON из переменной окружения
import base64
service_account_info = json.loads(base64.b64decode(os.environ["GOOGLE_SERVICE_ACCOUNT_B64"]).decode("utf-8"))


creds = ServiceAccountCredentials.from_json_keyfile_dict(service_account_info, scope)

client = gspread.authorize(creds)
sheet = client.open_by_key(os.environ["SPREADSHEET_ID"]).worksheet("Партии")

print(client.open_by_key(os.environ["SPREADSHEET_ID"]).title)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я бот управления созреванием сыра. Напиши /new чтобы добавить новую партию.")


async def new_batch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Напиши данные партии в формате:\n\n"
        "`Камамбер; буйвол; 12 шт; 2025-03-11`\n\n"
        "сыр; молоко; количество; дата",
        parse_mode="Markdown"
    )


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if ";" in text:
        parts = [v.strip() for v in text.split(";")]
        if len(parts) == 4:
            cheese, milk, qty, date = parts
            sheet.append_row([cheese, milk, qty, date])
            await update.message.reply_text("✅ Партия добавлена в таблицу.")
            return

    await update.message.reply_text("Не понял. Напиши /new для добавления партии.")


def main():
    app = ApplicationBuilder().token("BOT_TOKEN").build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("new", new_batch))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    app.run_polling()


if __name__ == "__main__":
    main()
