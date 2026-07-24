import os
import sys
import json
import time
import asyncio
import logging
import random
import re
from datetime import datetime, timedelta
from collections import defaultdict
import aiohttp
import pytz
import psycopg2
import psycopg2.extras
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.tl.functions.account import UpdateProfileRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.functions.contacts import BlockRequest, UnblockRequest
from telethon.tl.functions.messages import SetTypingRequest, CheckChatInviteRequest
from telethon.tl.types import (
    SendMessageTypingAction,
    SendMessageGamePlayAction,
    InputMediaDice,
    UserStatusOnline,
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

AI_API_BASE = os.environ.get("AI_API_BASE", "https://hermes-railway-production-8d21.up.railway.app/v1")
AI_API_KEY  = os.environ.get("AI_API_KEY", "sk-6d104f6ab1112776-8seizg-1bb2a0b1")
AI_MODEL     = os.environ.get("AI_MODEL", "A")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("service")

AI_SYSTEM_DEFAULT = "تو یک منشی هوشمند هستی. به پیام‌هایی که بهت میرسه با ادب و حرفه‌ای جواب بده. جواب‌هات کوتاه و مفید باشه."


async def get_ai_response(system_prompt: str, user_message: str) -> str:
    """Call AI API and return the response text."""
    url = f"{AI_API_BASE}/chat/completions"
    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": AI_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt or AI_SYSTEM_DEFAULT},
            {"role": "user", "content": user_message},
        ],
        "max_tokens": 1024,
        "temperature": 0.7,
        "stream": False,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.error("AI API error %s: %s", resp.status, body[:200])
                    return "❌ مشکلی پیش اومد، لطفاً بعداً دوباره امتحان کن."
                raw = await resp.text()
                # strip SSE trailer if present
                if "data: [DONE]" in raw:
                    raw = raw[:raw.index("data: [DONE]")]
                data = json.loads(raw)
                return data["choices"][0]["message"]["content"].strip()
    except asyncio.TimeoutError:
        log.error("AI API timeout")
        return "⏳ پاسخ دیر شد، لطفاً دوباره امتحان کن."
    except Exception as e:
        log.error("AI API exception: %s", e)
        return "❌ مشکلی پیش اومد، لطفاً بعداً دوباره امتحان کن."


# ═══════════════════════════════════════════════════
# DIGITAL TWIN
# ═══════════════════════════════════════════════════
TWIN_QUESTIONS = [
    ("name", "اسمت چیه؟"),
    ("age", "چند سالته؟"),
    ("city", "کجایی هستی؟"),
    ("job", "شغلت چیه؟ (یا دانشجوی چی هستی؟)"),
    ("hobbies", "چه کارایی دوست داری انجام بدی؟"),
    ("fav_food", "غذای مورد علاقت؟"),
    ("fav_music", "چه موزیکی گوش میدی؟"),
    ("fav_movie", "فیلم یا سریال مورد علاقت؟"),
    ("personality", "چند تا صفت که خودت میدونی بنویس (مثلاً: شوخ، آرام، عصبانی)"),
    ("style", "سبک حرف زدنت چطوره؟ (صمیمی/رسمی/طنز/کوتاه)"),
    ("common_words", "چه کلماتی زیاد استفاده میکنی؟"),
    ("common_emojis", "ایموجی‌هایی که زیاد استفاده میکنی؟"),
    ("angry_at", "چه چیزی عصبانیت میکنه؟"),
    ("happy_at", "چه چیزی خوشحالیت میکنه？"),
    ("dream", "بزرگترین آرزوت چیه؟"),
    ("motto", "یه جمله خاص که زیاد تکرار میکنی بنویس"),
]


async def build_twin_prompt(uid_s):
    """Build system prompt from twin profile + chat analysis."""
    u = db.get(uid_s, {})
    profile = u.get("twin_profile", {})
    analysis = u.get("twin_analysis", "")

    if not profile and not analysis:
        return None

    lines = ["تو یک کلون دیجیتال هستی. باید دقیقاً مثل صاحبت صحبت کنی.", ""]

    if profile:
        lines.append("=== اطلاعات شخصی ===")
        for key, val in profile.items():
            if val:
                lines.append(f"- {key}: {val}")
        lines.append("")

    if analysis:
        lines.append("=== سبک صحبت (تحلیل شده از چت‌ها) ===")
        lines.append(analysis)
        lines.append("")

    lines.append("قوانین:")
    lines.append("- دقیقاً مثل صاحبت حرف بزن")
    lines.append("- همون کلمات و ایموجی‌ها رو استفاده کن")
    lines.append("- اگه صاحبت فارسی حرف میزنه، فارسی جواب بده")
    lines.append("- اگه صاحبت انگلیسی حرف میزنه، انگلیسی جواب بده")
    lines.append("- لحن و سبک صحبت دقیقاً مثل صاحبت باشه")

    return "\n".join(lines)


async def analyze_chat_messages(messages):
    """Analyze messages to extract speaking patterns."""
    if not messages:
        return ""

    texts = [m.text for m in messages if m.text and len(m.text) > 2]
    if not texts:
        return ""

    # common words (top 10)
    word_freq = {}
    for t in texts:
        for w in t.split():
            w = w.lower().strip("،.؟!():")
            if len(w) > 1:
                word_freq[w] = word_freq.get(w, 0) + 1
    common = sorted(word_freq.items(), key=lambda x: -x[1])[:10]

    # emojis
    import unicodedata
    emojis = []
    for t in texts:
        for ch in t:
            if unicodedata.category(ch) in ("So", "Sk"):
                emojis.append(ch)
    emoji_freq = {}
    for e in emojis:
        emoji_freq[e] = emoji_freq.get(e, 0) + 1
    top_emojis = sorted(emoji_freq.items(), key=lambda x: -x[1])[:5]

    # avg length
    avg_len = sum(len(t) for t in texts) // len(texts)

    # style detection
    has_informal = any(w in " ".join(texts).lower() for w in ["خخخ", "ااا", "یعنی", "بلا", "والا", "دیگه", "یورا"])
    has_english = any(w in " ".join(texts).lower() for w in ["lol", "ok", "hey", "thanks", "yes", "no"])

    lines = []
    if common:
        lines.append(f"کلمات پرتکرار: {', '.join(c[0] for c in common)}")
    if top_emojis:
        lines.append(f"ایموجی‌های مورد علاقه: {' '.join(e[0] for e in top_emojis)}")
    lines.append(f"میانگین طول پیام: {avg_len} کاراکتر")
    if has_informal:
        lines.append("سبک صحبت: غیررسمی و صمیمی")
    if has_english:
        lines.append("کلمات انگلیسی زیاد استفاده میکنه")
    if avg_len < 20:
        lines.append("پیام‌ها کوتاه و مختصر مینویسه")
    elif avg_len > 100:
        lines.append("پیام‌ها بلند و توضیحی مینویسه")

    return "\n".join(lines)


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
            uid                 BIGINT PRIMARY KEY,
            session_string      TEXT NOT NULL DEFAULT '',
            phone               TEXT NOT NULL DEFAULT '',
            base_name           TEXT NOT NULL DEFAULT '',
            timezone            TEXT NOT NULL DEFAULT 'Asia/Tehran',
            time_format         TEXT NOT NULL DEFAULT '%%H:%%M',
            update_interval     INT  NOT NULL DEFAULT 60,
            separator           TEXT NOT NULL DEFAULT ' | ',
            active              BOOLEAN NOT NULL DEFAULT TRUE,
            orig_first          TEXT NOT NULL DEFAULT '',
            orig_last           TEXT NOT NULL DEFAULT '',
            orig_about          TEXT NOT NULL DEFAULT '',
            silent_blocked      TEXT NOT NULL DEFAULT '[]',
            font_style          TEXT,
            font_auto           BOOLEAN NOT NULL DEFAULT FALSE,
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
            game_mode           BOOLEAN NOT NULL DEFAULT FALSE,
            keyword_filters     BOOLEAN NOT NULL DEFAULT FALSE,
            no_read             BOOLEAN NOT NULL DEFAULT FALSE,
            anti_delete         BOOLEAN NOT NULL DEFAULT FALSE,
            ar_multi_texts      TEXT NOT NULL DEFAULT '[]',
            ar_mode             TEXT NOT NULL DEFAULT 'single',
            notify_online       TEXT NOT NULL DEFAULT '[]'
        )
    """)
    new_columns = [
        ("keyword_filters", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("no_read", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("anti_delete", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("ar_multi_texts", "TEXT NOT NULL DEFAULT '[]'"),
        ("ar_mode", "TEXT NOT NULL DEFAULT 'single'"),
        ("notify_online", "TEXT NOT NULL DEFAULT '[]'"),
        ("secretary_ai", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("twin_enabled", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("twin_profile", "TEXT NOT NULL DEFAULT '{}'"),
        ("twin_analysis", "TEXT NOT NULL DEFAULT ''"),
        ("twin问卷_done", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ]
    for col_name, col_def in new_columns:
        try:
            cur.execute(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col_name} {col_def}")
        except Exception:
            pass
    cur.execute("""
        CREATE TABLE IF NOT EXISTS kw_filters (
            id          SERIAL PRIMARY KEY,
            uid         BIGINT NOT NULL,
            keyword     TEXT NOT NULL,
            response    TEXT NOT NULL,
            enabled     BOOLEAN NOT NULL DEFAULT TRUE
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_msgs (
            id          SERIAL PRIMARY KEY,
            uid         BIGINT NOT NULL,
            chat_id     BIGINT NOT NULL,
            text        TEXT NOT NULL,
            send_at     TIMESTAMP WITH TIME ZONE NOT NULL,
            sent        BOOLEAN NOT NULL DEFAULT FALSE,
            created_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS deleted_msgs (
            id          SERIAL PRIMARY KEY,
            uid         BIGINT NOT NULL,
            chat_id     BIGINT NOT NULL,
            sender_id   BIGINT,
            msg_id      INT,
            text        TEXT,
            ts          TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS auto_posts (
            id              SERIAL PRIMARY KEY,
            uid             BIGINT NOT NULL,
            chat_id         BIGINT NOT NULL,
            chat_name       TEXT NOT NULL DEFAULT '',
            text            TEXT NOT NULL,
            interval_min    INT NOT NULL DEFAULT 60,
            next_send       TIMESTAMP WITH TIME ZONE NOT NULL,
            enabled         BOOLEAN NOT NULL DEFAULT TRUE,
            created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
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
        d["silent_blocked"] = json.loads(d.get("silent_blocked", "[]"))
        d["auto_reply_sent_to"] = json.loads(d.get("auto_reply_sent_to", "{}"))
        d["secretary_sent_to"] = json.loads(d.get("secretary_sent_to", "{}"))
        d["muted_users"] = json.loads(d.get("muted_users", "[]"))
        d["ar_multi_texts"] = json.loads(d.get("ar_multi_texts", "[]"))
        d["notify_online"] = json.loads(d.get("notify_online", "[]"))
        result[uid_s] = d
    cur.close()

    cur2 = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur2.execute("SELECT * FROM kw_filters")
    for row in cur2.fetchall():
        uid_s = str(row["uid"])
        if uid_s in result:
            result[uid_s].setdefault("kw_list", []).append(dict(row))
    cur2.close()

    cur3 = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur3.execute("SELECT * FROM deleted_msgs ORDER BY ts DESC LIMIT 5000")
    for row in cur3.fetchall():
        uid_s = str(row["uid"])
        if uid_s in result:
            result[uid_s].setdefault("deleted_log", []).append(dict(row))
    cur3.close()

    cur4 = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur4.execute("SELECT * FROM scheduled_msgs ORDER BY send_at")
    for row in cur4.fetchall():
        uid_s = str(row["uid"])
        if uid_s in result:
            result[uid_s].setdefault("scheduled_list", []).append(dict(row))
    cur4.close()

    cur5 = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur5.execute("SELECT * FROM auto_posts ORDER BY id")
    for row in cur5.fetchall():
        uid_s = str(row["uid"])
        if uid_s in result:
            result[uid_s].setdefault("auto_post_list", []).append(dict(row))
    cur5.close()

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
            muted_users, pv_lock, typing_mode, game_mode,
            notify_online,
            secretary_ai,
            twin_enabled, twin_profile, twin_analysis, twin问卷_done
        ) VALUES (
            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
        ) ON CONFLICT (uid) DO UPDATE SET
            session_string=EXCLUDED.session_string, phone=EXCLUDED.phone,
            base_name=EXCLUDED.base_name, timezone=EXCLUDED.timezone,
            time_format=EXCLUDED.time_format, update_interval=EXCLUDED.update_interval,
            separator=EXCLUDED.separator, active=EXCLUDED.active,
            orig_first=EXCLUDED.orig_first, orig_last=EXCLUDED.orig_last,
            orig_about=EXCLUDED.orig_about, silent_blocked=EXCLUDED.silent_blocked,
            font_style=EXCLUDED.font_style, font_auto=EXCLUDED.font_auto,
            auto_reply_enabled=EXCLUDED.auto_reply_enabled, auto_reply_text=EXCLUDED.auto_reply_text,
            auto_reply_cooldown=EXCLUDED.auto_reply_cooldown, auto_reply_sent_to=EXCLUDED.auto_reply_sent_to,
            clock_enabled=EXCLUDED.clock_enabled, name_font_style=EXCLUDED.name_font_style,
            secretary_enabled=EXCLUDED.secretary_enabled, secretary_text=EXCLUDED.secretary_text,
            secretary_sent_to=EXCLUDED.secretary_sent_to, muted_users=EXCLUDED.muted_users,
            pv_lock=EXCLUDED.pv_lock, typing_mode=EXCLUDED.typing_mode,
            game_mode=EXCLUDED.game_mode, keyword_filters=EXCLUDED.keyword_filters,
            no_read=EXCLUDED.no_read, anti_delete=EXCLUDED.anti_delete,
            ar_multi_texts=EXCLUDED.ar_multi_texts, ar_mode=EXCLUDED.ar_mode,
            notify_online=EXCLUDED.notify_online,
            secretary_ai=EXCLUDED.secretary_ai,
            twin_enabled=EXCLUDED.twin_enabled, twin_profile=EXCLUDED.twin_profile,
            twin_analysis=EXCLUDED.twin_analysis, twin问卷_done=EXCLUDED.twin问卷_done
    """, (
        int(uid_s), u.get("session_string", ""), u.get("phone", ""),
        u.get("base_name", ""), u.get("timezone", DEFAULT_TZ),
        u.get("time_format", DEFAULT_FMT), u.get("update_interval", DEFAULT_INT),
        u.get("separator", " | "), u.get("active", True),
        u.get("orig_first", ""), u.get("orig_last", ""), u.get("orig_about", ""),
        json.dumps(u.get("silent_blocked", []), ensure_ascii=False),
        u.get("font_style"), u.get("font_auto", False),
        u.get("auto_reply_enabled", False), u.get("auto_reply_text", ""),
        u.get("auto_reply_cooldown", 3600),
        json.dumps(u.get("auto_reply_sent_to", {}), ensure_ascii=False),
        u.get("clock_enabled", True), u.get("name_font_style", "normal"),
        u.get("secretary_enabled", False), u.get("secretary_text", ""),
        json.dumps(u.get("secretary_sent_to", {}), ensure_ascii=False),
        json.dumps(u.get("muted_users", []), ensure_ascii=False),
        u.get("pv_lock", False), u.get("typing_mode", False),
        u.get("game_mode", False), u.get("keyword_filters", False),
        u.get("no_read", False), u.get("anti_delete", False),
        json.dumps(u.get("ar_multi_texts", []), ensure_ascii=False),
        u.get("ar_mode", "single"),
        json.dumps(u.get("notify_online", []), ensure_ascii=False),
        u.get("secretary_ai", False),
        u.get("twin_enabled", False),
        json.dumps(u.get("twin_profile", {}), ensure_ascii=False),
        u.get("twin_analysis", ""),
        u.get("twin问卷_done", False),
    ))
    cur.close()


def delete_user(uid_s):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE uid = %s", (int(uid_s),))
    cur.execute("DELETE FROM kw_filters WHERE uid = %s", (int(uid_s),))
    cur.execute("DELETE FROM scheduled_msgs WHERE uid = %s", (int(uid_s),))
    cur.execute("DELETE FROM deleted_msgs WHERE uid = %s", (int(uid_s),))
    cur.execute("DELETE FROM auto_posts WHERE uid = %s", (int(uid_s),))
    cur.close()


# ═══════════════════════════════════════════════════
# DB HELPERS
# ═══════════════════════════════════════════════════
def add_kw_filter(uid_s, keyword, response):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO kw_filters (uid, keyword, response, enabled) VALUES (%s,%s,%s,TRUE)",
                (int(uid_s), keyword, response))
    cur.close()


def del_kw_filter(uid_s, fid):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM kw_filters WHERE id = %s AND uid = %s", (fid, int(uid_s)))
    ok = cur.rowcount > 0
    cur.close()
    return ok


def toggle_kw_filter(uid_s, fid):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE kw_filters SET enabled = NOT enabled WHERE id = %s AND uid = %s", (fid, int(uid_s)))
    ok = cur.rowcount > 0
    cur.close()
    return ok


def find_kw_response(uid_s, text):
    filters = db.get(uid_s, {}).get("kw_list", [])
    if not filters:
        return None
    tl = text.lower()
    for f in filters:
        if f.get("enabled") and f.get("keyword", "").lower() in tl:
            return f["response"]
    return None


def add_scheduled_msg(uid_s, chat_id, text, send_at_dt):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO scheduled_msgs (uid, chat_id, text, send_at) VALUES (%s,%s,%s,%s)",
                (int(uid_s), chat_id, text, send_at_dt))
    cur.close()


def del_scheduled_msg(uid_s, mid):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM scheduled_msgs WHERE id = %s AND uid = %s AND sent = FALSE", (mid, int(uid_s)))
    ok = cur.rowcount > 0
    cur.close()
    return ok


def save_deleted_msg(uid_s, chat_id, sender_id, msg_id, text):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO deleted_msgs (uid, chat_id, sender_id, msg_id, text) VALUES (%s,%s,%s,%s,%s)",
                (int(uid_s), chat_id, sender_id, msg_id, text))
    cur.close()


def add_auto_post(uid_s, chat_id, chat_name, text, interval_min):
    next_send = datetime.now(pytz.UTC) + timedelta(minutes=interval_min)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO auto_posts (uid, chat_id, chat_name, text, interval_min, next_send, enabled) "
        "VALUES (%s,%s,%s,%s,%s,%s,TRUE)",
        (int(uid_s), chat_id, chat_name, text, interval_min, next_send))
    cur.close()


def del_auto_post(uid_s, pid):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM auto_posts WHERE id = %s AND uid = %s", (pid, int(uid_s)))
    ok = cur.rowcount > 0
    cur.close()
    return ok


def toggle_auto_post(uid_s, pid):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE auto_posts SET enabled = NOT enabled WHERE id = %s AND uid = %s", (pid, int(uid_s)))
    ok = cur.rowcount > 0
    cur.close()
    return ok


# ═══════════════════════════════════════════════════
# CHAT RESOLUTION
# ═══════════════════════════════════════════════════
def _entity_name(entity):
    if hasattr(entity, "title") and entity.title:
        return entity.title
    first = getattr(entity, "first_name", "") or ""
    last = getattr(entity, "last_name", "") or ""
    name = f"{first} {last}".strip()
    return name or getattr(entity, "username", "") or str(entity.id)


async def resolve_chat_entity(c, text):
    text = text.strip()

    # 1) آیدی عددی
    if text.lstrip("-").isdigit():
        try:
            entity = await c.get_entity(int(text))
            return entity, _entity_name(entity)
        except Exception:
            pass

    # 2) لینک دعوت (پرایوت)
    invite_hash = None
    for pat in [
        r"(?:https?://)?t\.me/\+([a-zA-Z0-9_-]+)",
        r"(?:https?://)?t\.me/joinchat/([a-zA-Z0-9_-]+)",
    ]:
        m = re.match(pat, text, re.IGNORECASE)
        if m:
            invite_hash = m.group(1)
            break

    if invite_hash:
        try:
            result = await c(CheckChatInviteRequest(invite_hash))
            if hasattr(result, "chat") and result.chat:
                return result.chat, result.chat.title
        except Exception:
            pass

    # 3) یوزرنیم یا لینک عمومی
    username = None
    for pat in [
        r"(?:https?://)?t\.me/([a-zA-Z_]\w{3,30})",
        r"(?:https?://)?telegram\.me/([a-zA-Z_]\w{3,30})",
        r"^@?([a-zA-Z_]\w{3,30})$",
    ]:
        m = re.match(pat, text, re.IGNORECASE)
        if m:
            username = m.group(1)
            break

    if username:
        try:
            entity = await c.get_entity(username)
            return entity, _entity_name(entity)
        except Exception:
            pass

    # 4) جستجو در دیالوگ‌ها
    query = text.lower()
    async for dialog in c.iter_dialogs():
        if dialog.title and query in dialog.title.lower():
            return dialog.entity, dialog.title

    return None, None


def parse_chat_link(text):
    text = text.strip()
    if text.lstrip("-").isdigit():
        return int(text), text
    patterns = [
        r"(?:https?://)?t\.me/([a-zA-Z_]\w{3,30})",
        r"(?:https?://)?telegram\.me/([a-zA-Z_]\w{3,30})",
        r"^@?([a-zA-Z_]\w{3,30})$",
    ]
    for pat in patterns:
        m = re.match(pat, text, re.IGNORECASE)
        if m:
            return m.group(1), m.group(1)
    return None, None


# ═══════════════════════════════════════════════════
# RUNTIME STATE
# ═══════════════════════════════════════════════════
db = {}
tasks = {}
clients = {}
conv = {}
setting_mode = {}
incoming_handlers = {}
cmd_handlers = {}
action_tasks = {}
delete_handlers = {}
online_handlers = {}
CONTROL_BOT_ID = None
_msg_buf = defaultdict(dict)
MAX_BUF = 500

NEW_USER_DEFAULTS = {
    "base_name": "", "timezone": DEFAULT_TZ, "time_format": DEFAULT_FMT,
    "update_interval": DEFAULT_INT, "separator": " | ", "active": True,
    "orig_first": "", "orig_last": "", "orig_about": "", "silent_blocked": [],
    "font_style": None, "font_auto": False,
    "auto_reply_enabled": False, "auto_reply_text": "", "auto_reply_cooldown": 3600,
    "auto_reply_sent_to": {}, "clock_enabled": True, "name_font_style": "normal",
    "secretary_enabled": False, "secretary_text": "", "secretary_sent_to": {},
    "secretary_ai": False,
    "twin_enabled": False, "twin_profile": {}, "twin_analysis": "", "twin问卷_done": False,
    "muted_users": [], "pv_lock": False, "typing_mode": False, "game_mode": False,
    "keyword_filters": False, "no_read": False, "anti_delete": False,
    "ar_multi_texts": [], "ar_mode": "single", "notify_online": [],
    "kw_list": [], "deleted_log": [], "scheduled_list": [], "auto_post_list": [],
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
    "normal": "Normal", "bold": "𝗕𝗼𝗹𝗱", "doublestruck": "𝔻𝕠𝕦𝕓𝕝𝕖",
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


def notify_ids(uid_s):
    return {n["id"] for n in db.get(uid_s, {}).get("notify_online", [])}


# ═══════════════════════════════════════════════════
# ACTION WORKER
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
# SCHEDULER WORKER
# ═══════════════════════════════════════════════════
async def _scheduler_worker():
    while True:
        try:
            conn = get_conn()
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cur.execute("SELECT * FROM scheduled_msgs WHERE sent = FALSE AND send_at <= NOW() ORDER BY send_at LIMIT 20")
            pending = cur.fetchall()
            cur.close()
            for msg in pending:
                uid_s = str(msg["uid"])
                c = clients.get(int(uid_s))
                if not c:
                    continue
                try:
                    target = msg["chat_id"] if msg["chat_id"] != 0 else "me"
                    await c.send_message(target, msg["text"])
                    cur2 = conn.cursor()
                    cur2.execute("UPDATE scheduled_msgs SET sent = TRUE WHERE id = %s", (msg["id"],))
                    cur2.close()
                    log.info(f"[{uid_s}] sched msg {msg['id']} sent")
                    if uid_s in db:
                        db[uid_s]["scheduled_list"] = [
                            m for m in db[uid_s].get("scheduled_list", []) if m["id"] != msg["id"]]
                except FloodWaitError as e:
                    await asyncio.sleep(e.seconds + 2)
                except Exception as e:
                    log.warning(f"[{uid_s}] sched err: {e}")
        except Exception as e:
            log.warning(f"scheduler err: {e}")
        await asyncio.sleep(30)


# ═══════════════════════════════════════════════════
# AUTO-POST WORKER
# ═══════════════════════════════════════════════════
async def _autopost_worker():
    while True:
        try:
            conn = get_conn()
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cur.execute("SELECT * FROM auto_posts WHERE enabled = TRUE AND next_send <= NOW() LIMIT 20")
            pending = cur.fetchall()
            cur.close()
            for post in pending:
                uid_s = str(post["uid"])
                c = clients.get(int(uid_s))
                if not c:
                    continue
                try:
                    await c.send_message(post["chat_id"], post["text"])
                    new_next = datetime.now(pytz.UTC) + timedelta(minutes=post["interval_min"])
                    cur2 = conn.cursor()
                    cur2.execute("UPDATE auto_posts SET next_send = %s WHERE id = %s",
                                 (new_next, post["id"]))
                    cur2.close()
                    log.info(f"[{uid_s}] autopost {post['id']} sent to {post['chat_name']}")
                except FloodWaitError as e:
                    await asyncio.sleep(e.seconds + 2)
                except Exception as e:
                    log.warning(f"[{uid_s}] autopost err: {e}")
                    cur3 = conn.cursor()
                    cur3.execute("UPDATE auto_posts SET enabled = FALSE WHERE id = %s", (post["id"],))
                    cur3.close()
        except Exception as e:
            log.warning(f"autopost worker err: {e}")
        await asyncio.sleep(30)


# ═══════════════════════════════════════════════════
# DELETE HANDLER
# ═══════════════════════════════════════════════════
def register_delete_handler(uid, c):
    uid_s = str(uid)
    if uid in delete_handlers:
        return

    async def _on_delete(event):
        try:
            u = db.get(uid_s, {})
            if not u.get("anti_delete"):
                return
            for mid in event.deleted_ids:
                info = _msg_buf.get(uid_s, {}).pop(mid, None)
                if info and info.get("text"):
                    save_deleted_msg(uid_s, info["chat_id"], info["sender_id"], mid, info["text"])
                    db[uid_s].setdefault("deleted_log", []).append({
                        "chat_id": info["chat_id"], "sender_id": info["sender_id"],
                        "msg_id": mid, "text": info["text"], "ts": datetime.now(pytz.UTC),
                    })
                    if len(db[uid_s]["deleted_log"]) > MAX_BUF:
                        db[uid_s]["deleted_log"] = db[uid_s]["deleted_log"][-MAX_BUF:]
        except Exception as e:
            log.warning(f"[{uid}] delete handler: {e}")

    c.add_event_handler(_on_delete, events.MessageDeleted)
    delete_handlers[uid] = _on_delete


def unregister_delete_handler(uid, c):
    handler = delete_handlers.pop(uid, None)
    if handler:
        try:
            c.remove_event_handler(handler, events.MessageDeleted)
        except Exception:
            pass


# ═══════════════════════════════════════════════════
# ONLINE HANDLER
# ═══════════════════════════════════════════════════
def register_online_handler(uid, c):
    uid_s = str(uid)
    if uid in online_handlers:
        return

    async def _on_update(event):
        try:
            if not isinstance(event.status, UserStatusOnline):
                return
            if event.user_id not in notify_ids(uid_s):
                return
            entity = None
            try:
                entity = await c.get_entity(event.user_id)
            except Exception:
                pass
            name = str(event.user_id)
            if entity:
                name = (getattr(entity, "first_name", "") or "") + " " + (getattr(entity, "last_name", "") or "")
                name = name.strip() or (getattr(entity, "username", "") or str(event.user_id))
            try:
                await c.send_message("me", f"🟢 **{name}** آنلاین شد!")
            except Exception:
                pass
        except Exception as e:
            log.warning(f"[{uid}] online handler: {e}")

    c.add_event_handler(_on_update, events.UserUpdate)
    online_handlers[uid] = _on_update


def unregister_online_handler(uid, c):
    handler = online_handlers.pop(uid, None)
    if handler:
        try:
            c.remove_event_handler(handler, events.UserUpdate)
        except Exception:
            pass


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
            u = db.get(uid_s, {})

            if u.get("anti_delete") and event.raw_text:
                _msg_buf[uid_s][event.id] = {
                    "text": event.raw_text,
                    "sender_id": sender_id,
                    "chat_id": event.chat_id,
                    "ts": time.time(),
                }
                if len(_msg_buf[uid_s]) > MAX_BUF:
                    oldest = sorted(_msg_buf[uid_s].keys())[:200]
                    for k in oldest:
                        del _msg_buf[uid_s][k]

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

            if u.get("secretary_enabled"):
                sent_to = u.get("secretary_sent_to", {})
                msg_text = event.raw_text or ""
                if u.get("secretary_ai"):
                    # AI mode — check if twin is active
                    if u.get("twin_enabled"):
                        twin_prompt = await build_twin_prompt(uid_s)
                        if twin_prompt:
                            ai_reply = await get_ai_response(
                                twin_prompt,
                                msg_text or "(کاربر بدون متن ارسال کرده)"
                            )
                        else:
                            ai_reply = await get_ai_response(
                                u.get("secretary_text", ""),
                                msg_text or "(کاربر بدون متن ارسال کرده)"
                            )
                    else:
                        ai_reply = await get_ai_response(
                            u.get("secretary_text", ""),
                            msg_text or "(کاربر بدون متن ارسال کرده)"
                        )
                    reply_text = ai_reply
                else:
                    reply_text = u.get("secretary_text", "")

                if reply_text:
                    try:
                        await event.reply(reply_text)
                    except FloodWaitError as e:
                        await asyncio.sleep(e.seconds + 2)
                        try:
                            await event.reply(reply_text)
                        except Exception:
                            pass
                    except Exception:
                        pass
                    return

            if u.get("keyword_filters") and event.raw_text:
                kw_resp = find_kw_response(uid_s, event.raw_text)
                if kw_resp:
                    try:
                        await event.reply(kw_resp)
                    except FloodWaitError as e:
                        await asyncio.sleep(e.seconds + 2)
                        try:
                            await event.reply(kw_resp)
                        except Exception:
                            pass
                    except Exception:
                        pass
                    return

            if u.get("auto_reply_enabled"):
                now = time.time()
                sent_to = u.get("auto_reply_sent_to", {})
                cooldown = u.get("auto_reply_cooldown", 3600)
                last = sent_to.get(str(sender_id), 0)
                if now - last < cooldown:
                    return
                reply_text = None
                if u.get("ar_mode") == "multi" and u.get("ar_multi_texts"):
                    reply_text = random.choice(u["ar_multi_texts"])
                else:
                    reply_text = u.get("auto_reply_text")
                if reply_text:
                    try:
                        await event.reply(reply_text)
                    except FloodWaitError as e:
                        await asyncio.sleep(e.seconds + 2)
                        try:
                            await event.reply(reply_text)
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
async def _cmd_chatid(event):
    chat = await event.get_chat()
    chat_id = chat.id
    if hasattr(chat, "megagroup") or hasattr(chat, "broadcast"):
        full_id = int(f"-100{chat_id}")
    else:
        full_id = chat_id
    title = getattr(chat, "title", None) or getattr(chat, "username", "") or str(chat_id)
    await event.edit(
        f"📋 **اطلاعات چت:**\n\n"
        f"📛 نام: `{title}`\n"
        f"🆔 آیدی: `{full_id}`\n\n"
        f"💡 آیدی رو برای پست خودکار یا زمانبندی استفاده کن:\n"
        f"`/apadd {full_id} | 30 | متن پیام`\n"
        f"`/schadd {full_id} | 2h | متن پیام`")


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
        await event.edit("❌ باید روی یه پیام ریپلای کنی.")
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
            "`/font <style> متن` — تبدیل\n"
            "`/font set <style>` — فونت خودکار\n"
            "`/font off` — خاموش\n\n"
            f"استایل‌ها:\n{styles}")
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
        await event.edit("❌ باید روی یه پیام متنی ریپلای کنی.")
        return
    await event.edit("🌐 در حال ترجمه...")
    try:
        from deep_translator import GoogleTranslator
        translated = GoogleTranslator(source="auto", target=target).translate(reply.raw_text)
    except Exception as e:
        await event.edit(f"❌ خطا: `{e}`")
        return
    await event.edit(f"🌐 **ترجمه:**\n\n{translated}")


async def _cmd_del(event, arg):
    if not arg.strip().isdigit():
        await event.edit("❌ عدد بفرست.")
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
        mode = u.get("ar_mode", "single")
        mode_s = "چندتایی تصادفی" if mode == "multi" else "تکی"
        multi_c = len(u.get("ar_multi_texts", []))
        s = "✅ روشن" if on else "❌ خاموش"
        await event.edit(
            f"📨 **پاسخ خودکار**\n\n"
            f"وضعیت: {s}\nحالت: `{mode_s}`\n"
            f"متن: `{txt or 'تنظیم نشده'}`\n"
            f"تعداد متن‌ها: `{multi_c}`\n"
            f"کول‌داون: `{cd}` ثانیه\n\n"
            "`/rr on` — روشن\n`/rr off` — خاموش\n"
            "`/rr متن` — تنظیم متن\n"
            "`/rrmulti م1 | م2 | م3` — چند پاسخ تصادفی")
        return
    if arg == "on":
        if not db[uid_s].get("auto_reply_text") and not db[uid_s].get("ar_multi_texts"):
            await event.edit("❌ اول متن پاسخ رو تنظیم کن.")
            return
        db[uid_s]["auto_reply_enabled"] = True
        save_user(uid_s)
        await event.edit("✅ پاسخ خودکار روشن شد.")
    elif arg == "off":
        db[uid_s]["auto_reply_enabled"] = False
        save_user(uid_s)
        await event.edit("❌ پاسخ خودکار خاموش شد.")
    else:
        db[uid_s]["auto_reply_text"] = arg[:500]
        db[uid_s]["ar_mode"] = "single"
        db[uid_s]["auto_reply_enabled"] = True
        save_user(uid_s)
        await event.edit(f"✅ پاسخ خودکار تنظیم شد:\n\n`{arg[:500]}`")


async def _cmd_rrmulti(uid, event, arg):
    uid_s = str(uid)
    arg = arg.strip()
    if not arg:
        u = db.get(uid_s, {})
        texts = u.get("ar_multi_texts", [])
        if not texts:
            await event.edit("📨 **پاسخ چندتایی تصادفی**\n\nتنظیم نشده.\n\n"
                             "مثال: `/rrmulti سلام | در دسترس نیستم | بعداً`")
        else:
            lst = "\n".join(f"• `{i+1}. {t}`" for i, t in enumerate(texts))
            await event.edit(f"📨 **پاسخ‌های چندتایی:**\n\n{lst}")
        return
    texts = [t.strip() for t in arg.split("|") if t.strip()]
    if len(texts) < 2:
        await event.edit("❌ حداقل ۲ متن با `|` جدا کن.")
        return
    db[uid_s]["ar_multi_texts"] = [t[:500] for t in texts]
    db[uid_s]["ar_mode"] = "multi"
    db[uid_s]["auto_reply_enabled"] = True
    save_user(uid_s)
    await event.edit(f"✅ `{len(texts)}` متن پاسخ تصادفی تنظیم شد.")


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
        await event.edit(f"❌ پیدا نشد: `{e}`")
        return
    tid = entity.id
    name = _entity_name(entity)
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
    await event.edit(f"🚫 `{name}` مسدود شد.")


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
        await event.edit("❌ پیدا نشد.")
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
        await event.edit("🚫 لیست بلاک خالیه.")
        return
    names = "\n".join(f"• `{b['name']}`" for b in lst)
    await event.edit(f"━━━ 🚫 لیست بلاک ━━━\n\n{names}")


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
        await event.edit(f"❌ پیدا نشد: `{e}`")
        return
    tid = entity.id
    name = _entity_name(entity)
    lst = db[uid_s].setdefault("muted_users", [])
    if any(b["id"] == tid for b in lst):
        await event.edit(f"⚠️ `{name}` از قبل ساکته.")
        return
    lst.append({"id": tid, "name": name})
    save_user(uid_s)
    await event.edit(f"🔇 `{name}` ساکت شد.")


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
        await event.edit("❌ پیدا نشد.")
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
        await event.edit("🔇 لیست سکوت خالیه.")
        return
    names = "\n".join(f"• `{b['name']}`" for b in lst)
    await event.edit(f"━━━ 🔇 لیست سکوت ━━━\n\n{names}")


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
        await event.edit("🔒 قفل پی‌وی روشن شد.")
    elif arg == "off":
        db[uid_s]["pv_lock"] = False
        save_user(uid_s)
        await event.edit("🔓 قفل پی‌وی خاموش شد.")


async def _cmd_twin(uid, event, arg):
    uid_s = str(uid)
    u = db.get(uid_s, {})
    arg = arg.strip().lower()

    if arg == "start" or arg == "شروع":
        # Start questionnaire
        db[uid_s]["_twin_q_step"] = 0
        db[uid_s]["_twin_q_answers"] = {}
        save_user(uid_s)
        key, question = TWIN_QUESTIONS[0]
        await event.reply(f"🤖 **ساخت Digital Twin**\n\n{question}\n\n(۱/{len(TWIN_QUESTIONS)})\n\nبرای رد کردن بنویس: skip\nبرای لغو بنویس: /twin cancel")
        return

    if arg == "cancel" or arg == "لغو":
        if "_twin_q_step" in db[uid_s]:
            del db[uid_s]["_twin_q_step"]
            del db[uid_s]["_twin_q_answers"]
            save_user(uid_s)
        await event.reply("❌ پرسشنامه لغو شد.")
        return

    if arg == "on":
        if u.get("twin_profile"):
            db[uid_s]["twin_enabled"] = True
            save_user(uid_s)
            await event.reply("🤖 Digital Twin فعال شد!\nحالا ربات مثل تو جواب میده.")
        else:
            await event.reply("⚠️ اول پرسشنامه رو پر کن: /twin start")
        return

    if arg == "off":
        db[uid_s]["twin_enabled"] = False
        save_user(uid_s)
        await event.reply("🔴 Digital Twin غیرفعال شد.")
        return

    if arg == "profile" or arg == "پروفایل":
        profile = u.get("twin_profile", {})
        if not profile:
            await event.reply("⚠️ هنوز پروفایلی ساخته نشده.\n/twin start")
            return
        lines = ["🤖 **پروفایل Digital Twin:**\n"]
        for key, val in profile.items():
            if val:
                lines.append(f"• **{key}**: {val}")
        analysis = u.get("twin_analysis", "")
        if analysis:
            lines.append(f"\n📊 **تحلیل چت:**\n{analysis}")
        lines.append(f"\nوضعیت: {'✅ فعال' if u.get('twin_enabled') else '❌ غیرفعال'}")
        await event.reply("\n".join(lines))
        return

    if arg == "scan" or arg == "اسکن":
        # Scan chat messages for analysis
        if not event.is_private:
            await event.reply("⚠️ این دستور فقط در پیوی ربات کار میکنه.")
            return
        await event.reply("🔍 در حال اسکن پیام‌ها...")
        try:
            messages = []
            async for msg in event.client.iter_messages(event.chat_id, limit=100):
                if msg.sender_id == uid:
                    messages.append(msg)
            analysis = await analyze_chat_messages(messages)
            if analysis:
                db[uid_s]["twin_analysis"] = analysis
                save_user(uid_s)
                await event.reply(f"✅ تحلیل انجام شد!\n\n📊 **نتیجه:**\n{analysis}")
            else:
                await event.reply("⚠️ پیام کافی برای تحلیل پیدا نشد.")
        except Exception as e:
            log.error("Twin scan error: %s", e)
            await event.reply("❌ خطا در اسکن پیام‌ها.")
        return

    if arg == "test" or arg == "تست":
        profile = u.get("twin_profile", {})
        if not profile:
            await event.reply("⚠️ اول پرسشنامه رو پر کن: /twin start")
            return
        prompt = await build_twin_prompt(uid_s)
        if prompt:
            await event.reply("🧪 **تست Digital Twin**\n\nیه پیام بفرست تا ببینی چطور جواب میده!")
            db[uid_s]["_twin_test_mode"] = True
            save_user(uid_s)
        else:
            await event.reply("⚠️ پروفایل ناقصه.")
        return

    if arg == "edit" or arg == "ویرایش":
        await event.reply(
            "📝 **ویرایش پروفایل:**\n\n"
            "برای ویرایش هر فیلد بنویس:\n"
            "`/twin set name امیر`\n"
            "`/twin set age 19`\n"
            "`/twin set personality شوخ، آرام`\n\n"
            "فیلدها: name, age, city, job, hobbies, fav_food, fav_music, fav_movie, "
            "personality, style, common_words, common_emojis, angry_at, happy_at, dream, motto"
        )
        return

    if arg.startswith("set "):
        parts = arg[4:].strip().split(" ", 1)
        if len(parts) == 2:
            key, val = parts
            profile = u.get("twin_profile", {})
            profile[key] = val
            db[uid_s]["twin_profile"] = profile
            save_user(uid_s)
            await event.reply(f"✅ `{key}` = `{val}` ذخیره شد.")
        else:
            await event.reply("❌ فرمت: `/twin set field value`")
        return

    # Default help
    await event.reply(
        "🤖 **Digital Twin — دستورات:**\n\n"
        "• `/twin start` — شروع پرسشنامه\n"
        "• `/twin scan` — اسکن پیام‌های چت\n"
        "• `/twin on` — فعال‌سازی\n"
        "• `/twin off` — غیرفعال\n"
        "• `/twin profile` — نمایش پروفایل\n"
        "• `/twin test` — تست شخصیت\n"
        "• `/twin edit` — ویرایش پروفایل\n"
        "• `/twin cancel` — لغو پرسشنامه"
    )


async def _cmd_secretary(uid, event, arg):
    uid_s = str(uid)
    arg = arg.strip()
    if not arg:
        u = db.get(uid_s, {})
        on = u.get("secretary_enabled", False)
        txt = u.get("secretary_text", "")
        cnt = len(u.get("secretary_sent_to", {}))
        ai = u.get("secretary_ai", False)
        await event.edit(
            f"🤖 **منشی**\n\n"
            f"وضعیت: {'✅ روشن' if on else '❌ خاموش'}\n"
            f"mode: {'🧠 AI' if ai else '📝 متن ثابت'}\n"
            f"متن/پرامپت: `{txt or 'تنظیم نشده'}`\n"
            f"پاسخ داده شده: `{cnt}` نفر\n\n"
            "`/secretary متن` — تنظیم و روشن\n"
            "`/secretary on` — روشن\n`/secretary off` — خاموش\n"
            "`/secretary ai on` — حالت AI\n`/secretary ai off` — حالت متن ثابت\n"
            "`/secretary reset` — ریست تاریخچه")
        return
    if arg.lower() == "ai on":
        db[uid_s]["secretary_ai"] = True
        if not db[uid_s].get("secretary_text"):
            db[uid_s]["secretary_text"] = AI_SYSTEM_DEFAULT
        db[uid_s]["secretary_enabled"] = True
        save_user(uid_s)
        await event.edit("🧠 حالت AI منشی روشن شد!\n\nمتن فعلی به عنوان system prompt استفاده میشه.\nبرای تغییر: `/secretary متن پرامپت جدید`")
        return
    if arg.lower() == "ai off":
        db[uid_s]["secretary_ai"] = False
        save_user(uid_s)
        await event.edit("📝 حالت متن ثابت منشی فعال شد.")
        return
    if arg == "on":
        if not db[uid_s].get("secretary_text"):
            await event.edit("❌ اول متن منشی رو تنظیم کن.")
            return
        db[uid_s]["secretary_enabled"] = True
        save_user(uid_s)
        await event.edit("✅ منشی روشن شد.")
    elif arg == "off":
        db[uid_s]["secretary_enabled"] = False
        save_user(uid_s)
        await event.edit("❌ منشی خاموش شد.")
    elif arg == "reset":
        db[uid_s]["secretary_sent_to"] = {}
        save_user(uid_s)
        await event.edit("🗑 تاریخچه منشی پاک شد.")
    else:
        db[uid_s]["secretary_text"] = arg[:500]
        db[uid_s]["secretary_enabled"] = True
        save_user(uid_s)
        mode = "🧠 AI" if db[uid_s].get("secretary_ai") else "📝 متن ثابت"
        await event.edit(f"✅ منشی تنظیم شد ({mode}):\n\n`{arg[:500]}`")


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
        await event.edit("⌨️ حالت تایپینگ روشن شد.")


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
        await event.edit("🎮 حالت بازی روشن شد.")


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
        await event.edit(f"⏰ ساعت: {'✅ روشن' if on else '❌ خاموش'}\n\n`/clock on` — روشن\n`/clock off` — خاموش")
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
                await c(UpdateProfileRequest(first_name=base, last_name=db[uid_s].get("orig_last", ""),
                                             about=db[uid_s].get("orig_about", "")))
            except Exception:
                pass
        await event.edit("❌ ساعت خاموش شد.")


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

    def st(key):
        return "✅ روشن" if u.get(key) else "❌ خاموش"

    clock = "✅ روشن" if u.get("clock_enabled", True) else "❌ خاموش"
    ar_mode = "چندتایی تصادفی" if u.get("ar_mode") == "multi" else "تکی"
    kw_c = len(u.get("kw_list", []))
    del_c = len(u.get("deleted_log", []))
    sched_c = len(u.get("scheduled_list", []))
    ap_c = len(u.get("auto_post_list", []))
    notif_c = len(u.get("notify_online", []))
    await event.edit(
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "       🎛 پنل مدیریت\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"━━ ⏰ ساعت و پروفایل ━━\n"
        f"⏰ ساعت: {clock}\n"
        f"🎨 فونت ساعت: `{u.get('name_font_style', 'normal')}`\n\n"
        f"━━ 💬 پاسخ‌گویی ━━\n"
        f"📨 پاسخ خودکار: {st('auto_reply_enabled')} (`{ar_mode}`)\n"
        f"🤖 منشی: {st('secretary_enabled')}\n"
        f"🔑 فیلتر کلمات: {st('keyword_filters')} (`{kw_c}` فیلتر)\n\n"
        f"━━ 🔒 حریم خصوصی ━━\n"
        f"🔒 قفل پی‌وی: {st('pv_lock')}\n"
        f"👀 بدون خواندن: {st('no_read')}\n"
        f"🗑 Anti-Delete: {st('anti_delete')} (`{del_c}` ذخیره)\n\n"
        f"━━ 📅 زمانبندی و پست ━━\n"
        f"📅 پیام زمانبندی: `{sched_c}` در صف\n"
        f"📢 پست خودکار گروه: `{ap_c}` فعال\n\n"
        f"━━ 🔔 اعلان و بلاک ━━\n"
        f"🔔 اعلان آنلاین: `{notif_c}` کاربر\n"
        f"🚫 بلاک: `{len(u.get('silent_blocked', []))}` نفر\n"
        f"🔇 سکوت: `{len(u.get('muted_users', []))}` نفر\n\n"
        f"━━ ⌨️ حالت‌ها ━━\n"
        f"⌨️ تایپینگ: {st('typing_mode')}\n"
        f"🎮 بازی: {st('game_mode')}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "━━ دستورات سریع ━━\n"
        "`/clock on/off` — ساعت\n"
        "`/nfont [style]` — فونت ساعت\n"
        "`/font` — فونت متن\n"
        "`/typing` — تایپینگ\n`/game` — بازی\n"
        "`/rr متن` — پاسخ خودکار\n"
        "`/rrmulti م1|m2|m3` — چند پاسخ تصادفی\n"
        "`/secretary متن` — منشی\n"
        "`/kwe on/off` `/kwa کلمه::متن` `/kwl`\n"
        "`/pvlock on/off` — قفل پی‌وی\n"
        "`/noread on/off` — بدون خواندن\n"
        "`/antidelete on/off` `/undelete`\n"
        "`/sched` `/schadd` `/schdel` — زمانبندی\n"
        "`/ap` `/apadd` `/apdel` `/aptoggle` — پست خودکار\n"
        "`/notif` `/notifadd` `/notifdel` — اعلان\n"
        "`/ban` `/unban` — بلاک مخفی\n"
        "`/mute` `/unmute` — سکوت\n"
        "`/chatid` — آیدی گروه/کانال\n"
        "`/tag` `/pin` `/ping` — گروه\n"
        "`/tr` — ترجمه\n"
        "`/r 100 متن` — تکرار\n`/del 100` — حذف\n"
        "`/dice` 🎲 `/bowl` 🎳\n"
        "`/help` — راهنمای کامل\n"
        "━━━━━━━━━━━━━━━━━━━━━━━")


async def _cmd_kwe(uid, event, arg):
    uid_s = str(uid)
    arg = arg.strip().lower()
    if not arg:
        on = db.get(uid_s, {}).get("keyword_filters", False)
        await event.edit(
            f"🔑 فیلتر کلمات: {'✅ روشن' if on else '❌ خاموش'}\n\n"
            "`/kwe on` — روشن\n`/kwe off` — خاموش\n"
            "`/kwa کلمه::متن` — افزودن\n`/kwl` — لیست\n`/kwd id` — حذف\n`/kt id` — toggle")
        return
    if arg == "on":
        db[uid_s]["keyword_filters"] = True
        save_user(uid_s)
        await event.edit("✅ فیلتر کلمات روشن شد.")
    elif arg == "off":
        db[uid_s]["keyword_filters"] = False
        save_user(uid_s)
        await event.edit("❌ فیلتر کلمات خاموش شد.")


async def _cmd_kwl(uid, event):
    uid_s = str(uid)
    filters = db.get(uid_s, {}).get("kw_list", [])
    if not filters:
        await event.edit("🔑 لیست فیلترها:\n\nخالی.\n\n`/kwa کلمه::متن` — افزودن")
        return
    lines = []
    for f in filters:
        st = "✅" if f.get("enabled") else "❌"
        lines.append(f"{st} ID=`{f['id']}` — `{f['keyword']}` → `{f['response'][:40]}`")
    await event.edit("━━━ 🔑 فیلترها ━━━\n\n" + "\n".join(lines) +
                     "\n\n`/kwd id` — حذف | `/kt id` — toggle")


async def _cmd_kwa(uid, event, arg):
    uid_s = str(uid)
    arg = arg.strip()
    if "::" not in arg:
        await event.edit("❌ فرمت: `/kwa کلمه::متن پاسخ`")
        return
    parts = arg.split("::", 1)
    keyword, response = parts[0].strip(), parts[1].strip()
    if not keyword or not response:
        await event.edit("❌ کلمه و متن هر دو لازمه.")
        return
    add_kw_filter(uid_s, keyword, response)
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute("SELECT * FROM kw_filters WHERE uid = %s ORDER BY id", (int(uid_s),))
    db[uid_s]["kw_list"] = [dict(r) for r in cur.fetchall()]
    cur.close()
    await event.edit(f"✅ فیلتر اضافه شد:\n`{keyword}` → `{response[:100]}`")


async def _cmd_kwd(uid, event, arg):
    uid_s = str(uid)
    if not arg.strip().isdigit():
        await event.edit("❌ فرمت: `/kwd شماره‌آیدی`")
        return
    fid = int(arg.strip())
    if del_kw_filter(uid_s, fid):
        db[uid_s]["kw_list"] = [f for f in db[uid_s].get("kw_list", []) if f["id"] != fid]
        await event.edit(f"✅ فیلتر `{fid}` حذف شد.")
    else:
        await event.edit("❌ پیدا نشد.")


async def _cmd_kt(uid, event, arg):
    uid_s = str(uid)
    if not arg.strip().isdigit():
        await event.edit("❌ فرمت: `/kt شماره‌آیدی`")
        return
    fid = int(arg.strip())
    if toggle_kw_filter(uid_s, fid):
        for f in db[uid_s].get("kw_list", []):
            if f["id"] == fid:
                f["enabled"] = not f.get("enabled", True)
                break
        await event.edit(f"✅ وضعیت فیلتر `{fid}` تغییر کرد.")
    else:
        await event.edit("❌ پیدا نشد.")


async def _cmd_sched(uid, event):
    uid_s = str(uid)
    scheduled = db.get(uid_s, {}).get("scheduled_list", [])
    if not scheduled:
        await event.edit(
            "📅 **پیام زمانبندی‌شده:**\n\nخالی.\n\n"
            "`/schadd chat_id | زمان | متن`\n"
            "مثال: `/schadd me | 2h | یادآوری`\n"
            "مثال: `/schadd -100123456 | 30m | سلام`")
        return
    lines = []
    for m in scheduled:
        send_at = m.get("send_at", "")
        if isinstance(send_at, datetime):
            send_at = send_at.strftime("%Y/%m/%d %H:%M")
        lines.append(f"• ID=`{m['id']}` | `{send_at}`\n  `{m['text'][:50]}`")
    await event.edit("━━━ 📅 زمانبندی‌شده ━━━\n\n" + "\n".join(lines) +
                     "\n\n`/schdel id` — حذف")


async def _cmd_schadd(uid, event, arg):
    uid_s = str(uid)
    args = [a.strip() for a in arg.split("|")]
    if len(args) < 3:
        await event.edit(
            "❌ فرمت: `/schadd chat_id | زمان | متن`\n\n"
            "مثال:\n"
            "• `/schadd me | 2h | یادآوری`\n"
            "• `/schadd -100123456 | 30m | سلام`\n"
            "• `/schadd @group | 1d2h | متن`\n\n"
            "💡 برای گروه پرایوت:\n"
            "توی گروه `/chatid` بزن و آیدی رو کپی کن")
        return
    chat_str, time_str, text = args[0], args[1], args[2]
    chat_id = 0
    if chat_str.lower() == "me":
        chat_id = 0
    elif chat_str.lstrip("-").isdigit():
        chat_id = int(chat_str)
    else:
        await event.edit("❌ chat_id باید عدد باشه یا `me`.\n\n💡 توی گروه `/chatid` بزن.")
        return
    send_at = None
    rel = re.match(r'^(\d+[hmd])+$', time_str.lower())
    if rel:
        total = 0
        for m in re.finditer(r'(\d+)([hmd])', time_str.lower()):
            val, unit = int(m.group(1)), m.group(2)
            total += val * {"h": 3600, "m": 60, "d": 86400}[unit]
        if total > 0:
            send_at = datetime.now(pytz.timezone(db.get(uid_s, {}).get("timezone", DEFAULT_TZ))) + timedelta(seconds=total)
    else:
        for fmt in ("%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M"):
            try:
                send_at = pytz.timezone(db.get(uid_s, {}).get("timezone", DEFAULT_TZ)).localize(datetime.strptime(time_str, fmt))
                break
            except Exception:
                pass
    if not send_at:
        await event.edit("❌ زمان نامعتبر.")
        return
    if not text.strip():
        await event.edit("❌ متن خالیه.")
        return
    add_scheduled_msg(uid_s, chat_id, text.strip(), send_at)
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute("SELECT * FROM scheduled_msgs WHERE uid = %s AND sent = FALSE ORDER BY send_at", (int(uid_s),))
    db[uid_s]["scheduled_list"] = [dict(r) for r in cur.fetchall()]
    cur.close()
    await event.edit(f"✅ زمانبندی شد!\n📅 `{send_at.strftime('%Y/%m/%d %H:%M')}`\n📝 `{text[:80]}`")


async def _cmd_schdel(uid, event, arg):
    uid_s = str(uid)
    if not arg.strip().isdigit():
        await event.edit("❌ فرمت: `/schdel شماره‌آیدی`")
        return
    if del_scheduled_msg(uid_s, int(arg.strip())):
        db[uid_s]["scheduled_list"] = [m for m in db[uid_s].get("scheduled_list", []) if m["id"] != int(arg.strip())]
        await event.edit("✅ حذف شد.")
    else:
        await event.edit("❌ پیدا نشد.")


async def _cmd_antidelete(uid, event, arg):
    uid_s = str(uid)
    arg = arg.strip().lower()
    if not arg:
        on = db.get(uid_s, {}).get("anti_delete", False)
        cnt = len(db.get(uid_s, {}).get("deleted_log", []))
        await event.edit(
            f"🗑 Anti-Delete: {'✅ روشن' if on else '❌ خاموش'}\n"
            f"ذخیره‌شده: `{cnt}`\n\n"
            "`/antidelete on` — روشن\n`/antidelete off` — خاموش\n"
            "`/undelete` — مشاهده")
        return
    if arg == "on":
        db[uid_s]["anti_delete"] = True
        save_user(uid_s)
        await event.edit("✅ Anti-Delete روشن شد.")
    elif arg == "off":
        db[uid_s]["anti_delete"] = False
        save_user(uid_s)
        await event.edit("❌ Anti-Delete خاموش شد.")


async def _cmd_undelete(uid, event):
    uid_s = str(uid)
    deleted = db.get(uid_s, {}).get("deleted_log", [])
    if not deleted:
        await event.edit("🗑 هیچ پیامی ذخیره نشده.")
        return
    recent = deleted[-10:]
    lines = []
    for d in reversed(recent):
        ts = d.get("ts", "")
        if isinstance(ts, datetime):
            ts = ts.strftime("%m/%d %H:%M")
        lines.append(f"• `{ts}` | `{d.get('sender_id','?')}`\n  `{(d.get('text') or '')[:60]}`")
    await event.edit(f"━━━ 🗑 حذف‌شده ━━━\n\n" + "\n".join(lines) + f"\n\nکل: `{len(deleted)}`")


async def _cmd_noread(uid, event, arg):
    uid_s = str(uid)
    arg = arg.strip().lower()
    if not arg:
        on = db.get(uid_s, {}).get("no_read", False)
        await event.edit(f"👀 بدون خواندن: {'✅ روشن' if on else '❌ خاموش'}\n\n`/noread on` — روشن\n`/noread off` — خاموش")
        return
    if arg == "on":
        db[uid_s]["no_read"] = True
        save_user(uid_s)
        await event.edit("👀 بدون خواندن روشن شد.")
    elif arg == "off":
        db[uid_s]["no_read"] = False
        save_user(uid_s)
        await event.edit("✅ بدون خواندن خاموش شد.")


async def _cmd_notif(uid, event):
    uid_s = str(uid)
    notify_list = db.get(uid_s, {}).get("notify_online", [])
    if not notify_list:
        await event.edit("🔔 **اعلان آنلاین**\n\nکاربری تنظیم نشده.\n\n`/notifadd @u` — افزودن")
        return
    names = "\n".join(f"• `{n['name']}` (ID: `{n['id']}`)" for n in notify_list)
    await event.edit(f"━━━ 🔔 کاربران تحت نظر ━━━\n\n{names}\n\n`/notifdel @u` — حذف")


async def _cmd_notifadd(uid, event, arg):
    uid_s = str(uid)
    target = arg.strip().lstrip("@")
    if not target:
        await event.edit("❌ فرمت: `/notifadd @username`")
        return
    c = clients.get(uid)
    if not c:
        await event.edit("❌ سلف‌بات فعال نیست.")
        return
    try:
        entity = await c.get_entity(int(target) if target.lstrip("-").isdigit() else target)
    except Exception as e:
        await event.edit(f"❌ پیدا نشد: `{e}`")
        return
    tid = entity.id
    name = _entity_name(entity)
    lst = db[uid_s].setdefault("notify_online", [])
    if any(n["id"] == tid for n in lst):
        await event.edit("⚠️ از قبل تحت نظره.")
        return
    lst.append({"id": tid, "name": name})
    save_user(uid_s)
    await event.edit(f"🔔 `{name}` اضافه شد.")


async def _cmd_notifdel(uid, event, arg):
    uid_s = str(uid)
    target = arg.strip().lstrip("@")
    if not target:
        await event.edit("❌ فرمت: `/notifdel @username`")
        return
    lst = db.get(uid_s, {}).get("notify_online", [])
    tid = int(target) if target.lstrip("-").isdigit() else None
    if tid is None:
        for n in lst:
            if target.lower() in n["name"].lower():
                tid = n["id"]
                break
    if tid is None:
        await event.edit("❌ پیدا نشد.")
        return
    entry = next((n for n in lst if n["id"] == tid), None)
    if not entry:
        await event.edit("❌ پیدا نشد.")
        return
    lst[:] = [n for n in lst if n["id"] != tid]
    save_user(uid_s)
    await event.edit(f"✅ `{entry['name']}` حذف شد.")


async def _cmd_ap(uid, event):
    uid_s = str(uid)
    posts = db.get(uid_s, {}).get("auto_post_list", [])
    if not posts:
        await event.edit(
            "📢 **پست خودکار گروه:**\n\nخالی.\n\n"
            "`/apadd @group | 30 | متن پیام`\n"
            "`/apadd -100123456 | 60 | متن`\n"
            "`/apadd t.me/+InviteHash | 120 | متن`\n\n"
            "💡 آیدی گروه پرایوت رو با `/chatid` توی گروه بگیر")
        return
    lines = []
    for p in posts:
        st = "✅" if p.get("enabled") else "❌"
        lines.append(f"{st} ID=`{p['id']}` | `{p['chat_name']}` | هر `{p['interval_min']}` دقیقه\n  `{p['text'][:50]}`")
    await event.edit("━━━ 📢 پست خودکار گروه ━━━\n\n" + "\n".join(lines) +
                     "\n\n`/apdel id` — حذف\n`/aptoggle id` — فعال/غیرفعال")


async def _cmd_apadd(uid, event, arg):
    uid_s = str(uid)
    args = [a.strip() for a in arg.split("|")]
    if len(args) < 3:
        await event.edit(
            "❌ فرمت: `/apadd لینک‌گروه | دقیقه | متن`\n\n"
            "مثال:\n"
            "• `/apadd @mygroup | 30 | سلام`\n"
            "• `/apadd t.me/+InviteHash | 60 | متن`\n"
            "• `/apadd -100123456 | 120 | متن`\n\n"
            "💡 آیدی گروه پرایوت:\n"
            "توی گروه `/chatid` بزن")
        return
    link_str, interval_str, text = args[0], args[1], args[2]
    if not interval_str.isdigit() or int(interval_str) < 1:
        await event.edit("❌ بازه زمانی باید عدد باشه (حداقل ۱ دقیقه).")
        return
    interval_min = int(interval_str)
    if not text.strip():
        await event.edit("❌ متن پیام خالیه.")
        return
    c = clients.get(uid)
    if not c:
        await event.edit("❌ سلف‌بات فعال نیست.")
        return

    entity, chat_name = await resolve_chat_entity(c, link_str)
    if not entity:
        await event.edit(
            "❌ گروه پیدا نشد!\n\n"
            "💡 **راهنما:**\n"
            "۱. توی گروه پرایوت `/chatid` بزن\n"
            "۲. آیدی عددی رو اینجا استفاده کن\n\n"
            "یا لینک دعوت: `t.me/+InviteHash`")
        return

    real_id = entity.id
    chat_name = _entity_name(entity)
    if hasattr(entity, "megagroup") or hasattr(entity, "broadcast"):
        real_id = int(f"-100{entity.id}")

    add_auto_post(uid_s, real_id, chat_name, text.strip(), interval_min)
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute("SELECT * FROM auto_posts WHERE uid = %s ORDER BY id", (int(uid_s),))
    db[uid_s]["auto_post_list"] = [dict(r) for r in cur.fetchall()]
    cur.close()
    await event.edit(
        f"✅ پست خودکار تنظیم شد!\n\n"
        f"📢 گروه: `{chat_name}`\n"
        f"⏱ هر `{interval_min}` دقیقه\n"
        f"📝 متن: `{text[:80]}`")


async def _cmd_apdel(uid, event, arg):
    uid_s = str(uid)
    if not arg.strip().isdigit():
        await event.edit("❌ فرمت: `/apdel شماره‌آیدی`")
        return
    pid = int(arg.strip())
    if del_auto_post(uid_s, pid):
        db[uid_s]["auto_post_list"] = [p for p in db[uid_s].get("auto_post_list", []) if p["id"] != pid]
        await event.edit(f"✅ پست `{pid}` حذف شد.")
    else:
        await event.edit("❌ پیدا نشد.")


async def _cmd_aptoggle(uid, event, arg):
    uid_s = str(uid)
    if not arg.strip().isdigit():
        await event.edit("❌ فرمت: `/aptoggle شماره‌آیدی`")
        return
    pid = int(arg.strip())
    if toggle_auto_post(uid_s, pid):
        for p in db[uid_s].get("auto_post_list", []):
            if p["id"] == pid:
                p["enabled"] = not p.get("enabled", True)
                break
        await event.edit(f"✅ وضعیت پست `{pid}` تغییر کرد.")
    else:
        await event.edit("❌ پیدا نشد.")


async def _cmd_help_self(uid, event):
    uid_s = str(uid)
    u = db.get(uid_s, {})

    def st(key):
        return "✅" if u.get(key) else "❌"

    clock_st = "✅" if u.get("clock_enabled", True) else "❌"
    await event.edit(
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "         📖 راهنمای کامل\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"━━ 🎛 پنل [`/panel`] ━━\n\n"

        f"━━ ⏰ ساعت در اسم [{clock_st}] ━━\n"
        "`/clock on` — روشن\n`/clock off` — خاموش\n"
        f"`/nfont [style]` — فونت ساعت (الان: `{u.get('name_font_style','normal')}`)\n"
        "استایل‌ها: `normal` `bold` `doublestruck` `monospace`\n"
        "`sans` `filled` `circled` `fullwidth` `cursive` `inverted`\n\n"

        "━━ 🎨 فونت متن ━━\n"
        "`/font` — لیست فونت‌ها و راهنما\n"
        "`/font bold متن` — تبدیل متن\n"
        "`/font set bold` — فونت خودکار\n`/font off` — خاموش\n\n"

        f"━━ 📨 پاسخ خودکار [{st('auto_reply_enabled')}] ━━\n"
        "`/rr متن` — تنظیم متن و روشن\n`/rr on` — روشن\n`/rr off` — خاموش\n`/rr` — وضعیت\n\n"

        "━━ 🎲 پاسخ چندتایی تصادفی ━━\n"
        "`/rrmulti متن1 | متن2 | متن3`\n"
        "هر بار یکی به صورت تصادفی ارسال میشه\n\n"

        f"━━ 🔑 فیلتر کلمات کلیدی [{st('keyword_filters')}] ━━\n"
        "`/kwe on` — روشن\n`/kwe off` — خاموش\n"
        "`/kwa کلمه::متن پاسخ` — افزودن فیلتر\n"
        "`/kwl` — لیست فیلترها\n`/kwd id` — حذف\n`/kt id` — فعال/غیرفعال\n\n"

        f"━━ 🤖 منشی [{st('secretary_enabled')}] ━━\n"
        "`/secretary متن پیام` — تنظیم و روشن\n"
        "`/secretary on` — روشن\n`/secretary off` — خاموش\n"
        "`/secretary ai on` — حالت AI 🧠\n`/secretary ai off` — حالت متن ثابت\n"
        "`/secretary reset` — ریست\n"
        "هر کاربر فقط یک‌بار پاسخ می‌گیره\n\n"

        f"━━ 🔒 قفل پی‌وی [{st('pv_lock')}] ━━\n"
        "`/pvlock on` — روشن (حذف تمام پیام‌های خصوصی)\n`/pvlock off` — خاموش\n\n"

        f"━━ 👀 بدون خواندن [{st('no_read')}] ━━\n"
        "`/noread on` — روشن (تیک دوم ارسال نمیشه)\n`/noread off` — خاموش\n\n"

        f"━━ 🗑 Anti-Delete [{st('anti_delete')}] ━━\n"
        "`/antidelete on` — ذخیره پیام‌های حذف‌شده\n`/antidelete off` — خاموش\n"
        "`/undelete` — مشاهده پیام‌های حذف‌شده\n\n"

        "━━ 📅 پیام زمانبندی‌شده ━━\n"
        "`/sched` — لیست پیام‌ها\n"
        "`/schadd chat_id | زمان | متن` — افزودن\n`/schdel id` — حذف\n"
        "فرمت زمان: `2h` `30m` `1d2h` `2025/01/15 10:30`\n"
        "💡 آیدی گروه پرایوت: توی گروه `/chatid` بزن\n\n"

        "━━ 📢 پست خودکار گروه ━━\n"
        "`/ap` — لیست پست‌های خودکار\n"
        "`/apadd @group | دقیقه | متن` — افزودن\n"
        "`/apdel id` — حذف\n`/aptoggle id` — فعال/غیرفعال\n"
        "مثال: `/apadd @mychannel | 60 | متن پیام`\n"
        "لینک دعوت: `/apadd t.me/+hash | 30 | سلام`\n"
        "آیدی عددی: `/apadd -100123456 | 120 | متن`\n\n"

        "━━ 🔔 اعلان آنلاین شدن ━━\n"
        "`/notif` — لیست کاربران\n`/notifadd @username` — افزودن\n`/notifdel @username` — حذف\n"
        "وقتی کاربر آنلاین بشه اعلان دریافت می‌کنی\n\n"

        "━━ 🚫 بلاک مخفی ━━\n"
        "`/ban @username` — بلاک مخفی\n`/ban 123456` — بلاک با آیدی\n"
        "`/unban @username` — آنبلاک\n`/banlist` — لیست\n\n"

        "━━ 🔇 سکوت کاربر ━━\n"
        "`/mute @username` — سکوت (حذف پیام در تمام چت‌ها)\n"
        "`/unmute @username` — خارج از سکوت\n`/mutelist` — لیست\n\n"

        f"━━ ⌨️ تایپینگ/بازی [{st('typing_mode')}/{st('game_mode')}] ━━\n"
        "`/typing` — روشن/خاموش تایپینگ (۳۰ چت اخیر)\n"
        "`/game` — روشن/خاموش بازی (۳۰ چت اخیر)\n"
        "این دو حالت یکدیگر رو غیرفعال می‌کنن\n\n"

        "━━ 🏷 گروه/کانال ━━\n"
        "`/chatid` — آیدی عددی گروه/کانال فعلی\n"
        "`/tag [متن]` — تگ کردن همه اعضا\n`/pin` (ریپلای) — پین پیام\n`/ping` — تست اتصال\n\n"

        "━━ 🌐 ترجمه ━━\n"
        "`/tr` (ریپلای) — فارسی\n`/tr en` — انگلیسی\n`/tr ar` — عربی\n\n"

        "━━ 📨 مدیریت پیام ━━\n"
        "`/r 100 متن` — ارسال ۱۰۰ بار (حداکثر ۵۰۰)\n`/del 100` — حذف ۱۰۰ پیام آخر (حداکثر ۳۰۰)\n\n"

        "━━ 🎲 سرگرمی ━━\n"
        "`/dice` — ارسال تاس 🎲\n`/bowl` — ارسال بولینگ 🎳\n\n"

        "━━ 📖 ربات کنترل (چت خصوصی با بات) ━━\n"
        "`/start` — منوی اصلی و ساخت سلف‌بات\n`/status` — وضعیت کامل\n"
        "`/stop` — توقف\n`/block` — مدیریت بلاک\n`/help` — راهنما\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━")


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

        # ── Digital Twin: questionnaire flow ──
        u_local = db.get(uid_s, {})
        if "_twin_q_step" in u_local and text and not text.startswith("/"):
            step = u_local["_twin_q_step"]
            answers = u_local.get("_twin_q_answers", {})
            if text.lower() == "skip":
                answers[TWIN_QUESTIONS[step][0]] = ""
            else:
                answers[TWIN_QUESTIONS[step][0]] = text
            step += 1
            if step >= len(TWIN_QUESTIONS):
                # questionnaire done
                db[uid_s]["twin_profile"] = answers
                db[uid_s]["twin问卷_done"] = True
                del db[uid_s]["_twin_q_step"]
                del db[uid_s]["_twin_q_answers"]
                save_user(uid_s)
                await event.reply("✅ پرسشنامه تموم شد!\n\n🤖 برای فعال‌سازی: `/twin on`\n🔍 برای اسکن چت: `/twin scan`")
            else:
                db[uid_s]["_twin_q_step"] = step
                db[uid_s]["_twin_q_answers"] = answers
                save_user(uid_s)
                key, question = TWIN_QUESTIONS[step]
                await event.reply(f"{question}\n\n({step+1}/{len(TWIN_QUESTIONS)})\n\nبرای رد کردن بنویس: skip")
            return

        # ── Digital Twin: test mode ──
        if u_local.get("_twin_test_mode") and text and not text.startswith("/"):
            prompt = await build_twin_prompt(uid_s)
            if prompt:
                reply = await get_ai_response(prompt, text)
                await event.reply(reply)
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
                elif cmd == "/rrmulti": await _cmd_rrmulti(uid, event, arg)
                elif cmd == "/ban":     await _cmd_ban(uid, event, arg)
                elif cmd == "/unban":   await _cmd_unban(uid, event, arg)
                elif cmd == "/banlist": await _cmd_banlist(uid, event)
                elif cmd == "/mute":    await _cmd_mute(uid, event, arg)
                elif cmd == "/unmute":  await _cmd_unmute(uid, event, arg)
                elif cmd == "/mutelist":await _cmd_mutelist(uid, event)
                elif cmd == "/pvlock":  await _cmd_pvlock(uid, event, arg)
                elif cmd == "/secretary": await _cmd_secretary(uid, event, arg)
                elif cmd == "/twin":     await _cmd_twin(uid, event, arg)
                elif cmd == "/typing":  await _cmd_typing(uid, event)
                elif cmd == "/game":    await _cmd_game(uid, event)
                elif cmd == "/dice":    await _cmd_dice(event)
                elif cmd == "/bowl":    await _cmd_bowl(event)
                elif cmd == "/panel":   await _cmd_panel(uid, event)
                elif cmd == "/clock":   await _cmd_clock(uid, event, arg)
                elif cmd == "/nfont":   await _cmd_nfont(uid, event, arg)
                elif cmd == "/help":    await _cmd_help_self(uid, event)
                elif cmd == "/kwe":     await _cmd_kwe(uid, event, arg)
                elif cmd == "/kwa":     await _cmd_kwa(uid, event, arg)
                elif cmd == "/kwl":     await _cmd_kwl(uid, event)
                elif cmd == "/kwd":     await _cmd_kwd(uid, event, arg)
                elif cmd == "/kt":      await _cmd_kt(uid, event, arg)
                elif cmd == "/sched":   await _cmd_sched(uid, event)
                elif cmd == "/schadd":  await _cmd_schadd(uid, event, arg)
                elif cmd == "/schdel":  await _cmd_schdel(uid, event, arg)
                elif cmd == "/antidelete": await _cmd_antidelete(uid, event, arg)
                elif cmd == "/undelete":   await _cmd_undelete(uid, event)
                elif cmd == "/noread":  await _cmd_noread(uid, event, arg)
                elif cmd == "/notif":   await _cmd_notif(uid, event)
                elif cmd == "/notifadd": await _cmd_notifadd(uid, event, arg)
                elif cmd == "/notifdel": await _cmd_notifdel(uid, event, arg)
                elif cmd == "/ap":      await _cmd_ap(uid, event)
                elif cmd == "/apadd":   await _cmd_apadd(uid, event, arg)
                elif cmd == "/apdel":   await _cmd_apdel(uid, event, arg)
                elif cmd == "/aptoggle": await _cmd_aptoggle(uid, event, arg)
                elif cmd == "/chatid":  await _cmd_chatid(event)
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
        return False, f"❌ پیدا نشد: `{e}`"
    tid = entity.id
    name = _entity_name(entity)
    lst = db[uid_s].setdefault("silent_blocked", [])
    if any(b["id"] == tid for b in lst):
        return False, f"⚠️ `{name}` از قبل مسدوده."
    try:
        await c(BlockRequest(id=entity))
    except Exception:
        pass
    lst.append({"id": tid, "name": name})
    save_user(uid_s)
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
        register_delete_handler(uid, c)
        register_online_handler(uid, c)

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
    finally:
        unregister_incoming_handler(uid, c)
        unregister_command_handlers(uid, c)
        unregister_delete_handler(uid, c)
        unregister_online_handler(uid, c)
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
        unregister_delete_handler(uid, clients[uid])
        unregister_online_handler(uid, clients[uid])
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

    asyncio.create_task(_scheduler_worker())
    asyncio.create_task(_autopost_worker())

    # ── UI helpers ──────────────────────────────
    def main_kb(uid):
        uid_s = str(uid)
        u = db.get(uid_s, {})
        if not u.get("session_string"):
            return [[Button.inline("🚀 ساخت سلف‌بات", b"setup")]]
        on = uid in tasks and not tasks[uid].done()
        rows = []
        if on:
            rows.append([Button.inline("⏹ توقف", b"stop"), Button.inline("🔄 ری‌استارت", b"restart")])
        else:
            rows.append([Button.inline("▶️ فعال‌سازی", b"restart")])
        rows += [
            [Button.inline("📊 وضعیت", b"status_btn"), Button.inline("🎛 پنل", b"panel")],
            [Button.inline("⚙️ تنظیمات", b"settings")],
            [Button.inline("📨 پاسخ خودکار", b"ar_menu"), Button.inline("🤖 منشی", b"sec_menu")],
            [Button.inline("🔑 فیلتر کلمات", b"kw_menu")],
            [Button.inline("🚫 بلاک مخفی", b"block_menu"), Button.inline("🔇 سکوت", b"mute_menu")],
            [Button.inline("🔒 قفل پی‌وی", b"pvlock_menu"), Button.inline("👀 بدون خواندن", b"nr_menu")],
            [Button.inline("🗑 Anti-Delete", b"ad_menu")],
            [Button.inline("📅 زمانبندی پیام", b"sched_menu")],
            [Button.inline("📢 پست خودکار گروه", b"ap_menu")],
            [Button.inline("🔔 اعلان آنلاین", b"notif_menu")],
            [Button.inline("🗑 حذف اطلاعات", b"ask_delete")],
        ]
        return rows

    def status_text(uid):
        uid_s = str(uid)
        u = db.get(uid_s, {})
        if not u.get("session_string"):
            return "❌ سلف‌بات نداری"
        on = uid in tasks and not tasks[uid].done()
        tz = u.get("timezone", DEFAULT_TZ)
        now = datetime.now(pytz.timezone(tz)).strftime("%H:%M")
        return f"⏰ {now} | {'✅ فعال' if on else '⏸ غیرفعال'}"

    def panel_text(uid):
        uid_s = str(uid)
        u = db.get(uid_s, {})

        def s(key):
            return "✅" if u.get(key) else "❌"

        clock = "✅" if u.get("clock_enabled", True) else "❌"
        ar_m = "چندتایی" if u.get("ar_mode") == "multi" else "تکی"
        return (
            "━━━ 🎛 پنل مدیریت ━━━\n\n"
            f"⏰ ساعت: {clock} | 🎨 فونت: `{u.get('name_font_style','normal')}`\n"
            f"📨 پاسخ: {s('auto_reply_enabled')} ({ar_m})\n"
            f"🤖 منشی: {s('secretary_enabled')} | 🔑 کلمات: {s('keyword_filters')}\n"
            f"🔒 PV: {s('pv_lock')} | 👀 ناخوانده: {s('no_read')}\n"
            f"🗑 Anti-Del: {s('anti_delete')} | ⌨️ تایپ: {s('typing_mode')} | 🎮 بازی: {s('game_mode')}")

    def panel_kb(uid):
        uid_s = str(uid)
        u = db.get(uid_s, {})

        def on(k):
            return u.get(k, False)

        def icon(k):
            return "✅" if on(k) else "❌"

        return [
            [Button.inline(f"⏰ ساعت: {icon('clock_enabled') if on('clock_enabled') is not False else '❌'}", b"p_clock"),
             Button.inline(f"🎨 {u.get('name_font_style','normal')}", b"p_nfont")],
            [Button.inline(f"📨 پاسخ: {icon('auto_reply_enabled')}", b"p_ar"),
             Button.inline(f"🤖 منشی: {icon('secretary_enabled')}", b"p_sec")],
            [Button.inline(f"🔑 کلمات: {icon('keyword_filters')}", b"p_kw")],
            [Button.inline(f"🔒 PV: {icon('pv_lock')}", b"p_pv"),
             Button.inline(f"👀 ناخوانده: {icon('no_read')}", b"p_nr")],
            [Button.inline(f"🗑 Anti-Del: {icon('anti_delete')}", b"p_ad")],
            [Button.inline(f"⌨️ تایپ: {icon('typing_mode')}", b"p_typ"),
             Button.inline(f"🎮 بازی: {icon('game_mode')}", b"p_gam")],
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
            return "━━━ 🚫 بلاک مخفی ━━━\n\nکسی مسدود نیست."
        names = "\n".join(f"• {b['name']}" for b in lst)
        return f"━━━ 🚫 بلاک مخفی ━━━\n\n{names}"

    def ar_info(uid):
        u = db.get(str(uid), {})
        mode_s = "چندتایی تصادفی" if u.get("ar_mode") == "multi" else "تکی"
        return (
            "━━━ 📨 پاسخ خودکار ━━━\n\n"
            f"وضعیت: {'✅' if u.get('auto_reply_enabled') else '❌'}\n"
            f"حالت: `{mode_s}`\n"
            f"متن: `{u.get('auto_reply_text') or 'تنظیم نشده'}`\n"
            f"تعداد متن‌ها: `{len(u.get('ar_multi_texts', []))}`\n"
            f"کول‌داون: `{u.get('auto_reply_cooldown', 3600)}` ثانیه")

    def ar_kb(uid):
        on = db.get(str(uid), {}).get("auto_reply_enabled", False)
        return [
            [Button.inline("✏️ متن", b"ar_set_text"), Button.inline("⏱ کول‌داون", b"ar_set_cd")],
            [Button.inline("🔴 خاموش" if on else "🟢 روشن", b"ar_off" if on else b"ar_on")],
            [Button.inline("🗑 پاک تاریخچه", b"ar_clear"), Button.inline("◀️ بازگشت", b"back")],
        ]

    def sec_info(uid):
        u = db.get(str(uid), {})
        return ("━━━ 🤖 منشی ━━━\n\n"
                f"وضعیت: {'✅' if u.get('secretary_enabled') else '❌'}\n"
                f"mode: {'🧠 AI' if u.get('secretary_ai') else '📝 متن ثابت'}\n"
                f"متن: `{u.get('secretary_text') or 'تنظیم نشده'}`\n"
                f"پاسخ داده: `{len(u.get('secretary_sent_to', {}))}` نفر")

    def sec_kb(uid):
        on = db.get(str(uid), {}).get("secretary_enabled", False)
        ai = db.get(str(uid), {}).get("secretary_ai", False)
        return [
            [Button.inline("✏️ متن منشی", b"sec_set_text")],
            [Button.inline("🧠 AI: " + ("✅" if ai else "❌"), b"sec_ai_off" if ai else b"sec_ai_on")],
            [Button.inline("🔴 خاموش" if on else "🟢 روشن", b"sec_off" if on else b"sec_on")],
            [Button.inline("🗑 ریست", b"sec_reset"), Button.inline("◀️ بازگشت", b"back")],
        ]

    def kw_info(uid):
        u = db.get(str(uid), {})
        return ("━━━ 🔑 فیلتر کلمات ━━━\n\n"
                f"وضعیت: {'✅' if u.get('keyword_filters') else '❌'}\n"
                f"تعداد فیلترها: `{len(u.get('kw_list', []))}`")

    def kw_kb(uid):
        on = db.get(str(uid), {}).get("keyword_filters", False)
        return [
            [Button.inline("➕ افزودن", b"kw_add")],
            [Button.inline("🔴 خاموش" if on else "🟢 روشن", b"kw_off" if on else b"kw_on")],
            [Button.inline("◀️ بازگشت", b"back")],
        ]

    def sched_info(uid):
        scheduled = db.get(str(uid), {}).get("scheduled_list", [])
        return ("━━━ 📅 زمانبندی پیام ━━━\n\n"
                f"در صف: `{len(scheduled)}`\n\n"
                "فرمت: `2h` `30m` `1d2h` `2025/01/15 10:30`")

    def sched_kb(uid):
        return [[Button.inline("➕ افزودن", b"sched_add")], [Button.inline("◀️ بازگشت", b"back")]]

    def ad_info(uid):
        u = db.get(str(uid), {})
        return ("━━━ 🗑 Anti-Delete ━━━\n\n"
                f"وضعیت: {'✅' if u.get('anti_delete') else '❌'}\n"
                f"ذخیره‌شده: `{len(u.get('deleted_log', []))}`")

    def ad_kb(uid):
        on = db.get(str(uid), {}).get("anti_delete", False)
        return [
            [Button.inline("🔴 خاموش" if on else "🟢 روشن", b"ad_off" if on else b"ad_on")],
            [Button.inline("👁 مشاهده", b"ad_view")],
            [Button.inline("◀️ بازگشت", b"back")],
        ]

    def nr_info(uid):
        u = db.get(str(uid), {})
        return ("━━━ 👀 بدون خواندن ━━━\n\n"
                f"وضعیت: {'✅' if u.get('no_read') else '❌'}\n\n"
                "وقتی روشن باشه، پیام‌ها بدون\n"
                "تیک دوم خوانده میشن.")

    def nr_kb(uid):
        on = db.get(str(uid), {}).get("no_read", False)
        return [
            [Button.inline("🔴 خاموش" if on else "🟢 روشن", b"nr_off" if on else b"nr_on")],
            [Button.inline("◀️ بازگشت", b"back")],
        ]

    def notif_info(uid):
        nl = db.get(str(uid), {}).get("notify_online", [])
        if not nl:
            return "━━━ 🔔 اعلان آنلاین ━━━\n\nکاربری تنظیم نشده."
        names = "\n".join(f"• `{n['name']}`" for n in nl)
        return f"━━━ 🔔 اعلان آنلاین ━━━\n\n{names}"

    def notif_kb(uid):
        return [
            [Button.inline("➕ افزودن", b"notif_add"), Button.inline("❌ حذف", b"notif_del")],
            [Button.inline("◀️ بازگشت", b"back")],
        ]

    def ap_info(uid):
        posts = db.get(str(uid), {}).get("auto_post_list", [])
        if not posts:
            return "━━━ 📢 پست خودکار گروه ━━━\n\nخالی.\n\nهر X دقیقه یه متن مشخص توی گروه ارسال میشه."
        lines = []
        for p in posts:
            st = "✅" if p.get("enabled") else "❌"
            lines.append(f"{st} ID=`{p['id']}` | `{p['chat_name']}` | هر `{p['interval_min']}` دقیقه\n  `{p['text'][:50]}`")
        return "━━━ 📢 پست خودکار گروه ━━━\n\n" + "\n".join(lines)

    def ap_kb(uid):
        rows = [[Button.inline("➕ افزودن پست", b"ap_add")]]
        posts = db.get(str(uid), {}).get("auto_post_list", [])
        for p in posts[:10]:
            st = "✅" if p.get("enabled") else "❌"
            rows.append([Button.inline(f"{st} {p['chat_name']} | {p['interval_min']}m",
                                       f"ap_toggle:{p['id']}".encode()),
                         Button.inline("❌", f"ap_del:{p['id']}".encode())])
        rows.append([Button.inline("◀️ بازگشت", b"back")])
        return rows

    # ── /start ──────────────────────────────────
    @bot.on(events.NewMessage(pattern=r"/start"))
    async def cmd_start(event):
        uid = event.sender_id
        u = db.get(str(uid), {})
        if u.get("session_string"):
            await event.respond(f"{status_text(uid)}\n\nاز منوی زیر استفاده کن:", buttons=main_kb(uid))
        else:
            await event.respond(
                "━━━ 🤖 سلف‌بات ساز ━━━\n\n"
                "✨ ساعت در اسم پروفایل\n🎨 فونت ساعت ۱۰ استایل\n"
                "📨 پاسخ خودکار (تکی/تصادفی)\n🔑 فیلتر کلمات کلیدی\n"
                "🤖 منشی هوشمند\n🔒 قفل پی‌وی\n👀 بدون خواندن\n"
                "🗑 Anti-Delete\n📅 زمانبندی پیام\n📢 پست خودکار گروه\n"
                "🔔 اعلان آنلاین کاربر\n🚫 بلاک مخفی\n🔇 سکوت\n"
                "⌨️ تایپینگ و بازی\n🎲 تاس و بولینگ\n🌐 ترجمه\n\n"
                "از دکمه زیر شروع کن:",
                buttons=main_kb(uid))

    # ── /help ───────────────────────────────────
    @bot.on(events.NewMessage(pattern=r"/help"))
    async def cmd_help(event):
        await event.respond(
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            "         📖 راهنمای کامل\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "━━ 🤖 ربات کنترل (این چت) ━━\n"
            "`/start` — منوی اصلی\n`/status` — وضعیت\n"
            "`/stop` — توقف\n`/block` — بلاک مخفی\n`/help` — راهنما\n\n"
            "━━ 🎛 پنل ━━\n"
            "  `/panel` — نمایش وضعیت و کنترل\n\n"
            "━━ ⏰ ساعت و پروفایل ━━\n"
            "  `/clock on`/`off` — ساعت در اسم\n"
            "  `/nfont [style]` — فونت ساعت (۱۰ استایل)\n"
            "  `/font` — فونت متن\n"
            "  `/font bold متن` — تبدیل فونت\n"
            "  `/font set bold` — فونت خودکار\n`/font off`\n\n"
            "━━ 📨 پاسخ خودکار ━━\n"
            "  `/rr متن` — تنظیم و روشن\n`/rr on`/`off`\n`/rr` — وضعیت\n\n"
            "━━ 🎲 پاسخ چندتایی تصادفی ━━\n"
            "  `/rrmulti متن1 | متن2 | متن3`\n\n"
            "━━ 🔑 فیلتر کلمات کلیدی ━━\n"
            "  `/kwe on`/`off` — فعال/غیرفعال\n"
            "  `/kwa کلمه::متن` — افزودن\n`/kwl` — لیست\n`/kwd id` — حذف\n`/kt id` — toggle\n\n"
            "━━ 🤖 منشی ━━\n"
            "  `/secretary متن` — تنظیم و روشن\n`/secretary on`/`off`/`reset`\n\n"
            "━━ 🔒 حریم خصوصی ━━\n"
            "  `/pvlock on`/`off` — قفل پی‌وی\n"
            "  `/noread on`/`off` — بدون خواندن\n"
            "  `/antidelete on`/`off` — Anti-Delete\n`/undelete` — مشاهده\n\n"
            "━━ 📅 زمانبندی پیام ━━\n"
            "  `/sched` — لیست\n`/schadd chat|زمان|متن` — افزودن\n`/schdel id` — حذف\n"
            "  💡 آیدی گروه پرایوت: `/chatid` توی گروه بزن\n\n"
            "━━ 📢 پست خودکار گروه ━━\n"
            "  `/ap` — لیست\n"
            "  `/apadd @group | دقیقه | متن` — افزودن\n"
            "  `/apdel id` — حذف\n`/aptoggle id` — فعال/غیرفعال\n"
            "  💡 آیدی گروه پرایوت: `/chatid` توی گروه بزن\n\n"
            "━━ 🔔 اعلان آنلاین ━━\n"
            "  `/notif` — لیست\n`/notifadd @u` — افزودن\n`/notifdel @u` — حذف\n\n"
            "━━ 🚫 بلاک و سکوت ━━\n"
            "  `/ban @u` `/unban @u` `/banlist`\n"
            "  `/mute @u` `/unmute @u` `/mutelist`\n\n"
            "━━ ⌨️ تایپینگ و بازی ━━\n"
            "  `/typing` — تایپینگ\n`/game` — بازی\n\n"
            "━━ 🏷 گروه ━━\n"
            "  `/chatid` — آیدی عددی گروه/کانال\n"
            "  `/tag` — تگ همه\n`/pin` — پین\n`/ping` — تست\n\n"
            "━━ 🌐 ترجمه ━━\n"
            "  `/tr` `/tr en` `/tr ar`\n\n"
            "━━ 📨 پیام ━━\n"
            "  `/r 100 متن` — تکرار\n`/del 100` — حذف\n\n"
            "━━ 🎲 سرگرمی ━━\n"
            "  `/dice` 🎲 `/bowl` 🎳\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━")

    # ── setup ───────────────────────────────────
    @bot.on(events.CallbackQuery(data=b"setup"))
    async def cb_setup(event):
        await event.answer()
        conv[event.sender_id] = {"step": "phone"}
        await event.respond("📱 **شماره تلفنت رو بفرست:**\nمثال: `+989123456789`",
                            buttons=[[Button.inline("❌ لغو", b"cancel")]])

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
        await event.respond("⛔ سلف‌بات متوقف شد.", buttons=main_kb(event.sender_id))

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
        await event.respond("⚠️ مطمئنی؟ تمام اطلاعات حذف میشه.",
                            buttons=[[Button.inline("✅ بله", b"confirm_del")],
                                     [Button.inline("❌ نه", b"back")]])

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

    # ── status_btn ──────────────────────────────
    @bot.on(events.CallbackQuery(data=b"status_btn"))
    async def cb_status_btn(event):
        await event.answer()
        uid = event.sender_id
        u = db.get(str(uid), {})
        if not u.get("session_string"):
            await event.respond("❌ سلف‌بات نداری.")
            return
        on = uid in tasks and not tasks[uid].done()
        now = datetime.now(pytz.timezone(u.get("timezone", DEFAULT_TZ))).strftime("%Y/%m/%d %H:%M")
        ar_m = "چندتایی" if u.get("ar_mode") == "multi" else "تکی"

        def s(k):
            return "✅" if u.get(k) else "❌"

        await event.respond(
            f"━━━ 📊 وضعیت کامل ━━━\n\n"
            f"وضعیت: {'✅ فعال' if on else '⏸ غیرفعال'}\n"
            f"📛 اسم: `{u.get('base_name') or u.get('orig_first', '...')}`\n"
            f"⏰ ساعت: {now}\n\n"
            f"⏰ ساعت: {s('clock_enabled') if u.get('clock_enabled', True) is not False else '❌'}"
            f" | 🎨 فونت: `{u.get('name_font_style', 'normal')}`\n"
            f"📨 پاسخ: {s('auto_reply_enabled')} ({ar_m})\n"
            f"🤖 منشی: {s('secretary_enabled')}\n"
            f"🔑 فیلتر کلمات: {s('keyword_filters')} ({len(u.get('kw_list', []))})\n"
            f"🔒 PV: {s('pv_lock')} | 👀 ناخوانده: {s('no_read')}\n"
            f"🗑 Anti-Del: {s('anti_delete')} ({len(u.get('deleted_log', []))})\n"
            f"📅 زمانبندی: {len(u.get('scheduled_list', []))}\n"
            f"📢 پست خودکار: {len(u.get('auto_post_list', []))}\n"
            f"🔔 اعلان: {len(u.get('notify_online', []))}\n"
            f"🚫 بلاک: {len(u.get('silent_blocked', []))}\n"
            f"🔇 سکوت: {len(u.get('muted_users', []))}\n"
            f"⌨️ تایپ: {s('typing_mode')} | 🎮 بازی: {s('game_mode')}",
            buttons=[[Button.inline("◀️ بازگشت", b"back")]])

    # ── panel ───────────────────────────────────
    @bot.on(events.CallbackQuery(data=b"panel"))
    async def cb_panel(event):
        await event.answer()
        await event.respond(panel_text(event.sender_id), buttons=panel_kb(event.sender_id))

    def _toggle(uid_s, key, default=False):
        db[uid_s][key] = not db[uid_s].get(key, default)
        save_user(uid_s)

    @bot.on(events.CallbackQuery(data=b"p_clock"))
    async def cb_p_clock(event):
        _toggle(str(event.sender_id), "clock_enabled", True)
        await event.answer("✅")
        await event.respond(panel_text(event.sender_id), buttons=panel_kb(event.sender_id))

    @bot.on(events.CallbackQuery(data=b"p_nfont"))
    async def cb_p_nfont(event):
        uid_s = str(event.sender_id)
        current = db[uid_s].get("name_font_style", "normal")
        idx = NAME_FONT_ORDER.index(current) if current in NAME_FONT_ORDER else 0
        db[uid_s]["name_font_style"] = NAME_FONT_ORDER[(idx + 1) % len(NAME_FONT_ORDER)]
        save_user(uid_s)
        await event.answer(f"🎨 {db[uid_s]['name_font_style']}")
        await event.respond(panel_text(event.sender_id), buttons=panel_kb(event.sender_id))

    @bot.on(events.CallbackQuery(data=b"p_ar"))
    async def cb_p_ar(event):
        _toggle(str(event.sender_id), "auto_reply_enabled")
        await event.answer("✅")
        await event.respond(panel_text(event.sender_id), buttons=panel_kb(event.sender_id))

    @bot.on(events.CallbackQuery(data=b"p_sec"))
    async def cb_p_sec(event):
        _toggle(str(event.sender_id), "secretary_enabled")
        await event.answer("✅")
        await event.respond(panel_text(event.sender_id), buttons=panel_kb(event.sender_id))

    @bot.on(events.CallbackQuery(data=b"p_kw"))
    async def cb_p_kw(event):
        _toggle(str(event.sender_id), "keyword_filters")
        await event.answer("✅")
        await event.respond(panel_text(event.sender_id), buttons=panel_kb(event.sender_id))

    @bot.on(events.CallbackQuery(data=b"p_pv"))
    async def cb_p_pv(event):
        _toggle(str(event.sender_id), "pv_lock")
        await event.answer("✅")
        await event.respond(panel_text(event.sender_id), buttons=panel_kb(event.sender_id))

    @bot.on(events.CallbackQuery(data=b"p_nr"))
    async def cb_p_nr(event):
        _toggle(str(event.sender_id), "no_read")
        await event.answer("✅")
        await event.respond(panel_text(event.sender_id), buttons=panel_kb(event.sender_id))

    @bot.on(events.CallbackQuery(data=b"p_ad"))
    async def cb_p_ad(event):
        _toggle(str(event.sender_id), "anti_delete")
        await event.answer("✅")
        await event.respond(panel_text(event.sender_id), buttons=panel_kb(event.sender_id))

    @bot.on(events.CallbackQuery(data=b"p_typ"))
    async def cb_p_typ(event):
        uid = event.sender_id
        uid_s = str(uid)
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
            f"📛 اسم: `{base}`\n🌍 تایم‌زون: `{u.get('timezone', DEFAULT_TZ)}`\n"
            f"⏱ بازه: `{u.get('update_interval', DEFAULT_INT)}` ثانیه\n"
            f"🔗 جداکننده: `{u.get('separator', ' | ')}`",
            buttons=[
                [Button.inline("📛 اسم", b"set_name"), Button.inline("🌍 تایم‌زون", b"set_tz")],
                [Button.inline("⏱ بازه", b"set_int"), Button.inline("🔗 جداکننده", b"set_sep")],
                [Button.inline("◀️ بازگشت", b"back")]])

    @bot.on(events.CallbackQuery(data=b"set_name"))
    async def cb_set_name(event):
        await event.answer()
        setting_mode[event.sender_id] = "name"
        await event.respond("📛 اسم جدید:", buttons=[[Button.inline("❌ لغو", b"cancel")]])

    @bot.on(events.CallbackQuery(data=b"set_tz"))
    async def cb_set_tz(event):
        await event.answer()
        setting_mode[event.sender_id] = "tz"
        await event.respond("🌍 تایم‌زون:\n`Asia/Tehran`\n`Asia/Dubai`\n`Europe/London`",
                            buttons=[[Button.inline("❌ لغو", b"cancel")]])

    @bot.on(events.CallbackQuery(data=b"set_int"))
    async def cb_set_int(event):
        await event.answer()
        setting_mode[event.sender_id] = "interval"
        await event.respond("⏱ بازه به ثانیه (حداقل ۳۰):", buttons=[[Button.inline("❌ لغو", b"cancel")]])

    @bot.on(events.CallbackQuery(data=b"set_sep"))
    async def cb_set_sep(event):
        await event.answer()
        setting_mode[event.sender_id] = "sep"
        await event.respond("🔗 جداکننده:\n` | ` ` • ` ` — ` ` ◆ `",
                            buttons=[[Button.inline("❌ لغو", b"cancel")]])

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

    # ── mute menu ───────────────────────────────
    @bot.on(events.CallbackQuery(data=b"mute_menu"))
    async def cb_mute_menu(event):
        await event.answer()
        uid = event.sender_id
        lst = db.get(str(uid), {}).get("muted_users", [])
        txt = "━━━ 🔇 سکوت ━━━\n\n" + ("\n".join(f"• {n['name']}" for n in lst) if lst else "کسی ساکت نیست.")
        rows = [[Button.inline("➕ سکوت کردن", b"mute_add")]]
        for n in lst[:20]:
            rows.append([Button.inline(f"🔊 آزاد: {n['name']}", f"mute_del:{n['id']}".encode())])
        rows.append([Button.inline("◀️ بازگشت", b"back")])
        await event.respond(txt, buttons=rows)

    @bot.on(events.CallbackQuery(data=b"mute_add"))
    async def cb_mute_add(event):
        await event.answer()
        setting_mode[event.sender_id] = "mute_add"
        await event.respond("🔇 یوزرنیم یا آیدی:", buttons=[[Button.inline("❌ لغو", b"cancel")]])

    @bot.on(events.CallbackQuery(pattern=rb"mute_del:(-?\d+)"))
    async def cb_mute_del(event):
        await event.answer("⏳")
        uid = event.sender_id
        uid_s = str(uid)
        tid = int(event.pattern_match.group(1))
        lst = db.get(uid_s, {}).get("muted_users", [])
        entry = next((n for n in lst if n["id"] == tid), None)
        if entry:
            lst[:] = [n for n in lst if n["id"] != tid]
            save_user(uid_s)
            await event.respond(f"🔊 `{entry['name']}` آزاد شد.")
        else:
            await event.respond("❌ پیدا نشد.")
        await cb_mute_menu(event)

    # ── pvlock menu ─────────────────────────────
    @bot.on(events.CallbackQuery(data=b"pvlock_menu"))
    async def cb_pvlock_menu(event):
        await event.answer()
        on = db.get(str(event.sender_id), {}).get("pv_lock", False)
        await event.respond(
            f"━━━ 🔒 قفل پی‌وی ━━━\n\nوضعیت: {'✅ روشن' if on else '❌ خاموش'}",
            buttons=[[Button.inline("🔴 خاموش" if on else "🟢 روشن", b"pv_off" if on else b"pv_on")],
                     [Button.inline("◀️ بازگشت", b"back")]])

    @bot.on(events.CallbackQuery(data=b"pv_on"))
    async def cb_pv_on(event):
        _toggle(str(event.sender_id), "pv_lock")
        await event.answer("✅")
        await cb_pvlock_menu(event)

    @bot.on(events.CallbackQuery(data=b"pv_off"))
    async def cb_pv_off(event):
        _toggle(str(event.sender_id), "pv_lock")
        await event.answer("✅")
        await cb_pvlock_menu(event)

    # ── auto-reply menu ─────────────────────────
    @bot.on(events.CallbackQuery(data=b"ar_menu"))
    async def cb_ar_menu(event):
        await event.answer()
        await event.respond(ar_info(event.sender_id), buttons=ar_kb(event.sender_id))

    @bot.on(events.CallbackQuery(data=b"ar_on"))
    async def cb_ar_on(event):
        await event.answer()
        uid_s = str(event.sender_id)
        if not db.get(uid_s, {}).get("auto_reply_text") and not db.get(uid_s, {}).get("ar_multi_texts"):
            await event.respond("❌ اول متن رو تنظیم کن.")
        else:
            db[uid_s]["auto_reply_enabled"] = True
            save_user(uid_s)
            await event.respond("✅ روشن شد.")
        await event.respond(ar_info(event.sender_id), buttons=ar_kb(event.sender_id))

    @bot.on(events.CallbackQuery(data=b"ar_off"))
    async def cb_ar_off(event):
        await event.answer()
        db[str(event.sender_id)]["auto_reply_enabled"] = False
        save_user(str(event.sender_id))
        await event.respond("❌ خاموش شد.")
        await event.respond(ar_info(event.sender_id), buttons=ar_kb(event.sender_id))

    @bot.on(events.CallbackQuery(data=b"ar_set_text"))
    async def cb_ar_set_text(event):
        await event.answer()
        setting_mode[event.sender_id] = "ar_text"
        await event.respond("✏️ متن پاسخ:", buttons=[[Button.inline("❌ لغو", b"cancel")]])

    @bot.on(events.CallbackQuery(data=b"ar_set_cd"))
    async def cb_ar_set_cd(event):
        await event.answer()
        setting_mode[event.sender_id] = "ar_cooldown"
        await event.respond("⏱ کول‌داون (ثانیه):", buttons=[[Button.inline("❌ لغو", b"cancel")]])

    @bot.on(events.CallbackQuery(data=b"ar_clear"))
    async def cb_ar_clear(event):
        await event.answer("🗑")
        uid_s = str(event.sender_id)
        if uid_s in db:
            db[uid_s]["auto_reply_sent_to"] = {}
            save_user(uid_s)
        await event.respond("🗑 تاریخچه پاک شد.")
        await event.respond(ar_info(event.sender_id), buttons=ar_kb(event.sender_id))

    # ── secretary menu ──────────────────────────
    @bot.on(events.CallbackQuery(data=b"sec_menu"))
    async def cb_sec_menu(event):
        await event.answer()
        await event.respond(sec_info(event.sender_id), buttons=sec_kb(event.sender_id))

    @bot.on(events.CallbackQuery(data=b"sec_on"))
    async def cb_sec_on(event):
        await event.answer()
        uid_s = str(event.sender_id)
        if not db.get(uid_s, {}).get("secretary_text"):
            await event.respond("❌ اول متن رو تنظیم کن.")
        else:
            db[uid_s]["secretary_enabled"] = True
            save_user(uid_s)
            await event.respond("✅ روشن شد.")
        await event.respond(sec_info(event.sender_id), buttons=sec_kb(event.sender_id))

    @bot.on(events.CallbackQuery(data=b"sec_off"))
    async def cb_sec_off(event):
        await event.answer()
        db[str(event.sender_id)]["secretary_enabled"] = False
        save_user(str(event.sender_id))
        await event.respond("❌ خاموش شد.")
        await event.respond(sec_info(event.sender_id), buttons=sec_kb(event.sender_id))

    @bot.on(events.CallbackQuery(data=b"sec_set_text"))
    async def cb_sec_set_text(event):
        await event.answer()
        setting_mode[event.sender_id] = "sec_text"
        await event.respond("✏️ متن منشی:", buttons=[[Button.inline("❌ لغو", b"cancel")]])

    @bot.on(events.CallbackQuery(data=b"sec_reset"))
    async def cb_sec_reset(event):
        await event.answer("🗑")
        uid_s = str(event.sender_id)
        if uid_s in db:
            db[uid_s]["secretary_sent_to"] = {}
            save_user(uid_s)
        await event.respond("🗑 ریست شد.")
        await event.respond(sec_info(event.sender_id), buttons=sec_kb(event.sender_id))

    @bot.on(events.CallbackQuery(data=b"sec_ai_on"))
    async def cb_sec_ai_on(event):
        await event.answer()
        uid_s = str(event.sender_id)
        db[uid_s]["secretary_ai"] = True
        if not db[uid_s].get("secretary_text"):
            db[uid_s]["secretary_text"] = AI_SYSTEM_DEFAULT
        db[uid_s]["secretary_enabled"] = True
        save_user(uid_s)
        await event.respond("🧠 حالت AI روشن شد!")
        await event.respond(sec_info(event.sender_id), buttons=sec_kb(event.sender_id))

    @bot.on(events.CallbackQuery(data=b"sec_ai_off"))
    async def cb_sec_ai_off(event):
        await event.answer()
        uid_s = str(event.sender_id)
        db[uid_s]["secretary_ai"] = False
        save_user(uid_s)
        await event.respond("📝 حالت متن ثابت فعال شد.")
        await event.respond(sec_info(event.sender_id), buttons=sec_kb(event.sender_id))

    # ── keyword menu ────────────────────────────
    @bot.on(events.CallbackQuery(data=b"kw_menu"))
    async def cb_kw_menu(event):
        await event.answer()
        await event.respond(kw_info(event.sender_id), buttons=kw_kb(event.sender_id))

    @bot.on(events.CallbackQuery(data=b"kw_on"))
    async def cb_kw_on(event):
        await event.answer()
        db[str(event.sender_id)]["keyword_filters"] = True
        save_user(str(event.sender_id))
        await event.respond("✅ روشن شد.")
        await event.respond(kw_info(event.sender_id), buttons=kw_kb(event.sender_id))

    @bot.on(events.CallbackQuery(data=b"kw_off"))
    async def cb_kw_off(event):
        await event.answer()
        db[str(event.sender_id)]["keyword_filters"] = False
        save_user(str(event.sender_id))
        await event.respond("❌ خاموش شد.")
        await event.respond(kw_info(event.sender_id), buttons=kw_kb(event.sender_id))

    @bot.on(events.CallbackQuery(data=b"kw_add"))
    async def cb_kw_add(event):
        await event.answer()
        setting_mode[event.sender_id] = "kw_text"
        await event.respond("✏️ فرمت: `کلمه::متن پاسخ`\nمثال: `سلام::سلام چطوری؟`",
                            buttons=[[Button.inline("❌ لغو", b"cancel")]])

    # ── schedule menu ───────────────────────────
    @bot.on(events.CallbackQuery(data=b"sched_menu"))
    async def cb_sched_menu(event):
        await event.answer()
        await event.respond(sched_info(event.sender_id), buttons=sched_kb(event.sender_id))

    @bot.on(events.CallbackQuery(data=b"sched_add"))
    async def cb_sched_add(event):
        await event.answer()
        setting_mode[event.sender_id] = "sched_chat"
        await event.respond("📅 **مرحله ۱: چت مقصد**\n\n`me` یا آیدی عددی:"
                            "\n💡 آیدی گروه پرایوت: توی گروه `/chatid` بزن",
                            buttons=[[Button.inline("❌ لغو", b"cancel")]])

    # ── anti-delete menu ────────────────────────
    @bot.on(events.CallbackQuery(data=b"ad_menu"))
    async def cb_ad_menu(event):
        await event.answer()
        await event.respond(ad_info(event.sender_id), buttons=ad_kb(event.sender_id))

    @bot.on(events.CallbackQuery(data=b"ad_on"))
    async def cb_ad_on(event):
        await event.answer()
        db[str(event.sender_id)]["anti_delete"] = True
        save_user(str(event.sender_id))
        await event.respond("✅ روشن شد.")
        await event.respond(ad_info(event.sender_id), buttons=ad_kb(event.sender_id))

    @bot.on(events.CallbackQuery(data=b"ad_off"))
    async def cb_ad_off(event):
        await event.answer()
        db[str(event.sender_id)]["anti_delete"] = False
        save_user(str(event.sender_id))
        await event.respond("❌ خاموش شد.")
        await event.respond(ad_info(event.sender_id), buttons=ad_kb(event.sender_id))

    @bot.on(events.CallbackQuery(data=b"ad_view"))
    async def cb_ad_view(event):
        await event.answer()
        deleted = db.get(str(event.sender_id), {}).get("deleted_log", [])
        if not deleted:
            await event.respond("🗑 هیچ پیامی ذخیره نشده.")
            return
        recent = deleted[-5:]
        lines = []
        for d in reversed(recent):
            ts = d.get("ts", "")
            if isinstance(ts, datetime):
                ts = ts.strftime("%m/%d %H:%M")
            lines.append(f"• `{ts}` | `{d.get('sender_id', '?')}`\n  `{(d.get('text') or '')[:50]}`")
        await event.respond(f"━━━ 🗑 حذف‌شده‌ها ━━━\n\n" + "\n".join(lines) + f"\n\nکل: `{len(deleted)}`")

    # ── no-read menu ────────────────────────────
    @bot.on(events.CallbackQuery(data=b"nr_menu"))
    async def cb_nr_menu(event):
        await event.answer()
        await event.respond(nr_info(event.sender_id), buttons=nr_kb(event.sender_id))

    @bot.on(events.CallbackQuery(data=b"nr_on"))
    async def cb_nr_on(event):
        await event.answer()
        db[str(event.sender_id)]["no_read"] = True
        save_user(str(event.sender_id))
        await event.respond("👀 روشن شد.")
        await event.respond(nr_info(event.sender_id), buttons=nr_kb(event.sender_id))

    @bot.on(events.CallbackQuery(data=b"nr_off"))
    async def cb_nr_off(event):
        await event.answer()
        db[str(event.sender_id)]["no_read"] = False
        save_user(str(event.sender_id))
        await event.respond("✅ خاموش شد.")
        await event.respond(nr_info(event.sender_id), buttons=nr_kb(event.sender_id))

    # ── auto-post menu ──────────────────────────
    @bot.on(events.CallbackQuery(data=b"ap_menu"))
    async def cb_ap_menu(event):
        await event.answer()
        await event.respond(ap_info(event.sender_id), buttons=ap_kb(event.sender_id))

    @bot.on(events.CallbackQuery(data=b"ap_add"))
    async def cb_ap_add(event):
        await event.answer()
        setting_mode[event.sender_id] = "ap_chat"
        await event.respond(
            "📢 **مرحله ۱: گروه/کانال**\n\n"
            "لینک، یوزرنیم یا آیدی عددی:\n"
            "مثال: `@mygroup`\n"
            "لینک دعوت: `t.me/+InviteHash`\n"
            "آیدی عددی: `-1001234567890`\n\n"
            "💡 آیدی گروه پرایوت:\n"
            "توی گروه `/chatid` بزن و آیدی رو کپی کن",
            buttons=[[Button.inline("❌ لغو", b"cancel")]])

    @bot.on(events.CallbackQuery(pattern=rb"ap_toggle:(\d+)"))
    async def cb_ap_toggle(event):
        await event.answer("⏳")
        uid_s = str(event.sender_id)
        pid = int(event.pattern_match.group(1))
        if toggle_auto_post(uid_s, pid):
            for p in db[uid_s].get("auto_post_list", []):
                if p["id"] == pid:
                    p["enabled"] = not p.get("enabled", True)
                    break
            await event.respond("✅ وضعیت تغییر کرد.")
        else:
            await event.respond("❌ پیدا نشد.")
        await event.respond(ap_info(event.sender_id), buttons=ap_kb(event.sender_id))

    @bot.on(events.CallbackQuery(pattern=rb"ap_del:(\d+)"))
    async def cb_ap_del(event):
        await event.answer("⏳")
        uid_s = str(event.sender_id)
        pid = int(event.pattern_match.group(1))
        if del_auto_post(uid_s, pid):
            db[uid_s]["auto_post_list"] = [p for p in db[uid_s].get("auto_post_list", []) if p["id"] != pid]
            await event.respond("✅ حذف شد.")
        else:
            await event.respond("❌ پیدا نشد.")
        await event.respond(ap_info(event.sender_id), buttons=ap_kb(event.sender_id))

    # ── notification menu ───────────────────────
    @bot.on(events.CallbackQuery(data=b"notif_menu"))
    async def cb_notif_menu(event):
        await event.answer()
        await event.respond(notif_info(event.sender_id), buttons=notif_kb(event.sender_id))

    @bot.on(events.CallbackQuery(data=b"notif_add"))
    async def cb_notif_add(event):
        await event.answer()
        setting_mode[event.sender_id] = "notif_add"
        await event.respond("🔔 یوزرنیم یا آیدی:", buttons=[[Button.inline("❌ لغو", b"cancel")]])

    @bot.on(events.CallbackQuery(data=b"notif_del"))
    async def cb_notif_del(event):
        await event.answer()
        setting_mode[event.sender_id] = "notif_del"
        await event.respond("❌ یوزرنیم یا آیدی برای حذف:", buttons=[[Button.inline("❌ لغو", b"cancel")]])

    # ── text handler ────────────────────────────
    @bot.on(events.NewMessage(func=lambda e: e.is_private))
    async def on_text(event):
        uid = event.sender_id
        text = event.text.strip()
        if text.startswith("/"):
            return

        if uid in conv:
            step = conv[uid].get("step")
            if step == "phone":
                if not text.startswith("+") or len(text) < 10:
                    await event.respond("❌ فرمت اشتباه.")
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
                    await msg.edit(f"❌ خطا: `{e}`")
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
                        await event.respond("🔒 رمز دو مرحله‌ای:")
                        return
                    try:
                        await tmp.disconnect()
                    except Exception:
                        pass
                    conv.pop(uid, None)
                    await event.respond(f"❌ خطا: `{e}`")
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
                await event.respond("━━━ ✅ سلف‌بات فعال شد! ━━━", buttons=main_kb(uid))
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
                await event.respond("━━━ ✅ سلف‌بات فعال شد! ━━━", buttons=main_kb(uid))
                await start_sb(uid, bot)
                return

            # مراحل زمانبندی
            if step == "sched_time":
                time_str = text.strip()
                uid_s = str(uid)
                chat_id = conv[uid].get("chat_id", 0)
                send_at = None
                rel = re.match(r'^(\d+[hmd])+$', time_str.lower())
                if rel:
                    total = 0
                    for m in re.finditer(r'(\d+)([hmd])', time_str.lower()):
                        val, unit = int(m.group(1)), m.group(2)
                        total += val * {"h": 3600, "m": 60, "d": 86400}[unit]
                    if total > 0:
                        send_at = datetime.now(pytz.timezone(db.get(uid_s, {}).get("timezone", DEFAULT_TZ))) + timedelta(seconds=total)
                else:
                    for fmt in ("%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M"):
                        try:
                            send_at = pytz.timezone(db.get(uid_s, {}).get("timezone", DEFAULT_TZ)).localize(
                                datetime.strptime(time_str, fmt))
                            break
                        except Exception:
                            pass
                if not send_at:
                    await event.respond("❌ زمان نامعتبر.")
                    return
                conv[uid]["step"] = "sched_text"
                conv[uid]["send_at"] = send_at
                await event.respond("📅 **مرحله ۳: متن پیام**",
                                    buttons=[[Button.inline("❌ لغو", b"cancel")]])
                return

            if step == "sched_text":
                uid_s = str(uid)
                chat_id = conv[uid].get("chat_id", 0)
                send_at = conv[uid].get("send_at")
                if not text.strip():
                    await event.respond("❌ متن خالیه.")
                    return
                add_scheduled_msg(uid_s, chat_id, text.strip(), send_at)
                conn = get_conn()
                cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
                cur.execute("SELECT * FROM scheduled_msgs WHERE uid = %s AND sent = FALSE ORDER BY send_at",
                            (int(uid_s),))
                db[uid_s]["scheduled_list"] = [dict(r) for r in cur.fetchall()]
                cur.close()
                conv.pop(uid, None)
                await event.respond(
                    f"✅ زمانبندی شد!\n📅 `{send_at.strftime('%Y/%m/%d %H:%M')}`\n📝 `{text[:80]}`",
                    buttons=main_kb(uid))
                return

            # مراحل پست خودکار
            if step == "ap_chat":
                uid_s = str(uid)
                c = clients.get(uid)
                if not c:
                    await event.respond("❌ سلف‌بات فعال نیست.")
                    conv.pop(uid, None)
                    return

                entity, chat_name = await resolve_chat_entity(c, text)
                if not entity:
                    await event.respond(
                        "❌ گروه پیدا نشد!\n\n"
                        "💡 **برای گروه‌های پرایوت:**\n"
                        "توی گروه `/chatid` بزن و آیدی عددی رو بفرست\n\n"
                        "یا لینک دعوت: `t.me/+hash`")
                    return

                real_id = entity.id
                chat_name = _entity_name(entity)
                if hasattr(entity, "megagroup") or hasattr(entity, "broadcast"):
                    real_id = int(f"-100{entity.id}")
                elif entity.id > 0:
                    real_id = entity.id

                conv[uid]["step"] = "ap_text"
                conv[uid]["ap_chat_id"] = real_id
                conv[uid]["ap_chat_name"] = chat_name
                await event.respond(f"📢 گروه: `{chat_name}`\n\n"
                                    "📅 **مرحله ۲: متن پیام**\n\n"
                                    "متنی که می‌خوای هر بار ارسال بشه:",
                                    buttons=[[Button.inline("❌ لغو", b"cancel")]])
                return

            if step == "ap_text":
                if not text.strip():
                    await event.respond("❌ متن خالیه.")
                    return
                conv[uid]["ap_text"] = text.strip()
                conv[uid]["step"] = "ap_interval"
                await event.respond("⏱ **مرحله ۳: بازه زمانی**\n\n"
                                    "هر چند دقیقه ارسال بشه؟\n"
                                    "مثال: `30` (هر ۳۰ دقیقه)\n"
                                    "یا: `60` (هر ۱ ساعت)",
                                    buttons=[[Button.inline("❌ لغو", b"cancel")]])
                return

            if step == "ap_interval":
                uid_s = str(uid)
                if not text.strip().isdigit() or int(text.strip()) < 1:
                    await event.respond("❌ باید عدد باشه (حداقل ۱ دقیقه).")
                    return
                interval_min = int(text.strip())
                chat_id = conv[uid].get("ap_chat_id")
                chat_name = conv[uid].get("ap_chat_name", str(chat_id))
                ap_text = conv[uid].get("ap_text", "")
                add_auto_post(uid_s, chat_id, chat_name, ap_text, interval_min)
                conn = get_conn()
                cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
                cur.execute("SELECT * FROM auto_posts WHERE uid = %s ORDER BY id", (int(uid_s),))
                db[uid_s]["auto_post_list"] = [dict(r) for r in cur.fetchall()]
                cur.close()
                conv.pop(uid, None)
                await event.respond(
                    f"✅ پست خودکار تنظیم شد!\n\n"
                    f"📢 گروه: `{chat_name}`\n"
                    f"⏱ هر `{interval_min}` دقیقه\n"
                    f"📝 متن: `{ap_text[:80]}`",
                    buttons=main_kb(uid))
                return

        # تنظیمات
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
                await event.respond(f"✅ اسم: `{text}`")
            elif mode == "tz":
                try:
                    pytz.timezone(text)
                except Exception:
                    await event.respond("❌ تایم‌زون نامعتبر.")
                    return
                db[uid_s]["timezone"] = text
                save_user(uid_s)
                await event.respond(f"✅ تایم‌زون: `{text}`")
            elif mode == "interval":
                if not text.isdigit() or int(text) < 30:
                    await event.respond("❌ حداقل ۳۰.")
                    return
                db[uid_s]["update_interval"] = int(text)
                save_user(uid_s)
                await event.respond(f"✅ بازه: {text} ثانیه")
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
                db[uid_s]["ar_mode"] = "single"
                save_user(uid_s)
                setting_mode.pop(uid, None)
                await event.respond("✅ متن پاسخ تنظیم شد.")
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
                await event.respond(f"✅ کول‌داون: {text} ثانیه")
                await event.respond(ar_info(uid), buttons=ar_kb(uid))
                return
            elif mode == "sec_text":
                need_restart = False
                db[uid_s]["secretary_text"] = text[:500]
                db[uid_s]["secretary_enabled"] = True
                save_user(uid_s)
                setting_mode.pop(uid, None)
                await event.respond("✅ متن منشی تنظیم شد.")
                await event.respond(sec_info(uid), buttons=sec_kb(uid))
                return
            elif mode == "kw_text":
                need_restart = False
                if "::" not in text:
                    await event.respond("❌ فرمت: `کلمه::متن`")
                    return
                parts = text.split("::", 1)
                kw, resp = parts[0].strip(), parts[1].strip()
                if not kw or not resp:
                    await event.respond("❌ هر دو لازمه.")
                    return
                add_kw_filter(uid_s, kw, resp)
                conn = get_conn()
                cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
                cur.execute("SELECT * FROM kw_filters WHERE uid = %s ORDER BY id", (int(uid_s),))
                db[uid_s]["kw_list"] = [dict(r) for r in cur.fetchall()]
                cur.close()
                setting_mode.pop(uid, None)
                await event.respond(f"✅ فیلتر: `{kw}` → `{resp[:100]}`")
                await event.respond(kw_info(uid), buttons=kw_kb(uid))
                return
            elif mode == "sched_chat":
                chat_id = 0
                if text.lower() == "me":
                    chat_id = 0
                elif text.lstrip("-").isdigit():
                    chat_id = int(text)
                else:
                    await event.respond("❌ `me` یا آیدی عددی.\n\n💡 توی گروه `/chatid` بزن.")
                    return
                conv[uid] = {"step": "sched_time", "chat_id": chat_id}
                setting_mode.pop(uid, None)
                await event.respond("📅 **مرحله ۲: زمان**\nمثال: `2h` یا `2025/01/15 10:30`",
                                    buttons=[[Button.inline("❌ لغو", b"cancel")]])
                return
            elif mode == "notif_add":
                need_restart = False
                target = text.lstrip("@").strip()
                c = clients.get(uid)
                if not c:
                    await event.respond("❌ سلف‌بات فعال نیست.")
                    setting_mode.pop(uid, None)
                    return
                try:
                    entity = await c.get_entity(int(target) if target.lstrip("-").isdigit() else target)
                except Exception as e:
                    await event.respond(f"❌ پیدا نشد: `{e}`")
                    return
                tid = entity.id
                name = _entity_name(entity)
                lst = db[uid_s].setdefault("notify_online", [])
                if any(n["id"] == tid for n in lst):
                    await event.respond("⚠️ از قبل تحت نظره.")
                    setting_mode.pop(uid, None)
                    return
                lst.append({"id": tid, "name": name})
                save_user(uid_s)
                setting_mode.pop(uid, None)
                await event.respond(f"🔔 `{name}` اضافه شد.")
                await event.respond(notif_info(uid), buttons=notif_kb(uid))
                return
            elif mode == "notif_del":
                need_restart = False
                target = text.lstrip("@").strip()
                lst = db.get(uid_s, {}).get("notify_online", [])
                tid = int(target) if target.lstrip("-").isdigit() else None
                if tid is None:
                    for n in lst:
                        if target.lower() in n["name"].lower():
                            tid = n["id"]
                            break
                if tid is None:
                    await event.respond("❌ پیدا نشد.")
                    setting_mode.pop(uid, None)
                    return
                entry = next((n for n in lst if n["id"] == tid), None)
                if entry:
                    lst[:] = [n for n in lst if n["id"] != tid]
                    save_user(uid_s)
                    await event.respond(f"✅ `{entry['name']}` حذف شد.")
                setting_mode.pop(uid, None)
                await event.respond(notif_info(uid), buttons=notif_kb(uid))
                return
            elif mode == "mute_add":
                need_restart = False
                target = text.lstrip("@").strip()
                c = clients.get(uid)
                if not c:
                    await event.respond("❌ سلف‌بات فعال نیست.")
                    setting_mode.pop(uid, None)
                    return
                try:
                    entity = await c.get_entity(int(target) if target.lstrip("-").isdigit() else target)
                except Exception as e:
                    await event.respond(f"❌ پیدا نشد: `{e}`")
                    return
                tid = entity.id
                name = _entity_name(entity)
                lst = db[uid_s].setdefault("muted_users", [])
                if any(n["id"] == tid for n in lst):
                    await event.respond("⚠️ از قبل ساکته.")
                    setting_mode.pop(uid, None)
                    return
                lst.append({"id": tid, "name": name})
                save_user(uid_s)
                setting_mode.pop(uid, None)
                await event.respond(f"🔇 `{name}` ساکت شد.")
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
        ar_m = "چندتایی" if u.get("ar_mode") == "multi" else "تکی"

        def s(k):
            return "✅" if u.get(k) else "❌"

        await event.respond(
            f"━━━ 📊 وضعیت کامل سلف‌بات ━━━\n\n"
            f"وضعیت: {'✅ فعال' if on else '⏸ غیرفعال'}\n"
            f"📛 اسم: `{u.get('base_name') or u.get('orig_first', '...')}`\n"
            f"⏰ ساعت: {now}\n\n"
            f"⏰ ساعت: {s('clock_enabled') if u.get('clock_enabled', True) is not False else '❌'}"
            f" | 🎨 فونت: `{u.get('name_font_style', 'normal')}`\n"
            f"📨 پاسخ: {s('auto_reply_enabled')} ({ar_m})"
            f" | 🤖 منشی: {s('secretary_enabled')}\n"
            f"🔑 فیلتر کلمات: {s('keyword_filters')} ({len(u.get('kw_list', []))})\n"
            f"🔒 PV: {s('pv_lock')} | 👀 ناخوانده: {s('no_read')}\n"
            f"🗑 Anti-Del: {s('anti_delete')} ({len(u.get('deleted_log', []))})\n"
            f"📅 زمانبندی: {len(u.get('scheduled_list', []))}"
            f" | 📢 پست خودکار: {len(u.get('auto_post_list', []))}\n"
            f"🔔 اعلان: {len(u.get('notify_online', []))}\n"
            f"🚫 بلاک: {len(u.get('silent_blocked', []))}"
            f" | 🔇 سکوت: {len(u.get('muted_users', []))}\n"
            f"⌨️ تایپ: {s('typing_mode')} | 🎮 بازی: {s('game_mode')}")

    @bot.on(events.NewMessage(pattern=r"/stop"))
    async def cmd_stop(event):
        await stop_sb(event.sender_id)
        await event.respond("⛔ سلف‌بات متوقف شد.")

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
                f"👥 کل: {total} | ✅ فعال: {active} | 🔄 اجرا: {running}\n"
                f"📨 پاسخ: {sum(1 for v in db.values() if v.get('auto_reply_enabled'))}\n"
                f"🤖 منشی: {sum(1 for v in db.values() if v.get('secretary_enabled'))}\n"
                f"🔑 کلمات: {sum(1 for v in db.values() if v.get('keyword_filters'))}\n"
                f"🗑 Anti-Del: {sum(1 for v in db.values() if v.get('anti_delete'))}")

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
        log.error("DATABASE_URL لازمه!")
        sys.exit(1)
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()