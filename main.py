import os
import sys
import json
import time
import asyncio
import logging
from datetime import datetime

import pytz
import psycopg2
import psycopg2.extras
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.tl.functions.account import UpdateProfileRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.functions.contacts import BlockRequest, UnblockRequest
from telethon.tl.functions.messages import SetTypingRequest
from telethon.tl.types import (
    SendMessageTypingAction,
    SendMessageGamePlayAction,
    InputMediaDice,
)
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
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# ═══════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("service")

# ═══════════════════════════════════════════════════
# POSTGRES DATABASE
# ═══════════════════════════════════════════════════
_db_conn = None


def get_conn():
    global _db_conn
    if _db_conn is None or _db_conn.closed:
        _db_conn = psycopg2.connect(DATABASE_URL, sslmode="require")
        _db_conn.autocommit = True
    return _db_conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            uid             BIGINT PRIMARY KEY,
            session_string  TEXT NOT NULL DEFAULT '',
            phone           TEXT NOT NULL DEFAULT '',
            base_name       TEXT NOT NULL DEFAULT '',
            timezone        TEXT NOT NULL DEFAULT 'Asia/Tehran',
            time_format     TEXT NOT NULL DEFAULT '%%H:%%M',
            update_interval INT  NOT NULL DEFAULT 60,
            separator       TEXT NOT NULL DEFAULT ' | ',
            active          BOOLEAN NOT NULL DEFAULT TRUE,
            orig_first      TEXT NOT NULL DEFAULT '',
            orig_last       TEXT NOT NULL DEFAULT '',
            orig_about      TEXT NOT NULL DEFAULT '',
            silent_blocked  TEXT NOT NULL DEFAULT '[]',
            font_style      TEXT,
            font_auto       BOOLEAN NOT NULL DEFAULT FALSE,
            auto_reply_enabled  BOOLEAN NOT NULL DEFAULT FALSE,
            auto_reply_text     TEXT NOT NULL DEFAULT '',
            auto_reply_cooldown INT NOT NULL DEFAULT 3600,
            auto_reply_sent_to  TEXT NOT NULL DEFAULT '{}',
            clock_enabled       BOOLEAN NOT NULL DEFAULT TRUE,
            name_font_style     TEXT NOT NULL DEFAULT 'normal',
            secretary_enabled   BOOLEAN NOT NULL DEFAULT FALSE,
            secretary_text      TEXT NOT NULL DEFAULT '',
            secretary_sent_to   TEXT NOT NULL DEFAULT '{}',
            muted_users         TEXT NOT NULL DEFAULT '[]',
            pv_lock             BOOLEAN NOT NULL DEFAULT FALSE,
            typing_mode         BOOLEAN NOT NULL DEFAULT FALSE,
            game_mode           BOOLEAN NOT NULL DEFAULT FALSE
        )
    """)
    cur.close()
    log.info("DB ready")


def load_all_users():
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute("SELECT * FROM users")
    rows = cur.fetchall()
    result = {}
    for row in rows:
        uid_s = str(row["uid"])
        d = dict(row)
        del d["uid"]
        d["silent_blocked"] = json.loads(d["silent_blocked"])
        d["auto_reply_sent_to"] = json.loads(d["auto_reply_sent_to"])
        d["secretary_sent_to"] = json.loads(d["secretary_sent_to"])
        d["muted_users"] = json.loads(d["muted_users"])
        result[uid_s] = d
    cur.close()
    return result


def save_user(uid_s):
    u = db.get(uid_s, {})
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO users (
            uid, session_string, phone, base_name, timezone, time_format,
            update_interval, separator, active, orig_first, orig_last, orig_about,
            silent_blocked, font_style, font_auto,
            auto_reply_enabled, auto_reply_text, auto_reply_cooldown, auto_reply_sent_to,
            clock_enabled, name_font_style, secretary_enabled, secretary_text, secretary_sent_to,
            muted_users, pv_lock, typing_mode, game_mode
        ) VALUES (
            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
        ) ON CONFLICT (uid) DO UPDATE SET
            session_string=EXCLUDED.session_string,
            phone=EXCLUDED.phone,
            base_name=EXCLUDED.base_name,
            timezone=EXCLUDED.timezone,
            time_format=EXCLUDED.time_format,
            update_interval=EXCLUDED.update_interval,
            separator=EXCLUDED.separator,
            active=EXCLUDED.active,
            orig_first=EXCLUDED.orig_first,
            orig_last=EXCLUDED.orig_last,
            orig_about=EXCLUDED.orig_about,
            silent_blocked=EXCLUDED.silent_blocked,
            font_style=EXCLUDED.font_style,
            font_auto=EXCLUDED.font_auto,
            auto_reply_enabled=EXCLUDED.auto_reply_enabled,
            auto_reply_text=EXCLUDED.auto_reply_text,
            auto_reply_cooldown=EXCLUDED.auto_reply_cooldown,
            auto_reply_sent_to=EXCLUDED.auto_reply_sent_to,
            clock_enabled=EXCLUDED.clock_enabled,
            name_font_style=EXCLUDED.name_font_style,
            secretary_enabled=EXCLUDED.secretary_enabled,
            secretary_text=EXCLUDED.secretary_text,
            secretary_sent_to=EXCLUDED.secretary_sent_to,
            muted_users=EXCLUDED.muted_users,
            pv_lock=EXCLUDED.pv_lock,
            typing_mode=EXCLUDED.typing_mode,
            game_mode=EXCLUDED.game_mode
    """, (
        int(uid_s),
        u.get("session_string", ""),
        u.get("phone", ""),
        u.get("base_name", ""),
        u.get("timezone", DEFAULT_TZ),
        u.get("time_format", DEFAULT_FMT),
        u.get("update_interval", DEFAULT_INT),
        u.get("separator", " | "),
        u.get("active", True),
        u.get("orig_first", ""),
        u.get("orig_last", ""),
        u.get("orig_about", ""),
        json.dumps(u.get("silent_blocked", []), ensure_ascii=False),
        u.get("font_style"),
        u.get("font_auto", False),
        u.get("auto_reply_enabled", False),
        u.get("auto_reply_text", ""),
        u.get("auto_reply_cooldown", 3600),
        json.dumps(u.get("auto_reply_sent_to", {}), ensure_ascii=False),
        u.get("clock_enabled", True),
        u.get("name_font_style", "normal"),
        u.get("secretary_enabled", False),
        u.get("secretary_text", ""),
        json.dumps(u.get("secretary_sent_to", {}), ensure_ascii=False),
        json.dumps(u.get("muted_users", []), ensure_ascii=False),
        u.get("pv_lock", False),
        u.get("typing_mode", False),
        u.get("game_mode", False),
    ))
    cur.close()


def delete_user(uid_s):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE uid = %s", (int(uid_s),))
    cur.close()


# runtime
db = {}
tasks = {}
clients = {}
conv = {}
setting_mode = {}
incoming_handlers = {}
cmd_handlers = {}
action_tasks = {}
CONTROL_BOT_ID = None

NEW_USER_DEFAULTS = {
    "base_name": "",
    "timezone": DEFAULT_TZ,
    "time_format": DEFAULT_FMT,
    "update_interval": DEFAULT_INT,
    "separator": " | ",
    "active": True,
    "orig_first": "",
    "orig_last": "",
    "orig_about": "",
    "silent_blocked": [],
    "font_style": None,
    "font_auto": False,
    "auto_reply_enabled": False,
    "auto_reply_text": "",
    "auto_reply_cooldown": 3600,
    "auto_reply_sent_to": {},
    "clock_enabled": True,
    "name_font_style": "normal",
    "secretary_enabled": False,
    "secretary_text": "",
    "secretary_sent_to": {},
    "muted_users": [],
    "pv_lock": False,
    "typing_mode": False,
    "game_mode": False,
}


def new_user_record(session_string, phone):
    rec = dict(NEW_USER_DEFAULTS)
    rec["session_string"] = session_string
    rec["phone"] = phone
    return rec


# ═══════════════════════════════════════════════════
# TEXT FONT ENGINE
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
_CIRCLED_DIGITS = {"1": "\u2460", "2": "\u2461", "3": "\u2462", "4": "\u2463", "5": "\u2464",
                   "6": "\u2465", "7": "\u2466", "8": "\u2467", "9": "\u2468", "0": "\u24EA"}
FONT_MAPS["circled"].update(_CIRCLED_DIGITS)

FONT_LABELS = {
    "bold": "𝗕𝗼𝗹𝗱", "italic": "𝘐𝘵𝘢𝘭𝘪𝘤", "bold_italic": "𝑩𝒐𝒍𝒅 𝑰𝒕𝒂𝒍𝒊𝒄",
    "script": "𝒮𝒸𝓇𝒾𝓅𝓉", "doublestruck": "𝔻𝕠𝕦𝕓𝕝𝕖", "fraktur": "𝔉𝔯𝔞𝔨𝔱𝔲𝔯",
    "monospace": "𝙼𝚘𝚗𝚘", "circled": "Ⓒⓘⓡⓒⓛⓔⓓ", "fullwidth": "Ｆｕｌｌｗｉｄｔｈ",
}


def apply_font(text, style):
    m = FONT_MAPS.get(style)
    if not m:
        return text
    return "".join(m.get(ch, ch) for ch in text)


# ═══════════════════════════════════════════════════
# NAME DIGIT FONT ENGINE (10 styles)
# ═══════════════════════════════════════════════════
NAME_FONT_MAPS = {
    "normal": {},
    "bold": {str(i): chr(0x1D7CE + i) for i in range(10)},
    "doublestruck": {str(i): chr(0x1D7D8 + i) for i in range(10)},
    "monospace": {str(i): chr(0x1D7F6 + i) for i in range(10)},
    "sans": {str(i): chr(0x1D7E2 + i) for i in range(10)},
    "filled": {str(i): chr(0x1D7EC + i) for i in range(10)},
    "circled": {"0": "\u24EA", "1": "\u2460", "2": "\u2461", "3": "\u2462", "4": "\u2463",
                "5": "\u2464", "6": "\u2465", "7": "\u2466", "8": "\u2467", "9": "\u2468"},
    "fullwidth": {str(i): chr(0xFF10 + i) for i in range(10)},
    "cursive": {"0": "\u2070", "1": "\u00B9", "2": "\u00B2", "3": "\u00B3", "4": "\u2074",
                "5": "\u2075", "6": "\u2076", "7": "\u2077", "8": "\u2078", "9": "\u2079"},
    "inverted": {"0": "\u2080", "1": "\u2081", "2": "\u2082", "3": "\u2083", "4": "\u2084",
                 "5": "\u2085", "6": "\u2086", "7": "\u2087", "8": "\u2088", "9": "\u2089"},
}

NAME_FONT_LABELS = {
    "normal": "معمولی", "bold": "𝗕𝗼𝗹𝗱", "doublestruck": "𝔻𝕠𝕦𝕓𝕝𝕖",
    "monospace": "𝙼𝚘𝚗𝚘", "sans": "𝖲𝖺𝗇𝗌", "filled": "𝙎𝙖𝙣𝙨 𝘽𝙤𝙡𝙙",
    "circled": "Ⓒⓘⓡⓒⓛⓔⓓ", "fullwidth": "Ｆｕｌｌｗｉｄｔｈ",
    "cursive": "بالانویس", "inverted": "زیرنویس",
}

NAME_FONT_ORDER = list(NAME_FONT_LABELS.keys())


def apply_name_font(text, style):
    m = NAME_FONT_MAPS.get(style, {})
    if not m:
        return text
    return "".join(m.get(ch, ch) for ch in text)


# ═══════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════
def blocked_ids(uid_s):
    return {b["id"] for b in db.get(uid_s, {}).get("silent_blocked", [])}


def muted_ids(uid_s):
    return {b["id"] for b in db.get(uid_s, {}).get("muted_users", [])}


# ═══════════════════════════════════════════════════
# ACTION WORKER (typing / game)
# ═══════════════════════════════════════════════════
async def _action_worker(uid, c, mode):
    action = SendMessageTypingAction() if mode == "typing" else SendMessageGamePlayAction()
    while True:
        try:
            dialogs = await c.get_dialogs(limit=30)
            for dialog in dialogs:
                if not dialog.is_user:
                    continue
                if getattr(dialog.entity, "bot", False):
                    continue
                try:
                    await c(SetTypingRequest(peer=dialog.input_peer, action=action))
                except Exception:
                    pass
                await asyncio.sleep(0.3)
        except Exception as e:
            log.warning(f"[{uid}] {mode} worker: {e}")
        await asyncio.sleep(5)


# ═══════════════════════════════════════════════════
# INCOMING HANDLER
# ═══════════════════════════════════════════════════
def register_incoming_handler(uid, c):
    uid_s = str(uid)
    if uid in incoming_handlers:
        return

    async def _handler(event):
        try:
            sender_id = event.sender_id

            if sender_id in muted_ids(uid_s):
                try:
                    await event.delete()
                except Exception:
                    pass
                return

            if not event.is_private:
                return

            if CONTROL_BOT_ID and sender_id == CONTROL_BOT_ID:
                return

            try:
                sender = await event.get_sender()
                if sender and getattr(sender, "bot", False):
                    return
            except Exception:
                pass

            u = db.get(uid_s, {})

            if sender_id in blocked_ids(uid_s):
                try:
                    await event.delete()
                except Exception:
                    pass
                return

            if u.get("pv_lock"):
                try:
                    await event.delete()
                except Exception:
                    pass
                return

            if u.get("secretary_enabled") and u.get("secretary_text"):
                sent_to = u.get("secretary_sent_to", {})
                if str(sender_id) not in sent_to:
                    try:
                        await event.reply(u["secretary_text"])
                    except FloodWaitError as e:
                        await asyncio.sleep(e.seconds + 2)
                        try:
                            await event.reply(u["secretary_text"])
                        except Exception:
                            pass
                    except Exception:
                        pass
                    sent_to[str(sender_id)] = True
                    db[uid_s]["secretary_sent_to"] = sent_to
                    save_user(uid_s)
                    return

            if u.get("auto_reply_enabled") and u.get("auto_reply_text"):
                now = time.time()
                sent_to = u.get("auto_reply_sent_to", {})
                cooldown = u.get("auto_reply_cooldown", 3600)
                last = sent_to.get(str(sender_id), 0)
                if now - last < cooldown:
                    return
                try:
                    await event.reply(u["auto_reply_text"])
                except FloodWaitError as e:
                    await asyncio.sleep(e.seconds + 2)
                    try:
                        await event.reply(u["auto_reply_text"])
                    except Exception:
                        pass
                    return
                except Exception:
                    return
                sent_to[str(sender_id)] = now
                db[uid_s]["auto_reply_sent_to"] = sent_to
                save_user(uid_s)

        except Exception as e:
            log.warning(f"[{uid}] incoming: {e}")

    c.add_event_handler(_handler, events.NewMessage(incoming=True))
    incoming_handlers[uid] = _handler


def unregister_incoming_handler(uid, c):
    handler = incoming_handlers.pop(uid, None)
    if handler:
        try:
            c.remove_event_handler(handler, events.NewMessage)
        except Exception:
            pass


# ═══════════════════════════════════════════════════
# SELF-ACCOUNT COMMANDS
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
        await event.edit(f"❌ خطا: `{e}`")
        return
    mentions = [f"[{p.first_name or p.username or p.id}](tg://user?id={p.id})"
                for p in participants if not p.bot and not p.deleted]
    if not mentions:
        await event.edit("❌ عضوی پیدا نشد.")
        return
    await event.delete()
    for i in range(0, len(mentions), 5):
        batch = mentions[i:i + 5]
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
            "`/font set <style>` — فعال کردن فونت خودکار\n"
            "`/font off` — خاموش کردن\n\n"
            f"استایل‌ها:\n{styles}"
        )
        return
    sub = args[0].lower()
    if sub == "off":
        db[uid_s]["font_auto"] = False
        save_user(uid_s)
        await event.edit("✅ فونت خودکار خاموش شد.")
        return
    if sub == "set":
        style = args[1].strip() if len(args) > 1 else ""
        if style not in FONT_MAPS:
            await event.edit("❌ استایل نامعتبر.")
            return
        db[uid_s]["font_style"] = style
        db[uid_s]["font_auto"] = True
        save_user(uid_s)
        await event.edit(f"✅ فونت خودکار: `{style}`")
        return
    if sub not in FONT_MAPS:
        await event.edit("❌ استایل نامعتبر.")
        return
    if len(args) < 2:
        await event.edit("❌ متن نفرستادی.")
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
    if not arg.strip().isdigit():
        await event.edit("❌ عدد بفرست.\nمثال: `/del 100`")
        return
    n = min(int(arg.strip()), 300)
    ids = []
    async for m in event.client.iter_messages(event.chat_id, from_user="me", limit=n):
        ids.append(m.id)
    if event.id not in ids:
        ids.append(event.id)
    if ids:
        await event.client.delete_messages(event.chat_id, ids)


async def _cmd_repeat(event, arg):
    args = arg.split(maxsplit=1)
    if len(args) < 2:
        await event.edit("❌ فرمت: `/r 100 سلام`")
        return
    count_str, text = args[0], args[1]
    if not count_str.isdigit():
        await event.edit("❌ تعداد باید عدد باشه.")
        return
    count = int(count_str)
    if count < 1 or count > 500:
        await event.edit("❌ ۱ تا ۵۰۰ بار.")
        return
    await event.delete()
    for i in range(count):
        try:
            await event.client.send_message(event.chat_id, text)
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds + 2)
            try:
                await event.client.send_message(event.chat_id, text)
            except Exception:
                pass
        except Exception:
            pass
        await asyncio.sleep(0.4)


async def _cmd_autoreply(uid, event, arg):
    uid_s = str(uid)
    arg = arg.strip()
    if not arg:
        u = db.get(uid_s, {})
        on = u.get("auto_reply_enabled", False)
        txt = u.get("auto_reply_text", "")
        cd = u.get("auto_reply_cooldown", 3600)
        s = "✅ روشن" if on else "❌ خاموش"
        await event.edit(
            f"📨 **پاسخ خودکار**\n\n"
            f"وضعیت: {s}\n"
            f"متن: `{txt or 'تنظیم نشده'}`\n"
            f"کول‌داون: `{cd}` ثانیه\n\n"
            f"`/rr on` — روشن\n"
            f"`/rr off` — خاموش\n"
            f"`/rr متن پیام` — تنظیم متن"
        )
        return
    if arg == "on":
        if not db[uid_s].get("auto_reply_text"):
            await event.edit("❌ اول متن پاسخ رو تنظیم کن.\nمثال: `/rr الان در دسترس نیستم`")
            return
        db[uid_s]["auto_reply_enabled"] = True
        save_user(uid_s)
        await event.edit("✅ پاسخ خودکار روشن شد.")
        return
    if arg == "off":
        db[uid_s]["auto_reply_enabled"] = False
        save_user(uid_s)
        await event.edit("❌ پاسخ خودکار خاموش شد.")
        return
    db[uid_s]["auto_reply_text"] = arg[:500]
    db[uid_s]["auto_reply_enabled"] = True
    save_user(uid_s)
    await event.edit(f"✅ پاسخ خودکار تنظیم شد:\n\n`{arg[:500]}`")


async def _cmd_ban(uid, event, arg):
    uid_s = str(uid)
    target = arg.strip().lstrip("@")
    if not target:
        await event.edit("❌ فرمت: `/ban @username` یا `/ban 123456`")
        return
    c = clients.get(uid)
    if not c:
        await event.edit("❌ سلف‌بات فعال نیست.")
        return
    try:
        entity = await c.get_entity(int(target) if target.lstrip("-").isdigit() else target)
    except Exception as e:
        await event.edit(f"❌ کاربر پیدا نشد: `{e}`")
        return
    tid = entity.id
    name = (getattr(entity, "first_name", "") or "") + " " + (getattr(entity, "last_name", "") or "")
    name = name.strip() or (getattr(entity, "username", "") or str(tid))
    lst = db[uid_s].setdefault("silent_blocked", [])
    if any(b["id"] == tid for b in lst):
        await event.edit(f"⚠️ `{name}` از قبل مسدوده.")
        return
    try:
        await c(BlockRequest(id=entity))
    except Exception:
        pass
    lst.append({"id": tid, "name": name})
    save_user(uid_s)
    await event.edit(f"🚫 `{name}` مسدود شد.\nپیام‌های جدیدش فوراً پاک میشن.")


async def _cmd_unban(uid, event, arg):
    uid_s = str(uid)
    target = arg.strip().lstrip("@")
    if not target:
        await event.edit("❌ فرمت: `/unban @username`")
        return
    lst = db.get(uid_s, {}).get("silent_blocked", [])
    tid = int(target) if target.lstrip("-").isdigit() else None
    if tid is None:
        for b in lst:
            if target.lower() in b["name"].lower():
                tid = b["id"]
                break
    if tid is None:
        await event.edit("❌ توی لیست بلاک پیدا نشد.")
        return
    entry = next((b for b in lst if b["id"] == tid), None)
    if not entry:
        await event.edit("❌ پیدا نشد.")
        return
    c = clients.get(uid)
    if c:
        try:
            await c(UnblockRequest(id=tid))
        except Exception:
            pass
    lst[:] = [b for b in lst if b["id"] != tid]
    save_user(uid_s)
    await event.edit(f"✅ `{entry['name']}` آنبلاک شد.")


async def _cmd_banlist(uid, event):
    lst = db.get(str(uid), {}).get("silent_blocked", [])
    if not lst:
        await event.edit("🚫 لیست بلاک خالیه.\n\n`/ban @username` — بلاک\n`/unban @username` — آنبلاک")
        return
    names = "\n".join(f"• `{b['name']}`" for b in lst)
    await event.edit(f"━━━ 🚫 لیست بلاک ━━━\n\n{names}\n\n`/unban @username` — آنبلاک")


async def _cmd_mute(uid, event, arg):
    uid_s = str(uid)
    target = arg.strip().lstrip("@")
    if not target:
        await event.edit("❌ فرمت: `/mute @username`")
        return
    c = clients.get(uid)
    if not c:
        await event.edit("❌ سلف‌بات فعال نیست.")
        return
    try:
        entity = await c.get_entity(int(target) if target.lstrip("-").isdigit() else target)
    except Exception as e:
        await event.edit(f"❌ کاربر پیدا نشد: `{e}`")
        return
    tid = entity.id
    name = (getattr(entity, "first_name", "") or "") + " " + (getattr(entity, "last_name", "") or "")
    name = name.strip() or (getattr(entity, "username", "") or str(tid))
    lst = db[uid_s].setdefault("muted_users", [])
    if any(b["id"] == tid for b in lst):
        await event.edit(f"⚠️ `{name}` از قبل ساکته.")
        return
    lst.append({"id": tid, "name": name})
    save_user(uid_s)
    await event.edit(f"🔇 `{name}` ساکت شد.\nپیام‌هاش توی تمام چت‌ها حذف میشن.")


async def _cmd_unmute(uid, event, arg):
    uid_s = str(uid)
    target = arg.strip().lstrip("@")
    if not target:
        await event.edit("❌ فرمت: `/unmute @username`")
        return
    lst = db.get(uid_s, {}).get("muted_users", [])
    tid = int(target) if target.lstrip("-").isdigit() else None
    if tid is None:
        for b in lst:
            if target.lower() in b["name"].lower():
                tid = b["id"]
                break
    if tid is None:
        await event.edit("❌ توی لیست سکوت پیدا نشد.")
        return
    entry = next((b for b in lst if b["id"] == tid), None)
    if not entry:
        await event.edit("❌ پیدا نشد.")
        return
    lst[:] = [b for b in lst if b["id"] != tid]
    save_user(uid_s)
    await event.edit(f"🔊 `{entry['name']}` از سکوت خارج شد.")


async def _cmd_mutelist(uid, event):
    lst = db.get(str(uid), {}).get("muted_users", [])
    if not lst:
        await event.edit("🔇 لیست سکوت خالیه.\n\n`/mute @username` — سکوت\n`/unmute @username` — خارج کردن")
        return
    names = "\n".join(f"• `{b['name']}`" for b in lst)
    await event.edit(f"━━━ 🔇 لیست سکوت ━━━\n\n{names}\n\n`/unmute @username` — خارج کردن")


async def _cmd_pvlock(uid, event, arg):
    uid_s = str(uid)
    arg = arg.strip().lower()
    if not arg:
        on = db.get(uid_s, {}).get("pv_lock", False)
        await event.edit(f"🔒 قفل پی‌وی: {'✅ روشن' if on else '❌ خاموش'}\n\n`/pvlock on` — روشن\n`/pvlock off` — خاموش")
        return
    if arg == "on":
        db[uid_s]["pv_lock"] = True
        save_user(uid_s)
        await event.edit("🔒 قفل پی‌وی روشن شد.\nتمام پیام‌های خصوصی حذف میشن.")
    elif arg == "off":
        db[uid_s]["pv_lock"] = False
        save_user(uid_s)
        await event.edit("🔓 قفل پی‌وی خاموش شد.")
    else:
        await event.edit("❌ `/pvlock on` یا `/pvlock off`")


async def _cmd_secretary(uid, event, arg):
    uid_s = str(uid)
    arg = arg.strip()
    if not arg:
        u = db.get(uid_s, {})
        on = u.get("secretary_enabled", False)
        txt = u.get("secretary_text", "")
        cnt = len(u.get("secretary_sent_to", {}))
        await event.edit(
            f"🤖 **منشی**\n\n"
            f"وضعیت: {'✅ روشن' if on else '❌ خاموش'}\n"
            f"متن: `{txt or 'تنظیم نشده'}`\n"
            f"پاسخ داده شده: `{cnt}` نفر\n\n"
            f"`/secretary متن پیام` — تنظیم و روشن\n"
            f"`/secretary on` — روشن\n"
            f"`/secretary off` — خاموش\n"
            f"`/secretary reset` — ریست تاریخچه"
        )
        return
    if arg == "on":
        if not db[uid_s].get("secretary_text"):
            await event.edit("❌ اول متن منشی رو تنظیم کن.\nمثال: `/secretary الان در دسترس نیستم`")
            return
        db[uid_s]["secretary_enabled"] = True
        save_user(uid_s)
        await event.edit("✅ منشی روشن شد.")
        return
    if arg == "off":
        db[uid_s]["secretary_enabled"] = False
        save_user(uid_s)
        await event.edit("❌ منشی خاموش شد.")
        return
    if arg == "reset":
        db[uid_s]["secretary_sent_to"] = {}
        save_user(uid_s)
        await event.edit("🗑 تاریخچه منشی پاک شد.\nبه همه دوباره پاسخ داده میشه.")
        return
    db[uid_s]["secretary_text"] = arg[:500]
    db[uid_s]["secretary_enabled"] = True
    save_user(uid_s)
    await event.edit(f"✅ منشی تنظیم شد:\n\n`{arg[:500]}`")


async def _cmd_typing(uid, event):
    uid_s = str(uid)
    c = clients.get(uid)
    if not c:
        await event.edit("❌ سلف‌بات فعال نیست.")
        return
    if db[uid_s].get("typing_mode"):
        db[uid_s]["typing_mode"] = False
        save_user(uid_s)
        if uid in action_tasks:
            action_tasks[uid].cancel()
            action_tasks.pop(uid, None)
        await event.edit("⌨️ حالت تایپینگ خاموش شد.")
    else:
        db[uid_s]["typing_mode"] = True
        db[uid_s]["game_mode"] = False
        save_user(uid_s)
        if uid in action_tasks:
            action_tasks[uid].cancel()
            action_tasks.pop(uid, None)
        action_tasks[uid] = asyncio.create_task(_action_worker(uid, c, "typing"))
        await event.edit("⌨️ حالت تایپینگ روشن شد.\nبه ۳۰ چت اخیر وضعیت تایپینگ ارسال میشه.")


async def _cmd_game(uid, event):
    uid_s = str(uid)
    c = clients.get(uid)
    if not c:
        await event.edit("❌ سلف‌بات فعال نیست.")
        return
    if db[uid_s].get("game_mode"):
        db[uid_s]["game_mode"] = False
        save_user(uid_s)
        if uid in action_tasks:
            action_tasks[uid].cancel()
            action_tasks.pop(uid, None)
        await event.edit("🎮 حالت بازی خاموش شد.")
    else:
        db[uid_s]["game_mode"] = True
        db[uid_s]["typing_mode"] = False
        save_user(uid_s)
        if uid in action_tasks:
            action_tasks[uid].cancel()
            action_tasks.pop(uid, None)
        action_tasks[uid] = asyncio.create_task(_action_worker(uid, c, "game"))
        await event.edit("🎮 حالت بازی روشن شد.\nبه ۳۰ چت اخیر وضعیت بازی ارسال میشه.")


async def _cmd_dice(event):
    try:
        await event.client.send_file(event.chat_id, InputMediaDice(emoticon="\U0001F3B2"))
        await event.delete()
    except Exception as e:
        await event.edit(f"❌ خطا: `{e}`")


async def _cmd_bowl(event):
    try:
        await event.client.send_file(event.chat_id, InputMediaDice(emoticon="\U0001F3B3"))
        await event.delete()
    except Exception as e:
        await event.edit(f"❌ خطا: `{e}`")


async def _cmd_clock(uid, event, arg):
    uid_s = str(uid)
    arg = arg.strip().lower()
    if not arg:
        on = db.get(uid_s, {}).get("clock_enabled", True)
        await event.edit(f"⏰ ساعت در اسم: {'✅ روشن' if on else '❌ خاموش'}\n\n`/clock on` — روشن\n`/clock off` — خاموش")
        return
    if arg == "on":
        db[uid_s]["clock_enabled"] = True
        save_user(uid_s)
        await event.edit("✅ ساعت روشن شد.")
    elif arg == "off":
        db[uid_s]["clock_enabled"] = False
        save_user(uid_s)
        c = clients.get(uid)
        if c:
            try:
                base = db[uid_s].get("base_name") or db[uid_s].get("orig_first", "")
                await c(UpdateProfileRequest(first_name=base, last_name=db[uid_s].get("orig_last", ""), about=db[uid_s].get("orig_about", "")))
            except Exception:
                pass
        await event.edit("❌ ساعت خاموش شد.")
    else:
        await event.edit("❌ `/clock on` یا `/clock off`")


async def _cmd_nfont(uid, event, arg):
    uid_s = str(uid)
    arg = arg.strip().lower()
    if not arg:
        current = db.get(uid_s, {}).get("name_font_style", "normal")
        styles = "\n".join(f"• `{k}` — {v}" for k, v in NAME_FONT_LABELS.items())
        await event.edit(f"🎨 فونت ساعت: `{current}`\n\n`/nfont [style]`\n\n{styles}")
        return
    if arg not in NAME_FONT_MAPS:
        await event.edit("❌ استایل نامعتبر.")
        return
    db[uid_s]["name_font_style"] = arg
    save_user(uid_s)
    await event.edit(f"✅ فونت ساعت: `{arg}`")


async def _cmd_panel(uid, event):
    uid_s = str(uid)
    u = db.get(uid_s, {})
    clock = "✅ روشن" if u.get("clock_enabled", True) else "❌ خاموش"
    ar = "✅ روشن" if u.get("auto_reply_enabled") else "❌ خاموش"
    sec = "✅ روشن" if u.get("secretary_enabled") else "❌ خاموش"
    pv = "✅ روشن" if u.get("pv_lock") else "❌ خاموش"
    typ = "✅ روشن" if u.get("typing_mode") else "❌ خاموش"
    gam = "✅ روشن" if u.get("game_mode") else "❌ خاموش"
    await event.edit(
        f"━━━ 🎛 پنل مدیریت ━━━\n\n"
        f"⏰ ساعت: {clock}\n🎨 فونت ساعت: `{u.get('name_font_style', 'normal')}`\n\n"
        f"📨 پاسخ خودکار: {ar}\n🤖 منشی: {sec}\n\n"
        f"🔒 قفل پی‌وی: {pv}\n\n"
        f"⌨️ تایپینگ: {typ}\n🎮 بازی: {gam}\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "━━ دستورات ━━\n"
        "`/clock on/off` — ساعت\n"
        "`/nfont [style]` — فونت ساعت\n"
        "`/typing` — تایپینگ\n"
        "`/game` — بازی\n"
        "`/pvlock on/off` — قفل پی‌وی\n"
        "`/secretary متن` — منشی\n"
        "`/rr متن` — پاسخ خودکار\n"
        "`/ban @u` — بلاک\n"
        "`/mute @u` — سکوت\n"
        "`/dice` 🎲 — تاس\n"
        "`/bowl` 🎳 — بولینگ"
    )


# ═══════════════════════════════════════════════════
# /help - SELF ACCOUNT (any chat)
# ═══════════════════════════════════════════════════
async def _cmd_help_self(uid, event):
    uid_s = str(uid)
    u = db.get(uid_s, {})
    clock_st = "✅ روشن" if u.get("clock_enabled", True) else "❌ خاموش"
    ar_st = "✅ روشن" if u.get("auto_reply_enabled") else "❌ خاموش"
    sec_st = "✅ روشن" if u.get("secretary_enabled") else "❌ خاموش"
    pv_st = "✅ روشن" if u.get("pv_lock") else "❌ خاموش"
    typ_st = "✅ روشن" if u.get("typing_mode") else "❌ خاموش"
    gam_st = "✅ روشن" if u.get("game_mode") else "❌ خاموش"
    font_st = u.get("name_font_style", "normal")
    await event.edit(
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "       📖 راهنمای کامل\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "━━ 🎛 پنل مدیریت ━━\n"
        "`/panel` — نمایش وضعیت و کنترل تمام قابلیت‌ها\n\n"
        f"━━ ⏰ ساعت در اسم [{clock_st}] ━━\n"
        "`/clock on` — روشن کردن ساعت\n"
        "`/clock off` — خاموش کردن ساعت\n"
        f"`/nfont [style]` — فونت ساعت (الان: `{font_st}`)\n"
        "استایل‌های ساعت:\n"
        "`normal` `bold` `doublestruck` `monospace`\n"
        "`sans` `filled` `circled` `fullwidth`\n"
        "`cursive`(بالانویس) `inverted`(زیرنویس)\n\n"
        "━━ 🎨 فونت متن ━━\n"
        "`/font` — لیست فونت‌ها و راهنما\n"
        "`/font bold متن` — تبدیل متن به فونت\n"
        "`/font set bold` — فونت خودکار برای پیام‌ها\n"
        "`/font off` — خاموش فونت خودکار\n"
        "استایل‌ها:\n"
        "`bold` `italic` `bold_italic` `script`\n"
        "`doublestruck` `fraktur` `monospace`\n"
        "`circled` `fullwidth`\n\n"
        "━━ 🚫 بلاک مخفی ━━\n"
        "`/ban @username` — بلاک مخفی (پیام‌ها فوراً پاک)\n"
        "`/ban 123456` — بلاک با آیدی عددی\n"
        "`/unban @username` — آنبلاک\n"
        "`/banlist` — نمایش لیست بلاک‌ها\n\n"
        "━━ 🔇 سکوت کاربر ━━\n"
        "`/mute @username` — سکوت (حذف پیام در تمام چت‌ها)\n"
        "`/unmute @username` — خارج از سکوت\n"
        "`/mutelist` — نمایش لیست سکوت‌ها\n\n"
        f"━━ 🔒 قفل پی‌وی [{pv_st}] ━━\n"
        "`/pvlock on` — روشن (حذف تمام پیام‌های خصوصی)\n"
        "`/pvlock off` — خاموش\n\n"
        f"━━ 🤖 منشی [{sec_st}] ━━\n"
        "`/secretary متن پیام` — تنظیم متن و روشن\n"
        "`/secretary on` — روشن\n"
        "`/secretary off` — خاموش\n"
        "`/secretary reset` — پاک کردن تاریخچه\n"
        "هر کاربر فقط یک‌بار پاسخ خودکار دریافت می‌کنه\n\n"
        f"━━ 📨 پاسخ خودکار [{ar_st}] ━━\n"
        "`/rr متن پیام` — تنظیم متن و روشن\n"
        "`/rr on` — روشن\n"
        "`/rr off` — خاموش\n"
        "`/rr` — نمایش وضعیت\n"
        "هر نفر فقط هر X ثانیه یه بار پاسخ می‌گیره\n\n"
        f"━━ ⌨️ تایپینگ/بازی [{typ_st}/{gam_st}] ━━\n"
        "`/typing` — روشن/خاموش تایپینگ (۳۰ چت اخیر)\n"
        "`/game` — روشن/خاموش بازی (۳۰ چت اخیر)\n"
        "این دو حالت یکدیگر رو غیرفعال می‌کنن\n\n"
        "━━ 🏷 گروه/کانال ━━\n"
        "`/tag [متن]` — تگ کردن همه اعضا\n"
        "`/pin` (ریپلای) — پین کردن پیام\n"
        "`/ping` — تست اتصال سلف‌بات\n\n"
        "━━ 🌐 ترجمه ━━\n"
        "`/tr` (ریپلای) — ترجمه به فارسی\n"
        "`/tr en` (ریپلای) — ترجمه به انگلیسی\n"
        "`/tr ar` (ریپلای) — ترجمه به عربی\n\n"
        "━━ 📨 پیام ━━\n"
        "`/r 100 متن` — ارسال ۱۰۰ بار (حداکثر ۵۰۰)\n"
        "`/del 100` — حذف ۱۰۰ پیام آخر (حداکثر ۳۰۰)\n\n"
        "━━ 🎲 سرگرمی ━━\n"
        "`/dice` — ارسال تاس 🎲\n"
        "`/bowl` — ارسال بولینگ 🎳\n\n"
        "━━ 📖 ربات کنترل (چت خصوصی با بات) ━━\n"
        "`/start` — منوی اصلی و ساخت سلف‌بات\n"
        "`/status` — وضعیت کامل سلف‌بات\n"
        "`/stop` — توقف سلف‌بات\n"
        "`/block` — مدیریت بلاک مخفی\n"
        "`/help` — همین راهنما\n\n"
        "━━━━━━━━━━━━━━━━━━━━━"
    )


# ═══════════════════════════════════════════════════
# COMMAND DISPATCH
# ═══════════════════════════════════════════════════
def register_command_handlers(uid, c):
    if uid in cmd_handlers:
        return

    async def _dispatch(event):
        text = event.raw_text or ""
        if CONTROL_BOT_ID and event.chat_id == CONTROL_BOT_ID:
            return
        if uid in conv or uid in setting_mode:
            return
        if text.startswith("/"):
            parts = text.split(maxsplit=1)
            cmd = parts[0].lower().split("@")[0]
            arg = parts[1] if len(parts) > 1 else ""
            try:
                if cmd == "/tag":       await _cmd_tag(event, arg)
                elif cmd == "/pin":     await _cmd_pin(event)
                elif cmd == "/ping":    await _cmd_ping(event)
                elif cmd == "/font":    await _cmd_font(uid, event, arg)
                elif cmd in ("/tr", "/translate"): await _cmd_translate(event, arg)
                elif cmd == "/del":     await _cmd_del(event, arg)
                elif cmd == "/r":       await _cmd_repeat(event, arg)
                elif cmd == "/rr":      await _cmd_autoreply(uid, event, arg)
                elif cmd == "/ban":     await _cmd_ban(uid, event, arg)
                elif cmd == "/unban":   await _cmd_unban(uid, event, arg)
                elif cmd == "/banlist": await _cmd_banlist(uid, event)
                elif cmd == "/mute":    await _cmd_mute(uid, event, arg)
                elif cmd == "/unmute":  await _cmd_unmute(uid, event, arg)
                elif cmd == "/mutelist":await _cmd_mutelist(uid, event)
                elif cmd == "/pvlock":  await _cmd_pvlock(uid, event, arg)
                elif cmd == "/secretary": await _cmd_secretary(uid, event, arg)
                elif cmd == "/typing":  await _cmd_typing(uid, event)
                elif cmd == "/game":    await _cmd_game(uid, event)
                elif cmd == "/dice":    await _cmd_dice(event)
                elif cmd == "/bowl":    await _cmd_bowl(event)
                elif cmd == "/panel":   await _cmd_panel(uid, event)
                elif cmd == "/clock":   await _cmd_clock(uid, event, arg)
                elif cmd == "/nfont":   await _cmd_nfont(uid, event, arg)
                elif cmd == "/help":    await _cmd_help_self(uid, event)
            except Exception as e:
                log.warning(f"[{uid}] cmd {cmd}: {e}")
                try:
                    await event.edit(f"❌ خطا: `{e}`")
                except Exception:
                    pass
            return
        uid_s = str(uid)
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
    uid_s = str(uid)
    c = clients.get(uid)
    if not c:
        return False, "❌ سلف‌بات فعال نیست."
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
    except Exception:
        pass
    lst.append({"id": tid, "name": name})
    save_user(uid_s)
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
        except Exception:
            pass
    lst[:] = [b for b in lst if b["id"] != target_id]
    save_user(uid_s)
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
        register_incoming_handler(uid, c)
        register_command_handlers(uid, c)

        me = await c.get_me()
        full = await c(GetFullUserRequest(me))
        db[uid_s]["orig_first"] = me.first_name or ""
        db[uid_s]["orig_last"] = me.last_name or ""
        db[uid_s]["orig_about"] = full.full_user.about or ""
        db[uid_s]["active"] = True
        save_user(uid_s)
        log.info(f"[{uid}] ON: {me.first_name}")

        if u.get("typing_mode"):
            action_tasks[uid] = asyncio.create_task(_action_worker(uid, c, "typing"))
        elif u.get("game_mode"):
            action_tasks[uid] = asyncio.create_task(_action_worker(uid, c, "game"))

        try:
            await bot_ref.send_message(uid, "✅ سلف‌بات فعال شد!")
        except Exception:
            pass

        while True:
            try:
                if db[uid_s].get("clock_enabled", True):
                    tz = pytz.timezone(u.get("timezone", DEFAULT_TZ))
                    fmt = u.get("time_format", DEFAULT_FMT)
                    sep = u.get("separator", " | ")
                    base = u.get("base_name") or db[uid_s].get("orig_first", "")
                    nfont = u.get("name_font_style", "normal")
                    t = datetime.now(tz).strftime(fmt)
                    if nfont and nfont != "normal":
                        t = apply_name_font(t, nfont)
                    name = f"{base}{sep}{t}"
                else:
                    name = u.get("base_name") or db[uid_s].get("orig_first", "")

                await c(UpdateProfileRequest(
                    first_name=name,
                    last_name=db[uid_s].get("orig_last", ""),
                    about=db[uid_s].get("orig_about", ""),
                ))
                log.info(f"[{uid}] -> {name}")
            except FloodWaitError as e:
                log.warning(f"[{uid}] Flood: {e.seconds}s")
                await asyncio.sleep(e.seconds + 5)
                continue
            except AuthKeyError:
                log.error(f"[{uid}] Session invalid!")
                db[uid_s]["active"] = False
                save_user(uid_s)
                try:
                    await bot_ref.send_message(uid, "❌ سشن منقضی شده.\n/start رو بزن.")
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
        save_user(uid_s)
        try:
            await bot_ref.send_message(uid, f"❌ خطا:\n`{e}`")
        except Exception:
            pass
    finally:
        unregister_incoming_handler(uid, c)
        unregister_command_handlers(uid, c)
        if uid in action_tasks:
            action_tasks[uid].cancel()
            action_tasks.pop(uid, None)
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
    if uid in action_tasks:
        action_tasks[uid].cancel()
        action_tasks.pop(uid, None)
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
        unregister_incoming_handler(uid, clients[uid])
        unregister_command_handlers(uid, clients[uid])
        try:
            await clients[uid].disconnect()
        except Exception:
            pass
        clients.pop(uid, None)
    if uid_s in db:
        db[uid_s]["active"] = False
        save_user(uid_s)


# ═══════════════════════════════════════════════════
# BOT
# ═══════════════════════════════════════════════════
async def run_bot():
    global CONTROL_BOT_ID, db

    init_db()
    db = load_all_users()
    log.info(f"Loaded {len(db)} users from DB")

    bot = TelegramClient("bot_session", API_ID, API_HASH)
    await bot.start(bot_token=BOT_TOKEN)
    bot_me = await bot.get_me()
    CONTROL_BOT_ID = bot_me.id
    log.info(f"Bot: @{bot_me.username} (ID: {CONTROL_BOT_ID})")

    for uid_s, u in db.items():
        if u.get("active") and u.get("session_string"):
            uid = int(uid_s)
            log.info(f"Restart selfbot {uid}")
            tasks[uid] = asyncio.create_task(selfbot_worker(uid, bot))

    def main_kb(uid):
        uid_s = str(uid)
        u = db.get(uid_s, {})
        if not u.get("session_string"):
            return [[Button.inline("🚀 ساخت سلف‌بات", b"setup")]]
        on = uid in tasks and not tasks[uid].done()
        if on:
            return [
                [Button.inline("⏹ توقف", b"stop"), Button.inline("🔄 ری‌استارت", b"restart")],
                [Button.inline("🎛 پنل مدیریت", b"panel")],
                [Button.inline("⚙️ تنظیمات", b"settings")],
                [Button.inline("🚫 بلاک مخفی", b"block_menu")],
                [Button.inline("📨 پاسخ خودکار", b"ar_menu")],
                [Button.inline("🤖 منشی", b"sec_menu")],
                [Button.inline("🗑 حذف", b"ask_delete")],
            ]
        return [
            [Button.inline("▶️ فعال‌سازی", b"restart")],
            [Button.inline("🎛 پنل مدیریت", b"panel")],
            [Button.inline("⚙️ تنظیمات", b"settings")],
            [Button.inline("🚫 بلاک مخفی", b"block_menu")],
            [Button.inline("📨 پاسخ خودکار", b"ar_menu")],
            [Button.inline("🤖 منشی", b"sec_menu")],
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

    def panel_text(uid):
        uid_s = str(uid)
        u = db.get(uid_s, {})
        clock = "✅" if u.get("clock_enabled", True) else "❌"
        ar = "✅" if u.get("auto_reply_enabled") else "❌"
        sec = "✅" if u.get("secretary_enabled") else "❌"
        pv = "✅" if u.get("pv_lock") else "❌"
        typ = "✅" if u.get("typing_mode") else "❌"
        gam = "✅" if u.get("game_mode") else "❌"
        return (
            "━━━ 🎛 پنل مدیریت ━━━\n\n"
            f"⏰ ساعت: {clock} | 🎨 فونت: `{u.get('name_font_style', 'normal')}`\n"
            f"📨 پاسخ خودکار: {ar}\n🤖 منشی: {sec}\n"
            f"🔒 قفل پی‌وی: {pv}\n⌨️ تایپینگ: {typ} | 🎮 بازی: {gam}"
        )

    def panel_kb(uid):
        uid_s = str(uid)
        u = db.get(uid_s, {})
        c_on = u.get("clock_enabled", True)
        ar_on = u.get("auto_reply_enabled", False)
        sec_on = u.get("secretary_enabled", False)
        pv_on = u.get("pv_lock", False)
        typ_on = u.get("typing_mode", False)
        gam_on = u.get("game_mode", False)
        return [
            [Button.inline(f"⏰ ساعت: {'✅' if c_on else '❌'}", b"p_clock"),
             Button.inline(f"🎨 {u.get('name_font_style', 'normal')}", b"p_nfont")],
            [Button.inline(f"📨 پاسخ: {'✅' if ar_on else '❌'}", b"p_ar"),
             Button.inline(f"🤖 منشی: {'✅' if sec_on else '❌'}", b"p_sec")],
            [Button.inline(f"🔒 قفل PV: {'✅' if pv_on else '❌'}", b"p_pv")],
            [Button.inline(f"⌨️ تایپینگ: {'✅' if typ_on else '❌'}", b"p_typ"),
             Button.inline(f"🎮 بازی: {'✅' if gam_on else '❌'}", b"p_gam")],
            [Button.inline("◀️ بازگشت", b"back")],
        ]

    def block_kb(uid):
        lst = db.get(str(uid), {}).get("silent_blocked", [])
        rows = [[Button.inline("➕ افزودن", b"block_add")]]
        for b in lst[:20]:
            rows.append([Button.inline(f"❌ آنبلاک: {b['name']}", f"block_del:{b['id']}".encode())])
        rows.append([Button.inline("◀️ بازگشت", b"back")])
        return rows

    def block_text(uid):
        lst = db.get(str(uid), {}).get("silent_blocked", [])
        if not lst:
            return "━━━ 🚫 بلاک مخفی ━━━\n\nکسی مسدود نیست.\n\nبا این قابلیت پیام‌های جدید هر کاربری که بلاک بشه فوراً حذف میشه."
        names = "\n".join(f"• {b['name']}" for b in lst)
        return f"━━━ 🚫 بلاک مخفی ━━━\n\nمسدودها:\n{names}"

    def ar_info(uid):
        u = db.get(str(uid), {})
        on = u.get("auto_reply_enabled", False)
        txt = u.get("auto_reply_text", "")
        cd = u.get("auto_reply_cooldown", 3600)
        cnt = len(u.get("auto_reply_sent_to", {}))
        return (
            "━━━ 📨 پاسخ خودکار ━━━\n\n"
            f"وضعیت: {'✅ روشن' if on else '❌ خاموش'}\n"
            f"متن: `{txt or 'تنظیم نشده'}`\n\n"
            f"⏱ کول‌داون: `{cd}` ثانیه\n"
            f"📊 تعداد پاسخ‌ها: `{cnt}`"
        )

    def ar_kb(uid):
        u = db.get(str(uid), {})
        on = u.get("auto_reply_enabled", False)
        return [
            [Button.inline("✏️ متن پاسخ", b"ar_set_text"), Button.inline("⏱ کول‌داون", b"ar_set_cd")],
            [Button.inline("🔴 خاموش کردن" if on else "🟢 روشن کردن", b"ar_off" if on else b"ar_on")],
            [Button.inline("🗑 پاک کردن تاریخچه", b"ar_clear"), Button.inline("◀️ بازگشت", b"back")],
        ]

    def sec_info(uid):
        u = db.get(str(uid), {})
        on = u.get("secretary_enabled", False)
        txt = u.get("secretary_text", "")
        cnt = len(u.get("secretary_sent_to", {}))
        return (
            "━━━ 🤖 منشی ━━━\n\n"
            f"وضعیت: {'✅ روشن' if on else '❌ خاموش'}\n"
            f"متن: `{txt or 'تنظیم نشده'}`\n\n"
            f"📊 پاسخ داده شده: `{cnt}` نفر"
        )

    def sec_kb(uid):
        u = db.get(str(uid), {})
        on = u.get("secretary_enabled", False)
        return [
            [Button.inline("✏️ متن منشی", b"sec_set_text")],
            [Button.inline("🔴 خاموش کردن" if on else "🟢 روشن کردن", b"sec_off" if on else b"sec_on")],
            [Button.inline("🗑 ریست تاریخچه", b"sec_reset"), Button.inline("◀️ بازگشت", b"back")],
        ]

    # ── /start ──────────────────────────────────
    @bot.on(events.NewMessage(pattern=r"/start"))
    async def cmd_start(event):
        uid = event.sender_id
        u = db.get(str(uid), {})
        if u.get("session_string"):
            await event.respond(
                f"{status_text(uid)}\n\nاز منوی زیر استفاده کن:",
                buttons=main_kb(uid),
            )
        else:
            await event.respond(
                "━━━ 🤖 سلف‌بات ساز ━━━\n\n"
                "ساعت رو توی اسم پروفایلت نشون بده!\n\n"
                "✨ آپدیت خودکار هر دقیقه\n"
                "🌍 پشتیبانی تایم‌زون‌های مختلف\n"
                "🎨 فونت ساعت ۱۰ استایل\n"
                "🚫 بلاک مخفی افراد مزاحم\n"
                "🔇 سکوت کاربران\n"
                "📨 پاسخ خودکار\n"
                "🤖 منشی هوشمند\n"
                "🔒 قفل پی‌وی\n"
                "⌨️ حالت تایپینگ و بازی\n"
                "🎲 سرگرمی (تاس و بولینگ)\n\n"
                "از دکمه زیر شروع کن:",
                buttons=main_kb(uid),
            )

    # ── /help (control bot) ─────────────────────
    @bot.on(events.NewMessage(pattern=r"/help"))
    async def cmd_help(event):
        await event.respond(
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "       📖 راهنمای کامل\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            "━━ ربات کنترل (این چت) ━━\n"
            "`/start` — منوی اصلی و ساخت سلف‌بات\n"
            "`/status` — وضعیت کامل سلف‌بات\n"
            "`/stop` — توقف سلف‌بات\n"
            "`/block` — مدیریت بلاک مخفی\n"
            "`/help` — همین راهنما\n\n"
            "━━ اکانت خودت (توی هر چت) ━━\n\n"
            "🎛 پنل:\n"
            "  `/panel` — پنل مدیریت\n\n"
            "⏰ ساعت:\n"
            "  `/clock on`/`off`\n"
            "  `/nfont [style]` — ۱۰ فونت ساعت\n\n"
            "🎨 فونت متن:\n"
            "  `/font` — لیست فونت‌ها\n"
            "  `/font bold متن` — تبدیل فونت\n"
            "  `/font set bold` — فونت خودکار\n"
            "  `/font off`\n\n"
            "🚫 بلاک مخفی:\n"
            "  `/ban @username` — بلاک\n"
            "  `/unban @username` — آنبلاک\n"
            "  `/banlist` — لیست\n\n"
            "🔇 سکوت:\n"
            "  `/mute @username` — سکوت\n"
            "  `/unmute @username`\n"
            "  `/mutelist`\n\n"
            "🔒 قفل پی‌وی:\n"
            "  `/pvlock on`/`off`\n\n"
            "🤖 منشی:\n"
            "  `/secretary متن` — تنظیم و روشن\n"
            "  `/secretary on`/`off`/`reset`\n\n"
            "📨 پاسخ خودکار:\n"
            "  `/rr متن` — تنظیم و روشن\n"
            "  `/rr on`/`off`\n\n"
            "⌨️ تایپینگ/بازی:\n"
            "  `/typing` — تایپینگ\n"
            "  `/game` — بازی\n\n"
            "🏷 گروه:\n"
            "  `/tag` `/pin` `/ping`\n\n"
            "🌐 ترجمه:\n"
            "  `/tr` `/tr en` `/tr ar`\n\n"
            "📨 پیام:\n"
            "  `/r 100 متن` — تکرار\n"
            "  `/del 100` — حذف\n\n"
            "🎲 سرگرمی:\n"
            "  `/dice` 🎲 `/bowl` 🎳\n\n"
            "━━━━━━━━━━━━━━━━━━━━━",
        )

    # ── setup ───────────────────────────────────
    @bot.on(events.CallbackQuery(data=b"setup"))
    async def cb_setup(event):
        await event.answer()
        conv[event.sender_id] = {"step": "phone"}
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
        await stop_sb(event.sender_id)
        await event.respond("⛔ سلف‌بات متوقف شد.\nاسم اصلی برگردانده شد.", buttons=main_kb(event.sender_id))

    @bot.on(events.CallbackQuery(data=b"restart"))
    async def cb_restart(event):
        await event.answer("⏳")
        uid = event.sender_id
        if uid in tasks and not tasks[uid].done():
            tasks[uid].cancel()
            await asyncio.sleep(2)
        uid_s = str(uid)
        if uid_s in db:
            db[uid_s]["active"] = True
            save_user(uid_s)
            await start_sb(uid, bot)
            await event.respond("🔄 سلف‌بات فعال شد!", buttons=main_kb(uid))
        else:
            await event.respond("❌ اطلاعاتی نیست.\n/start رو بزن.")

    @bot.on(events.CallbackQuery(data=b"ask_delete"))
    async def cb_ask_delete(event):
        await event.answer()
        await event.respond(
            "⚠️ مطمئنی؟ تمام اطلاعات حذف میشه.",
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
        uid_s = str(uid)
        delete_user(uid_s)
        db.pop(uid_s, None)
        await event.respond("🗑 تمام اطلاعات حذف شد.\n/start رو بزن.")

    @bot.on(events.CallbackQuery(data=b"back"))
    async def cb_back(event):
        await event.answer()
        await event.respond(status_text(event.sender_id), buttons=main_kb(event.sender_id))

    # ── panel ───────────────────────────────────
    @bot.on(events.CallbackQuery(data=b"panel"))
    async def cb_panel(event):
        await event.answer()
        uid = event.sender_id
        await event.respond(panel_text(uid), buttons=panel_kb(uid))

    @bot.on(events.CallbackQuery(data=b"p_clock"))
    async def cb_p_clock(event):
        uid = event.sender_id
        uid_s = str(uid)
        if uid_s not in db: return
        db[uid_s]["clock_enabled"] = not db[uid_s].get("clock_enabled", True)
        save_user(uid_s)
        await event.answer("✅")
        await event.respond(panel_text(uid), buttons=panel_kb(uid))

    @bot.on(events.CallbackQuery(data=b"p_nfont"))
    async def cb_p_nfont(event):
        uid = event.sender_id
        uid_s = str(uid)
        if uid_s not in db: return
        current = db[uid_s].get("name_font_style", "normal")
        idx = NAME_FONT_ORDER.index(current) if current in NAME_FONT_ORDER else 0
        nxt = NAME_FONT_ORDER[(idx + 1) % len(NAME_FONT_ORDER)]
        db[uid_s]["name_font_style"] = nxt
        save_user(uid_s)
        await event.answer(f"🎨 {nxt}")
        await event.respond(panel_text(uid), buttons=panel_kb(uid))

    @bot.on(events.CallbackQuery(data=b"p_ar"))
    async def cb_p_ar(event):
        uid = event.sender_id
        uid_s = str(uid)
        if uid_s not in db: return
        db[uid_s]["auto_reply_enabled"] = not db[uid_s].get("auto_reply_enabled", False)
        save_user(uid_s)
        await event.answer("✅")
        await event.respond(panel_text(uid), buttons=panel_kb(uid))

    @bot.on(events.CallbackQuery(data=b"p_sec"))
    async def cb_p_sec(event):
        uid = event.sender_id
        uid_s = str(uid)
        if uid_s not in db: return
        db[uid_s]["secretary_enabled"] = not db[uid_s].get("secretary_enabled", False)
        save_user(uid_s)
        await event.answer("✅")
        await event.respond(panel_text(uid), buttons=panel_kb(uid))

    @bot.on(events.CallbackQuery(data=b"p_pv"))
    async def cb_p_pv(event):
        uid = event.sender_id
        uid_s = str(uid)
        if uid_s not in db: return
        db[uid_s]["pv_lock"] = not db[uid_s].get("pv_lock", False)
        save_user(uid_s)
        await event.answer("✅")
        await event.respond(panel_text(uid), buttons=panel_kb(uid))

    @bot.on(events.CallbackQuery(data=b"p_typ"))
    async def cb_p_typ(event):
        uid = event.sender_id
        uid_s = str(uid)
        if uid_s not in db: return
        c = clients.get(uid)
        if db[uid_s].get("typing_mode"):
            db[uid_s]["typing_mode"] = False
            if uid in action_tasks:
                action_tasks[uid].cancel()
                action_tasks.pop(uid, None)
        else:
            db[uid_s]["typing_mode"] = True
            db[uid_s]["game_mode"] = False
            if uid in action_tasks:
                action_tasks[uid].cancel()
                action_tasks.pop(uid, None)
            if c:
                action_tasks[uid] = asyncio.create_task(_action_worker(uid, c, "typing"))
        save_user(uid_s)
        await event.answer("✅")
        await event.respond(panel_text(uid), buttons=panel_kb(uid))

    @bot.on(events.CallbackQuery(data=b"p_gam"))
    async def cb_p_gam(event):
        uid = event.sender_id
        uid_s = str(uid)
        if uid_s not in db: return
        c = clients.get(uid)
        if db[uid_s].get("game_mode"):
            db[uid_s]["game_mode"] = False
            if uid in action_tasks:
                action_tasks[uid].cancel()
                action_tasks.pop(uid, None)
        else:
            db[uid_s]["game_mode"] = True
            db[uid_s]["typing_mode"] = False
            if uid in action_tasks:
                action_tasks[uid].cancel()
                action_tasks.pop(uid, None)
            if c:
                action_tasks[uid] = asyncio.create_task(_action_worker(uid, c, "game"))
        save_user(uid_s)
        await event.answer("✅")
        await event.respond(panel_text(uid), buttons=panel_kb(uid))

    # ── settings ────────────────────────────────
    @bot.on(events.CallbackQuery(data=b"settings"))
    async def cb_settings(event):
        await event.answer()
        uid = event.sender_id
        u = db.get(str(uid), {})
        base = u.get("base_name") or u.get("orig_first", "...")
        await event.respond(
            f"━━━ ⚙️ تنظیمات ━━━\n\n"
            f"📛 اسم پایه: `{base}`\n"
            f"🌍 تایم‌زون: `{u.get('timezone', DEFAULT_TZ)}`\n"
            f"⏱ بازه آپدیت: هر `{u.get('update_interval', DEFAULT_INT)}` ثانیه\n"
            f"🔗 جداکننده: `{u.get('separator', ' | ')}`",
            buttons=[
                [Button.inline("📛 اسم پایه", b"set_name"), Button.inline("🌍 تایم‌زون", b"set_tz")],
                [Button.inline("⏱ بازه آپدیت", b"set_int"), Button.inline("🔗 جداکننده", b"set_sep")],
                [Button.inline("◀️ بازگشت", b"back")],
            ],
        )

    @bot.on(events.CallbackQuery(data=b"set_name"))
    async def cb_set_name(event):
        await event.answer()
        setting_mode[event.sender_id] = "name"
        await event.respond("📛 اسم جدید رو بفرست:\nمثال: `علی`", buttons=[[Button.inline("❌ لغو", b"cancel")]])

    @bot.on(events.CallbackQuery(data=b"set_tz"))
    async def cb_set_tz(event):
        await event.answer()
        setting_mode[event.sender_id] = "tz"
        await event.respond(
            "🌍 تایم‌زون رو بفرست:\n\n"
            "`Asia/Tehran`\n`Asia/Dubai`\n`Europe/London`\n"
            "`America/New_York`\n`Asia/Kabul`",
            buttons=[[Button.inline("❌ لغو", b"cancel")]],
        )

    @bot.on(events.CallbackQuery(data=b"set_int"))
    async def cb_set_int(event):
        await event.answer()
        setting_mode[event.sender_id] = "interval"
        await event.respond("⏱ بازه آپدیت رو به ثانیه بفرست:\nتوصیه: `60` — حداقل: `30`", buttons=[[Button.inline("❌ لغو", b"cancel")]])

    @bot.on(events.CallbackQuery(data=b"set_sep"))
    async def cb_set_sep(event):
        await event.answer()
        setting_mode[event.sender_id] = "sep"
        await event.respond(
            "🔗 جداکننده بین اسم و ساعت:\n\n"
            "` ǀ ` ` • ` ` — ` ` | ` ` ◆ `",
            buttons=[[Button.inline("❌ لغو", b"cancel")]],
        )

    # ── block menu ──────────────────────────────
    @bot.on(events.CallbackQuery(data=b"block_menu"))
    async def cb_block_menu(event):
        await event.answer()
        uid = event.sender_id
        await event.respond(block_text(uid), buttons=block_kb(uid))

    @bot.on(events.CallbackQuery(data=b"block_add"))
    async def cb_block_add(event):
        await event.answer()
        setting_mode[event.sender_id] = "block_add"
        await event.respond(
            "🚫 یوزرنیم (بدون @) یا آیدی عددی رو بفرست:\n\n"
            "مثال: `John_doe` یا `123456789`",
            buttons=[[Button.inline("❌ لغو", b"cancel")]],
        )

    @bot.on(events.CallbackQuery(pattern=rb"block_del:(-?\d+)"))
    async def cb_block_del(event):
        await event.answer("⏳")
        uid = event.sender_id
        ok, msg = await silent_unblock_user(uid, int(event.pattern_match.group(1)))
        await event.respond(msg)
        await event.respond(block_text(uid), buttons=block_kb(uid))

    # ── auto-reply menu ─────────────────────────
    @bot.on(events.CallbackQuery(data=b"ar_menu"))
    async def cb_ar_menu(event):
        await event.answer()
        uid = event.sender_id
        await event.respond(ar_info(uid), buttons=ar_kb(uid))

    @bot.on(events.CallbackQuery(data=b"ar_on"))
    async def cb_ar_on(event):
        await event.answer()
        uid = event.sender_id
        uid_s = str(uid)
        if not db.get(uid_s, {}).get("auto_reply_text"):
            await event.respond("❌ اول متن پاسخ رو تنظیم کن.")
            await event.respond(ar_info(uid), buttons=ar_kb(uid))
            return
        db[uid_s]["auto_reply_enabled"] = True
        save_user(uid_s)
        await event.respond("✅ پاسخ خودکار روشن شد.")
        await event.respond(ar_info(uid), buttons=ar_kb(uid))

    @bot.on(events.CallbackQuery(data=b"ar_off"))
    async def cb_ar_off(event):
        await event.answer()
        uid = event.sender_id
        db[str(uid)]["auto_reply_enabled"] = False
        save_user(str(uid))
        await event.respond("❌ پاسخ خودکار خاموش شد.")
        await event.respond(ar_info(uid), buttons=ar_kb(uid))

    @bot.on(events.CallbackQuery(data=b"ar_set_text"))
    async def cb_ar_set_text(event):
        await event.answer()
        setting_mode[event.sender_id] = "ar_text"
        await event.respond("✏️ متن پاسخ خودکار رو بفرست:", buttons=[[Button.inline("❌ لغو", b"cancel")]])

    @bot.on(events.CallbackQuery(data=b"ar_set_cd"))
    async def cb_ar_set_cd(event):
        await event.answer()
        setting_mode[event.sender_id] = "ar_cooldown"
        await event.respond(
            "⏱ کول‌داون رو به ثانیه بفرست:\n\n"
            "`3600` = هر ۱ ساعت\n`600` = هر ۱۰ دقیقه\n`0` = هر بار\n\n"
            "پیش‌فرض: `3600`",
            buttons=[[Button.inline("❌ لغو", b"cancel")]],
        )

    @bot.on(events.CallbackQuery(data=b"ar_clear"))
    async def cb_ar_clear(event):
        await event.answer("🗑")
        uid = event.sender_id
        uid_s = str(uid)
        if uid_s in db:
            db[uid_s]["auto_reply_sent_to"] = {}
            save_user(uid_s)
        await event.respond("🗑 تاریخچه پاسخ‌ها پاک شد.\n(به همه دوباره پاسخ داده میشه)")
        await event.respond(ar_info(uid), buttons=ar_kb(uid))

    # ── secretary menu ──────────────────────────
    @bot.on(events.CallbackQuery(data=b"sec_menu"))
    async def cb_sec_menu(event):
        await event.answer()
        uid = event.sender_id
        await event.respond(sec_info(uid), buttons=sec_kb(uid))

    @bot.on(events.CallbackQuery(data=b"sec_on"))
    async def cb_sec_on(event):
        await event.answer()
        uid = event.sender_id
        uid_s = str(uid)
        if not db.get(uid_s, {}).get("secretary_text"):
            await event.respond("❌ اول متن منشی رو تنظیم کن.")
            await event.respond(sec_info(uid), buttons=sec_kb(uid))
            return
        db[uid_s]["secretary_enabled"] = True
        save_user(uid_s)
        await event.respond("✅ منشی روشن شد.")
        await event.respond(sec_info(uid), buttons=sec_kb(uid))

    @bot.on(events.CallbackQuery(data=b"sec_off"))
    async def cb_sec_off(event):
        await event.answer()
        uid = event.sender_id
        db[str(uid)]["secretary_enabled"] = False
        save_user(str(uid))
        await event.respond("❌ منشی خاموش شد.")
        await event.respond(sec_info(uid), buttons=sec_kb(uid))

    @bot.on(events.CallbackQuery(data=b"sec_set_text"))
    async def cb_sec_set_text(event):
        await event.answer()
        setting_mode[event.sender_id] = "sec_text"
        await event.respond("✏️ متن منشی رو بفرست:", buttons=[[Button.inline("❌ لغو", b"cancel")]])

    @bot.on(events.CallbackQuery(data=b"sec_reset"))
    async def cb_sec_reset(event):
        await event.answer("🗑")
        uid = event.sender_id
        uid_s = str(uid)
        if uid_s in db:
            db[uid_s]["secretary_sent_to"] = {}
            save_user(uid_s)
        await event.respond("🗑 تاریخچه منشی پاک شد.\n(به همه دوباره پاسخ داده میشه)")
        await event.respond(sec_info(uid), buttons=sec_kb(uid))

    # ── text handler ────────────────────────────
    @bot.on(events.NewMessage(func=lambda e: e.is_private))
    async def on_text(event):
        uid = event.sender_id
        text = event.text.strip()
        if text.startswith("/"):
            return

        if uid in conv:
            step = conv[uid]["step"]
            if step == "phone":
                if not text.startswith("+") or len(text) < 10:
                    await event.respond("❌ فرمت اشتباه.\nمثال: `+989123456789`")
                    return
                conv[uid]["phone"] = text
                conv[uid]["step"] = "code"
                msg = await event.respond("⏳ در حال ارسال کد تایید...")
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
                    await msg.edit(f"❌ خطا: `{e}`\nشماره رو دوباره بفرست.")
                return

            if step == "code":
                code = text.replace(" ", "").replace("-", "")
                if not code.isdigit():
                    await event.respond("❌ کد باید عددی باشه.")
                    return
                tmp = conv[uid]["temp"]
                try:
                    await tmp.sign_in(phone=conv[uid]["phone"], code=code, phone_code_hash=conv[uid]["hash"])
                except Exception as e:
                    if "password" in str(e).lower():
                        conv[uid]["step"] = "2fa"
                        await event.respond("🔒 رمز دو مرحله‌ای رو بفرست:")
                        return
                    try:
                        await tmp.disconnect()
                    except Exception:
                        pass
                    conv.pop(uid, None)
                    await event.respond(f"❌ خطا: `{e}`\n/start رو بزن.")
                    return
                ss = tmp.session.save()
                try:
                    await tmp.disconnect()
                except Exception:
                    pass
                uid_s = str(uid)
                db[uid_s] = new_user_record(ss, conv[uid]["phone"])
                save_user(uid_s)
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
                uid_s = str(uid)
                db[uid_s] = new_user_record(ss, conv[uid]["phone"])
                save_user(uid_s)
                conv.pop(uid, None)
                await event.respond(
                    "━━━ ✅ سلف‌بات فعال شد! ━━━\n\n"
                    "اسمت هر دقیقه با ساعت آپدیت میشه!",
                    buttons=main_kb(uid),
                )
                await start_sb(uid, bot)
                return

        if uid in setting_mode:
            uid_s = str(uid)
            if uid_s not in db:
                setting_mode.pop(uid, None)
                return
            mode = setting_mode[uid]
            need_restart = True

            if mode == "name":
                db[uid_s]["base_name"] = text[:32]
                save_user(uid_s)
                await event.respond(f"✅ اسم پایه: `{text}`")
            elif mode == "tz":
                try:
                    pytz.timezone(text)
                except Exception:
                    await event.respond("❌ تایم‌زون نامعتبر.\nمثال: `Asia/Tehran`")
                    return
                db[uid_s]["timezone"] = text
                save_user(uid_s)
                await event.respond(f"✅ تایم‌زون: `{text}`")
            elif mode == "interval":
                if not text.isdigit() or int(text) < 30:
                    await event.respond("❌ حداقل ۳۰ ثانیه.")
                    return
                db[uid_s]["update_interval"] = int(text)
                save_user(uid_s)
                await event.respond(f"✅ بازه آپدیت: هر {text} ثانیه")
            elif mode == "sep":
                db[uid_s]["separator"] = text[:10]
                save_user(uid_s)
                await event.respond(f"✅ جداکننده: `{text}`")
            elif mode == "block_add":
                need_restart = False
                setting_mode.pop(uid, None)
                ok, msg = await silent_block_user(uid, text.lstrip("@").strip())
                await event.respond(msg)
                await event.respond(block_text(uid), buttons=block_kb(uid))
                return
            elif mode == "ar_text":
                need_restart = False
                db[uid_s]["auto_reply_text"] = text[:500]
                save_user(uid_s)
                setting_mode.pop(uid, None)
                await event.respond(f"✅ متن پاسخ خودکار تنظیم شد:\n\n`{text[:500]}`")
                await event.respond(ar_info(uid), buttons=ar_kb(uid))
                return
            elif mode == "ar_cooldown":
                need_restart = False
                if not text.isdigit():
                    await event.respond("❌ عدد بفرست.")
                    return
                db[uid_s]["auto_reply_cooldown"] = int(text)
                save_user(uid_s)
                setting_mode.pop(uid, None)
                await event.respond(f"✅ کول‌داون: `{text}` ثانیه")
                await event.respond(ar_info(uid), buttons=ar_kb(uid))
                return
            elif mode == "sec_text":
                need_restart = False
                db[uid_s]["secretary_text"] = text[:500]
                db[uid_s]["secretary_enabled"] = True
                save_user(uid_s)
                setting_mode.pop(uid, None)
                await event.respond(f"✅ متن منشی تنظیم شد:\n\n`{text[:500]}`")
                await event.respond(sec_info(uid), buttons=sec_kb(uid))
                return

            setting_mode.pop(uid, None)
            if need_restart:
                if uid in tasks and not tasks[uid].done():
                    tasks[uid].cancel()
                    await asyncio.sleep(2)
                db[uid_s]["active"] = True
                save_user(uid_s)
                await start_sb(uid, bot)
                await event.respond("✅ تنظیمات اعمال شد.", buttons=main_kb(uid))
            return

        await event.respond("/start رو بزن.", buttons=main_kb(uid))

    # ── bot commands ────────────────────────────
    @bot.on(events.NewMessage(pattern=r"/status"))
    async def cmd_status(event):
        uid = event.sender_id
        u = db.get(str(uid), {})
        if not u.get("session_string"):
            await event.respond("❌ سلف‌بات نداری.\n/start رو بزن.")
            return
        on = uid in tasks and not tasks[uid].done()
        now = datetime.now(pytz.timezone(u.get("timezone", DEFAULT_TZ))).strftime("%Y/%m/%d %H:%M")
        await event.respond(
            f"━━━ 📊 وضعیت سلف‌بات ━━━\n\n"
            f"وضعیت: {'✅ فعال' if on else '⏸ غیرفعال'}\n"
            f"📛 اسم: `{u.get('base_name') or u.get('orig_first', '...')}`\n"
            f"⏰ ساعت: {'✅' if u.get('clock_enabled', True) else '❌'} | 🎨 فونت: `{u.get('name_font_style', 'normal')}`\n"
            f"📨 پاسخ خودکار: {'✅' if u.get('auto_reply_enabled') else '❌'}\n"
            f"🤖 منشی: {'✅' if u.get('secretary_enabled') else '❌'}\n"
            f"🔒 قفل پی‌وی: {'✅' if u.get('pv_lock') else '❌'}\n"
            f"🔇 سکوت: `{len(u.get('muted_users', []))}` نفر\n"
            f"🚫 بلاک: `{len(u.get('silent_blocked', []))}` نفر\n"
            f"⌨️ تایپینگ: {'✅' if u.get('typing_mode') else '❌'} | 🎮 بازی: {'✅' if u.get('game_mode') else '❌'}\n"
            f"⏰ ساعت فعلی: {now}",
        )

    @bot.on(events.NewMessage(pattern=r"/stop"))
    async def cmd_stop(event):
        await stop_sb(event.sender_id)
        await event.respond("⛔ سلف‌بات متوقف شد.\nاسم اصلی برگردانده شد.")

    @bot.on(events.NewMessage(pattern=r"/block"))
    async def cmd_block(event):
        await event.respond(block_text(event.sender_id), buttons=block_kb(event.sender_id))

    if ADMIN_ID:
        @bot.on(events.NewMessage(from_users=ADMIN_ID, pattern=r"/stats"))
        async def cmd_stats(event):
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM users")
            total = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM users WHERE active = true")
            active = cur.fetchone()[0]
            cur.close()
            running = len([t for t in tasks.values() if not t.done()])
            await event.respond(
                f"━━━ 📊 آمار ادمین ━━━\n\n"
                f"👥 کل کاربران: {total}\n"
                f"✅ فعال: {active}\n"
                f"🔄 در حال اجرا: {running}\n"
                f"📨 پاسخ خودکار: {sum(1 for v in db.values() if v.get('auto_reply_enabled'))}\n"
                f"🤖 منشی: {sum(1 for v in db.values() if v.get('secretary_enabled'))}",
            )

    log.info("Bot ready!")
    await bot.run_until_disconnected()


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
    if not DATABASE_URL:
        log.error("DATABASE_URL لازمه! PostgreSQL اضافه کن.")
        sys.exit(1)
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()