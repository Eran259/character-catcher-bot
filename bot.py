import os
import sqlite3
import random
import html
import requests
import io
from PIL import Image, ImageFilter, ImageEnhance, ImageDraw, ImageOps
from datetime import datetime, timezone

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
    LabeledPrice,
    InputFile,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_FILE = "character.db"
MESSAGE_LIMIT = 20

RARITIES = {
    "Common": ("🟢", 10),
    "Rare": ("🔵", 25),
    "Epic": ("🟣", 50),
    "Legendary": ("🟠", 100),
    "Mythic": ("🔴", 250),
}

REWARD_RANGES = {
    "Common": (100, 150),
    "Rare": (175, 250),
    "Epic": (275, 350),
    "Legendary": (375, 450),
    "Mythic": (500, 500),
}

SHOP_ITEMS = {
    "small": {"name": "🎁 Small Gift", "stars": 10},
    "rare": {"name": "💎 Rare Gift", "stars": 30},
    "legendary": {"name": "👑 Legendary Gift", "stars": 75},
    "ticket": {"name": "🎟️ Card Ticket", "stars": 100},
}

ANILIST_URL = "https://graphql.anilist.co"
JIKAN_URL = "https://api.jikan.moe/v4"
ARTWORK_CACHE = {}
ARTWORK_TIMEOUT = 10
ANILIST_QUERY = """
query {
  Page(page: 1, perPage: 50) {
    characters(sort: FAVOURITES_DESC) {
      id
      name { full }
      image { large }
      media(perPage: 1) {
        nodes {
          title { romaji }
        }
      }
    }
  }
}
"""


# =========================
# P1 — DATABASE / API / MENU
# =========================

def get_db():
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    con = get_db()
    cur = con.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            coins INTEGER DEFAULT 100,
            messages INTEGER DEFAULT 0,
            caught INTEGER DEFAULT 0,
            last_daily TEXT,
            started INTEGER DEFAULT 0
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS collection (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            card_id INTEGER,
            character_id INTEGER,
            name TEXT,
            series TEXT,
            image_url TEXT,
            rarity TEXT,
            coins INTEGER
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS active_cards (
            chat_id INTEGER PRIMARY KEY,
            card_id INTEGER,
            character_id INTEGER,
            name TEXT,
            series TEXT,
            image_url TEXT,
            rarity TEXT,
            reward INTEGER
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS group_stats (
            chat_id INTEGER PRIMARY KEY,
            messages INTEGER DEFAULT 0
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS gifts (
            user_id INTEGER,
            item_key TEXT,
            item_name TEXT,
            quantity INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, item_key)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS shop_purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            item_key TEXT,
            item_name TEXT,
            stars INTEGER,
            quantity INTEGER DEFAULT 1,
            telegram_payment_charge_id TEXT,
            provider_payment_charge_id TEXT,
            purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS star_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            payload TEXT,
            item_key TEXT,
            stars INTEGER,
            telegram_payment_charge_id TEXT,
            provider_payment_charge_id TEXT,
            paid_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Safe migrations for older DBs
    for table, column, definition in [
        ("users", "last_daily", "TEXT"),
        ("users", "started", "INTEGER DEFAULT 0"),
        ("collection", "card_id", "INTEGER"),
        ("active_cards", "card_id", "INTEGER"),
    ]:
        cur.execute(f"PRAGMA table_info({table})")
        cols = [r["name"] for r in cur.fetchall()]
        if column not in cols:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    con.commit()
    con.close()


def ensure_user(user):
    if not user:
        return
    con = get_db()
    cur = con.cursor()
    cur.execute("""
        INSERT OR IGNORE INTO users
        (user_id, username, coins, messages, caught, last_daily)
        VALUES (?, ?, 100, 0, 0, NULL)
    """, (user.id, user.username or ""))
    cur.execute(
        "UPDATE users SET username=? WHERE user_id=?",
        (user.username or "", user.id)
    )
    con.commit()
    con.close()


def generate_card_id():
    con = get_db()
    cur = con.cursor()
    while True:
        card_id = random.randint(1000, 9999)
        cur.execute("SELECT 1 FROM active_cards WHERE card_id=?", (card_id,))
        if cur.fetchone():
            continue
        cur.execute("SELECT 1 FROM collection WHERE card_id=?", (card_id,))
        if cur.fetchone():
            continue
        con.close()
        return card_id


def fetch_characters():
    try:
        r = requests.post(
            ANILIST_URL,
            json={"query": ANILIST_QUERY},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        return data["data"]["Page"]["characters"]
    except Exception as e:
        print("AniList error:", e)
        return []


def fetch_premium_artwork(character_name, fallback_url, series_name=""):
    """Find a high-quality, character-matching anime artwork automatically.

    Priority:
      1) Danbooru safe artwork using character/copyright tags, ranked by score.
      2) Jikan/MAL exact-character artwork.
      3) Waifu.im character-tag artwork when an exact tag exists.
      4) AniList fallback.

    Only safe artwork is requested; the bot never requires manual image uploads.
    A pool is cached so repeated spawns can use different images.
    """
    key = (character_name or "").strip().lower()
    if not key:
        return fallback_url

    cache = getattr(fetch_premium_artwork, "_cache", {})
    last = getattr(fetch_premium_artwork, "_last", {})
    if key in cache and cache[key]:
        choices = [u for u in cache[key] if u != last.get(key)] or cache[key]
        chosen = random.choice(choices)
        last[key] = chosen
        fetch_premium_artwork._last = last
        return chosen

    urls = []
    headers = {
        "User-Agent": "ErenCharacterBot/3.0 (anime card artwork selector)"
    }

    def add_url(url):
        if isinstance(url, str) and url.startswith(("http://", "https://")) and url not in urls:
            urls.append(url)

    def slug(value):
        import re
        value = (value or "").lower().strip()
        value = re.sub(r"[’'`]+", "", value)
        value = re.sub(r"[^a-z0-9]+", "_", value)
        return value.strip("_")

    # ---------- Danbooru: themed fan-art, safe only ----------
    # This is the main source for the requested "cool artwork" look.
    try:
        char_slug = slug(character_name)
        series_slug = slug(series_name)
        tag_candidates = [char_slug]

        # Resolve a character tag first. Danbooru character tags can have a
        # franchise suffix, e.g. nami_(one_piece), so search the tag database.
        tr = requests.get(
            "https://danbooru.donmai.us/tags.json",
            params={"search[name_matches]": char_slug + "*", "limit": 20},
            headers=headers,
            timeout=ARTWORK_TIMEOUT,
        )
        if tr.ok:
            for tag in tr.json() or []:
                if int(tag.get("category", 0) or 0) == 4:
                    nm = str(tag.get("name", ""))
                    if nm:
                        tag_candidates.insert(0, nm)

        for char_tag in tag_candidates[:4]:
            params = {
                "tags": f"{char_tag} rating:safe",
                "limit": 100,
                "page": random.randint(1, 5),
            }
            # Add the series/copyright tag only when we have a useful slug.
            if series_slug:
                params["tags"] += f" {series_slug}"

            pr = requests.get(
                "https://danbooru.donmai.us/posts.json",
                params=params,
                headers=headers,
                timeout=ARTWORK_TIMEOUT,
            )
            if not pr.ok:
                continue

            posts = pr.json() or []
            # Prefer large, non-animated images and posts with higher scores.
            scored = []
            for post in posts:
                if post.get("rating") not in ("g", "s"):
                    continue
                w = int(post.get("image_width") or 0)
                h = int(post.get("image_height") or 0)
                if w < 700 or h < 700:
                    continue
                if post.get("file_ext") in ("gif", "webm", "mp4"):
                    continue
                url = post.get("large_file_url") or post.get("file_url")
                if not url:
                    continue
                score = int(post.get("score") or 0)
                fav = int(post.get("fav_count") or 0)
                area = min(w * h, 5000000) / 1000000
                # Ranking fav/score + resolution; then randomise within the
                # good pool so the same character is not always identical.
                rank = score * 1.5 + fav * 2.0 + area
                scored.append((rank, url))

            scored.sort(reverse=True, key=lambda x: x[0])
            pool = [u for _, u in scored[:30]]
            random.shuffle(pool)
            for u in pool:
                add_url(u)
            if len(urls) >= 12:
                break
    except Exception as e:
        print("Danbooru artwork search:", e)

    # ---------- Jikan: exact character fallback ----------
    try:
        r = requests.get(
            f"{JIKAN_URL}/characters",
            params={"q": character_name, "limit": 3},
            headers=headers,
            timeout=ARTWORK_TIMEOUT,
        )
        r.raise_for_status()
        results = r.json().get("data") or []
        target = key.replace("-", " ")
        chosen_result = next(
            (item for item in results
             if ((item.get("name") or "").strip().lower()) == target),
            results[0] if results else None,
        )
        if chosen_result and chosen_result.get("mal_id"):
            r2 = requests.get(
                f"{JIKAN_URL}/characters/{chosen_result['mal_id']}/pictures",
                headers=headers,
                timeout=ARTWORK_TIMEOUT,
            )
            r2.raise_for_status()
            for item in r2.json().get("data") or []:
                images = item.get("images") or {}
                for source in (images.get("webp") or {}, images.get("jpg") or {}):
                    add_url(source.get("large_image_url") or source.get("image_url"))
    except Exception as e:
        print("Jikan artwork search:", e)

    # ---------- Waifu.im: exact tag fallback ----------
    try:
        tags = getattr(fetch_premium_artwork, "_waifu_tags", None)
        if tags is None:
            tr = requests.get(
                "https://api.waifu.im/tags",
                params={"full": "true"},
                headers=headers,
                timeout=ARTWORK_TIMEOUT,
            )
            tr.raise_for_status()
            raw = tr.json().get("versatile") or []
            tags = []
            for t in raw:
                if isinstance(t, dict):
                    tags.append((str(t.get("name", "")), str(t.get("slug", ""))))
            fetch_premium_artwork._waifu_tags = tags

        norm = lambda v: " ".join(str(v).lower().replace("_", " ").replace("-", " ").split())
        target_norm = norm(character_name)
        matched = next(
            (slug_name for tag_name, slug_name in tags
             if norm(tag_name) == target_norm or norm(slug_name) == target_norm),
            None,
        )
        if matched:
            wr = requests.get(
                "https://api.waifu.im/search",
                params={
                    "included_tags": matched,
                    "is_nsfw": "false",
                    "gif": "false",
                    "orientation": "PORTRAIT",
                    "width": ">=800",
                    "height": ">=1000",
                    "many": "true",
                },
                headers=headers,
                timeout=ARTWORK_TIMEOUT,
            )
            if wr.ok:
                for item in wr.json().get("images") or []:
                    add_url(item.get("url") or item.get("preview_url"))
    except Exception as e:
        print("Waifu.im artwork search:", e)

    urls = list(dict.fromkeys(urls))[:40]
    if urls:
        cache[key] = urls
        fetch_premium_artwork._cache = cache
        choices = [u for u in urls if u != last.get(key)] or urls
        chosen = random.choice(choices)
        last[key] = chosen
        fetch_premium_artwork._last = last
        return chosen

    return fallback_url


def choose_card():
    chars = fetch_characters()
    if not chars:
        return None

    c = random.choice(chars)
    series = "Unknown"
    media = c.get("media", {}).get("nodes", [])
    if media:
        series = (
            media[0].get("title", {}).get("romaji")
            or "Unknown"
        )

    roll = random.random()
    if roll < 0.50:
        rarity = "Common"
    elif roll < 0.78:
        rarity = "Rare"
    elif roll < 0.93:
        rarity = "Epic"
    elif roll < 0.99:
        rarity = "Legendary"
    else:
        rarity = "Mythic"

    reward = random.randint(*REWARD_RANGES[rarity])

    name = c["name"]["full"]
    fallback_image = c["image"]["large"] if c.get("image") else ""
    artwork_url = fetch_premium_artwork(name, fallback_image, series)

    return {
        "character_id": c["id"],
        "name": name,
        "series": series,
        "image_url": artwork_url,
        "rarity": rarity,
        "reward": reward,
    }


def main_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📚 Collection", callback_data="collection"),
            InlineKeyboardButton("📊 Stats", callback_data="stats"),
        ],
        [
            InlineKeyboardButton("💰 Balance", callback_data="balance"),
            InlineKeyboardButton("🏆 Top 10", callback_data="top"),
        ],
        [
            InlineKeyboardButton("🎁 Daily", callback_data="daily"),
            InlineKeyboardButton("🛒 Shop ⭐", callback_data="shop"),
        ],
        [
            InlineKeyboardButton("🎁 Gifts", callback_data="gifts"),
            InlineKeyboardButton("👤 Check Info", callback_data="check"),
        ],
        [
            InlineKeyboardButton("❓ Help", callback_data="help"),
        ],
    ])


def shop_keyboard():
    rows = []
    for key, item in SHOP_ITEMS.items():
        rows.append([
            InlineKeyboardButton(
                f"{item['name']} — ⭐ {item['stars']}",
                callback_data=f"buy:{key}"
            )
        ])
    rows.append([InlineKeyboardButton("🎁 My Gifts", callback_data="gifts")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="start")])
    return InlineKeyboardMarkup(rows)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user)

    # User must press /start before they can catch cards.
    con = get_db()
    cur = con.cursor()
    cur.execute("UPDATE users SET started=1 WHERE user_id=?", (user.id,))
    con.commit()
    con.close()

    name = html.escape(user.first_name or "User")
    text = (
        "🎴 <b>Eren Character Bot</b>\n\n"
        f"ဟယ်လို 👋 <b>{name}</b>!\n\n"
        "Anime Character တွေကို Catch လုပ်ပြီး Collection စုနိုင်ပါတယ်။ 🎴\n\n"
        "🎯 Group ထဲမှာ Message 20 ခုတိုင်း Character ပေါ်လာပါမယ်။\n\n"
        "Character ပေါ်လာရင် <code>/guess Character Name</code> နဲ့ ဖမ်းနိုင်ပါတယ်။"
    )
    if update.message:
        await update.message.reply_text(
            text, parse_mode="HTML", reply_markup=main_menu()
        )


# =========================
# P2 — CHARACTER COMMANDS
# =========================

async def spawn_card(context, chat_id):
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT 1 FROM active_cards WHERE chat_id=?", (chat_id,))
    if cur.fetchone():
        con.close()
        return False
    con.close()

    card = choose_card()
    if not card:
        return False

    card_id = generate_card_id()
    con = get_db()
    cur = con.cursor()
    cur.execute("""
        INSERT INTO active_cards
        (chat_id, card_id, character_id, name, series, image_url, rarity, reward)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        chat_id, card_id, card["character_id"], card["name"],
        card["series"], card["image_url"], card["rarity"], card["reward"]
    ))
    con.commit()
    con.close()

    emoji = RARITIES[card["rarity"]][0]
    caption = (
        f"🎴 <b>CHARACTER SPAWN!</b>\n\n"
        f"🆔 Card ID: <code>{card_id}</code>\n"
        f"{emoji} Rarity: <b>{card['rarity']}</b>\n"
        f"💰 Reward: <b>+{card['reward']} coins</b>\n\n"
        "🎯 <code>/guess Character Name</code>"
    )

    try:
        if card["image_url"]:
            # Premium card artwork: blurred backdrop + fitted character art + rarity glow/frame.
            r = requests.get(card["image_url"], timeout=15)
            r.raise_for_status()
            src = Image.open(io.BytesIO(r.content)).convert("RGB")

            W, H = 900, 1125
            bg = ImageOps.fit(src, (W, H), method=Image.Resampling.LANCZOS)
            bg = bg.filter(ImageFilter.GaussianBlur(18))
            bg = ImageEnhance.Brightness(bg).enhance(0.45)

            art = ImageOps.fit(src, (760, 950), method=Image.Resampling.LANCZOS)
            art = ImageEnhance.Sharpness(art).enhance(1.25)
            art = ImageEnhance.Contrast(art).enhance(1.08)

            canvas = bg.copy()
            x, y = 70, 55
            canvas.paste(art, (x, y))

            draw = ImageDraw.Draw(canvas)
            rarity_colors = {
                "Common": (90, 210, 120),
                "Rare": (70, 150, 255),
                "Epic": (185, 90, 255),
                "Legendary": (255, 175, 45),
                "Mythic": (255, 75, 90),
            }
            rc = rarity_colors.get(card["rarity"], (255, 255, 255))

            # Outer premium frame
            for width in range(18, 0, -1):
                alpha = max(40, 255 - width * 10)
                c = tuple(int(v * alpha / 255) for v in rc)
                draw.rectangle((width, width, W-width-1, H-width-1), outline=c, width=2)

            draw.rounded_rectangle((42, 975, 858, 1085), radius=28, fill=(8, 8, 15), outline=rc, width=5)
            draw.text((70, 1000), f"{emoji}  {card['rarity'].upper()}", fill=rc)
            draw.text((70, 1040), f"CARD ID  {card_id}", fill=(245,245,245))

            # Small hidden-name marker keeps the game playable while looking like a collectible card.
            draw.rounded_rectangle((650, 35, 855, 92), radius=20, fill=(8,8,15), outline=rc, width=3)
            draw.text((684, 51), "???", fill=(255,255,255))

            buf = io.BytesIO()
            buf.name = f"card_{card_id}.jpg"
            canvas.save(buf, format="JPEG", quality=94, optimize=True)
            buf.seek(0)

            await context.bot.send_photo(
                chat_id=chat_id,
                photo=InputFile(buf, filename=buf.name),
                caption=caption,
                parse_mode="HTML",
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id, text=caption, parse_mode="HTML"
            )
    except Exception as e:
        print("Premium card image error:", e)
        try:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=card["image_url"],
                caption=caption,
                parse_mode="HTML",
            )
        except Exception:
            await context.bot.send_message(
                chat_id=chat_id, text=caption, parse_mode="HTML"
            )

    return True


async def count_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat:
        return
    if update.effective_chat.type == "private":
        return

    user = update.effective_user
    if not user or user.is_bot:
        return

    ensure_user(user)
    chat_id = update.effective_chat.id

    con = get_db()
    cur = con.cursor()

    cur.execute(
        "UPDATE users SET messages=messages+1 WHERE user_id=?",
        (user.id,)
    )
    cur.execute(
        "INSERT OR IGNORE INTO group_stats(chat_id,messages) VALUES(?,0)",
        (chat_id,)
    )
    cur.execute(
        "UPDATE group_stats SET messages=messages+1 WHERE chat_id=?",
        (chat_id,)
    )
    cur.execute(
        "SELECT messages FROM group_stats WHERE chat_id=?",
        (chat_id,)
    )
    count = cur.fetchone()["messages"]
    con.commit()
    con.close()

    if count % MESSAGE_LIMIT == 0:
        await spawn_card(context, chat_id)


def user_has_started(user_id):
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT started FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    con.close()
    return bool(row and row["started"] == 1)


def names_match(guess, real):
    g = " ".join(guess.lower().strip().split())
    r = " ".join(real.lower().strip().split())
    if not g:
        return False
    return g == r or g == r.split(" ")[0] or g in r


async def guess(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    ensure_user(update.effective_user)

    if not user_has_started(update.effective_user.id):
        await update.message.reply_text(
            "❌ အရင်ဆုံး /start ကိုနှိပ်ပြီး Bot ကို Start လုပ်ပါ။"
        )
        return

    if not context.args:
        await update.message.reply_text(
            "🎯 သုံးနည်း: <code>/guess Character Name</code>",
            parse_mode="HTML"
        )
        return

    guess_text = " ".join(context.args)
    chat_id = update.effective_chat.id

    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT * FROM active_cards WHERE chat_id=?", (chat_id,))
    card = cur.fetchone()

    if not card:
        con.close()
        await update.message.reply_text("❌ လက်ရှိဖမ်းစရာ Card မရှိပါဘူး။")
        return

    if not names_match(guess_text, card["name"]):
        con.close()
        await update.message.reply_text("❌ မမှန်သေးပါဘူး။ ထပ်ကြိုးစားပါ။")
        return

    cur.execute("""
        INSERT INTO collection
        (user_id, card_id, character_id, name, series, image_url, rarity, coins)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        update.effective_user.id, card["card_id"], card["character_id"],
        card["name"], card["series"], card["image_url"],
        card["rarity"], card["reward"]
    ))
    cur.execute(
        "UPDATE users SET coins=coins+?, caught=caught+1 WHERE user_id=?",
        (card["reward"], update.effective_user.id)
    )
    cur.execute("DELETE FROM active_cards WHERE chat_id=?", (chat_id,))
    con.commit()
    con.close()

    emoji = RARITIES.get(card["rarity"], ("🎴", 0))[0]
    await update.message.reply_text(
        f"🎉 <b>Caught!</b>\n\n"
        f"{emoji} <b>{html.escape(card['name'])}</b>\n"
        f"📺 {html.escape(card['series'])}\n"
        f"🆔 <code>{card['card_id']}</code>\n"
        f"💰 +{card['reward']} coins",
        parse_mode="HTML"
    )


async def reveal_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.reply_to_message:
        return

    chat_id = update.effective_chat.id
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT * FROM active_cards WHERE chat_id=?", (chat_id,))
    card = cur.fetchone()
    con.close()

    if not card:
        await update.message.reply_text("❌ Active Card မရှိပါဘူး။")
        return

    reply_id = update.message.reply_to_message.message_id
    if reply_id:
        emoji = RARITIES.get(card["rarity"], ("🎴", 0))[0]
        await update.message.reply_text(
            f"{emoji} <b>{html.escape(card['name'])}</b>\n"
            f"📺 {html.escape(card['series'])}\n"
            f"🆔 <code>{card['card_id']}</code>\n\n"
            f"🎯 <code>/guess {html.escape(card['name'])}</code>",
            parse_mode="HTML"
        )


async def collection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(update.effective_user)

    con = get_db()
    cur = con.cursor()
    cur.execute("""
        SELECT card_id,name,series,rarity
        FROM collection
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT 50
    """, (user_id,))
    rows = cur.fetchall()
    con.close()

    if not rows:
        await update.message.reply_text("📚 Collection မရှိသေးပါဘူး။")
        return

    groups = {}
    for row in rows:
        groups.setdefault(row["series"] or "Unknown", []).append(row)

    lines = ["熱•| <b>єяєη ιѕη'ѕ ʀᴇᴄᴇɴᴛ ᴄʜᴀʀᴀᴄᴛᴇʀꜱ</b>", ""]
    for series, cards in groups.items():
        lines.append(
            f"🗽 <b>{html.escape(series)}</b> ({len(cards)})"
        )
        lines.append("༺━━━━༻")
        for row in cards:
            emoji = RARITIES.get(row["rarity"], ("🎴", 0))[0]
            lines.append(
                f"<code>{row['card_id']}</code> | {emoji} | "
                f"{html.escape(row['name'])} (x1)"
            )
        lines.append("")

    await update.message.reply_text(
        "\n".join(lines), parse_mode="HTML"
    )


async def harem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    con = get_db()
    cur = con.cursor()
    cur.execute("""
        SELECT name,series,rarity,COUNT(*) AS qty
        FROM collection
        WHERE user_id=?
        GROUP BY character_id,name,series,rarity
        ORDER BY qty DESC,name ASC
        LIMIT 50
    """, (update.effective_user.id,))
    rows = cur.fetchall()
    con.close()

    if not rows:
        await update.message.reply_text("💖 Harem မရှိသေးပါဘူး။")
        return

    lines = ["💖 <b>Harem</b>", ""]
    for row in rows:
        emoji = RARITIES.get(row["rarity"], ("🎴", 0))[0]
        lines.append(
            f"{emoji} {html.escape(row['name'])} × <b>{row['qty']}</b>"
        )
    await update.message.reply_text(
        "\n".join(lines), parse_mode="HTML"
    )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    con = get_db()
    cur = con.cursor()
    cur.execute(
        "SELECT coins,messages,caught FROM users WHERE user_id=?",
        (update.effective_user.id,)
    )
    row = cur.fetchone()
    con.close()

    await update.message.reply_text(
        f"📊 <b>Your Stats</b>\n\n"
        f"💰 Coins: <b>{row['coins']}</b>\n"
        f"💬 Messages: <b>{row['messages']}</b>\n"
        f"🎴 Caught: <b>{row['caught']}</b>",
        parse_mode="HTML"
    )


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    con = get_db()
    cur = con.cursor()
    cur.execute(
        "SELECT coins FROM users WHERE user_id=?",
        (update.effective_user.id,)
    )
    coins = cur.fetchone()["coins"]
    con.close()
    await update.message.reply_text(f"💰 Balance: <b>{coins} coins</b>", parse_mode="HTML")


async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    con = get_db()
    cur = con.cursor()
    cur.execute("""
        SELECT username,coins,caught
        FROM users
        ORDER BY coins DESC,caught DESC
        LIMIT 10
    """)
    rows = cur.fetchall()
    con.close()

    lines = ["🏆 <b>Top 10</b>", ""]
    for i, row in enumerate(rows, 1):
        name = "@" + row["username"] if row["username"] else "User"
        lines.append(
            f"{i}. {html.escape(name)} — 💰 {row['coins']} | 🎴 {row['caught']}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def name_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.reply_to_message:
        await update.message.reply_text(
            "🔎 Character Card ကို Reply လုပ်ပြီး <code>/name</code> သုံးပါ။",
            parse_mode="HTML"
        )
        return
    await reveal_name(update, context)


async def check_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user profile/info. Supports self, reply-to-user, or @username."""
    if not update.message:
        return

    target = None

    # Reply mode: /check while replying to another user
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target = update.message.reply_to_message.from_user

    # Username mode: /check @username
    elif context.args:
        username = context.args[0].lstrip("@").lower()
        con = get_db()
        cur = con.cursor()
        cur.execute(
            "SELECT user_id,username FROM users WHERE LOWER(username)=? LIMIT 1",
            (username,)
        )
        row = cur.fetchone()
        con.close()
        if row:
            class DBUser:
                pass
            target = DBUser()
            target.id = row["user_id"]
            target.username = row["username"]
            target.first_name = row["username"] or "User"

    # Default: check yourself
    if target is None:
        target = update.effective_user

    ensure_user(target)
    con = get_db()
    cur = con.cursor()
    cur.execute(
        "SELECT username,coins,messages,caught,last_daily,started FROM users WHERE user_id=?",
        (target.id,)
    )
    row = cur.fetchone()
    con.close()

    if not row:
        await update.message.reply_text("❌ User info မတွေ့ပါဘူး။")
        return

    display_name = getattr(target, "first_name", None) or row["username"] or "User"
    username_text = f"@{row['username']}" if row["username"] else "မရှိပါ"
    status = "✅ Started" if row["started"] else "❌ Not Started"

    text = (
        "👤 <b>User Check Info</b>\n\n"
        f"🧑 Name: <b>{html.escape(display_name)}</b>\n"
        f"🔗 Username: <b>{html.escape(username_text)}</b>\n"
        f"🆔 User ID: <code>{target.id}</code>\n"
        f"{status}\n\n"
        f"💰 Coins: <b>{row['coins']}</b>\n"
        f"🎴 Caught: <b>{row['caught']}</b>\n"
        f"💬 Messages: <b>{row['messages']}</b>"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def card_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "🆔 သုံးနည်း: <code>/card 3356</code>",
            parse_mode="HTML"
        )
        return

    try:
        card_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Card ID မမှန်ပါဘူး။")
        return

    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT * FROM active_cards WHERE card_id=?", (card_id,))
    row = cur.fetchone()

    if not row:
        cur.execute("SELECT * FROM collection WHERE card_id=?", (card_id,))
        row = cur.fetchone()
    con.close()

    if not row:
        await update.message.reply_text("❌ Card မတွေ့ပါဘူး။")
        return

    emoji = RARITIES.get(row["rarity"], ("🎴", 0))[0]
    text = (
        f"🆔 <code>{row['card_id']}</code>\n"
        f"{emoji} <b>{html.escape(row['name'])}</b>\n"
        f"📺 {html.escape(row['series'])}\n"
        f"⭐ {html.escape(row['rarity'])}"
    )

    if row["image_url"]:
        try:
            await update.message.reply_photo(
                row["image_url"], caption=text, parse_mode="HTML"
            )
            return
        except Exception:
            pass
    await update.message.reply_text(text, parse_mode="HTML")


# =========================
# P3 — DAILY / STARS / GIFTS / HELP
# =========================

async def daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    today = datetime.now(timezone.utc).date().isoformat()

    con = get_db()
    cur = con.cursor()
    cur.execute(
        "SELECT last_daily FROM users WHERE user_id=?",
        (update.effective_user.id,)
    )
    row = cur.fetchone()

    if row["last_daily"] == today:
        con.close()
        await update.message.reply_text(
            "⏳ ဒီနေ့ Daily Reward ယူပြီးပါပြီ။ မနက်ဖြန် ပြန်လာပါ။"
        )
        return

    reward = random.randint(50, 100)
    cur.execute("""
        UPDATE users
        SET coins=coins+?, last_daily=?
        WHERE user_id=?
    """, (reward, today, update.effective_user.id))
    con.commit()
    con.close()

    await update.message.reply_text(
        f"🎁 <b>Daily Reward!</b>\n\n💰 +{reward} coins ရပါပြီ။",
        parse_mode="HTML"
    )


async def shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    await update.message.reply_text(
        "🛒 <b>Telegram Stars Shop</b>\n\n"
        "⭐ အောက်က Item ကိုရွေးပြီး Telegram Stars နဲ့ ဝယ်ပါ။",
        parse_mode="HTML",
        reply_markup=shop_keyboard()
    )


async def send_shop_invoice(query, context, item_key):
    item = SHOP_ITEMS.get(item_key)
    if not item:
        await query.answer("❌ Item မတွေ့ပါဘူး။", show_alert=True)
        return

    user_id = query.from_user.id
    payload = f"shop:{item_key}:{user_id}"

    await context.bot.send_invoice(
        chat_id=query.message.chat_id,
        title=item["name"],
        description=f"{item['name']} — {item['stars']} Telegram Stars",
        payload=payload,
        currency="XTR",
        prices=[LabeledPrice(item["name"], item["stars"])],
        provider_token="",
    )
    await query.answer()


async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    if not query:
        return

    payload = query.invoice_payload
    parts = payload.split(":")
    if len(parts) != 3 or parts[0] != "shop":
        await query.answer(ok=False, error_message="Invalid payment.")
        return

    item = SHOP_ITEMS.get(parts[1])
    try:
        uid = int(parts[2])
    except ValueError:
        uid = -1

    if not item or uid != query.from_user.id:
        await query.answer(ok=False, error_message="Invalid shop payment.")
        return

    if query.currency != "XTR" or query.total_amount != item["stars"]:
        await query.answer(ok=False, error_message="Payment amount မမှန်ပါ။")
        return

    await query.answer(ok=True)


async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    if not payment:
        return

    parts = payment.invoice_payload.split(":")
    if len(parts) != 3 or parts[0] != "shop":
        return

    item_key = parts[1]
    try:
        uid = int(parts[2])
    except ValueError:
        return

    item = SHOP_ITEMS.get(item_key)
    if not item or uid != update.effective_user.id:
        return
    if payment.currency != "XTR" or payment.total_amount != item["stars"]:
        return

    con = get_db()
    cur = con.cursor()

    cur.execute(
        "SELECT id FROM star_payments WHERE telegram_payment_charge_id=?",
        (payment.telegram_payment_charge_id,)
    )
    if cur.fetchone():
        con.close()
        return

    cur.execute("""
        INSERT INTO gifts(user_id,item_key,item_name,quantity)
        VALUES(?,?,?,1)
        ON CONFLICT(user_id,item_key)
        DO UPDATE SET quantity=quantity+1
    """, (uid, item_key, item["name"]))

    cur.execute("""
        INSERT INTO star_payments
        (user_id,payload,item_key,stars,telegram_payment_charge_id,provider_payment_charge_id)
        VALUES(?,?,?,?,?,?)
    """, (
        uid, payment.invoice_payload, item_key, payment.total_amount,
        payment.telegram_payment_charge_id,
        payment.provider_payment_charge_id
    ))

    cur.execute("""
        INSERT INTO shop_purchases
        (user_id,item_key,item_name,stars,quantity,telegram_payment_charge_id,provider_payment_charge_id)
        VALUES(?,?,?,?,1,?,?)
    """, (
        uid, item_key, item["name"], payment.total_amount,
        payment.telegram_payment_charge_id,
        payment.provider_payment_charge_id
    ))

    con.commit()
    con.close()

    await update.message.reply_text(
        f"✅ <b>Payment Successful!</b>\n\n"
        f"{item['name']}\n"
        f"⭐ Paid: <b>{payment.total_amount} Stars</b>\n\n"
        "🎁 Gifts ထဲ ထည့်ပြီးပါပြီ။\n"
        "<code>/gifts</code>",
        parse_mode="HTML"
    )


async def gifts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    con = get_db()
    cur = con.cursor()
    cur.execute("""
        SELECT item_name,quantity FROM gifts
        WHERE user_id=? AND quantity>0 ORDER BY item_key
    """, (update.effective_user.id,))
    rows = cur.fetchall()
    con.close()

    if not rows:
        await update.message.reply_text(
            "🎁 <b>My Gifts</b>\n\nGift မရှိသေးပါဘူး။\n\n/shop ကနေ ဝယ်နိုင်ပါတယ်။",
            parse_mode="HTML"
        )
        return

    lines = ["🎁 <b>My Gifts</b>", ""]
    for r in rows:
        lines.append(f"{r['item_name']} × <b>{r['quantity']}</b>")
    lines += ["", "🎁 ပို့ရန်: <code>/give small</code> (Reply mode)"]
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def give(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sender = update.effective_user
    ensure_user(sender)

    recipient_id = None
    item_key = None

    if update.message.reply_to_message:
        recipient_id = update.message.reply_to_message.from_user.id
        if context.args:
            item_key = context.args[0].lower()
    elif len(context.args) >= 2:
        username = context.args[0].lstrip("@").lower()
        item_key = context.args[1].lower()
        con = get_db()
        cur = con.cursor()
        cur.execute(
            "SELECT user_id FROM users WHERE LOWER(username)=? LIMIT 1",
            (username,)
        )
        row = cur.fetchone()
        con.close()
        if row:
            recipient_id = row["user_id"]

    if not recipient_id or not item_key:
        await update.message.reply_text(
            "🎁 Reply လုပ်ပြီး <code>/give small</code>\n"
            "သို့မဟုတ် <code>/give @username small</code>",
            parse_mode="HTML"
        )
        return

    if recipient_id == sender.id:
        await update.message.reply_text("❌ ကိုယ့်ကိုယ်ကို Gift ပို့လို့မရပါဘူး။")
        return

    if item_key not in SHOP_ITEMS:
        await update.message.reply_text("❌ Gift အမျိုးအစား မရှိပါဘူး။")
        return

    item = SHOP_ITEMS[item_key]
    con = get_db()
    cur = con.cursor()
    try:
        cur.execute("BEGIN IMMEDIATE")
        cur.execute(
            "SELECT quantity FROM gifts WHERE user_id=? AND item_key=?",
            (sender.id, item_key)
        )
        own = cur.fetchone()
        if not own or own["quantity"] <= 0:
            con.rollback()
            con.close()
            await update.message.reply_text(f"❌ {item['name']} မရှိပါဘူး။")
            return

        cur.execute(
            "SELECT user_id FROM users WHERE user_id=?",
            (recipient_id,)
        )
        if not cur.fetchone():
            con.rollback()
            con.close()
            await update.message.reply_text("❌ ဒီ User က Bot ကို မသုံးဖူးသေးပါဘူး။")
            return

        cur.execute(
            "UPDATE gifts SET quantity=quantity-1 WHERE user_id=? AND item_key=?",
            (sender.id, item_key)
        )
        cur.execute("""
            INSERT INTO gifts(user_id,item_key,item_name,quantity)
            VALUES(?,?,?,1)
            ON CONFLICT(user_id,item_key)
            DO UPDATE SET quantity=quantity+1
        """, (recipient_id, item_key, item["name"]))
        con.commit()
    except Exception as e:
        con.rollback()
        print("Gift error:", e)
        con.close()
        await update.message.reply_text("❌ Gift ပို့ရာမှာ Error ဖြစ်ပါတယ်။")
        return
    con.close()

    await update.message.reply_text(
        f"🎁 <b>Gift Sent!</b>\n\n{item['name']}\n\n✅ 1 ခု ပို့ပြီးပါပြီ။",
        parse_mode="HTML"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❓ <b>Help</b>\n\n"
        "🎯 /guess Name — Character ဖမ်းရန်\n"
        "📚 /collection — Collection\n"
        "💖 /harem — Harem\n"
        "📊 /stats — Stats\n"
        "💰 /balance — Coins\n"
        "🏆 /top — Top 10\n"
        "🎁 /daily — Daily Reward\n"
        "🛒 /shop — Telegram Stars Shop\n"
        "🎁 /gifts — Gifts\n"
        "🎁 /give — Gift ပေးရန်\n"
        "🔎 /name — Name\n"
        "🆔 /card ID — Card Info\n"
        "👤 /check — User Info",
        parse_mode="HTML"
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user = query.from_user
    ensure_user(user)

    if data == "shop":
        await query.answer()
        await query.edit_message_text("🛒 <b>Telegram Stars Shop</b>\n\n⭐ Item ရွေးပါ။", parse_mode="HTML", reply_markup=shop_keyboard())
        return
    if data.startswith("buy:"):
        await send_shop_invoice(query, context, data.split(":", 1)[1])
        return
    if data == "start":
        await query.answer()
        await query.edit_message_text("🎴 <b>Eren Character Bot</b>\n\nCharacter Catch & Collection Bot 🎴", parse_mode="HTML", reply_markup=main_menu())
        return
    if data == "gifts":
        await query.answer()
        con=get_db(); cur=con.cursor(); cur.execute("SELECT item_name,quantity FROM gifts WHERE user_id=? AND quantity>0",(user.id,)); rows=cur.fetchall(); con.close()
        text="🎁 <b>My Gifts</b>\n\n" + ("\n".join(f"{r['item_name']} × <b>{r['quantity']}</b>" for r in rows) if rows else "Gift မရှိသေးပါဘူး။")
        await query.edit_message_text(text,parse_mode="HTML",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Shop ⭐",callback_data="shop")],[InlineKeyboardButton("⬅️ Back",callback_data="start")]]))
        return
    if data == "collection":
        await query.answer(); con=get_db(); cur=con.cursor(); cur.execute("SELECT card_id,name,series,rarity FROM collection WHERE user_id=? ORDER BY id DESC LIMIT 50",(user.id,)); rows=cur.fetchall(); con.close()
        if not rows: text="📚 <b>Collection</b>\n\nCollection မရှိသေးပါဘူး။"
        else:
            groups={}
            for r in rows: groups.setdefault(r['series'] or 'Unknown',[]).append(r)
            lines=["📚 <b>Collection</b>",""]
            for series,cards in groups.items():
                lines += [f"🗽 <b>{html.escape(series)}</b> ({len(cards)})","༺━━━━༻"]
                for r in cards: lines.append(f"<code>{r['card_id']}</code> | {RARITIES.get(r['rarity'],('🎴',0))[0]} | {html.escape(r['name'])} (x1)")
                lines.append("")
            text="\n".join(lines)
        await query.edit_message_text(text,parse_mode="HTML",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💖 Harem",callback_data="harem")],[InlineKeyboardButton("⬅️ Back",callback_data="start")]])); return
    if data == "harem":
        await query.answer(); con=get_db(); cur=con.cursor(); cur.execute("SELECT name,series,rarity,COUNT(*) AS qty FROM collection WHERE user_id=? GROUP BY character_id,name,series,rarity ORDER BY qty DESC,name ASC LIMIT 50",(user.id,)); rows=cur.fetchall(); con.close()
        text="💖 <b>Harem</b>\n\n"+ ("\n".join(f"{RARITIES.get(r['rarity'],('🎴',0))[0]} {html.escape(r['name'])} × <b>{r['qty']}</b>" for r in rows) if rows else "Harem မရှိသေးပါဘူး။")
        await query.edit_message_text(text,parse_mode="HTML",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📚 Collection",callback_data="collection")],[InlineKeyboardButton("⬅️ Back",callback_data="start")]])); return
    if data == "stats":
        await query.answer(); con=get_db(); cur=con.cursor(); cur.execute("SELECT coins,messages,caught FROM users WHERE user_id=?",(user.id,)); r=cur.fetchone(); con.close(); r=r or {'coins':0,'messages':0,'caught':0}
        text=f"📊 <b>Your Stats</b>\n\n💰 Coins: <b>{r['coins']}</b>\n💬 Messages: <b>{r['messages']}</b>\n🎴 Caught: <b>{r['caught']}</b>"
        await query.edit_message_text(text,parse_mode="HTML",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💰 Balance",callback_data="balance")],[InlineKeyboardButton("⬅️ Back",callback_data="start")]])); return
    if data == "balance":
        await query.answer(); con=get_db(); cur=con.cursor(); cur.execute("SELECT coins FROM users WHERE user_id=?",(user.id,)); r=cur.fetchone(); con.close(); coins=r['coins'] if r else 0
        await query.edit_message_text(f"💰 <b>Balance</b>\n\n🪙 Coins: <b>{coins}</b>",parse_mode="HTML",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📊 Stats",callback_data="stats"),InlineKeyboardButton("🎁 Daily",callback_data="daily")],[InlineKeyboardButton("⬅️ Back",callback_data="start")]])); return
    if data == "top":
        await query.answer(); con=get_db(); cur=con.cursor(); cur.execute("SELECT username,coins,caught FROM users ORDER BY coins DESC,caught DESC LIMIT 10"); rows=cur.fetchall(); con.close(); lines=["🏆 <b>Top 10</b>",""]
        for i,r in enumerate(rows,1): lines.append(f"{i}. {html.escape('@'+r['username'] if r['username'] else 'User')} — 💰 {r['coins']} | 🎴 {r['caught']}")
        await query.edit_message_text("\n".join(lines),parse_mode="HTML",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back",callback_data="start")]])); return
    if data == "daily":
        await query.answer(); today=datetime.now(timezone.utc).date().isoformat(); con=get_db(); cur=con.cursor(); cur.execute("SELECT last_daily FROM users WHERE user_id=?",(user.id,)); r=cur.fetchone()
        if r and r['last_daily']==today: text="⏳ ဒီနေ့ Daily Reward ယူပြီးပါပြီ။ မနက်ဖြန် ပြန်လာပါ။"
        else:
            reward=random.randint(50,100); cur.execute("UPDATE users SET coins=coins+?, last_daily=? WHERE user_id=?",(reward,today,user.id)); con.commit(); text=f"🎁 <b>Daily Reward!</b>\n\n💰 +{reward} coins ရပါပြီ။"
        con.close(); await query.edit_message_text(text,parse_mode="HTML",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💰 Balance",callback_data="balance")],[InlineKeyboardButton("⬅️ Back",callback_data="start")]])); return
    if data == "check":
        await query.answer(); con=get_db(); cur=con.cursor(); cur.execute("SELECT username,coins,messages,caught,started FROM users WHERE user_id=?",(user.id,)); r=cur.fetchone(); con.close(); r=r or {'username':'','coins':0,'messages':0,'caught':0,'started':0}
        uname=f"@{r['username']}" if r['username'] else 'မရှိပါ'; status='✅ Started' if r['started'] else '❌ Not Started'
        text=f"👤 <b>User Check Info</b>\n\n🧑 Name: <b>{html.escape(user.first_name or 'User')}</b>\n🔗 Username: <b>{html.escape(uname)}</b>\n🆔 User ID: <code>{user.id}</code>\n{status}\n\n💰 Coins: <b>{r['coins']}</b>\n🎴 Caught: <b>{r['caught']}</b>\n💬 Messages: <b>{r['messages']}</b>"
        await query.edit_message_text(text,parse_mode="HTML",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back",callback_data="start")]])); return
    if data == "help":
        await query.answer(); text=("❓ <b>Help</b>\n\n🎯 /guess Name — Character ဖမ်းရန်\n📚 /collection — Collection\n💖 /harem — Harem\n📊 /stats — Stats\n💰 /balance — Coins\n🏆 /top — Top 10\n🎁 /daily — Daily Reward\n🛒 /shop — Telegram Stars Shop\n🎁 /gifts — Gifts\n🎁 /give — Gift ပေးရန်\n🔎 /name — Name\n🆔 /card ID — Card Info\n👤 /check — User Info")
        await query.edit_message_text(text,parse_mode="HTML",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back",callback_data="start")]])); return
    await query.answer("Unknown button",show_alert=True)


async def post_init(application):
    commands = [
        BotCommand("start", "🎴 Start"),
        BotCommand("daily", "🎁 Daily Reward"),
        BotCommand("guess", "🎯 Guess Character"),
        BotCommand("name", "🔎 Character Name"),
        BotCommand("card", "🆔 Card Info"),
        BotCommand("collection", "📚 Collection"),
        BotCommand("harem", "💖 Harem"),
        BotCommand("stats", "📊 Stats"),
        BotCommand("balance", "💰 Balance"),
        BotCommand("top", "🏆 Top 10"),
        BotCommand("shop", "🛒 Stars Shop"),
        BotCommand("gifts", "🎁 My Gifts"),
        BotCommand("give", "🎁 Give Gift"),
        BotCommand("check", "👤 Check User Info"),
        BotCommand("help", "❓ Help"),
    ]
    await application.bot.set_my_commands(commands)
    print("✅ Telegram Menu Button updated!")


async def error_handler(update, context):
    print("❌ Bot Error:", context.error)


# =========================
# MAIN
# =========================

def main():
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN မတွေ့ပါဘူး။")
        return

    init_db()
    print("🤖 Eren Character Bot is starting...")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("daily", daily))
    app.add_handler(CommandHandler("shop", shop))
    app.add_handler(CommandHandler("gifts", gifts))
    app.add_handler(CommandHandler("give", give))
    app.add_handler(CommandHandler("check", check_info))
    app.add_handler(CommandHandler("help", help_command))

    app.add_handler(CommandHandler("guess", guess))
    app.add_handler(CommandHandler("collection", collection))
    app.add_handler(CommandHandler("harem", harem))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("top", top))
    app.add_handler(CommandHandler("name", name_info))
    app.add_handler(CommandHandler("card", card_info))

    app.add_handler(MessageHandler(filters.Regex(r"^\.n$"), reveal_name))

    app.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    app.add_handler(
        MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback)
    )
    app.add_handler(CallbackQueryHandler(button_handler))

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            count_messages
        )
    )

    app.add_error_handler(error_handler)

    print("✅ Bot is running!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
