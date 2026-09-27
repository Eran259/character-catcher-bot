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
)

BOT_TOKEN = os.getenv("8889628848:AAFrUVGXPYMXTiF0RZ1Wjkdxp4rYKyOKw_M")
DB_FILE = "character.db"


def init_db():
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS characters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            name TEXT,
            image_url TEXT,
            rarity TEXT
        )
    """)

    con.commit()
    con.close()


def get_waifu():
    try:
        url = "https://api.waifu.im/search"

        params = {
            "included_tags": "waifu",
            "is_nsfw": "false"
        }

        response = requests.get(
            url,
            params=params,
            timeout=15
        )

        if response.status_code != 200:
            return None

        data = response.json()

        if not data.get("images"):
            return None

        image = data["images"][0]

        return {
            "url": image["url"],
            "tags": image.get("tags", [])
        }

    except Exception as e:
        print("Waifu API error:", e)
        return None


def random_rarity():
    chance = random.randint(1, 100)

    if chance <= 50:
        return "⚪ Common"
    elif chance <= 80:
        return "🔵 Rare"
    elif chance <= 95:
        return "🟣 Epic"
    elif chance <= 99:
        return "🟡 Legendary"
    else:
        return "🔴 Mythic"
      async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = (
        "🎴 <b>Character Catcher Bot</b>\n\n"
        "Welcome! 👋\n\n"
        "🎯 <b>How to play</b>\n"
        "• /catch — Catch a random character\n"
        "• /collection — View your collection\n"
        "• /profile — View your profile\n\n"
        "✨ Good luck!"
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML"
    )


async def catch(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "🎴 <b>A character is appearing...</b>",
        parse_mode="HTML"
    )

    character = get_waifu()

    if not character:
        await update.message.reply_text(
            "❌ Character API is currently unavailable.\n"
            "Please try again later."
        )
        return

    rarity = random_rarity()

    keyboard = [
        [
            InlineKeyboardButton(
                "🎴 CATCH",
                callback_data=f"catch:{rarity}:{character['url']}"
            )
        ]
    ]

    caption = (
        "✨ <b>A wild character appeared!</b>\n\n"
        f"💎 Rarity: {rarity}\n\n"
        "👇 Catch this character!"
    )

    await update.message.reply_photo(
        photo=character["url"],
        caption=caption,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
)
async def catch_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    if not query.data.startswith("catch:"):
        return

    parts = query.data.split(":", 2)

    rarity = parts[1]
    image_url = parts[2]

    user = query.from_user

    username = user.username or user.first_name

    names = [
        "Mystery Waifu",
        "Anime Girl",
        "Unknown Character",
        "Cute Waifu",
        "Mystery Character"
    ]

    name = random.choice(names)

    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()

    cur.execute(
        """
        INSERT INTO characters
        (user_id, username, name, image_url, rarity)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            user.id,
            username,
            name,
            image_url,
            rarity
        )
    )

    con.commit()
    con.close()

    text = (
        "🎉 <b>Character Caught!</b>\n\n"
        f"👤 <b>{name}</b>\n"
        f"💎 Rarity: {rarity}\n\n"
        f"👑 Owner: @{username}\n\n"
        "📦 Added to your collection!"
    )

    await query.edit_message_caption(
        caption=text,
        parse_mode="HTML"
  )
  async def collection(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()

    cur.execute(
        """
        SELECT name, rarity
        FROM characters
        WHERE user_id = ?
        ORDER BY id DESC
        """,
        (user_id,)
    )

    rows = cur.fetchall()

    con.close()

    if not rows:
        await update.message.reply_text(
            "📦 <b>Your collection is empty!</b>\n\n"
            "Use /catch to catch your first character 🎴",
            parse_mode="HTML"
        )
        return

    text = "📦 <b>Your Collection</b>\n\n"

    for i, (name, rarity) in enumerate(rows, 1):
        text += (
            f"{i}. 👤 {name}\n"
            f"   💎 {rarity}\n\n"
        )

    await update.message.reply_text(
        text,
        parse_mode="HTML"
    )


async def profile(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()

    cur.execute(
        """
        SELECT COUNT(*)
        FROM characters
        WHERE user_id = ?
        """,
        (user_id,)
    )

    total = cur.fetchone()[0]

    con.close()

    username = update.effective_user.username

    if username:
        username = "@" + username
    else:
        username = update.effective_user.first_name

    text = (
        "👤 <b>Profile</b>\n\n"
        f"Username: {username}\n"
        f"🎴 Characters: <b>{total}</b>\n\n"
        "✨ Keep catching!"
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML"
    )
  def main():

    if not BOT_TOKEN:
        print("❌ BOT_TOKEN environment variable is missing!")
        return

    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CommandHandler("catch", catch)
    )

    app.add_handler(
        CommandHandler("collection", collection)
    )

    app.add_handler(
        CommandHandler("profile", profile)
    )

    app.add_handler(
        CallbackQueryHandler(catch_button)
    )

    print("🤖 Character Catcher Bot is starting...")
    print("✅ Bot is running!")

    app.run_polling()


if __name__ == "__main__":
    main()
