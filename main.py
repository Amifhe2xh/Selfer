import os
import sys
import json
import asyncio
import logging
from datetime import datetime
from pathlib import Path

import pytz
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.tl.functions.account import UpdateProfileRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.errors import FloodWaitError, AuthKeyError

# ═══════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════

API_ID      = int(os.environ.get("API_ID", 0))
API_HASH    = os.environ.get("API_HASH", "")
BOT_TOKEN   = os.environ.get("BOT_TOKEN", "")
DEFAULT_TZ  = os.environ.get("TIMEZONE", "Asia/Tehran")
DEFAULT_FMT = os.environ.get("TIME_FORMAT", "%H:%M")
DEFAULT_INT = int(os.environ.get("UPDATE_INTERVAL", "60"))
ADMIN_ID    = int(os.environ.get("ADMIN_ID", "0"))

# ═══════════════════════════════════════════════════
#  LOGGING
# ═══════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("service")

# ═══════════════════════════════════════════════════
#  DATABASE
# ═══════════════════════════════════════════════════

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
DB_FILE = DATA_DIR / "users.json"


def load_db():
    if DB_FILE.exists():
        return json.loads(DB_FILE.read_text("utf-8"))
    return {}


def save_db(d):
    DB_FILE.write_text(
        json.dumps(d, indent=2, ensure_ascii=False), "utf-8"
    )


db = load_db()

# runtime
tasks = {}
clients = {}
conv = {}
setting_mode = {}

# ═══════════════════════════════════════════════════
#  SELFBOT ENGINE
# ═══════════════════════════════════════════════════


async def selfbot_worker(uid, bot_ref):
    uid_s = str(uid)
    u = db[uid_s]
    c = TelegramClient(StringSession(u["session_string"]), API_ID, API_HASH)

    try:
        await c.start()
        clients[uid] = c
        me = await c.get_me()
        full = await c(GetFullUserRequest(me))

        db[uid_s]["orig_first"] = me.first_name or ""
        db[uid_s]["orig_last"] = me.last_name or ""
        db[uid_s]["orig_about"] = full.full_user.about or ""
        db[uid_s]["active"] = True
        save_db(db)

        log.info(f"[{uid}] ON: {me.first_name}")
        try:
            await bot_ref.send_message(uid, "✅ سلف‌بات فعال شد!")
        except:
            pass

        while True:
            try:
                tz = pytz.timezone(u.get("timezone", DEFAULT_TZ))
                fmt = u.get("time_format", DEFAULT_FMT)
                sep = u.get("separator", " ǀ ")
                base = u.get("base_name") or db[uid_s].get("orig_first", "")
                t = datetime.now(tz).strftime(fmt)
                name = f"{base}{sep}{t}"

                await c(UpdateProfileRequest(
                    first_name=name,
                    last_name=db[uid_s].get("orig_last", ""),
                    about=db[uid_s].get("orig_about", ""),
                ))
                log.info(f"[{uid}] → {name}")

            except FloodWaitError as e:
                log.warning(f"[{uid}] Flood: {e.seconds}s")
                await asyncio.sleep(e.seconds + 5)
                continue

            except AuthKeyError:
                log.error(f"[{uid}] Session invalid!")
                db[uid_s]["active"] = False
                save_db(db)
                try:
                    await bot_ref.send_message(
                        uid, "❌ سشن منقضی شده.\n/start رو بزن."
                    )
                except:
                    pass
                break

            except Exception as e:
                log.error(f"[{uid}] err: {e}")

            await asyncio.sleep(u.get("update_interval", DEFAULT_INT))

    except asyncio.CancelledError:
        log.info(f"[{uid}] cancelled")
        try:
            await c(UpdateProfileRequest(
                first_name=db[uid_s].get("orig_first", ""),
                last_name=db[uid_s].get("orig_last", ""),
                about=db[uid_s].get("orig_about", ""),
            ))
        except:
            pass

    except Exception as e:
        log.error(f"[{uid}] crash: {e}")
        db[uid_s]["active"] = False
        save_db(db)
        try:
            await bot_ref.send_message(uid, f"❌ خطا:\n`{e}`")
        except:
            pass

    finally:
        clients.pop(uid, None)
        try:
            await c.disconnect()
        except:
            pass


async def start_sb(uid, bot_ref):
    uid_s = str(uid)
    if uid_s not in db:
        return False
    u = db[uid_s]
    if not u.get("session_string") or not u.get("active"):
        return False
    if uid in tasks and not tasks[uid].done():
        return True
    tasks[uid] = asyncio.create_task(selfbot_worker(uid, bot_ref))
    return True


async def stop_sb(uid):
    uid_s = str(uid)
    if uid in tasks:
        if not tasks[uid].done():
            tasks[uid].cancel()
        tasks.pop(uid, None)

    if uid in clients:
        try:
            u = db.get(uid_s, {})
            await clients[uid](UpdateProfileRequest(
                first_name=u.get("orig_first", ""),
                last_name=u.get("orig_last", ""),
                about=u.get("orig_about", ""),
            ))
        except:
            pass
        try:
            await clients[uid].disconnect()
        except:
            pass
        clients.pop(uid, None)

    if uid_s in db:
        db[uid_s]["active"] = False
        save_db(db)


# ═══════════════════════════════════════════════════
#  BOT
# ═══════════════════════════════════════════════════


async def run_bot():
    bot = TelegramClient("bot_session", API_ID, API_HASH)
    await bot.start(bot_token=BOT_TOKEN)
    me = await bot.get_me()
    log.info(f"Bot: @{me.username}")

    # restart saved selfbots
    for uid_s, u in db.items():
        if u.get("active") and u.get("session_string"):
            uid = int(uid_s)
            log.info(f"Restart selfbot {uid}")
            tasks[uid] = asyncio.create_task(selfbot_worker(uid, bot))

    # ── helpers ──────────────────────────────────

    def main_kb(uid):
        uid_s = str(uid)
        u = db.get(uid_s, {})
        if not u.get("session_string"):
            return [[Button.inline("🚀 ساخت سلف‌بات", b"setup")]]

        on = uid in tasks and not tasks[uid].done()
        if on:
            return [
                [
                    Button.inline("⏹ توقف", b"stop"),
                    Button.inline("🔄 ری‌استارت", b"restart"),
                ],
                [Button.inline("⚙️ تنظیمات", b"settings")],
                [Button.inline("🗑 حذف", b"ask_delete")],
            ]
        return [
            [Button.inline("▶️ فعال‌سازی", b"restart")],
            [Button.inline("⚙️ تنظیمات", b"settings")],
            [Button.inline("🗑 حذف", b"ask_delete")],
        ]

    def status_text(uid):
        uid_s = str(uid)
        u = db.get(uid_s, {})
        if not u.get("session_string"):
            return "❌ سلف‌بات نداری"
        on = uid in tasks and not tasks[uid].done()
        s = "✅ فعال" if on else "⏸ غیرفعال"
        tz = u.get("timezone", DEFAULT_TZ)
        now = datetime.now(pytz.timezone(tz)).strftime("%H:%M")
        return f"⏰ {now}  |  {s}"

    # ── /start ──────────────────────────────────

    @bot.on(events.NewMessage(pattern=r"/start"))
    async def cmd_start(event):
        uid = event.sender_id
        uid_s = str(uid)
        u = db.get(uid_s, {})

        if u.get("session_string"):
            await event.respond(
                f"{status_text(uid)}\n\nاز منو استفاده کن:",
                buttons=main_kb(uid),
            )
        else:
            await event.respond(
                "━━━ 🤖 سلف‌بات ساز ━━━\n\n"
                "ساعت رو توی اسم پروفایلت نشون بده!\n\n"
                "✨ آپدیت خودکار هر دقیقه\n"
                "🌍 پشتیبانی تایم‌زون‌های مختلف\n"
                "🎨 قابل تنظیم\n\n"
                "روی دکمه زیر بزن:",
                buttons=main_kb(uid),
            )

    # ── setup ───────────────────────────────────

    @bot.on(events.CallbackQuery(data=b"setup"))
    async def cb_setup(event):
        await event.answer()
        uid = event.sender_id
        conv[uid] = {"step": "phone"}
        await event.respond(
            "📱 **شماره تلفنت رو بفرست:**\n\n"
            "مثال: `+989123456789`",
            buttons=[[Button.inline("❌ لغو", b"cancel")]],
        )

    @bot.on(events.CallbackQuery(data=b"cancel"))
    async def cb_cancel(event):
        await event.answer("لغو شد")
        uid = event.sender_id
        if uid in conv and conv[uid].get("temp"):
            try:
                await conv[uid]["temp"].disconnect()
            except:
                pass
        conv.pop(uid, None)
        setting_mode.pop(uid, None)
        await event.respond("لغو شد.", buttons=main_kb(uid))

    # ── control ─────────────────────────────────

    @bot.on(events.CallbackQuery(data=b"stop"))
    async def cb_stop(event):
        await event.answer("⏳")
        uid = event.sender_id
        await stop_sb(uid)
        await event.respond("⛔ متوقف شد. اسم اصلی برگردانده شد.", buttons=main_kb(uid))

    @bot.on(events.CallbackQuery(data=b"restart"))
    async def cb_restart(event):
        await event.answer("⏳")
        uid = event.sender_id
        uid_s = str(uid)
        if uid in tasks and not tasks[uid].done():
            tasks[uid].cancel()
            await asyncio.sleep(2)
        if uid_s in db:
            db[uid_s]["active"] = True
            save_db(db)
            await start_sb(uid, bot)
            await event.respond("🔄 فعال شد!", buttons=main_kb(uid))
        else:
            await event.respond("❌ اطلاعاتی نیست. /start بزن.")

    @bot.on(events.CallbackQuery(data=b"ask_delete"))
    async def cb_ask_delete(event):
        await event.answer()
        await event.respond(
            "⚠️ مطمئنی؟ همه چی حذف میشه.",
            buttons=[
                [Button.inline("✅ بله حذف کن", b"confirm_del")],
                [Button.inline("❌ نه", b"back")],
            ],
        )

    @bot.on(events.CallbackQuery(data=b"confirm_del"))
    async def cb_confirm_del(event):
        await event.answer("🗑")
        uid = event.sender_id
        await stop_sb(uid)
        db.pop(str(uid), None)
        save_db(db)
        await event.respond("🗑 حذف شد.\n/start رو بزن.")

    @bot.on(events.CallbackQuery(data=b"back"))
    async def cb_back(event):
        await event.answer()
        uid = event.sender_id
        await event.respond(status_text(uid), buttons=main_kb(uid))

    # ── settings ────────────────────────────────

    @bot.on(events.CallbackQuery(data=b"settings"))
    async def cb_settings(event):
        await event.answer()
        uid = event.sender_id
        uid_s = str(uid)
        u = db.get(uid_s, {})

        base = u.get("base_name") or u.get("orig_first", "...")
        await event.respond(
            f"━━━ ⚙️ تنظیمات ━━━\n\n"
            f"📛 اسم: `{base}`\n"
            f"🌍 تایم‌زون: `{u.get('timezone', DEFAULT_TZ)}`\n"
            f"⏱ بازه: هر `{u.get('update_interval', DEFAULT_INT)}` ثانیه\n"
            f"🕐 فرمت: `{u.get('time_format', DEFAULT_FMT)}`\n"
            f"🔗 جداکننده: `{u.get('separator', ' ǀ ')}`",
            buttons=[
                [Button.inline("📛 اسم پایه", b"set_name")],
                [Button.inline("🌍 تایم‌زون", b"set_tz")],
                [Button.inline("⏱ بازه آپدیت", b"set_int")],
                [Button.inline("🔗 جداکننده", b"set_sep")],
                [Button.inline("◀️ بازگشت", b"back")],
            ],
        )

    @bot.on(events.CallbackQuery(data=b"set_name"))
    async def cb_set_name(event):
        await event.answer()
        uid = event.sender_id
        setting_mode[uid] = "name"
        await event.respond(
            "📛 اسم جدید رو بفرست:\n"
            "مثال: `علی`",
            buttons=[[Button.inline("❌ لغو", b"cancel")]],
        )

    @bot.on(events.CallbackQuery(data=b"set_tz"))
    async def cb_set_tz(event):
        await event.answer()
        uid = event.sender_id
        setting_mode[uid] = "tz"
        await event.respond(
            "🌍 تایم‌زون رو بفرست:\n\n"
            "`Asia/Tehran`\n`Asia/Dubai`\n`Europe/London`\n"
            "`America/New_York`\n`Asia/Kabul`",
            buttons=[[Button.inline("❌ لغو", b"cancel")]],
        )

    @bot.on(events.CallbackQuery(data=b"set_int"))
    async def cb_set_int(event):
        await event.answer()
        uid = event.sender_id
        setting_mode[uid] = "interval"
        await event.respond(
            "⏱ بازه آپدیت (ثانیه):\n"
            "توصیه: `60`  —  حداقل: `30`",
            buttons=[[Button.inline("❌ لغو", b"cancel")]],
        )

    @bot.on(events.CallbackQuery(data=b"set_sep"))
    async def cb_set_sep(event):
        await event.answer()
        uid = event.sender_id
        setting_mode[uid] = "sep"
        await event.respond(
            "🔗 جداکننده:\n"
            "` ǀ `  ` • `  ` — `  ` | `  ` ◆ `",
            buttons=[[Button.inline("❌ لغو", b"cancel")]],
        )

    # ── text handler ────────────────────────────

    @bot.on(events.NewMessage(func=lambda e: e.is_private))
    async def on_text(event):
        uid = event.sender_id
        text = event.text.strip()
        if text.startswith("/"):
            return

        # ── setup conversation ──────────────────
        if uid in conv:
            step = conv[uid]["step"]

            if step == "phone":
                if not text.startswith("+") or len(text) < 10:
                    await event.respond("❌ فرمت اشتباه. مثال: `+989123456789`")
                    return

                conv[uid]["phone"] = text
                conv[uid]["step"] = "code"

                msg = await event.respond("⏳ ارسال کد...")

                tmp = TelegramClient(StringSession(), API_ID, API_HASH)
                await tmp.connect()
                conv[uid]["temp"] = tmp

                try:
                    res = await tmp.send_code_request(text)
                    conv[uid]["hash"] = res.phone_code_hash
                    await msg.edit("📨 کد تایید رو بفرست:")
                except Exception as e:
                    conv[uid]["step"] = "phone"
                    await tmp.disconnect()
                    await msg.edit(f"❌ خطا: `{e}`\nشماره رو دوباره بفرست:")
                return

            if step == "code":
                code = text.replace(" ", "").replace("-", "")
                if not code.isdigit():
                    await event.respond("❌ کد باید عددی باشه.")
                    return

                tmp = conv[uid]["temp"]
                try:
                    await tmp.sign_in(
                        phone=conv[uid]["phone"],
                        code=code,
                        phone_code_hash=conv[uid]["hash"],
                    )
                except Exception as e:
                    if "password" in str(e).lower():
                        conv[uid]["step"] = "2fa"
                        await event.respond("🔒 رمز دو مرحله‌ای رو بفرست:")
                        return
                    conv[uid]["step"] = "phone"
                    try:
                        await tmp.disconnect()
                    except:
                        pass
                    await event.respond(f"❌ خطا: `{e}`\n/start رو بزن.")
                    return

                ss = tmp.session.save()
                try:
                    await tmp.disconnect()
                except:
                    pass

                db[str(uid)] = {
                    "session_string": ss,
                    "phone": conv[uid]["phone"],
                    "base_name": "",
                    "timezone": DEFAULT_TZ,
                    "time_format": DEFAULT_FMT,
                    "update_interval": DEFAULT_INT,
                    "separator": " ǀ ",
                    "active": True,
                    "orig_first": "",
                    "orig_last": "",
                    "orig_about": "",
                }
                save_db(db)
                conv.pop(uid, None)

                await event.respond(
                    "━━━ ✅ سلف‌بات فعال شد! ━━━\n\n"
                    "اسمت هر دقیقه با ساعت آپدیت میشه!\n"
                    "از تنظیمات میتونی همه چیز رو عوض کنی.",
                    buttons=main_kb(uid),
                )
                await start_sb(uid, bot)
                return

            if step == "2fa":
                tmp = conv[uid]["temp"]
                try:
                    await tmp.sign_in(password=text)
                except Exception as e:
                    await event.respond(f"❌ رمز اشتباه: `{e}`")
                    return

                ss = tmp.session.save()
                try:
                    await tmp.disconnect()
                except:
                    pass

                db[str(uid)] = {
                    "session_string": ss,
                    "phone": conv[uid]["phone"],
                    "base_name": "",
                    "timezone": DEFAULT_TZ,
                    "time_format": DEFAULT_FMT,
                    "update_interval": DEFAULT_INT,
                    "separator": " ǀ ",
                    "active": True,
                    "orig_first": "",
                    "orig_last": "",
                    "orig_about": "",
                }
                save_db(db)
                conv.pop(uid, None)

                await event.respond(
                    "━━━ ✅ سلف‌بات فعال شد! ━━━\n\n"
                    "اسمت هر دقیقه با ساعت آپدیت میشه!",
                    buttons=main_kb(uid),
                )
                await start_sb(uid, bot)
                return

        # ── settings input ──────────────────────
        if uid in setting_mode:
            uid_s = str(uid)
            if uid_s not in db:
                setting_mode.pop(uid, None)
                return

            mode = setting_mode[uid]
            need_restart = True

            if mode == "name":
                db[uid_s]["base_name"] = text[:32]
                save_db(db)
                await event.respond(f"✅ اسم پایه: `{text}`")

            elif mode == "tz":
                try:
                    pytz.timezone(text)
                except pytz.exceptions.UnknownTimeZoneError:
                    await event.respond("❌ نامعتبر. مثال: `Asia/Tehran`")
                    return
                db[uid_s]["timezone"] = text
                save_db(db)
                await event.respond(f"✅ تایم‌زون: `{text}`")

            elif mode == "interval":
                if not text.isdigit():
                    await event.respond("❌ عدد بفرست.")
                    return
                v = int(text)
                if v < 30:
                    await event.respond("❌ حداقل ۳۰.")
                    return
                db[uid_s]["update_interval"] = v
                save_db(db)
                await event.respond(f"✅ بازه: هر {v} ثانیه")

            elif mode == "sep":
                db[uid_s]["separator"] = text[:10]
                save_db(db)
                await event.respond(f"✅ جداکننده: `{text}`")

            setting_mode.pop(uid, None)

            if need_restart:
                if uid in tasks and not tasks[uid].done():
                    tasks[uid].cancel()
                    await asyncio.sleep(2)
                db[uid_s]["active"] = True
                save_db(db)
                await start_sb(uid, bot)

            await event.respond("تنظیمات اعمال شد ✅", buttons=main_kb(uid))
            return

        # ── unknown ─────────────────────────────
        await event.respond("/start رو بزن.", buttons=main_kb(uid))

    # ── commands ────────────────────────────────

    @bot.on(events.NewMessage(pattern=r"/status"))
    async def cmd_status(event):
        uid = event.sender_id
        uid_s = str(uid)
        u = db.get(uid_s, {})
        if not u.get("session_string"):
            await event.respond("❌ سلف‌بات نداری. /start بزن.")
            return

        on = uid in tasks and not tasks[uid].done()
        s = "✅ فعال" if on else "⏸ غیرفعال"
        tz = pytz.timezone(u.get("timezone", DEFAULT_TZ))
        now = datetime.now(tz).strftime("%Y/%m/%d %H:%M:%S")
        base = u.get("base_name") or u.get("orig_first", "...")

        await event.respond(
            f"━━━ 📊 وضعیت ━━━\n\n"
            f"وضعیت: {s}\n"
            f"📛 اسم: `{base}`\n"
            f"🌍 تایم‌زون: `{u.get('timezone', DEFAULT_TZ)}`\n"
            f"⏱ بازه: هر `{u.get('update_interval', DEFAULT_INT)}` ثانیه\n"
            f"🔗 جداکننده: `{u.get('separator', ' ǀ ')}`\n"
            f"⏰ الان: {now}",
        )

    @bot.on(events.NewMessage(pattern=r"/stop"))
    async def cmd_stop(event):
        uid = event.sender_id
        await stop_sb(uid)
        await event.respond("⛔ متوقف شد.")

    @bot.on(events.NewMessage(pattern=r"/help"))
    async def cmd_help(event):
        await event.respond(
            "━━━ 📖 راهنما ━━━\n\n"
            "/start — منوی اصلی\n"
            "/status — وضعیت\n"
            "/stop — توقف\n"
            "/help — راهنما\n\n"
            "━━━ مثال خروجی ━━━\n\n"
            "علی → علی ǀ 14:32\n"
            "سارا → سارا • 20:15",
        )

    if ADMIN_ID:
        @bot.on(events.NewMessage(from_users=ADMIN_ID, pattern=r"/stats"))
        async def cmd_stats(event):
            total = len(db)
            active = sum(1 for v in db.values() if v.get("active"))
            running = len([t for t in tasks.values() if not t.done()])
            size = DB_FILE.stat().st_size if DB_FILE.exists() else 0
            await event.respond(
                f"━━━ 📊 آمار ادمین ━━━\n\n"
                f"👥 کل: {total}\n"
                f"✅ فعال: {active}\n"
                f"🔄 اجرا: {running}\n"
                f"💾 {size / 1024:.1f} KB",
            )

    log.info("Bot ready!")
    await bot.run_until_disconnected()


# ═══════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════


def main():
    if not API_ID:
        log.error("API_ID لازمه!")
        sys.exit(1)
    if not API_HASH:
        log.error("API_HASH لازمه!")
        sys.exit(1)
    if not BOT_TOKEN:
        log.error("BOT_TOKEN لازمه!")
        sys.exit(1)
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()