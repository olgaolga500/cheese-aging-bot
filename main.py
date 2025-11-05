# main.py — финальный рабочий файл
import os
import json
import base64
import logging
from datetime import datetime, date, time as dtime
from zoneinfo import ZoneInfo

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

# ---------- CONFIG ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_SERVICE_ACCOUNT_B64 = os.getenv("GOOGLE_SERVICE_ACCOUNT_B64")
PODGORICA_TZ = "Europe/Podgorica"
# ----------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if not BOT_TOKEN or not SPREADSHEET_ID or not GOOGLE_SERVICE_ACCOUNT_B64:
    raise RuntimeError("Please set BOT_TOKEN, SPREADSHEET_ID and GOOGLE_SERVICE_ACCOUNT_B64 env vars")

# --------- Google Sheets auth ----------
scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
try:
    service_json = base64.b64decode(GOOGLE_SERVICE_ACCOUNT_B64).decode("utf-8")
    service_account_info = json.loads(service_json)
except Exception as e:
    raise RuntimeError("Failed to parse GOOGLE_SERVICE_ACCOUNT_B64: " + str(e))

creds = ServiceAccountCredentials.from_json_keyfile_dict(service_account_info, scope)
gc = gspread.authorize(creds)

# worksheets
wb = gc.open_by_key(SPREADSHEET_ID)
batches_sheet = wb.worksheet("Batches")
actions_sheet = wb.worksheet("Actions")
sales_sheet = wb.worksheet("Sales")
subscribers_sheet = wb.worksheet("Subscribers")
# ---------------------------------------

# ---------- Utility helpers ----------
def now_iso():
    return datetime.now(ZoneInfo(PODGORICA_TZ)).strftime("%Y-%m-%d %H:%M:%S")

def today_iso_date():
    return date.today().strftime("%Y-%m-%d")

def read_unique_cheeses():
    try:
        rows = batches_sheet.get_all_records()
    except Exception:
        return []
    cheeses = []
    for r in rows:
        c = r.get("Cheese")
        if c and c not in cheeses:
            cheeses.append(c)
    return cheeses

def get_next_batch_id():
    rows = batches_sheet.col_values(1)  # BatchID column
    # skip header
    numeric = []
    for v in rows[1:]:
        try:
            numeric.append(int(v))
        except Exception:
            pass
    return (max(numeric) + 1) if numeric else 1

def add_subscriber(chat_id: int, name: str, role: str = "staff"):
    try:
        vals = subscribers_sheet.get_all_records()
    except Exception:
        vals = []
    existing_ids = [str(r.get("ChatID")) for r in vals]
    if str(chat_id) not in existing_ids:
        subscribers_sheet.append_row([chat_id, name, role, "TRUE"])
        logger.info(f"Added subscriber {name} ({chat_id})")

def get_active_subscribers():
    try:
        recs = subscribers_sheet.get_all_records()
    except Exception:
        return []
    result = []
    for r in recs:
        active = str(r.get("Active", "")).strip().lower()
        if active in ("true", "yes", "1"):
            result.append({"ChatID": r.get("ChatID"), "Name": r.get("Name")})
    return result

def format_task_row(row):
    # row: dict from actions.get_all_records
    # We will fetch Batch info to enrich display
    batchid = row.get("BatchID")
    action = row.get("Action", "")
    # find batch details
    try:
        batches = batches_sheet.get_all_records()
    except Exception:
        batches = []
    batch_info = None
    for b in batches:
        if str(b.get("BatchID")) == str(batchid):
            batch_info = b
            break
    if batch_info:
        cheese = batch_info.get("Cheese", "")
        head = batch_info.get("HeadNumbers", "")
        date_v = batch_info.get("Date", "")
        if head:
            title = f"{cheese} №{head} (партия {batchid})"
        else:
            title = f"{cheese} от {date_v} (партия {batchid})"
    else:
        title = f"Партия {batchid}"
    return title, action
# -------------------------------------

# ---------- Conversation states ----------
(ADD_CHEESE, ADD_MILK, ADD_QTY, ADD_TYPE, ADD_HEAD) = range(5)
(SALE_MODE, SALE_HEAD, SALE_HEAD_QTY, SALE_CHEESE, SALE_MILK, SALE_DATE, SALE_PICK_BATCH, SALE_QTY) = range(100, 108)
# -----------------------------------------

# ---------- Handlers ----------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = user.username or f"{user.first_name or ''} {user.last_name or ''}".strip()
    add_subscriber(update.effective_chat.id, name)
    keyboard = [["Добавить партию"], ["Списать сыр"], ["Мои задачи на сегодня"]]
    await update.message.reply_text("Привет! Ты подписан на уведомления. Выбери действие:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))


# -------- Add Batch flow ----------
async def addbatch_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cheeses = read_unique_cheeses()
    keyboard = []
    for c in cheeses:
        keyboard.append([c])
    keyboard.append(["+ Ввести вручную"])
    await update.message.reply_text("Выберите сыр из списка или нажмите '+ Ввести вручную':", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    return ADD_CHEESE

async def addbatch_cheese(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "+ Ввести вручную":
        await update.message.reply_text("Введи название нового сыра (пример: Камамбер буйволиный):")
        # user will type name -> handle as ADD_CHEESE
        return ADD_CHEESE
    else:
        context.user_data["cheese"] = text
        keyboard = [["коровье", "козье"], ["буйволиное", "смесь"]]
        await update.message.reply_text("Выберите тип молока:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
        return ADD_MILK

async def addbatch_milk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["milk"] = update.message.text.strip()
    await update.message.reply_text("Сколько головок? (в штуках, целое число):")
    return ADD_QTY

async def addbatch_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    try:
        qty = int(txt)
        if qty <= 0:
            raise ValueError()
    except Exception:
        await update.message.reply_text("Пожалуйста, введи целое положительное число для количества.")
        return ADD_QTY
    context.user_data["qty"] = qty
    keyboard = [["small", "big"]]
    await update.message.reply_text("Тип партии (small — маленькие головки, big — одиночная нумерованная головка):", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    return ADD_TYPE

async def addbatch_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    typ = update.message.text.strip().lower()
    if typ not in ("small", "big"):
        await update.message.reply_text("Выбери 'small' или 'big'.")
        return ADD_TYPE
    context.user_data["type"] = typ
    if typ == "big":
        await update.message.reply_text("Введите номер головки (например: 14):")
        return ADD_HEAD
    else:
        # finalize
        cheese = context.user_data.get("cheese")
        milk = context.user_data.get("milk")
        qty = context.user_data.get("qty")
        batch_id = get_next_batch_id()
        date_iso = date.today().strftime("%Y-%m-%d")
        row = [batch_id, date_iso, cheese, milk, qty, qty, "", "small", "Active", ""]
        batches_sheet.append_row(row)
        await update.message.reply_text(f"Добавлена партия {cheese} ({milk}), {qty} шт. BatchID={batch_id}")
        context.user_data.clear()
        return ConversationHandler.END

async def addbatch_head(update: Update, context: ContextTypes.DEFAULT_TYPE):
    head = update.message.text.strip()
    # accept as string
    cheese = context.user_data.get("cheese")
    milk = context.user_data.get("milk")
    qty = context.user_data.get("qty")
    batch_id = get_next_batch_id()
    date_iso = date.today().strftime("%Y-%m-%d")
    row = [batch_id, date_iso, cheese, milk, qty, qty, head, "big", "Active", ""]
    batches_sheet.append_row(row)
    await update.message.reply_text(f"Добавлена большая головка {cheese} №{head}. BatchID={batch_id}")
    context.user_data.clear()
    return ConversationHandler.END

# -------- Sale flow ----------
async def sale_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [["По номеру головки"], ["По партии (дата + молоко)"]]
    await update.message.reply_text("Как списываем?", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    return SALE_MODE

async def sale_mode_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    if txt == "По номеру головки":
        await update.message.reply_text("Введи номер головки (например: 14):")
        return SALE_HEAD
    else:
        # choose cheese
        cheeses = read_unique_cheeses()
        if not cheeses:
            await update.message.reply_text("Нет доступных сыров в базе.")
            return ConversationHandler.END
        kb = [[c] for c in cheeses]
        await update.message.reply_text("Выберите сыр:", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
        return SALE_CHEESE

async def sale_by_head(update: Update, context: ContextTypes.DEFAULT_TYPE):
    head = update.message.text.strip()
    context.user_data["head"] = head
    # find batch where HeadNumbers contains head
    rows = batches_sheet.get_all_records()
    target = None
    for r in rows:
        hn = str(r.get("HeadNumbers") or "").strip()
        if hn == str(head) or ("," in hn and str(head) in [x.strip() for x in hn.split(",")]):
            target = r
            break
    if not target:
        await update.message.reply_text("Не нашёл партию с таким номером головки.")
        return ConversationHandler.END
    context.user_data["batchid"] = target.get("BatchID")
    await update.message.reply_text("Сколько головок списать? (обычно 1):")
    return SALE_HEAD_QTY

async def sale_by_head_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        qty = int(update.message.text.strip())
    except Exception:
        await update.message.reply_text("Введи целое число.")
        return SALE_HEAD_QTY
    batchid = context.user_data.get("batchid")
    # append to Sales: SaleDate | BatchID | Qty (pcs) | Customer | Who | Timestamp
    sdate = date.today().strftime("%Y-%m-%d")
    who = update.effective_user.username or update.effective_user.full_name
    sales_sheet.append_row([sdate, batchid, qty, "", who, now_iso()])
    await update.message.reply_text(f"Записано в Sales: Batch {batchid} — {qty} шт.")
    context.user_data.clear()
    return ConversationHandler.END

async def sale_choose_cheese(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cheese = update.message.text.strip()
    context.user_data["cheese"] = cheese
    keyboard = [["коровье", "козье"], ["буйволиное", "смесь"]]
    await update.message.reply_text("Выберите тип молока:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    return SALE_MILK

async def sale_choose_milk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["milk"] = update.message.text.strip()
    await update.message.reply_text("Введите дату партии (ISO, например 2025-09-03):")
    return SALE_DATE

async def sale_choose_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dt = update.message.text.strip()
    # validate ISO date
    try:
        # simple check
        datetime.strptime(dt, "%Y-%m-%d")
    except Exception:
        await update.message.reply_text("Неверный формат даты. Используй YYYY-MM-DD.")
        return SALE_DATE
    context.user_data["date"] = dt
    # find matching batches
    rows = batches_sheet.get_all_records()
    candidates = []
    for r in rows:
        if str(r.get("Cheese")) == str(context.user_data["cheese"]) and str(r.get("MilkType")) == str(context.user_data["milk"]) and str(r.get("Date")) == dt:
            # include only with Remaining >0
            try:
                rem = int(r.get("Remaining") or 0)
            except Exception:
                rem = 0
            if rem > 0:
                candidates.append(r)
    if not candidates:
        await update.message.reply_text("Не найдено партий по этим параметрам с остатком >0.")
        return ConversationHandler.END
    # show options
    kb = [[f'Batch {c.get("BatchID")} — осталось {c.get("Remaining")}']] 
    # use first candidate if multiple? better list them
    kb = [[f'Batch {c.get("BatchID")} — осталось {c.get("Remaining")}'] for c in candidates]
    await update.message.reply_text("Выберите партию:", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
    return SALE_PICK_BATCH

async def sale_pick_batch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    # extract BatchID
    try:
        bid = int(txt.split()[1])
    except Exception:
        await update.message.reply_text("Не понял выбор. Нажми на строку с Batch ...")
        return ConversationHandler.END
    context.user_data["batchid"] = bid
    await update.message.reply_text("Количество головок для списания (шт):")
    return SALE_QTY

async def sale_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        qty = int(update.message.text.strip())
    except Exception:
        await update.message.reply_text("Введи целое число.")
        return SALE_QTY
    batchid = context.user_data.get("batchid")
    sdate = date.today().strftime("%Y-%m-%d")
    who = update.effective_user.username or update.effective_user.full_name
    sales_sheet.append_row([sdate, batchid, qty, "", who, now_iso()])
    await update.message.reply_text(f"Записано в Sales: Batch {batchid} — {qty} шт.")
    context.user_data.clear()
    return ConversationHandler.END

# -------- Today tasks and Done callback ----------
async def send_daily_notifications(context: ContextTypes.DEFAULT_TYPE):
    # get today's actions where Done empty
    try:
        rows = actions_sheet.get_all_records()
    except Exception:
        return
    today = date.today().strftime("%Y-%m-%d")
    tasks = []
    row_indices = []
    raw = actions_sheet.get_all_values()  # for row indexing
    # iterate records with index mapping to sheet row number (header row is 1)
    for idx, r in enumerate(rows, start=2):
        if str(r.get("ActionDate")) == today and not r.get("Done"):
            tasks.append((idx, r))
    if not tasks:
        # optional: notify subscribers there's nothing
        subs = get_active_subscribers()
        for s in subs:
            try:
                await context.bot.send_message(chat_id=int(s["ChatID"]), text="На сегодня нет задач по Actions. Хорошего дня!")
            except Exception:
                pass
        return
    subs = get_active_subscribers()
    for s in subs:
        cid = int(s["ChatID"])
        for idx, r in tasks:
            title, action_text = format_task_row(r)
            text = f"🧀 {title}\n— {action_text}"
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Done", callback_data=f"done:{idx}")]])
            try:
                await context.bot.send_message(chat_id=cid, text=text, reply_markup=kb)
            except Exception:
                pass

async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # user asked to see today's tasks
    try:
        rows = actions_sheet.get_all_records()
    except Exception:
        await update.message.reply_text("Ошибка чтения Actions.")
        return
    today = date.today().strftime("%Y-%m-%d")
    tasks = []
    for idx, r in enumerate(rows, start=2):
        if str(r.get("ActionDate")) == today and not r.get("Done"):
            tasks.append((idx, r))
    if not tasks:
        await update.message.reply_text("На сегодня нет задач.")
        return
    for idx, r in tasks:
        title, action_text = format_task_row(r)
        text = f"🧀 {title}\n— {action_text}"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Done", callback_data=f"done:{idx}")]])
        await update.message.reply_text(text, reply_markup=kb)

async def callback_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data  # done:{row}
    try:
        row_idx = int(data.split(":")[1])
    except Exception:
        await query.edit_message_text("Неверный формат callback.")
        return
    user = query.from_user
    who = user.username or f"{user.first_name or ''} {user.last_name or ''}".strip()
    ts = now_iso()
    # write to actions_sheet columns: Done (col 4), Who (col5), Timestamp (col6)
    try:
        actions_sheet.update_cell(row_idx, 4, "YES")
        actions_sheet.update_cell(row_idx, 5, who)
        actions_sheet.update_cell(row_idx, 6, ts)
    except Exception as e:
        logger.exception("Failed to mark done: " + str(e))
        await query.edit_message_text("Ошибка при записи статуса.")
        return
    # get row content to include in broadcast
    row = actions_sheet.row_values(row_idx)
    # columns: BatchID(1), ActionDate(2), Action(3), Done(4), Who(5), Timestamp(6)
    batchid = row[0] if len(row) >= 1 else ""
    action_text = row[2] if len(row) >= 3 else ""
    # try to get batch info to format message
    try:
        batch_recs = batches_sheet.get_all_records()
    except Exception:
        batch_recs = []
    batch_info = None
    for b in batch_recs:
        if str(b.get("BatchID")) == str(batchid):
            batch_info = b
            break
    if batch_info:
        cheese = batch_info.get("Cheese", "")
        head = batch_info.get("HeadNumbers", "")
        date_v = batch_info.get("Date", "")
        if head:
            title = f"{cheese} №{head} (партия {batchid})"
        else:
            title = f"{cheese} от {date_v} (партия {batchid})"
    else:
        title = f"Партия {batchid}"
    broadcast_text = f"✅ {who} выполнил:\n{title}\n— {action_text}"
    # broadcast to all active subscribers
    subs = get_active_subscribers()
    for s in subs:
        try:
            await context.bot.send_message(chat_id=int(s["ChatID"]), text=broadcast_text)
        except Exception:
            pass
    # edit original message to show done
    try:
        await query.edit_message_text(f"✅ Выполнено ({who})\n{title}\n— {action_text}")
    except Exception:
        pass

# ---------- Build application ----------
def build_app():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", cmd_start))
    # add batch conversation
    add_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^(Добавить партию)$"), addbatch_start), CommandHandler("addbatch", addbatch_start)],
        states={
            ADD_CHEESE: [MessageHandler(filters.TEXT & ~filters.COMMAND, addbatch_cheese)],
            ADD_MILK: [MessageHandler(filters.TEXT & ~filters.COMMAND, addbatch_milk)],
            ADD_QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, addbatch_qty)],
            ADD_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, addbatch_type)],
            ADD_HEAD: [MessageHandler(filters.TEXT & ~filters.COMMAND, addbatch_head)],
        },
        fallbacks=[MessageHandler(filters.Regex("^Отмена$"), lambda u, c: ConversationHandler.END)],
        allow_reentry=True,
    )
    app.add_handler(add_conv)

    # sale conversation
    sale_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^(Списать сыр)$"), sale_start), CommandHandler("sale", sale_start)],
        states={
            SALE_MODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, sale_mode_choice)],
            SALE_HEAD: [MessageHandler(filters.TEXT & ~filters.COMMAND, sale_by_head)],
            SALE_HEAD_QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, sale_by_head_qty)],
            SALE_CHEESE: [MessageHandler(filters.TEXT & ~filters.COMMAND, sale_choose_cheese)],
            SALE_MILK: [MessageHandler(filters.TEXT & ~filters.COMMAND, sale_choose_milk)],
            SALE_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, sale_choose_date)],
            SALE_PICK_BATCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, sale_pick_batch)],
            SALE_QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, sale_qty)],
        },
        fallbacks=[MessageHandler(filters.Regex("^Отмена$"), lambda u, c: ConversationHandler.END)],
        allow_reentry=True,
    )
    app.add_handler(sale_conv)

    # today tasks commands
    app.add_handler(MessageHandler(filters.Regex("^(Мои задачи на сегодня)$"), cmd_today))
    app.add_handler(CommandHandler("today", cmd_today))

    # callback for Done
    app.add_handler(CallbackQueryHandler(callback_done, pattern="^done:"))

    # schedule daily job at 09:00 Europe/Podgorica
    tz = ZoneInfo(PODGORICA_TZ)
    # 09:00 local Podgorica
    run_time = dtime(9, 0, tzinfo=tz)
    app.job_queue.run_daily(send_daily_notifications, time=run_time)

    return app

def main():
    app = build_app()
    logger.info("Bot starting...")
    app.run_polling()

if __name__ == "__main__":
    main()


