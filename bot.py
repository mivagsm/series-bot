import os, json, asyncio, logging, requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# ── تنظیمات ───────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = "8424424279:AAFI-Zcvp8KgS6B7sN-niDs9tiHGMWPwReo"
TMDB_API_KEY   = "c30cbc32c804c4f11212693b3841f14b"
DATA_FILE      = "data.json"
CHECK_INTERVAL = 6 * 3600

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

# ── ترجمه‌ها ───────────────────────────────────────────────────────────────────
STATUS_FA = {
    "Returning Series": ("🟢", "در حال پخش"),
    "In Production":    ("🟡", "در حال تولید"),
    "Planned":          ("🔵", "برنامه‌ریزی‌شده"),
    "Ended":            ("🔴", "پایان یافته"),
    "Canceled":         ("⛔", "کنسل شده"),
    "Pilot":            ("🟠", "پایلوت"),
}
DAYS_FA = {
    "Monday": "دوشنبه", "Tuesday": "سه‌شنبه", "Wednesday": "چهارشنبه",
    "Thursday": "پنج‌شنبه", "Friday": "جمعه", "Saturday": "شنبه", "Sunday": "یک‌شنبه"
}

# ── داده ──────────────────────────────────────────────────────────────────────
def load():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"users": {}}

def save(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_user(data, chat_id):
    uid = str(chat_id)
    if uid not in data["users"]:
        data["users"][uid] = {"series": {}, "waiting_for": None}
    return data["users"][uid]

# ── TMDB ──────────────────────────────────────────────────────────────────────
def tmdb_search(name):
    r = requests.get(
        "https://api.themoviedb.org/3/search/tv",
        params={"api_key": TMDB_API_KEY, "query": name, "language": "en-US"},
        timeout=10
    )
    results = r.json().get("results", [])
    return results[0] if results else None

def tmdb_info(series_id):
    r = requests.get(
        f"https://api.themoviedb.org/3/tv/{series_id}",
        params={"api_key": TMDB_API_KEY, "language": "en-US"},
        timeout=10
    )
    d = r.json()

    ep      = d.get("last_episode_to_air")
    next_ep = d.get("next_episode_to_air")
    status  = d.get("status", "")
    emoji, status_text = STATUS_FA.get(status, ("⚪", status))

    # روز پخش از next_episode یا last_episode
    air_day = ""
    ref_ep  = next_ep or ep
    if ref_ep and ref_ep.get("air_date"):
        from datetime import datetime
        try:
            dt      = datetime.strptime(ref_ep["air_date"], "%Y-%m-%d")
            day_en  = dt.strftime("%A")
            air_day = DAYS_FA.get(day_en, day_en)
        except Exception:
            pass

    networks = d.get("networks", [])
    network  = networks[0]["name"] if networks else ""

    return {
        "ep":          ep,
        "next_ep":     next_ep,
        "status_emoji": emoji,
        "status_text":  status_text,
        "air_day":      air_day,
        "network":      network,
        "seasons":      d.get("number_of_seasons", 0),
        "name":         d.get("name", ""),
    }

# ── کیبورد اصلی ───────────────────────────────────────────────────────────────
def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ افزودن سریال", callback_data="ask_add"),
         InlineKeyboardButton("📋 لیست من",      callback_data="list")],
        [InlineKeyboardButton("🔄 چک الان",      callback_data="check"),
         InlineKeyboardButton("🗑 حذف سریال",    callback_data="ask_remove")],
    ])

# ── /start ────────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load()
    user = get_user(data, update.effective_chat.id)
    user["waiting_for"] = None
    save(data)
    await update.message.reply_text(
        "🎬 *ربات ردیاب سریال*\n\nهر وقت قسمت جدیدی بیاد، فوری بهت خبر میدم.",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )

# ── هندلر متن آزاد ────────────────────────────────────────────────────────────
async def text_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data    = load()
    user    = get_user(data, update.effective_chat.id)
    waiting = user.get("waiting_for")

    if waiting == "add":
        user["waiting_for"] = None
        save(data)
        await do_add(update, data, update.effective_chat.id, update.message.text.strip())
    elif waiting == "remove":
        user["waiting_for"] = None
        save(data)
        await do_remove(update, data, update.effective_chat.id, update.message.text.strip())
    else:
        await update.message.reply_text("از دکمه‌های زیر استفاده کن 👇", reply_markup=main_keyboard())

# ── افزودن سریال ──────────────────────────────────────────────────────────────
async def do_add(update, data, chat_id, name):
    msg = await update.message.reply_text(f"🔍 دارم دنبال *{name}* می‌گردم...", parse_mode="Markdown")

    try:
        series = tmdb_search(name)
    except Exception:
        await msg.edit_text("❌ خطا در اتصال به سرور. دوباره امتحان کن.")
        return

    if not series:
        await msg.edit_text(
            "❌ سریالی پیدا نشد.\nاسم انگلیسی رو امتحان کن.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="back")]]),
        )
        return

    user = get_user(data, chat_id)
    sid  = str(series["id"])

    if sid in user["series"]:
        await msg.edit_text(
            f"⚠️ *{series['name']}* قبلاً توی لیستته!",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📋 مشاهده لیست", callback_data="list")]]),
        )
        return

    try:
        info = tmdb_info(series["id"])
    except Exception:
        info = {"ep": None, "next_ep": None, "status_emoji": "⚪", "status_text": "", "air_day": "", "network": "", "seasons": 0, "name": series["name"]}

    ep = info["ep"]
    user["series"][sid] = {
        "name":         info["name"] or series["name"],
        "s":            ep["season_number"]   if ep else 0,
        "e":            ep["episode_number"]  if ep else 0,
        "ep_name":      ep.get("name", "")    if ep else "",
        "date":         ep.get("air_date", "") if ep else "",
        "status_emoji": info["status_emoji"],
        "status_text":  info["status_text"],
        "air_day":      info["air_day"],
        "network":      info["network"],
        "seasons":      info["seasons"],
    }
    save(data)

    ep_code = f"S{ep['season_number']:02d}E{ep['episode_number']:02d}" if ep else "نامشخص"
    await msg.edit_text(
        f"✅ *{user['series'][sid]['name']}* اضافه شد!\n\n"
        f"└ آخرین قسمت: *{ep_code}* — {ep.get('name','') if ep else ''}\n"
        f"└ 📅 {ep.get('air_date','') if ep else ''}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📋 مشاهده لیست", callback_data="list")]]),
    )

# ── حذف سریال ─────────────────────────────────────────────────────────────────
async def do_remove(update, data, chat_id, name):
    user  = get_user(data, chat_id)
    found = None
    for sid, s in user["series"].items():
        if s["name"].lower() == name.lower():
            found = sid
            break

    if not found:
        await update.message.reply_text(
            f"❌ *{name}* توی لیستت نیست.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📋 مشاهده لیست", callback_data="list")]]),
        )
        return

    sname = user["series"][found]["name"]
    del user["series"][found]
    save(data)
    await update.message.reply_text(f"🗑 *{sname}* حذف شد.", parse_mode="Markdown", reply_markup=main_keyboard())

# ── نمایش لیست ────────────────────────────────────────────────────────────────
async def show_list(obj, chat_id, edit=False):
    data = load()
    user = get_user(data, chat_id)

    if not user["series"]:
        text = "📭 لیستت خالیه!\n\nیه سریال اضافه کن:"
        kb   = InlineKeyboardMarkup([[InlineKeyboardButton("➕ افزودن سریال", callback_data="ask_add")]])
        if edit: await obj.edit_text(text, reply_markup=kb)
        else:    await obj.reply_text(text, reply_markup=kb)
        return

    lines = ["📋 *سریال‌های من:*\n"]
    for s in user["series"].values():
        ep_code = f"S{s['s']:02d}E{s['e']:02d}"

        # وضعیت
        status_line = f"{s.get('status_emoji','⚪')} {s.get('status_text','')}"

        # روز پخش فقط اگه در حال پخشه
        air_line = ""
        if s.get("air_day") and s.get("status_text") == "در حال پخش":
            air_line = f"\n   └ 📡 پخش: {s['air_day']}ها"

        # شبکه
        network_line = f" — {s['network']}" if s.get("network") else ""

        lines.append(
            f"▪️ *{s['name']}*\n"
            f"   └ {status_line}{network_line}{air_line}\n"
            f"   └ 🎬 فصل {s.get('seasons',0)} | آخرین قسمت: {ep_code}\n"
            f"   └ 📅 {s['date']}\n"
        )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 چک الان", callback_data="check"),
         InlineKeyboardButton("🔙 برگشت",  callback_data="back")],
    ])

    if edit: await obj.edit_text("\n".join(lines), parse_mode="Markdown", reply_markup=kb)
    else:    await obj.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=kb)

# ── چک قسمت جدید برای یه کاربر ───────────────────────────────────────────────
async def do_check_user(app, chat_id, user):
    found = False
    for sid, s in list(user["series"].items()):
        try:
            info = tmdb_info(int(sid))
        except Exception:
            continue
        ep = info["ep"]
        if not ep:
            continue

        ns, ne = ep["season_number"], ep["episode_number"]
        if ns > s["s"] or (ns == s["s"] and ne > s["e"]):
            found   = True
            ep_code = f"S{ns:02d}E{ne:02d}"
            await app.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"🎉 *قسمت جدید اومد!*\n\n"
                    f"📺 *{s['name']}*\n"
                    f"└ {ep_code} — {ep.get('name','')}\n"
                    f"└ 📅 {ep.get('air_date','')}"
                ),
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📋 لیست من", callback_data="list")]]),
            )
            s.update({
                "s": ns, "e": ne,
                "ep_name":      ep.get("name", ""),
                "date":         ep.get("air_date", ""),
                "status_emoji": info["status_emoji"],
                "status_text":  info["status_text"],
                "air_day":      info["air_day"],
                "seasons":      info["seasons"],
            })
    return found

async def do_check_all(app):
    data = load()
    for chat_id, user in data["users"].items():
        await do_check_user(app, int(chat_id), user)
    save(data)

# ── callback دکمه‌ها ───────────────────────────────────────────────────────────
async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q       = update.callback_query
    chat_id = update.effective_chat.id
    await q.answer()
    data = load()
    user = get_user(data, chat_id)

    if q.data == "back":
        await q.message.edit_text(
            "🎬 *ربات ردیاب سریال*\n\nهر وقت قسمت جدیدی بیاد، فوری بهت خبر میدم.",
            parse_mode="Markdown",
            reply_markup=main_keyboard()
        )

    elif q.data == "list":
        await show_list(q.message, chat_id, edit=True)

    elif q.data == "ask_add":
        user["waiting_for"] = "add"
        save(data)
        await q.message.edit_text(
            "➕ اسم سریال رو بنویس:\n\nمثال: `Breaking Bad`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="back")]]),
        )

    elif q.data == "ask_remove":
        if not user["series"]:
            await q.message.edit_text(
                "📭 لیستت خالیه!",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="back")]]),
            )
            return
        user["waiting_for"] = "remove"
        save(data)
        names = "\n".join(f"• {s['name']}" for s in user["series"].values())
        await q.message.edit_text(
            f"🗑 اسم سریالی که می‌خوای حذف کنی رو بنویس:\n\n{names}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="back")]]),
        )

    elif q.data == "check":
        await q.message.edit_text("🔄 دارم چک می‌کنم...")
        data  = load()
        user  = get_user(data, chat_id)
        found = await do_check_user(ctx.application, chat_id, user)
        save(data)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 لیست من", callback_data="list"),
             InlineKeyboardButton("🔙 برگشت",  callback_data="back")],
        ])
        if found:
            await q.message.edit_text("✅ قسمت‌های جدید پیدا شد! بالا نگاه کن 👆", reply_markup=kb)
        else:
            await q.message.edit_text("✅ همه چیز آپدیته!\nقسمت جدیدی نیومده.", reply_markup=kb)

# ── چک خودکار ─────────────────────────────────────────────────────────────────
async def periodic_check(app):
    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        await do_check_all(app)

# ── اجرا ──────────────────────────────────────────────────────────────────────
async def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    print("🤖 ربات شروع به کار کرد!")
    await periodic_check(app)

if __name__ == "__main__":
    asyncio.run(main())