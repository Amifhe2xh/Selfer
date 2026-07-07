import os
import sys
import json
import time
import asyncio
import logging
from datetime import datetime
from pathlib import Path

import pytz
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.tl.functions.account import UpdateProfileRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.functions.contacts import BlockRequest, UnblockRequest
from telethon.errors import FloodWaitError, AuthKeyError

# ═══════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
DEFAULT_TZ = os.environ.get("TIMEZONE", "Asia/Tehran")
DEFAULT_FMT = os.environ.get("TIME_FORMAT", "%H:%M")
DEFAULT_INT = int(os.environ.get("UPDATE_INTERVAL", "60"))
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

# ═══════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("service")

# ═══════════════════════════════════════════════════
# DATABASE
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
block_handlers = {}  # uid -> registered NewMessage handler function (so we don't double-register)
cmd_handlers = {}    # uid -> list of registered handler functions for the self-account commands

NEW_USER_DEFAULTS = {
    "base_name": "",
    "timezone": DEFAULT_TZ,
    "time_format": DEFAULT_FMT,
    "update_interval": DEFAULT_INT,
    "separator": " ǀ ",
    "active": True,
    "orig_first": "",
    "orig_last": "",
    "orig_about": "",
    "silent_blocked": [],  # list of {"id": int, "name": str}
    "font_style": None,    # active unicode font style name, or None
    "font_auto": False,    # if True, every outgoing text gets auto-styled
}


def new_user_record(session_string, phone):
    rec = dict(NEW_USER_DEFAULTS)
    rec["session_string"] = session_string
    rec["phone"] = phone
    rec["silent_blocked"] = []
    rec["font_style"] = None
    rec["font_auto"] = False
    return rec


# ═══════════════════════════════════════════════════
# UNICODE FONT ENGINE
# ═══════════════════════════════════════════════════
def _build_font_map(upper_base, lower_base, digit_base=None, upper_ex=None, lower_ex=None):
    upper_ex = upper_ex or {}
    lower_ex = lower_ex or {}
    m = {}
    for i in range(26):
        chu = chr(ord("A") + i)
        chl = chr(ord("a") + i)
        m[chu] = upper_ex.get(chu, chr(upper_base + i) if upper_base is not None else chu)
        m[chl] = lower_ex.get(chl, chr(lower_base + i) if lower_base is not None else chl)
    if digit_base is not None:
        for i in range(10):
            m[str(i)] = chr(digit_base + i)
    return m


FONT_MAPS = {
    "bold": _build_font_map(0x1D400, 0x1D41A, 0x1D7CE),
    "italic": _build_font_map(0x1D434, 0x1D44E, None, lower_ex={"h": "\u210E"}),
    "bold_italic": _build_font_map(0x1D468, 0x1D482),
    "script": _build_font_map(
        0x1D49C, 0x1D4B6, None,
        upper_ex={"B": "\u212C", "E": "\u2130", "F": "\u2131", "H": "\u210B",
                  "I": "\u2110", "L": "\u2112", "M": "\u2133", "R": "\u211B"},
        lower_ex={"e": "\u212F", "g": "\u210A", "o": "\u2134"},
    ),
    "doublestruck": _build_font_map(
        0x1D538, 0x1D552, 0x1D7D8,
        upper_ex={"C": "\u2102", "H": "\u210D", "N": "\u2115", "P": "\u2119",
                  "Q": "\u211A", "R": "\u211D", "Z": "\u2124"},
    ),
    "fraktur": _build_font_map(
        0x1D504, 0x1D51E, None,
        upper_ex={"C": "\u212D", "H": "\u210C", "I": "\u2111", "R": "\u211C", "Z": "\u2128"},
    ),
    "monospace": _build_font_map(0x1D670, 0x1D68A, 0x1D7F6),
    "circled": _build_font_map(0x24B6, 0x24D0),
    "fullwidth": _build_font_map(0xFF21, 0xFF41, 0xFF10),
}
_CIRCLED_DIGITS = {"1": "①", "2": "②", "3": "③", "4": "④", "5": "⑤",
                   "6": "⑥", "7": "⑦", "8": "⑧", "9": "⑨", "0": "⓪"}
FONT_MAPS["circled"].update(_CIRCLED_DIGITS)

FONT_LABELS = {
    "bold": "𝗕𝗼𝗹𝗱",
    "italic": "𝘐𝘵𝘢𝘭𝘪𝘤",
    "bold_italic": "𝑩𝒐𝒍𝒅 𝑰𝒕𝒂𝒍𝒊𝒄",
    "script": "𝒮𝒸𝓇𝒾𝓅𝓉",
    "doublestruck": "𝔻𝕠𝕦𝕓𝕝𝕖",
    "fraktur": "𝔉𝔯𝔞𝔨𝔱𝔲𝔯",
    "monospace": "𝙼𝚘𝚗𝚘",
    "circled": "Ⓒⓘⓡⓒⓛⓔⓓ",
    "fullwidth": "Ｆｕｌｌｗｉｄｔｈ",
}


def apply_font(text, style):
    m = FONT_MAPS.get(style)
    if not m:
        return text
    return "".join(m.get(ch, ch) for ch in text)


# ═══════════════════════════════════════════════════
# SILENT BLOCK ENGINE
# ═══════════════════════════════════════════════════
def blocked_ids(uid_s):
    return {b["id"] for b in db.get(uid_s, {}).get("silent_blocked", [])}


def register_block_handler(uid, c):
    """Attach a live message-deleting handler for this user's client."""
    uid_s = str(uid)

    if uid in block_handlers:
        return  # already registered for this client instance

    async def _handler(event):
        try:
            if not event.is_private:
                return
            sender_id = event.sender_id
            if sender_id in blocked_ids(uid_s):
                try:
                    await event.delete()
                except Exception as e:
                    log.warning(f"[{uid}] couldn't delete blocked msg: {e}")
        except Exception as e:
            log.warning(f"[{uid}] block handler err: {e}")

    c.add_event_handler(_handler, events.NewMessage(incoming=True))
    block_handlers[uid] = _handler


def unregister_block_handler(uid, c):
    handler = block_handlers.pop(uid, None)
    if handler:
        try:
            c.remove_event_handler(handler, events.NewMessage)
        except Exception:
            pass


# ═══════════════════════════════════════════════════
# SELF-ACCOUNT COMMANDS (/tag /pin /ping /font /tr /del)
# These run from the user's own Telegram app, in any chat.
# ═══════════════════════════════════════════════════
async def _cmd_tag(event, arg):
    chat = await event.get_chat()
    if not (getattr(chat, "megagroup", False) or getattr(chat, "gigagroup", False)
            or hasattr(chat, "participants_count") or getattr(chat, "broadcast", False)):
        await event.edit("❌ این دستور فقط توی گروه/کانال کار می‌کنه.")
        return

    try:
        participants = await event.client.get_participants(event.chat_id, aggressive=True)
    except Exception as e:
        await event.edit(f"❌ خطا در گرفتن لیست اعضا: `{e}`")
        return

    mentions = [
        f"[{p.first_name or p.username or p.id}](tg://user?id={p.id})"
        for p in participants if not p.bot and not p.deleted
    ]
    if not mentions:
        await event.edit("❌ عضوی پیدا نشد.")
        return

    await event.delete()
    batch_size = 5
    for i in range(0, len(mentions), batch_size):
        batch = mentions[i:i + batch_size]
        txt = (arg + "\n" if arg else "") + " ".join(batch)
        try:
            await event.client.send_message(event.chat_id, txt, parse_mode="md")
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds + 2)
        except Exception:
            pass
        await asyncio.sleep(3)


async def _cmd_pin(event):
    reply = await event.get_reply_message()
    if not reply:
        await event.edit("❌ باید روی یه پیام ریپلای کنی.\nمثال: ریپلای + `/pin`")
        return
    try:
        await event.client.pin_message(event.chat_id, reply, notify=False)
        await event.edit("📌 پیام پین شد.")
        await asyncio.sleep(2)
        await event.delete()
    except Exception as e:
        await event.edit(f"❌ خطا: `{e}`")


async def _cmd_ping(event):
    t0 = time.time()
    await event.edit("🏓 در حال بررسی...")
    ms = round((time.time() - t0) * 1000, 2)
    await event.edit(f"🏓 **Pong!**\n⚡ تاخیر: `{ms}ms`\n✅ سلف‌بات آنلاینه")


async def _cmd_font(uid, event, arg):
    uid_s = str(uid)
    args = arg.split(maxsplit=1)

    if not args:
        styles = "\n".join(f"• `{k}` — {v}" for k, v in FONT_LABELS.items())
        await event.edit(
            "🎨 **راهنمای فونت:**\n\n"
            "`/font <style> متن` — تبدیل یه متن\n"
            "`/font set <style>` — فعال کردن فونت خودکار برای پیام‌های بعدی\n"
            "`/font off` — خاموش کردن حالت خودکار\n\n"
            f"استایل‌ها:\n{styles}"
        )
        return

    sub = args[0].lower()

    if sub == "off":
        db[uid_s]["font_auto"] = False
        save_db(db)
        await event.edit("✅ فونت خودکار خاموش شد.")
        return

    if sub == "set":
        style = args[1].strip() if len(args) > 1 else ""
        if style not in FONT_MAPS:
            await event.edit("❌ استایل نامعتبر.\n`/font` رو بدون آرگومان بزن برای لیست.")
            return
        db[uid_s]["font_style"] = style
        db[uid_s]["font_auto"] = True
        save_db(db)
        await event.edit(f"✅ فونت خودکار روی `{style}` فعال شد.\nهر پیامی بفرستی خودکار تبدیل میشه.")
        return

    if sub not in FONT_MAPS:
        await event.edit("❌ استایل نامعتبر.\n`/font` رو بدون آرگومان بزن برای لیست.")
        return
    if len(args) < 2:
        await event.edit("❌ متنی برای تبدیل نفرستادی.\nمثال: `/font bold سلام`")
        return
    await event.edit(apply_font(args[1], sub))


async def _cmd_translate(event, arg):
    reply = await event.get_reply_message()
    target = arg.strip() or "fa"
    if not reply or not reply.raw_text:
        await event.edit("❌ باید روی یه پیام متنی ریپلای کنی.\nمثال: ریپلای + `/tr`")
        return
    await event.edit("🌐 در حال ترجمه...")
    try:
        from deep_translator import GoogleTranslator
        translated = GoogleTranslator(source="auto", target=target).translate(reply.raw_text)
    except Exception as e:
        await event.edit(f"❌ خطا در ترجمه: `{e}`\n\nنصب کن: `pip install deep-translator`")
        return
    await event.edit(f"🌐 **ترجمه:**\n\n{translated}")


async def _cmd_del(event, arg):
    arg = arg.strip()
    if not arg.isdigit():
        await event.edit("❌ عدد بفرست.\nمثال: `/del 100`")
        return
    n = min(int(arg), 300)
    client = event.client
    chat_id = event.chat_id
    ids = []
    async for m in client.iter_messages(chat_id, from_user="me", limit=n):
        ids.append(m.id)
    if event.id not in ids:
        ids.append(event.id)
    if ids:
        await client.delete_messages(chat_id, ids)


def register_command_handlers(uid, c):
    """Register the /tag /pin /ping /font /tr /del commands + auto-font,
    triggered by the account owner's own outgoing messages."""
    if uid in cmd_handlers:
        return

    async def _dispatch(event):
        text = event.raw_text or ""
        uid_s = str(uid)

        if text.startswith("/"):
            parts = text.split(maxsplit=1)
            cmd = parts[0].lower().split("@")[0]
            arg = parts[1] if len(parts) > 1 else ""
            try:
                if cmd == "/tag":
                    await _cmd_tag(event, arg)
                elif cmd == "/pin":
                    await _cmd_pin(event)
                elif cmd == "/ping":
                    await _cmd_ping(event)
                elif cmd == "/font":
                    await _cmd_font(uid, event, arg)
                elif cmd in ("/tr", "/translate"):
                    await _cmd_translate(event, arg)
                elif cmd == "/del":
                    await _cmd_del(event, arg)
            except Exception as e:
                log.warning(f"[{uid}] cmd {cmd} error: {e}")
                try:
                    await event.edit(f"❌ خطا: `{e}`")
                except Exception:
                    pass
            return

        # auto-font mode: style every plain outgoing message
        u = db.get(uid_s, {})
        if u.get("font_auto") and u.get("font_style") and text.strip():
            styled = apply_font(text, u["font_style"])
            if styled != text:
                try:
                    await event.edit(styled)
                except Exception:
                    pass

    c.add_event_handler(_dispatch, events.NewMessage(outgoing=True))
    cmd_handlers[uid] = [_dispatch]


def unregister_command_handlers(uid, c):
    handlers = cmd_handlers.pop(uid, None)
    if handlers:
        for h in handlers:
            try:
                c.remove_event_handler(h, events.NewMessage)
            except Exception:
                pass


async def silent_block_user(uid, target):
    """target: username (str, without @) or numeric id (int/str digits)."""
    uid_s = str(uid)
    c = clients.get(uid)
    if not c:
        return False, "❌ اول باید سلف‌بات فعال باشه."

    try:
        entity = await c.get_entity(int(target) if str(target).lstrip("-").isdigit() else target)
    except Exception as e:
        return False, f"❌ کاربر پیدا نشد: `{e}`"

    tid = entity.id
    name = (getattr(entity, "first_name", "") or "") + " " + (getattr(entity, "last_name", "") or "")
    name = name.strip() or (getattr(entity, "username", "") or str(tid))

    lst = db[uid_s].setdefault("silent_blocked", [])
    if any(b["id"] == tid for b in lst):
        return False, f"⚠️ `{name}` از قبل مسدوده."

    try:
        await c(BlockRequest(id=entity))
    except Exception as e:
        log.warning(f"[{uid}] native block failed: {e}")

    lst.append({"id": tid, "name": name})
    save_db(db)
    return True, f"🚫 `{name}` مسدود شد.\nپیام‌های جدیدش فوراً پاک میشن."


async def silent_unblock_user(uid, target_id):
    uid_s = str(uid)
    c = clients.get(uid)
    lst = db.get(uid_s, {}).get("silent_blocked", [])
    entry = next((b for b in lst if b["id"] == target_id), None)
    if not entry:
        return False, "❌ پیدا نشد."

    if c:
        try:
            await c(UnblockRequest(id=target_id))
        except Exception as e:
            log.warning(f"[{uid}] native unblock failed: {e}")

    lst[:] = [b for b in lst if b["id"] != target_id]
    save_db(db)
    return True, f"✅ `{entry['name']}` آنبلاک شد."


# ═══════════════════════════════════════════════════
# SELFBOT ENGINE
# ═══════════════════════════════════════════════════
async def selfbot_worker(uid, bot_ref):
    uid_s = str(uid)
    u = db[uid_s]
    c = TelegramClient(StringSession(u["session_string"]), API_ID, API_HASH)
    try:
        await c.start()
        clients[uid] = c
        register_block_handler(uid, c)
        register_command_handlers(uid, c)

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
        except Exception:
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
                except Exception:
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
        except Exception:
            pass
    except Exception as e:
        log.error(f"[{uid}] crash: {e}")
        db[uid_s]["active"] = False
        save_db(db)
        try:
            await bot_ref.send_message(uid, f"❌ خطا:\n`{e}`")
        except Exception:
            pass
    finally:
        unregister_block_handler(uid, c)
        unregister_command_handlers(uid, c)
        clients.pop(uid, None)
        try:
            await c.disconnect()
        except Exception:
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
        except Exception:
            pass
        unregister_block_handler(uid, clients[uid])
        unregister_command_handlers(uid, clients[uid])
        try:
            await clients[uid].disconnect()
        except Exception:
            pass
        clients.pop(uid, None)
    if uid_s in db:
        db[uid_s]["active"] = False
        save_db(db)


# ═══════════════════════════════════════════════════
# BOT
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
                [Button.inline("🚫 بلاک مخفی", b"block_menu")],
                [Button.inline("🗑 حذف", b"ask_delete")],
            ]
        return [
            [Button.inline("▶️ فعال‌سازی", b"restart")],
            [Button.inline("⚙️ تنظیمات", b"settings")],
            [Button.inline("🚫 بلاک مخفی", b"block_menu")],
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
        return f"⏰ {now} | {s}"

    def block_kb(uid):
        uid_s = str(uid)
        lst = db.get(uid_s, {}).get("silent_blocked", [])
        rows = [[Button.inline("➕ افزودن (یوزرنیم/آیدی)", b"block_add")]]
        for b in lst[:20]:
            rows.append([
                Button.inline(f"❌ آنبلاک: {b['name']}", f"block_del:{b['id']}".encode())
            ])
        rows.append([Button.inline("◀️ بازگشت", b"back")])
        return rows

    def block_text(uid):
        uid_s = str(uid)
        lst = db.get(uid_s, {}).get("silent_blocked", [])
        if not lst:
            return (
                "━━━ 🚫 بلاک مخفی ━━━\n\n"
                "کسی مسدود نیست.\n\n"
                "با این قابلیت هر پیام جدیدی که از یه نفر خاص بیاد "
                "فوراً پاک میشه و اون شخص عملاً مسدود میشه."
            )
        names = "\n".join(f"• {b['name']}" for b in lst)
        return f"━━━ 🚫 بلاک مخفی ━━━\n\nمسدودها:\n{names}"

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
                "🎨 قابل تنظیم\n"
                "🚫 بلاک مخفی افراد مزاحم\n\n"
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
            except Exception:
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
            "توصیه: `60` — حداقل: `30`",
            buttons=[[Button.inline("❌ لغو", b"cancel")]],
        )

    @bot.on(events.CallbackQuery(data=b"set_sep"))
    async def cb_set_sep(event):
        await event.answer()
        uid = event.sender_id
        setting_mode[uid] = "sep"
        await event.respond(
            "🔗 جداکننده:\n"
            "` ǀ ` ` • ` ` — ` ` | ` ` ◆ `",
            buttons=[[Button.inline("❌ لغو", b"cancel")]],
        )

    # ── silent block menu ────────────────────────
    @bot.on(events.CallbackQuery(data=b"block_menu"))
    async def cb_block_menu(event):
        await event.answer()
        uid = event.sender_id
        await event.respond(block_text(uid), buttons=block_kb(uid))

    @bot.on(events.CallbackQuery(data=b"block_add"))
    async def cb_block_add(event):
        await event.answer()
        uid = event.sender_id
        setting_mode[uid] = "block_add"
        await event.respond(
            "🚫 یوزرنیم (بدون @) یا آیدی عددی شخص رو بفرست:\n\n"
            "مثال: `John_doe` یا `123456789`",
            buttons=[[Button.inline("❌ لغو", b"cancel")]],
        )

    @bot.on(events.CallbackQuery(pattern=rb"block_del:(-?\d+)"))
    async def cb_block_del(event):
        await event.answer("⏳")
        uid = event.sender_id
        target_id = int(event.pattern_match.group(1))
        ok, msg = await silent_unblock_user(uid, target_id)
        await event.respond(msg)
        await event.respond(block_text(uid), buttons=block_kb(uid))

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
                    except Exception:
                        pass
                    await event.respond(f"❌ خطا: `{e}`\n/start رو بزن.")
                    return

                ss = tmp.session.save()
                try:
                    await tmp.disconnect()
                except Exception:
                    pass
                db[str(uid)] = new_user_record(ss, conv[uid]["phone"])
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
                except Exception:
                    pass
                db[str(uid)] = new_user_record(ss, conv[uid]["phone"])
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
            elif mode == "block_add":
                need_restart = False
                setting_mode.pop(uid, None)
                target = text.lstrip("@").strip()
                ok, msg = await silent_block_user(uid, target)
                await event.respond(msg)
                await event.respond(block_text(uid), buttons=block_kb(uid))
                return

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
            f"🚫 بلاک‌شده‌ها: {len(u.get('silent_blocked', []))}\n"
            f"🎨 فونت خودکار: `{u.get('font_style') if u.get('font_auto') else 'خاموش'}`\n"
            f"⏰ الان: {now}",
        )

    @bot.on(events.NewMessage(pattern=r"/stop"))
    async def cmd_stop(event):
        uid = event.sender_id
        await stop_sb(uid)
        await event.respond("⛔ متوقف شد.")

    @bot.on(events.NewMessage(pattern=r"/block"))
    async def cmd_block_list(event):
        uid = event.sender_id
        await event.respond(block_text(uid), buttons=block_kb(uid))

    @bot.on(events.NewMessage(pattern=r"/help"))
    async def cmd_help(event):
        await event.respond(
            "━━━ 📖 راهنمای ربات کنترل ━━━\n\n"
            "/start — منوی اصلی\n"
            "/status — وضعیت\n"
            "/stop — توقف\n"
            "/block — مدیریت بلاک مخفی\n"
            "/help — همین راهنما\n\n"
            "━━━ مثال خروجی اسم ━━━\n\n"
            "علی → علی ǀ 14:32\n"
            "سارا → سارا • 20:15\n\n"
            "━━━ بلاک مخفی ━━━\n\n"
            "با /block یا از منو یه نفر رو با یوزرنیم/آیدی مسدود کن.\n"
            "پیام‌های جدیدش فوراً پاک میشن.\n\n"
            "━━━ 🛠 دستورات داخل اکانت خودت ━━━\n"
            "(این‌ها رو مستقیم توی هر چت از گوشی/دسکتاپ خودت تایپ کن)\n\n"
            "`/tag [متن]` — تگ همه اعضای گروه\n"
            "`/pin` (ریپلای) — پین پیام\n"
            "`/ping` — تست اتصال\n"
            "`/font` — لیست فونت‌ها و راهنما\n"
            "`/font bold متن` — تبدیل به فونت بولد\n"
            "`/font set italic` — فونت خودکار برای پیام‌های بعدی\n"
            "`/tr` (ریپلای) — ترجمه به فارسی\n"
            "`/del 100` — حذف ۱۰۰ پیام آخر خودت در همین چت",
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
# MAIN
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
