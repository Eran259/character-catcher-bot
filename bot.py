import os
import sqlite3
import random
import requests

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_FILE = "character.db"

MESSAGE_LIMIT = 20


# =========================
# DATABASE
# =========================

def init_db():
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            coins INTEGER DEFAULT 100,
            messages INTEGER DEFAULT 0,
            caught INTEGER DEFAULT 0
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS collection (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
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
            character_id INTEGER,
            name TEXT,
            series TEXT,
            image_url TEXT,
            rarity TEXT,
            reward INTEGER
        )
    """)

    con.commit()
    con.close()


# =========================
# CHARACTER API
# =========================

def get_character():
    try:
        url = "https://graphql.anilist.co"

        query = """
        query {
            Page(page: 1, perPage: 50) {
                characters(sort: FAVOURITES_DESC) {
                    id
                    name {
                        full
                    }
                    image {
                        large
                    }
                    media {
                        nodes {
                            title {
                                romaji
                            }
                        }
                    }
                }
            }
        }
        """

        response = requests.post(
            url,
            json={"query": query},
            timeout=20
        )

        print("Character API status:", response.status_code)

        if response.status_code != 200:
            print("Character API error:", response.text)
            return None

        data = response.json()

        characters = data["data"]["Page"]["characters"]

        if not characters:
            return None

        character = random.choice(characters)

        series = "Unknown Anime"

        media = character.get("media", {}).get("nodes", [])

        if media:
            title = media[0].get("title", {})
            series = title.get("romaji") or "Unknown Anime"

        return {
            "id": character["id"],
            "name": character["name"]["full"],
            "series": series,
            "image_url": character["image"]["large"],
        }

    except Exception as e:
        print("Character API error:", e)
        return None


# =========================
# RARITY
# =========================

def random_rarity():

    chance = random.randint(1, 100)

    if chance <= 50:
        return "🟢 Common", 10

    elif chance <= 80:
        return "🔵 Rare", 25

    elif chance <= 95:
        return "🟣 Epic", 50

    elif chance <= 99:
        return "🟠 Legendary", 100

    else:
        return "🔴 Mythic", 250


# =========================
# USER
# =========================

def register_user(user):

    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()

    cur.execute("""
        INSERT OR IGNORE INTO users
        (user_id, username, coins, messages, caught)
        VALUES (?, ?, 100, 0, 0)
    """, (
        user.id,
        user.username or user.first_name
    ))

    cur.execute("""
        UPDATE users
        SET username = ?
        WHERE user_id = ?
    """, (
        user.username or user.first_name,
        user.id
    ))

    con.commit()
    con.close()


# =========================
# MAIN MENU
# =========================

def main_menu():

    buttons = [
        [
            InlineKeyboardButton(
                "🎒 Collection",
                callback_data="collection"
            ),
            InlineKeyboardButton(
                "👤 Stats",
                callback_data="stats"
            )
        ],
        [
            InlineKeyboardButton(
                "💰 Balance",
                callback_data="balance"
            ),
            InlineKeyboardButton(
                "🏆 Top 10",
                callback_data="top"
            )
        ],
        [
            InlineKeyboardButton(
                "📖 Commands",
                callback_data="commands"
            )
        ]
    ]

    return InlineKeyboardMarkup(buttons)


# =========================
# START
# =========================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    register_user(user)

    username = (
        f"@{user.username}"
        if user.username
        else "No Username"
    )

    text = (
        "🎮 <b>Character Catcher Bot မှ ကြိုဆိုပါတယ်!</b>\n\n"
        "👤 <b>User Info:</b>\n"
        f"📛 Name: {user.full_name}\n"
        f"🔗 Username: {username}\n"
        f"🆔 Chat ID: {user.id}\n\n"
        "🎴 <b>Card တွေ ကောက်ယူနည်း:</b>\n"
        "• Group ထဲမှာ စာတွေ ရေးပါ\n"
        "• Character Card တွေ ကျလာပါလိမ့်မယ်\n"
        "• /guess &lt;name&gt; ရိုက်ပြီး ကောက်ယူပါ\n"
        "• မှန်ရင် Card က သင့်ဆီ ရောက်သွားပါမယ်\n"
        "• Coins လည်း ရပါမယ်\n\n"
        "💬 <b>Group ထဲမှာ စာရေးပြီး ဇာတ်ကောင် ဖမ်းပါ!</b>"
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=main_menu()
    )


# =========================
# MESSAGE COUNTER
# =========================

async def count_messages(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    if not update.effective_chat:
        return

    if update.effective_chat.type not in (
        "group",
        "supergroup"
    ):
        return

    if not update.effective_user:
        return

    user = update.effective_user

    if user.is_bot:
        return

    register_user(user)

    chat_id = update.effective_chat.id

    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()

    cur.execute("""
        UPDATE users
        SET messages = messages + 1
        WHERE user_id = ?
    """, (user.id,))

    con.commit()

    cur.execute("""
        SELECT messages
        FROM users
        WHERE user_id = ?
    """, (user.id,))

    row = cur.fetchone()

    con.close()

    if not row:
        return

    total_messages = row[0]

    if total_messages % MESSAGE_LIMIT != 0:
        return

    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()

    cur.execute("""
        SELECT character_id
        FROM active_cards
        WHERE chat_id = ?
    """, (chat_id,))

    existing = cur.fetchone()

    con.close()

    if existing:
        return

    character = get_character()

    if not character:
        await update.message.reply_text(
            "❌ Character API is currently unavailable."
        )
        return

    rarity, reward = random_rarity()

    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()

    cur.execute("""
        INSERT OR REPLACE INTO active_cards
        (
            chat_id,
            character_id,
            name,
            series,
            image_url,
            rarity,
            reward
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        chat_id,
        character["id"],
        character["name"],
        character["series"],
        character["image_url"],
        rarity,
        reward
    ))

    con.commit()
    con.close()

    text = (
        "🎴 <b>A NEW CHARACTER HAS APPEARED!</b>\n\n"
        f"💎 Rarity: {rarity}\n"
        f"📺 Anime: <b>{character['series']}</b>\n\n"
        "❓ <b>Who is this character?</b>\n\n"
        "💬 Use:\n"
        "<code>/guess &lt;name&gt;</code>"
    )

    await update.message.reply_photo(
        photo=character["image_url"],
        caption=text,
        parse_mode="HTML"
)

# =========================
# GUESS
# =========================

async def guess(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    user = update.effective_user
    chat = update.effective_chat

    register_user(user)

    if not context.args:
        await update.message.reply_text(
            "❓ Character name ထည့်ပေးပါ။\n\n"
            "ဥပမာ:\n"
            "/guess Gojo Satoru"
        )
        return

    guess_name = " ".join(context.args).strip().lower()

    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()

    cur.execute("""
        SELECT character_id, name, series, image_url, rarity, reward
        FROM active_cards
        WHERE chat_id = ?
    """, (chat.id,))

    card = cur.fetchone()

    if not card:
        con.close()

        await update.message.reply_text(
            "❌ လက်ရှိမှာ ဖမ်းစရာ Card မရှိသေးပါဘူး။"
        )
        return

    character_id, name, series, image_url, rarity, reward = card

    real_name = name.lower()
    first_name = name.split()[0].lower()

    if guess_name != real_name and guess_name != first_name:
        con.close()

        await update.message.reply_text(
            "❌ မမှန်သေးပါဘူး!\n"
            "ဆက်ပြီး Guess လုပ်ကြည့်ပါ 🎴"
        )
        return

    # Add card to collection
    cur.execute("""
        INSERT INTO collection
        (
            user_id,
            character_id,
            name,
            series,
            image_url,
            rarity,
            coins
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        user.id,
        character_id,
        name,
        series,
        image_url,
        rarity,
        reward
    ))

    # Add coins
    cur.execute("""
        UPDATE users
        SET coins = coins + ?,
            caught = caught + 1
        WHERE user_id = ?
    """, (
        reward,
        user.id
    ))

    # Remove active card
    cur.execute("""
        DELETE FROM active_cards
        WHERE chat_id = ?
    """, (chat.id,))

    con.commit()
    con.close()

    await update.message.reply_text(
        "🎉 <b>CARD CAUGHT!</b>\n\n"
        f"👤 Character: <b>{name}</b>\n"
        f"📺 Anime: <b>{series}</b>\n"
        f"💎 Rarity: {rarity}\n"
        f"💰 Reward: +{reward} Coins\n\n"
        f"🎴 <b>{user.first_name}</b> က Card ကို ဖမ်းလိုက်ပါပြီ!"
        ,
        parse_mode="HTML"
    )


# =========================
# COLLECTION
# =========================

async def collection(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    register_user(user)

    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()

    cur.execute("""
        SELECT name, series, rarity
        FROM collection
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT 20
    """, (user.id,))

    cards = cur.fetchall()

    con.close()

    if not cards:
        await update.message.reply_text(
            "🎒 <b>Your Collection</b>\n\n"
            "📭 Card မရှိသေးပါဘူး။\n"
            "Group ထဲမှာ စာရေးပြီး Card ဖမ်းပါ 🎴",
            parse_mode="HTML"
        )
        return

    text = "🎒 <b>YOUR COLLECTION</b>\n\n"

    for i, (name, series, rarity) in enumerate(cards, 1):
        text += (
            f"{i}. {rarity} <b>{name}</b>\n"
            f"   📺 {series}\n\n"
        )

    await update.message.reply_text(
        text,
        parse_mode="HTML"
    )


# =========================
# HAREM
# =========================

async def harem(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()

    cur.execute("""
        SELECT name, rarity
        FROM collection
        WHERE user_id = ?
        ORDER BY id DESC
    """, (user.id,))

    cards = cur.fetchall()

    con.close()

    if not cards:
        await update.message.reply_text(
            "💖 <b>Your Harem</b>\n\n"
            "Empty ဖြစ်နေပါသေးတယ် 😅\n"
            "Character တွေကို ဖမ်းယူပါ!",
            parse_mode="HTML"
        )
        return

    text = "💖 <b>YOUR HAREM</b>\n\n"

    for i, (name, rarity) in enumerate(cards, 1):
        text += f"{i}. {rarity} <b>{name}</b>\n"

    await update.message.reply_text(
        text,
        parse_mode="HTML"
    )


# =========================
# STATS
# =========================

async def stats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    register_user(user)

    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()

    cur.execute("""
        SELECT coins, messages, caught
        FROM users
        WHERE user_id = ?
    """, (user.id,))

    row = cur.fetchone()

    con.close()

    if not row:
        return

    coins, messages, caught = row

    await update.message.reply_text(
        "👤 <b>YOUR STATS</b>\n\n"
        f"📛 Name: <b>{user.full_name}</b>\n"
        f"💰 Coins: <b>{coins}</b>\n"
        f"💬 Messages: <b>{messages}</b>\n"
        f"🎴 Cards Caught: <b>{caught}</b>",
        parse_mode="HTML"
    )


# =========================
# BALANCE
# =========================

async def balance(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    register_user(user)

    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()

    cur.execute("""
        SELECT coins
        FROM users
        WHERE user_id = ?
    """, (user.id,))

    row = cur.fetchone()

    con.close()

    coins = row[0] if row else 0

    await update.message.reply_text(
        "💰 <b>YOUR BALANCE</b>\n\n"
        f"🪙 Coins: <b>{coins}</b>",
        parse_mode="HTML"
    )


# =========================
# TOP 10
# =========================

async def top(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()

    cur.execute("""
        SELECT username, coins, caught
        FROM users
        ORDER BY coins DESC
        LIMIT 10
    """)

    rows = cur.fetchall()

    con.close()

    if not rows:
        await update.message.reply_text(
            "🏆 Leaderboard မရှိသေးပါဘူး။"
        )
        return

    text = "🏆 <b>TOP 10 PLAYERS</b>\n\n"

    for i, (username, coins, caught) in enumerate(rows, 1):

        display_name = username or "Unknown"

        text += (
            f"{i}. <b>{display_name}</b>\n"
            f"   🪙 {coins} Coins | 🎴 {caught} Cards\n\n"
        )

    await update.message.reply_text(
        text,
        parse_mode="HTML"
    )


# =========================
# CHARACTER INFO
# =========================

async def name_info(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not context.args:
        await update.message.reply_text(
            "❓ Character name ထည့်ပေးပါ။\n\n"
            "ဥပမာ:\n"
            "/name Gojo"
        )
        return

    search_name = " ".join(context.args).strip().lower()

    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()

    cur.execute("""
        SELECT name, series, image_url, rarity
        FROM collection
        WHERE user_id = ?
        AND LOWER(name) LIKE ?
        ORDER BY id DESC
        LIMIT 1
    """, (
        user.id,
        "%" + search_name + "%"
    ))

    row = cur.fetchone()

    con.close()

    if not row:
        await update.message.reply_text(
            "❌ ဒီ Character ကို Collection ထဲမှာ မတွေ့ပါဘူး။"
        )
        return

    name, series, image_url, rarity = row

    text = (
        f"🎴 <b>{name}</b>\n\n"
        f"📺 Anime: <b>{series}</b>\n"
        f"💎 Rarity: {rarity}"
    )

    try:
        await update.message.reply_photo(
            photo=image_url,
            caption=text,
            parse_mode="HTML"
        )
    except Exception:
        await update.message.reply_text(
            text,
            parse_mode="HTML"
        )


# =========================
# BUTTON HANDLER
# =========================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    user = query.from_user

    register_user(user)

    if query.data == "collection":

        con = sqlite3.connect(DB_FILE)
        cur = con.cursor()

        cur.execute("""
            SELECT name, series, rarity
            FROM collection
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 20
        """, (user.id,))

        cards = cur.fetchall()

        con.close()

        if not cards:
            text = (
                "🎒 <b>YOUR COLLECTION</b>\n\n"
                "📭 Card မရှိသေးပါဘူး။"
            )
        else:
            text = "🎒 <b>YOUR COLLECTION</b>\n\n"

            for i, (name, series, rarity) in enumerate(cards, 1):
                text += (
                    f"{i}. {rarity} <b>{name}</b>\n"
                    f"   📺 {series}\n\n"
                )

        await query.edit_message_text(
            text,
            parse_mode="HTML"
        )

    elif query.data == "stats":

        con = sqlite3.connect(DB_FILE)
        cur = con.cursor()

        cur.execute("""
            SELECT coins, messages, caught
            FROM users
            WHERE user_id = ?
        """, (user.id,))

        row = cur.fetchone()

        con.close()

        if row:
            coins, messages, caught = row

            text = (
                "👤 <b>YOUR STATS</b>\n\n"
                f"💰 Coins: <b>{coins}</b>\n"
                f"💬 Messages: <b>{messages}</b>\n"
                f"🎴 Cards Caught: <b>{caught}</b>"
            )
        else:
            text = "❌ User data မတွေ့ပါဘူး။"

        await query.edit_message_text(
            text,
            parse_mode="HTML"
        )

    elif query.data == "balance":

        con = sqlite3.connect(DB_FILE)
        cur = con.cursor()

        cur.execute("""
            SELECT coins
            FROM users
            WHERE user_id = ?
        """, (user.id,))

        row = cur.fetchone()

        con.close()

        coins = row[0] if row else 0

        await query.edit_message_text(
            "💰 <b>YOUR BALANCE</b>\n\n"
            f"🪙 Coins: <b>{coins}</b>",
            parse_mode="HTML"
        )

    elif query.data == "top":

        con = sqlite3.connect(DB_FILE)
        cur = con.cursor()

        cur.execute("""
            SELECT username, coins, caught
            FROM users
            ORDER BY coins DESC
            LIMIT 10
        """)

        rows = cur.fetchall()

        con.close()

        text = "🏆 <b>TOP 10 PLAYERS</b>\n\n"

        for i, (username, coins, caught) in enumerate(rows, 1):
            text += (
                f"{i}. <b>{username or 'Unknown'}</b>\n"
                f"   🪙 {coins} | 🎴 {caught}\n\n"
            )

        await query.edit_message_text(
            text,
            parse_mode="HTML"
        )

    elif query.data == "commands":

        text = (
            "📖 <b>COMMANDS</b>\n\n"
            "/start — Start Bot\n"
            "/guess &lt;name&gt; — Catch Card\n"
            "/name &lt;name&gt; — Character Info\n"
            "/collection — My Cards\n"
            "/harem — My Harem\n"
            "/stats — My Stats\n"
            "/balance — Coins\n"
            "/top — Top 10 Players"
        )

        await query.edit_message_text(
            text,
            parse_mode="HTML"
        )


# =========================
# MAIN
# =========================

def main():

    init_db()

    if not BOT_TOKEN:
        print("❌ BOT_TOKEN is missing!")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CommandHandler("guess", guess)
    )

    app.add_handler(
        CommandHandler("collection", collection)
    )

    app.add_handler(
        CommandHandler("harem", harem)
    )

    app.add_handler(
        CommandHandler("stats", stats)
    )

    app.add_handler(
        CommandHandler("balance", balance)
    )

    app.add_handler(
        CommandHandler("top", top)
    )

    app.add_handler(
        CommandHandler("name", name_info)
    )

    app.add_handler(
        CallbackQueryHandler(button_handler)
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            count_messages
        )
    )

    print("🤖 Character Catcher Bot is starting...")
    print("✅ Bot is running!")

    app.run_polling()


# =========================
# START BOT
# =========================

if __name__ == "__main__":
    main()
    
