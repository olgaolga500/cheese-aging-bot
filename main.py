# main.py — финальная версия (с кешированием чтений от Google Sheets)
import os
import json
import base64
import logging
import time as _time
from datetime import datetime, date, time as dtime, timedelta
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
    ConversationHandler,
    CallbackQueryHandler,
    ContextTypes,
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

wb = gc.open_by_key(SPREADSHEET_ID)
# worksheets (must exist)
batches_sheet = wb.worksheet("Batches")
actions_sheet = wb.worksheet("Actions")
cheese_sheet = wb.worksheet("Cheese-Recipes")
sales_sheet = wb.worksheet("Sales")
subs_sheet = wb.worksheet("Subscribers")
# ---------------------------------------

# ---------- Simple sheet read-cache ----------
# cache structure: { sheet_title: (timestamp, data) }
SHEET_RECORDS_CACHE = {}
CACHE_TTL_SECONDS = 60  # increased TTL to 60s to avoid Read-request bursts causing 429

def cached_get_all_records(sheet_obj, ttl_seconds=CACHE_TTL_SECONDS):
    title = sheet_obj.title
    now = _time.time()
    entry = SHEET_RECORDS_CACHE.get(title)
    if entry:
        ts, data = entry
        if now - ts < ttl_seconds:
            return data
    # fetch fresh
    data = sheet_obj.get_all_records()
    SHEET_RECORDS_CACHE[title] = (now, data)
    return data

def invalidate_sheet_cache_by_title(sheet_title):
    if sheet_title in SHEET_RECORDS_CACHE:
        SHEET_RECORDS_CACHE.pop(sheet_title, None)

def invalidate_sheet_cache(sheet_obj):
    invalidate_sheet_cache_by_title(sheet_obj.title)

# ---------------------------------------------

# ---------- Conversation states ----------
(ADD_CHEESE, ADD_MILK, ADD_QTY, ADD_TYPE, ADD_HEAD) = range(5)
(SALE_MODE, SALE_HEAD, SALE_HEAD_QTY, SALE_CHEESE, SALE_MILK, SALE_DATE, SALE_PICK_BATCH, SALE_QTY) = range(100, 108)
# -----------------------------------------

# ---------- Helpers ----------
def now_iso():
    return datetime.now(ZoneInfo(PODGORICA_TZ)).strftime("%Y-%m-%d %H:%M:%S")

def today_iso():
    # use Podgorica local date
    return datetime.now(ZoneInfo(PODGORICA_TZ)).date().isoformat()

def read_unique_cheeses():
    vals = cached_get_all_records(cheese_sheet)
    res = []
    # vals are list of dicts; but original code used col_values — support both possibilities:
    if vals and isinstance(vals, list) and isinstance(vals[0], dict):
        for r in vals:
            v = r.get("Cheese") if isinstance(r, dict) else None
            if v and v not in res:
                res.append(v)
        return res
    # fallback: read first column from sheet directly (rare)
    col = cheese_sheet.col_values(1)
    for v in col[1:]:
        if v and v not in res:
            res.append(v)
    return res

def get_next_batch_id():
    # faster to use col_values; low-frequency operation
    col = batches_sheet.col_values(1)
    nums = []
    for v in col[1:]:
        try:
            nums.append(int(v))
        except Exception:
            continue
    return max(nums) + 1 if nums else 1

def get_active_subscribers():
    recs = cached_get_all_records(subs_sheet)
    out = []
    for r in recs:
        active = str(r.get("Active", "")).strip().lower()
        if active in ("true", "yes", "1"):
            try:
                out.append({"ChatID": int(r.get("ChatID")), "Name": r.get("Name")})
            except Exception:
                continue
    return out

def main_menu_keyboard():
    return ReplyKeyboardMarkup([["Сварить сыр", "Списать сыр"], ["Задания на сегодня"]], resize_keyboard=True)
# -------------------------------------

# ---------- Handlers ----------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = user.username or (user.first_name or "") + (" " + user.last_name if user.last_name else "")
    # add to subscribers if not present
    try:
        recs = cached_get_all_records(subs_sheet)
    except Exception:
        recs = []
    ids = [str(r.get("ChatID")) for r in recs]
    if str(update.effective_chat.id) not in ids:
        try:
            subs_sheet.append_row([update.effective_chat.id, name, "staff", "TRUE"])
            invalidate_sheet_cache(subs_sheet)
        except Exception:
            logger.exception("Failed to add subscriber")
    await update.message.reply_text("Привет! Выбери действие:", reply_markup=main_menu_keyboard())

# ---- Add batch flow ----
async def addbatch_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cheeses = read_unique_cheeses()
    if not cheeses:
        await update.message.reply_text("Список сыров пуст. Пожалуйста, добавь сыры в лист Cheese-Recipes и попробуй снова.", reply_markup=main_menu_keyboard())
        return ConversationHandler.END
    kb = [[c] for c in cheeses]
    await update.message.reply_text("Выберите сыр (берётся из Cheese-Recipes):", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
    return ADD_CHEESE

async def addbatch_cheese(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data["cheese"] = text
    kb = [["коровье", "козье"], ["буйволиное", "смесь"]]
    await update.message.reply_text("Выберите тип молока:", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
    return ADD_MILK

async def addbatch_milk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["milk"] = update.message.text.strip()
    await update.message.reply_text("Сколько головок? (целое число):")
    return ADD_QTY

async def addbatch_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    try:
        qty = int(txt)
        if qty <= 0:
            raise ValueError()
    except Exception:
        await update.message.reply_text("Введи целое положительное число.")
        return ADD_QTY
    context.user_data["qty"] = qty
    await update.message.reply_text("Тип партии: small или big", reply_markup=ReplyKeyboardMarkup([["small","big"]], resize_keyboard=True))
    return ADD_TYPE

async def addbatch_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    typ = update.message.text.strip().lower()
    if typ not in ("small", "big"):
        await update.message.reply_text("Выбери 'small' или 'big'.")
        return ADD_TYPE
    context.user_data["type"] = typ
    if typ == "big":
        await update.message.reply_text("Введите номер головки (пример: 14):")
        return ADD_HEAD
    # small — finalize
    cheese = context.user_data.get("cheese")
    milk = context.user_data.get("milk")
    qty = context.user_data.get("qty")
    batch_id = get_next_batch_id()
    date_iso = today_iso()
    row = [batch_id, date_iso, cheese, milk, qty, qty, "", "small", "Active", ""]
    try:
        batches_sheet.append_row(row)
        invalidate_sheet_cache(batches_sheet)
    except Exception:
        logger.exception("Failed to append batch")
        await update.message.reply_text("Ошибка при записи партии в таблицу.", reply_markup=main_menu_keyboard())
        context.user_data.clear()
        return ConversationHandler.END
    await update.message.reply_text(f"✅ Партия добавлена: {cheese} ({milk}) — {qty} шт.\nBatchID = {batch_id}", reply_markup=main_menu_keyboard())
    context.user_data.clear()
    return ConversationHandler.END

async def addbatch_head(update: Update, context: ContextTypes.DEFAULT_TYPE):
    head = update.message.text.strip()
    cheese = context.user_data.get("cheese")
    milk = context.user_data.get("milk")
    qty = context.user_data.get("qty")
    batch_id = get_next_batch_id()
    date_iso = today_iso()
    row = [batch_id, date_iso, cheese, milk, qty, qty, head, "big", "Active", ""]
    try:
        batches_sheet.append_row(row)
        invalidate_sheet_cache(batches_sheet)
    except Exception:
        logger.exception("Failed to append big batch")
        await update.message.reply_text("Ошибка при записи партии.", reply_markup=main_menu_keyboard())
        context.user_data.clear()
        return ConversationHandler.END
    await update.message.reply_text(f"✅ Добавлена большая головка: {cheese} №{head}\nBatchID = {batch_id}", reply_markup=main_menu_keyboard())
    context.user_data.clear()
    return ConversationHandler.END

# ---- Sale flow ----
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
        cheeses = read_unique_cheeses()
        if not cheeses:
            await update.message.reply_text("Нет доступных сыров в базе.", reply_markup=main_menu_keyboard())
            return ConversationHandler.END
        kb = [[c] for c in cheeses]
        await update.message.reply_text("Выберите сыр:", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
        return SALE_CHEESE

async def sale_by_head(update: Update, context: ContextTypes.DEFAULT_TYPE):
    head = update.message.text.strip()
    context.user_data["head"] = head
    rows = cached_get_all_records(batches_sheet)
    target = None
    for r in rows:
        hn = str(r.get("HeadNumbers") or "").strip()
        if hn == str(head) or ("," in hn and str(head) in [x.strip() for x in hn.split(",")]):
            target = r
            break
    if not target:
        await update.message.reply_text("Не нашёл партию с таким номером головки.", reply_markup=main_menu_keyboard())
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
    sdate = today_iso()
    who = update.effective_user.username or (update.effective_user.full_name or "")
    try:
        sales_sheet.append_row([sdate, batchid, qty, "", who, now_iso()])
        invalidate_sheet_cache(sales_sheet)
        invalidate_sheet_cache(batches_sheet)  # because Sales script may change Remaining
    except Exception:
        logger.exception("Failed to append sale")
        await update.message.reply_text("Ошибка при записи в Sales.", reply_markup=main_menu_keyboard())
        context.user_data.clear()
        return ConversationHandler.END
    await update.message.reply_text(f"Записано в Sales: Batch {batchid} — {qty} шт.", reply_markup=main_menu_keyboard())
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
    try:
        datetime.strptime(dt, "%Y-%m-%d")
    except Exception:
        await update.message.reply_text("Неверный формат даты. Используй YYYY-MM-DD.")
        return SALE_DATE
    context.user_data["date"] = dt
    rows = cached_get_all_records(batches_sheet)
    candidates = []
    for r in rows:
        if str(r.get("Cheese")) == str(context.user_data["cheese"]) and str(r.get("MilkType")) == str(context.user_data["milk"]) and str(r.get("Date")) == dt:
            try:
                rem = int(r.get("Remaining") or 0)
            except Exception:
                rem = 0
            if rem > 0:
                candidates.append(r)
    if not candidates:
        await update.message.reply_text("Не найдено партий по этим параметрам с остатком >0.", reply_markup=main_menu_keyboard())
        return ConversationHandler.END
    kb = [[f'Batch {c.get("BatchID")} — осталось {c.get("Remaining")}'] for c in candidates]
    await update.message.reply_text("Выберите партию:", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
    return SALE_PICK_BATCH

async def sale_pick_batch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    try:
        bid = int(txt.split()[1])
    except Exception:
        await update.message.reply_text("Не понял выбор. Нажми на строку с Batch ...", reply_markup=main_menu_keyboard())
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
    sdate = today_iso()
    who = update.effective_user.username or (update.effective_user.full_name or "")
    try:
        sales_sheet.append_row([sdate, batchid, qty, "", who, now_iso()])
        invalidate_sheet_cache(sales_sheet)
        invalidate_sheet_cache(batches_sheet)
    except Exception:
        logger.exception("Failed to append sale")
        await update.message.reply_text("Ошибка при записи в Sales.", reply_markup=main_menu_keyboard())
        context.user_data.clear()
        return ConversationHandler.END
    await update.message.reply_text(f"Записано в Sales: Batch {batchid} — {qty} шт.", reply_markup=main_menu_keyboard())
    context.user_data.clear()
    return ConversationHandler.END

# ---- Actions / Today / Done ----
def format_task_row_enriched(r, batches_cache=None):
    # r is dict from actions_sheet.get_all_records
    batchid = r.get("BatchID")
    title = f"Партия {batchid}"
    if batches_cache is None:
        batches_cache = cached_get_all_records(batches_sheet)
    for b in batches_cache:
        if str(b.get("BatchID")) == str(batchid):
            cheese = b.get("Cheese", "")
            head = b.get("HeadNumbers", "")
            d = b.get("Date", "")
            if head:
                title = f"{cheese} №{head} (партия {batchid})"
            else:
                title = f"{cheese} от {d} (партия {batchid})"
            break
    action_text = r.get("Action", "")
    return title, action_text

async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        rows = cached_get_all_records(actions_sheet)
    except Exception:
        await update.message.reply_text("Ошибка чтения Actions.", reply_markup=main_menu_keyboard())
        return
    today = today_iso()
    tasks = []
    for idx, r in enumerate(rows, start=2):
        if str(r.get("ActionDate")) == today and not r.get("Done"):
            tasks.append((idx, r))
    if not tasks:
        await update.message.reply_text("На сегодня нет задач.", reply_markup=main_menu_keyboard())
        return
    batches_cache = cached_get_all_records(batches_sheet)
    for idx, r in tasks:
        title, action_text = format_task_row_enriched(r, batches_cache=batches_cache)
        text = f"🧀 {title}\n— {action_text}"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Done", callback_data=f"done:{idx}")]])
        await update.message.reply_text(text, reply_markup=kb)

async def send_daily_notifications(context: ContextTypes.DEFAULT_TYPE):
    try:
        rows = cached_get_all_records(actions_sheet)
    except Exception:
        return
    today = today_iso()
    tasks = []
    for idx, r in enumerate(rows, start=2):
        if str(r.get("ActionDate")) == today and not r.get("Done"):
            tasks.append((idx, r))
    if not tasks:
        return
    subs = get_active_subscribers()
    batches_cache = cached_get_all_records(batches_sheet)
    for s in subs:
        cid = s["ChatID"]
        for idx, r in tasks:
            title, action_text = format_task_row_enriched(r, batches_cache=batches_cache)
            text = f"🧀 {title}\n— {action_text}"
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Done", callback_data=f"done:{idx}")]])
            try:
                await context.bot.send_message(chat_id=cid, text=text, reply_markup=kb)
            except Exception:
                logger.exception("Failed to send daily message to " + str(cid))

async def callback_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    try:
        row_idx = int(data.split(":")[1])
    except Exception:
        await query.edit_message_text("Неверный callback.")
        return
    user = query.from_user
    who = user.username or (user.first_name or "")
    ts = now_iso()
    try:
        actions_sheet.update_cell(row_idx, 4, "TRUE")   # Done col -> TRUE now
        actions_sheet.update_cell(row_idx, 5, who)    # Who col
        actions_sheet.update_cell(row_idx, 6, ts)     # Timestamp col
        invalidate_sheet_cache(actions_sheet)
    except Exception:
        logger.exception("Failed to write done to Actions")
        await query.edit_message_text("Ошибка записи статуса.")
        return
    # read row to build broadcast
    try:
        row_vals = actions_sheet.row_values(row_idx)
    except Exception:
        row_vals = []
    batchid = row_vals[0] if len(row_vals) >= 1 else ""
    action_text = row_vals[2] if len(row_vals) >= 3 else ""
    # try get batch info for title
    try:
        batch_recs = cached_get_all_records(batches_sheet)
    except Exception:
        batch_recs = []
    title = f"Партия {batchid}"
    for b in batch_recs:
        if str(b.get("BatchID")) == str(batchid):
            cheese = b.get("Cheese", "")
            head = b.get("HeadNumbers", "")
            d = b.get("Date", "")
            if head:
                title = f"{cheese} №{head} (партия {batchid})"
            else:
                title = f"{cheese} от {d} (партия {batchid})"
            break
    broadcast = f"✅ {who} выполнил:\n{title}\n— {action_text}"
    subs = get_active_subscribers()
    for s in subs:
        try:
            await context.bot.send_message(chat_id=s["ChatID"], text=broadcast)
        except Exception:
            logger.exception("Failed to broadcast done to " + str(s.get("ChatID")))
    try:
        await query.edit_message_text(f"✅ Выполнено ({who})\n{title}\n— {action_text}")
    except Exception:
        pass

# ---------- Build and run ----------
def build_app():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    addbatch_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Сварить сыр$"), addbatch_start), CommandHandler("addbatch", addbatch_start)],
        states={
            ADD_CHEESE: [MessageHandler(filters.TEXT & ~filters.COMMAND, addbatch_cheese)],
            ADD_MILK: [MessageHandler(filters.TEXT & ~filters.COMMAND, addbatch_milk)],
            ADD_QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, addbatch_qty)],
            ADD_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, addbatch_type)],
            ADD_HEAD: [MessageHandler(filters.TEXT & ~filters.COMMAND, addbatch_head)],
        },
        fallbacks=[CommandHandler("start", cmd_start)],
        allow_reentry=True,
    )

    sale_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Списать сыр$"), sale_start), CommandHandler("sale", sale_start)],
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
        fallbacks=[CommandHandler("start", cmd_start)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("check", lambda update, context: ContextTypes.DEFAULT_TYPE))  # placeholder, replaced below
    app.add_handler(addbatch_conv)
    app.add_handler(sale_conv)
    app.add_handler(MessageHandler(filters.Regex("^Задания на сегодня$"), cmd_today))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CallbackQueryHandler(callback_done, pattern="^done:"))

    # replace placeholder: proper /check handler
    async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("Запускаю проверку и рассылку заданий (тест)...")
        await send_daily_notifications(context)
        await update.message.reply_text("Готово. Проверьте сообщения у подписчиков (и у себя).")
    app.add_handler(CommandHandler("check", cmd_check))

    # schedule daily job at 09:00 in Podgorica
    tz = ZoneInfo(PODGORICA_TZ)
    run_time = dtime(9, 0)

    app.job_queue.run_daily(
        send_daily_notifications,
        time=run_time,
        timezone=tz,
        days=(0, 1, 2, 3, 4, 5, 6)  # каждый день
    )

    return app


def main():
    app = build_app()
    logger.info("Bot starting...")
    app.run_polling()

if __name__ == "__main__":
    main()
