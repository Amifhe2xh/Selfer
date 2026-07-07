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
block_handlers = {}
cmd_handlers = {}
autoreply_handlers = {}
CONTROL_BOT_ID = None

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
    "silent_blocked": [],
    "font_style": None,
    "font_auto": False,
    "auto_reply_enabled": False,
    "auto_reply_text": "",
    "auto_reply_cooldown": 3600,
    "auto_reply_sent_to": {},
}


def new_user_record(session_string, phone):
    rec = dict(NEW_USER_DEFAULTS)
    rec["session_string"] = session_string
    rec["phone"] = phone
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
    uid_s = str(uid)
    if uid in block_handlers:
        return

    async def _handler(event):
        try:
            if not event.is_private:
                return
            if event.sender_id in blocked_ids(uid_s):
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
# AUTO-REPLY ENGINE
# ═══════════════════════════════════════════════════
def register_autoreply_handler(uid, c):
    uid_s = str(uid)
    if uid in autoreply_handlers:
        return

    async def _handler(event):
        try:
            if not event.is_private:
                return
            if CONTROL_BOT_ID and event.sender_id == CONTROL_BOT_ID:
                return
            me_id = (await c.get_me()).id
            if event.sender_id == me_id:
                return
            sender = await event.get_sender()
            if getattr(sender, "bot", False):
                return
            u = db.get(uid_s, {})
            if not u.get("auto_reply_enabled") or not u.get("auto_reply_text"):
                return
            if event.sender_id in blocked_ids(uid_s):
                return
            now = time.time()
            sent_to = u.get("auto_reply_sent_to", {})
            cooldown = u.get("auto_reply_cooldown", 3600)
            last = sent_to.get(str(event.sender_id), 0)
            if now - last < cooldown:
                return
            try:
                await event.reply(u["auto_reply_text"])
            except FloodWaitError as e:
                log.warning(f"[{uid}] autoreply flood: {e.seconds}s")
                await asyncio.sleep(e.seconds + 2)
                try:
                    await event.reply(u["auto_reply_text"])
                except Exception:
                    pass
            except Exception as e:
                log.warning(f"[{uid}] autoreply send err: {e}")
                return
            sent_to[str(event.sender_id)] = now
            db[uid_s]["auto_reply_sent_to"] = sent_to
            save_db(db)
        except Exception as e:
            log.warning(f"[{uid}] autoreply handler err: {e}")

    c.add_event_handler(_handler, events.NewMessage(incoming=True))
    autoreply_handlers[uid] = _handler


def unregister_autoreply_handler(uid, c):
    handler = autoreply_handlers.pop(uid, None)
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
        await event.edit("❌ فقط توی گروه/کانال.")
        return
    try:
        participants = await event.client.get_participants(event.chat_id, aggressive=True)
    except Exception as e:
        await event.edit(f"❌ `{e}`")
        return
    mentions = [
        f"[{p.first_name or p.username or p.id}](tg://user?id={p.id})"
        for p in participants if not p.bot and not p.deleted
    ]
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
        await event.edit("❌ ریپلای کن + `/pin`")
        return
    try:
        await event.client.pin_message(event.chat_id, reply, notify=False)
        await event.edit("📌 پین شد.")
        await asyncio.sleep(2)
        await event.delete()
    except Exception as e:
        await event.edit(f"❌ `{e}`")


async def _cmd_ping(event):
    t0 = time.time()
    await event.edit("🏓 ...")
    ms = round((time.time() - t0) * 1000, 2)
    await event.edit(f"🏓 **Pong!** `{ms}ms`")


async def _cmd_font(uid, event, arg):
    uid_s = str(uid)
    args = arg.split(maxsplit=1)
    if not args:
        styles = "\n".join(f"• `{k}` — {v}" for k, v in FONT_LABELS.items())
        await event.edit(
            "🎨 **راهنمای فونت:**\n\n"
            "`/font <style> متن`\n`/font set <style>`\n`/font off`\n\n"
            f"{styles}"
        )
        return
    sub = args[0].lower()
    if sub == "off":
        db[uid_s]["font_auto"] = False
        save_db(db)
        await event.edit("✅ فونت خودکار خاموش.")
        return
    if sub == "set":
        style = args[1].strip() if len(args) > 1 else ""
        if style not in FONT_MAPS:
            await event.edit("❌ استایل نامعتبر.")
            return
        db[uid_s]["font_style"] = style
        db[uid_s]["font_auto"] = True
        save_db(db)
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
        await event.edit("❌ ریپلای کن + `/tr`")
        return
    await event.edit("🌐 ...")
    try:
        from deep_translator import GoogleTranslator
        translated = GoogleTranslator(source="auto", target=target).translate(reply.raw_text)
    except Exception as e:
        await event.edit(f"❌ `{e}`")
        return
    await event.edit(f"🌐 {translated}")


async def _cmd_del(event, arg):
    arg = arg.strip()
    if not arg.isdigit():
        await event.edit("❌ عدد بفرست.")
        return
    n = min(int(arg), 300)
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
        await event.edit("❌ `/r 100 سلام`")
        return
    count_str, text = args[0], args[1]
    if not count_str.isdigit():
        await event.edit("❌ تعداد عددی.")
        return
    count = int(count_str)
    if count < 1 or count > 500:
        await event.edit("❌ ۱ تا ۵۰۰.")
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
    arg = arg.strip().lower()
    if not arg:
        u = db.get(uid_s, {})
        on = u.get("auto_reply_enabled", False)
        txt = u.get("auto_reply_text", "")
        cd = u.get("auto_reply_cooldown", 3600)
        s = "✅ روشن" if on else "❌ خاموش"
        await event.edit(
            f"📨 پاسخ خودکار\n\nوضعیت: {s}\nمتن: `{txt or 'تنظیم نشده'}`\n"
            f"کول‌داون: `{cd}` ثانیه\n\n`/rr on` `/rr off` `/rr متن`"
        )
        return
    if arg == "on":
        if not db[uid_s].get("auto_reply_text"):
            await event.edit("❌ اول متن تنظیم کن.")
            return
        db[uid_s]["auto_reply_enabled"] = True
        save_db(db)
        await event.edit("✅ روشن شد.")
        return
    if arg == "off":
        db[uid_s]["auto_reply_enabled"] = False
        save_db(db)
        await event.edit("❌ خاموش شد.")
        return
    db[uid_s]["auto_reply_text"] = arg[:500]
    db[uid_s]["auto_reply_enabled"] = True
    save_db(db)
    await event.edit(f"✅ تنظیم شد:\n`{arg[:500]}`")


# ──── NEW: /ban and /unban from self-account ────
async def _cmd_ban(uid, event, arg):
    """Ban (silent block) a user. Usage: /ban @username or /ban 123456"""
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
    except Exception as e:
        log.warning(f"[{uid}] native block failed: {e}")
    lst.append({"id": tid, "name": name})
    save_db(db)
    await event.edit(f"🚫 `{name}` مسدود شد.\nپیام‌های جدیدش فوراً پاک میشن.")


async def _cmd_unban(uid, event, arg):
    """Unban (silent unblock) a user. Usage: /unban @username or /unban 123456"""
    uid_s = str(uid)
    target = arg.strip().lstrip("@")
    if not target:
        await event.edit("❌ فرمت: `/unban @username` یا `/unban 123456`")
        return
    c = clients.get(uid)
    lst = db.get(uid_s, {}).get("silent_blocked", [])
    # find by id or name
    target_id = None
    if target.lstrip("-").isdigit():
        target_id = int(target)
    else:
        for b in lst:
            if b["name"].lower() == target.lower() or target.lower() in b["name"].lower():
                target_id = b["id"]
                break
    if target_id is None:
        await event.edit("❌ توی لیست بلاک پیدا نشد.")
        return
    entry = next((b for b in lst if b["id"] == target_id), None)
    if not entry:
        await event.edit("❌ توی لیست بلاک پیدا نشد.")
        return
    if c:
        try:
            await c(UnblockRequest(id=target_id))
        except Exception as e:
            log.warning(f"[{uid}] native unblock failed: {e}")
    lst[:] = [b for b in lst if b["id"] != target_id]
    save_db(db)
    await event.edit(f"✅ `{entry['name']}` آنبلاک شد.")


async def _cmd_banlist(uid, event):
    """Show ban list from self-account. Usage: /banlist"""
    uid_s = str(uid)
    lst = db.get(uid_s, {}).get("silent_blocked", [])
    if not lst:
        await event.edit("🚫 لیست بلاک خالیه.\n\n`/ban @username` — بلاک\n`/unban @username` — آنبلاک")
        return
    names = "\n".join(f"• `{b['name']}` (`{b['id']}`)" for b in lst)
    await event.edit(f"━━━ 🚫 لیست بلاک ━━━\n\n{names}\n\n`/unban @username` — آنبلاک")


async def _cmd_help_self(uid, event):
    """Show help from self-account. Usage: /help"""
    await event.edit(
        "━━━ 📖 راهنما ━━━\n\n"
        "━━ گروه/کانال ━━\n"
        "`/tag [متن]` — تگ همه اعضا\n"
        "`/pin` (ریپلای) — پین پیام\n"
        "`/ping` — تست اتصال\n\n"
        "━━ فونت ━━\n"
        "`/font` — لیست فونت‌ها\n"
        "`/font bold متن` — تبدیل فونت\n"
        "`/font set bold` — فونت خودکار\n"
        "`/font off` — خاموش فونت خودکار\n\n"
        "━━ ترجمه ━━\n"
        "`/tr` (ریپلای) — ترجمه به فارسی\n"
        "`/tr en` (ریپلای) — ترجمه به انگلیسی\n\n"
        "━━ بلاک ━━\n"
        "`/ban @username` — بلاک مخفی\n"
        "`/unban @username` — آنبلاک\n"
        "`/banlist` — لیست بلاک‌ها\n\n"
        "━━ پیام ━━\n"
        "`/r 100 متن` — ارسال تکراری\n"
        "`/del 100` — حذف پیام‌ها\n\n"
        "━━ پاسخ خودکار ━━\n"
        "`/rr` — وضعیت\n"
        "`/rr on` / `/rr off`\n"
        "`/rr متن پیام` — تنظیم و روشن\n\n"
        "━━ استایل‌های فونت ━━\n"
        "`bold` `italic` `bold_italic`\n"
        "`script` `doublestruck` `fraktur`\n"
        "`monospace` `circled` `fullwidth`"
    )


# ═══════════════════════════════════════════════════
# COMMAND HANDLER REGISTRATION
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
                elif cmd == "/r":
                    await _cmd_repeat(event, arg)
                elif cmd == "/rr":
                    await _cmd_autoreply(uid, event, arg)
                elif cmd == "/ban":
                    await _cmd_ban(uid, event, arg)
                elif cmd == "/unban":
                    await _cmd_unban(uid, event, arg)
                elif cmd == "/banlist":
                    await _cmd_banlist(uid, event)
                elif cmd == "/help":
                    await _cmd_help_self(uid, event)
            except Exception as e:
                log.warning(f"[{uid}] cmd {cmd} error: {e}")
                try:
                    await event.edit(f"❌ `{e}`")
                except Exception:
                    pass
            return

        # auto-font
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
        return False, f"❌ پیدا نشد: `{e}`"
    tid = entity.id
    name = (getattr(entity, "first_name", "") or "") + " " + (getattr(entity, "last_name", "") or "")
    name = name.strip() or (getattr(entity, "username", "") or str(tid))
    lst = db[uid_s].setdefault("silent_blocked", [])
    if any(b["id"] == tid for b in lst):
        return False, f"⚠️ `{name}` از قبل مسدوده."
    try:
        await c(BlockRequest(id=entity))
    except Exception as e:
        log.warning(f"[{uid}] block failed: {e}")
    lst.append({"id": tid, "name": name})
    save_db(db)
    return True, f"🚫 `{name}` مسدود شد."


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
            log.warning(f"[{uid}] unblock failed: {e}")
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
        register_autoreply_handler(uid, c)

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
                log.info(f"[{uid}] -> {name}")
            except FloodWaitError as e:
                log.warning(f"[{uid}] Flood: {e.seconds}s")
                await asyncio.sleep(e.seconds + 5)
                continue
            except AuthKeyError:
                log.error(f"[{uid}] Session invalid!")
                db[uid_s]["active"] = False
                save_db(db)
                try:
                    await bot_ref.send_message(uid, "❌ سشن منقضی.\n/start بزن.")
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
            await bot_ref.send_message(uid, f"❌ `{e}`")
        except Exception:
            pass
    finally:
        unregister_block_handler(uid, c)
        unregister_command_handlers(uid, c)
        unregister_autoreply_handler(uid, c)
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
        unregister_autoreply_handler(uid, clients[uid])
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
    global CONTROL_BOT_ID
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
                [Button.inline("⚙️ تنظیمات", b"settings")],
                [Button.inline("🚫 بلاک مخفی", b"block_menu")],
                [Button.inline("📨 پاسخ خودکار", b"autoreply_menu")],
                [Button.inline("🗑 حذف", b"ask_delete")],
            ]
        return [
            [Button.inline("▶️ فعال‌سازی", b"restart")],
            [Button.inline("⚙️ تنظیمات", b"settings")],
            [Button.inline("🚫 بلاک مخفی", b"block_menu")],
            [Button.inline("📨 پاسخ خودکار", b"autoreply_menu")],
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
        lst = db.get(str(uid), {}).get("silent_blocked", [])
        rows = [[Button.inline("➕ افزودن", b"block_add")]]
        for b in lst[:20]:
            rows.append([Button.inline(f"❌ {b['name']}", f"block_del:{b['id']}".encode())])
        rows.append([Button.inline("◀️ بازگشت", b"back")])
        return rows

    def block_text(uid):
        lst = db.get(str(uid), {}).get("silent_blocked", [])
        if not lst:
            return "━━━ 🚫 بلاک مخفی ━━━\n\nکسی مسدود نیست."
        names = "\n".join(f"• {b['name']}" for b in lst)
        return f"━━━ 🚫 بلاک مخفی ━━━\n\n{names}"

    def ar_info(uid):
        u = db.get(str(uid), {})
        on = u.get("auto_reply_enabled", False)
        txt = u.get("auto_reply_text", "")
        cd = u.get("auto_reply_cooldown", 3600)
        cnt = len(u.get("auto_reply_sent_to", {}))
        s = "✅ روشن" if on else "❌ خاموش"
        return f"━━━ 📨 پاسخ خودکار ━━━\n\nوضعیت: {s}\nمتن: `{txt or 'تنظیم نشده'}`\nکول‌داون: `{cd}` ثانیه\nپاسخ‌ها: `{cnt}`"

    def ar_kb(uid):
        u = db.get(str(uid), {})
        on = u.get("auto_reply_enabled", False)
        return [
            [Button.inline("✏️ متن پاسخ", b"ar_set_text")],
            [Button.inline("⏱ کول‌داون", b"ar_set_cd")],
            [Button.inline("🔴 خاموش" if on else "🟢 روشن", b"ar_off" if on else b"ar_on")],
            [Button.inline("🗑 پاک کردن تاریخچه", b"ar_clear")],
            [Button.inline("◀️ بازگشت", b"back")],
        ]

    # ── /start ──────────────────────────────────
    @bot.on(events.NewMessage(pattern=r"/start"))
    async def cmd_start(event):
        uid = event.sender_id
        u = db.get(str(uid), {})
        if u.get("session_string"):
            await event.respond(f"{status_text(uid)}\n\nاز منو استفاده کن:", buttons=main_kb(uid))
        else:
            await event.respond(
                "━━━ 🤖 سلف‌بات ساز ━━━\n\nساعت توی اسم پروفایلت!\n\n✨ آپدیت خودکار\n🌍 تایم‌زون\n🚫 بلاک مخفی\n📨 پاسخ خودکار",
                buttons=main_kb(uid),
            )

    # ── /help (control bot) ─────────────────────
    @bot.on(events.NewMessage(pattern=r"/help"))
    async def cmd_help(event):
        await event.respond(
            "━━━ 📖 راهنمای کامل ━━━\n\n"
            "━━ ربات کنترل (این چت) ━━\n"
            "`/start` — منوی اصلی\n"
            "`/status` — وضعیت سلف‌بات\n"
            "`/stop` — توقف سلف‌بات\n"
            "`/block` — مدیریت بلاک مخفی\n"
            "`/help` — همین راهنما\n\n"
            "━━ اکانت خودت (توی هر چت) ━━\n\n"
            "📌 گروه:\n"
            "  `/tag [متن]` — تگ همه اعضا\n"
            "  `/pin` (ریپلای) — پین پیام\n"
            "  `/ping` — تست اتصال\n\n"
            "🎨 فونت:\n"
            "  `/font` — لیست فونت‌ها\n"
            "  `/font bold متن` — تبدیل فونت\n"
            "  `/font set bold` — فونت خودکار\n"
            "  `/font off` — خاموش\n\n"
            "🌐 ترجمه:\n"
            "  `/tr` (ریپلای) — ترجمه به فارسی\n"
            "  `/tr en` (ریپلای) — ترجمه به انگلیسی\n\n"
            "🚫 بلاک:\n"
            "  `/ban @username` — بلاک مخفی\n"
            "  `/unban @username` — آنبلاک\n"
            "  `/banlist` — لیست بلاک‌ها\n\n"
            "📨 پاسخ خودکار:\n"
            "  `/rr` — وضعیت\n"
            "  `/rr on` / `/rr off`\n"
            "  `/rr متن پیام` — تنظیم و روشن\n\n"
            "💬 پیام:\n"
            "  `/r 100 متن` — ارسال تکراری\n"
            "  `/del 100` — حذف پیام‌ها\n\n"
            "━━ استایل‌های فونت ━━\n"
            "`bold` `italic` `bold_italic` `script`\n"
            "`doublestruck` `fraktur` `monospace`\n"
            "`circled` `fullwidth`",
        )

    # ── setup ───────────────────────────────────
    @bot.on(events.CallbackQuery(data=b"setup"))
    async def cb_setup(event):
        await event.answer()
        conv[event.sender_id] = {"step": "phone"}
        await event.respond("📱 شماره تلفن:", buttons=[[Button.inline("❌ لغو", b"cancel")]])

    @bot.on(events.CallbackQuery(data=b"cancel"))
    async def cb_cancel(event):
        await event.answer("لغو")
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
        await event.respond("⛔ متوقف شد.", buttons=main_kb(event.sender_id))

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
            save_db(db)
            await start_sb(uid, bot)
            await event.respond("🔄 فعال!", buttons=main_kb(uid))
        else:
            await event.respond("❌ /start بزن.")

    @bot.on(events.CallbackQuery(data=b"ask_delete"))
    async def cb_ask_delete(event):
        await event.answer()
        await event.respond("⚠️ مطمئنی?", buttons=[
            [Button.inline("✅ بله", b"confirm_del")],
            [Button.inline("❌ نه", b"back")],
        ])

    @bot.on(events.CallbackQuery(data=b"confirm_del"))
    async def cb_confirm_del(event):
        await event.answer("🗑")
        uid = event.sender_id
        await stop_sb(uid)
        db.pop(str(uid), None)
        save_db(db)
        await event.respond("🗑 حذف شد.")

    @bot.on(events.CallbackQuery(data=b"back"))
    async def cb_back(event):
        await event.answer()
        await event.respond(status_text(event.sender_id), buttons=main_kb(event.sender_id))

    # ── settings ────────────────────────────────
    @bot.on(events.CallbackQuery(data=b"settings"))
    async def cb_settings(event):
        await event.answer()
        uid = event.sender_id
        u = db.get(str(uid), {})
        base = u.get("base_name") or u.get("orig_first", "...")
        await event.respond(
            f"━━━ ⚙️ تنظیمات ━━━\n\n"
            f"📛 `{base}`\n🌍 `{u.get('timezone', DEFAULT_TZ)}`\n"
            f"⏱ هر `{u.get('update_interval', DEFAULT_INT)}` ثانیه\n"
            f"🔗 `{u.get('separator', ' ǀ ')}`",
            buttons=[
                [Button.inline("📛 اسم", b"set_name"), Button.inline("🌍 تایم‌زون", b"set_tz")],
                [Button.inline("⏱ بازه", b"set_int"), Button.inline("🔗 جداکننده", b"set_sep")],
                [Button.inline("◀️ بازگشت", b"back")],
            ],
        )

    @bot.on(events.CallbackQuery(data=b"set_name"))
    async def cb_set_name(event):
        await event.answer()
        setting_mode[event.sender_id] = "name"
        await event.respond("📛 اسم جدید:", buttons=[[Button.inline("❌ لغو", b"cancel")]])

    @bot.on(events.CallbackQuery(data=b"set_tz"))
    async def cb_set_tz(event):
        await event.answer()
        setting_mode[event.sender_id] = "tz"
        await event.respond("🌍 تایم‌زون:\n`Asia/Tehran`\n`Asia/Dubai`\n`Europe/London`", buttons=[[Button.inline("❌ لغو", b"cancel")]])

    @bot.on(events.CallbackQuery(data=b"set_int"))
    async def cb_set_int(event):
        await event.answer()
        setting_mode[event.sender_id] = "interval"
        await event.respond("⏱ بازه (ثانیه): حداقل ۳۰", buttons=[[Button.inline("❌ لغو", b"cancel")]])

    @bot.on(events.CallbackQuery(data=b"set_sep"))
    async def cb_set_sep(event):
        await event.answer()
        setting_mode[event.sender_id] = "sep"
        await event.respond("🔗 جداکننده:", buttons=[[Button.inline("❌ لغو", b"cancel")]])

    # ── block menu ──────────────────────────────
    @bot.on(events.CallbackQuery(data=b"block_menu"))
    async def cb_block_menu(event):
        await event.answer()
        await event.respond(block_text(event.sender_id), buttons=block_kb(event.sender_id))

    @bot.on(events.CallbackQuery(data=b"block_add"))
    async def cb_block_add(event):
        await event.answer()
        setting_mode[event.sender_id] = "block_add"
        await event.respond("🚫 یوزرنیم یا آیدی:", buttons=[[Button.inline("❌ لغو", b"cancel")]])

    @bot.on(events.CallbackQuery(pattern=rb"block_del:(-?\d+)"))
    async def cb_block_del(event):
        await event.answer("⏳")
        uid = event.sender_id
        ok, msg = await silent_unblock_user(uid, int(event.pattern_match.group(1)))
        await event.respond(msg)
        await event.respond(block_text(uid), buttons=block_kb(uid))

    # ── auto-reply menu ─────────────────────────
    @bot.on(events.CallbackQuery(data=b"autoreply_menu"))
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
            await event.respond("❌ اول متن تنظیم کن.")
            await event.respond(ar_info(uid), buttons=ar_kb(uid))
            return
        db[uid_s]["auto_reply_enabled"] = True
        save_db(db)
        await event.respond("✅ روشن شد.")
        await event.respond(ar_info(uid), buttons=ar_kb(uid))

    @bot.on(events.CallbackQuery(data=b"ar_off"))
    async def cb_ar_off(event):
        await event.answer()
        uid = event.sender_id
        db[str(uid)]["auto_reply_enabled"] = False
        save_db(db)
        await event.respond("❌ خاموش شد.")
        await event.respond(ar_info(uid), buttons=ar_kb(uid))

    @bot.on(events.CallbackQuery(data=b"ar_set_text"))
    async def cb_ar_text(event):
        await event.answer()
        setting_mode[event.sender_id] = "ar_text"
        await event.respond("✏️ متن پاسخ:", buttons=[[Button.inline("❌ لغو", b"cancel")]])

    @bot.on(events.CallbackQuery(data=b"ar_set_cd"))
    async def cb_ar_cd(event):
        await event.answer()
        setting_mode[event.sender_id] = "ar_cooldown"
        await event.respond("⏱ کول‌داون (ثانیه):\n`3600` = ۱ ساعت", buttons=[[Button.inline("❌ لغو", b"cancel")]])

    @bot.on(events.CallbackQuery(data=b"ar_clear"))
    async def cb_ar_clear(event):
        await event.answer("🗑")
        uid = event.sender_id
        if str(uid) in db:
            db[str(uid)]["auto_reply_sent_to"] = {}
            save_db(db)
        await event.respond("🗑 تاریخچه پاک شد.")
        await event.respond(ar_info(uid), buttons=ar_kb(uid))

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
                    await event.respond("❌ فرمت: `+989123456789`")
                    return
                conv[uid]["phone"] = text
                conv[uid]["step"] = "code"
                msg = await event.respond("⏳ ...")
                tmp = TelegramClient(StringSession(), API_ID, API_HASH)
                await tmp.connect()
                conv[uid]["temp"] = tmp
                try:
                    res = await tmp.send_code_request(text)
                    conv[uid]["hash"] = res.phone_code_hash
                    await msg.edit("📨 کد تایید:")
                except Exception as e:
                    conv[uid]["step"] = "phone"
                    await tmp.disconnect()
                    await msg.edit(f"❌ `{e}`")
                return

            if step == "code":
                code = text.replace(" ", "").replace("-", "")
                if not code.isdigit():
                    await event.respond("❌ عددی.")
                    return
                tmp = conv[uid]["temp"]
                try:
                    await tmp.sign_in(phone=conv[uid]["phone"], code=code, phone_code_hash=conv[uid]["hash"])
                except Exception as e:
                    if "password" in str(e).lower():
                        conv[uid]["step"] = "2fa"
                        await event.respond("🔒 رمز دو مرحله‌ای:")
                        return
                    try:
                        await tmp.disconnect()
                    except Exception:
                        pass
                    conv.pop(uid, None)
                    await event.respond(f"❌ `{e}`")
                    return
                ss = tmp.session.save()
                try:
                    await tmp.disconnect()
                except Exception:
                    pass
                db[str(uid)] = new_user_record(ss, conv[uid]["phone"])
                save_db(db)
                conv.pop(uid, None)
                await event.respond("✅ سلف‌بات فعال!", buttons=main_kb(uid))
                await start_sb(uid, bot)
                return

            if step == "2fa":
                tmp = conv[uid]["temp"]
                try:
                    await tmp.sign_in(password=text)
                except Exception as e:
                    await event.respond(f"❌ `{e}`")
                    return
                ss = tmp.session.save()
                try:
                    await tmp.disconnect()
                except Exception:
                    pass
                db[str(uid)] = new_user_record(ss, conv[uid]["phone"])
                save_db(db)
                conv.pop(uid, None)
                await event.respond("✅ سلف‌بات فعال!", buttons=main_kb(uid))
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
                save_db(db)
                await event.respond(f"✅ اسم: `{text}`")
            elif mode == "tz":
                try:
                    pytz.timezone(text)
                except Exception:
                    await event.respond("❌ نامعتبر.")
                    return
                db[uid_s]["timezone"] = text
                save_db(db)
                await event.respond(f"✅ تایم‌زون: `{text}`")
            elif mode == "interval":
                if not text.isdigit() or int(text) < 30:
                    await event.respond("❌ حداقل ۳۰.")
                    return
                db[uid_s]["update_interval"] = int(text)
                save_db(db)
                await event.respond(f"✅ بازه: {text}s")
            elif mode == "sep":
                db[uid_s]["separator"] = text[:10]
                save_db(db)
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
                save_db(db)
                setting_mode.pop(uid, None)
                await event.respond("✅ متن تنظیم شد.")
                await event.respond(ar_info(uid), buttons=ar_kb(uid))
                return
            elif mode == "ar_cooldown":
                need_restart = False
                if not text.isdigit():
                    await event.respond("❌ عدد.")
                    return
                db[uid_s]["auto_reply_cooldown"] = int(text)
                save_db(db)
                setting_mode.pop(uid, None)
                await event.respond(f"✅ کول‌داون: `{text}`s")
                await event.respond(ar_info(uid), buttons=ar_kb(uid))
                return

            setting_mode.pop(uid, None)
            if need_restart:
                if uid in tasks and not tasks[uid].done():
                    tasks[uid].cancel()
                    await asyncio.sleep(2)
                db[uid_s]["active"] = True
                save_db(db)
                await start_sb(uid, bot)
                await event.respond("✅ اعمال شد.", buttons=main_kb(uid))
            return

        await event.respond("/start بزن.", buttons=main_kb(uid))

    # ── bot commands ────────────────────────────
    @bot.on(events.NewMessage(pattern=r"/status"))
    async def cmd_status(event):
        uid = event.sender_id
        u = db.get(str(uid), {})
        if not u.get("session_string"):
            await event.respond("❌ /start بزن.")
            return
        on = uid in tasks and not tasks[uid].done()
        now = datetime.now(pytz.timezone(u.get("timezone", DEFAULT_TZ))).strftime("%Y/%m/%d %H:%M")
        await event.respond(
            f"━━━ 📊 وضعیت ━━━\n\n"
            f"{'✅ فعال' if on else '⏸ غیرفعال'}\n"
            f"📛 `{u.get('base_name') or u.get('orig_first', '...')}`\n"
            f"🌍 `{u.get('timezone')}`\n⏱ هر `{u.get('update_interval')}`s\n"
            f"🚫 بلاک: `{len(u.get('silent_blocked', []))}`\n"
            f"📨 پاسخ خودکار: {'✅' if u.get('auto_reply_enabled') else '❌'}\n"
            f"⏰ {now}",
        )

    @bot.on(events.NewMessage(pattern=r"/stop"))
    async def cmd_stop(event):
        await stop_sb(event.sender_id)
        await event.respond("⛔ متوقف.")

    @bot.on(events.NewMessage(pattern=r"/block"))
    async def cmd_block(event):
        await event.respond(block_text(event.sender_id), buttons=block_kb(event.sender_id))

    if ADMIN_ID:
        @bot.on(events.NewMessage(from_users=ADMIN_ID, pattern=r"/stats"))
        async def cmd_stats(event):
            size = DB_FILE.stat().st_size if DB_FILE.exists() else 0
            await event.respond(
                f"━━━ 📊 آمار ━━━\n\n"
                f"👥 {len(db)} | ✅ {sum(1 for v in db.values() if v.get('active'))} | "
                f"🔄 {len([t for t in tasks.values() if not t.done()])} | "
                f"📨 {sum(1 for v in db.values() if v.get('auto_reply_enabled'))} | "
                f"💾 {size/1024:.1f}KB",
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
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()