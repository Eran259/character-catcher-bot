import os
import sqlite3
import random
import html
import requests

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
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


# =========================
# TELEGRAM STARS SHOP
# =========================

SHOP_ITEMS = {
    "small": {
        "name": "🎁 Small Gift",
        "stars": 10,
    },

    "rare": {
        "name": "💎 Rare Gift",
        "stars": 30,
    },

    "legendary": {
        "name": "👑 Legendary Gift",
        "stars": 75,
    },

    "ticket": {
        "name": "🎟️ Card Ticket",
        "stars": 100,
    },
}


# =========================
# DATABASE
# =========================

def get_db():
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    return con


def init_db():

    con = get_db()
    cur = con.cursor()

    # USERS
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            coins INTEGER DEFAULT 100,
            messages INTEGER DEFAULT 0,
            caught INTEGER DEFAULT 0
        )
    """)

    # COLLECTION
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

    # COLLECTION MIGRATION
    cur.execute("PRAGMA table_info(collection)")
    columns = [row["name"] for row in cur.fetchall()]

    if "card_id" not in columns:
        cur.execute(
            "ALTER TABLE collection ADD COLUMN card_id INTEGER"
        )

    # ACTIVE CARDS
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

    # ACTIVE CARDS MIGRATION
    cur.execute("PRAGMA table_info(active_cards)")
    columns = [row["name"] for row in cur.fetchall()]

    if "card_id" not in columns:
        cur.execute(
            "ALTER TABLE active_cards ADD COLUMN card_id INTEGER"
        )

    # GROUP STATS
    cur.execute("""
        CREATE TABLE IF NOT EXISTS group_stats (
            chat_id INTEGER PRIMARY KEY,
            messages INTEGER DEFAULT 0
        )
    """)

    # =========================
    # GIFTS
    # =========================

    cur.execute("""
        CREATE TABLE IF NOT EXISTS gifts (
            user_id INTEGER,
            item_key TEXT,
            item_name TEXT,
            quantity INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, item_key)
        )
    """)

    # =========================
    # SHOP PURCHASES
    # =========================

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

    # =========================
    # STAR PAYMENTS
    # =========================

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

    con.commit()
    con.close()


# =========================
# USER
# =========================

def ensure_user(user):

    if not user:
        return

    con = get_db()
    cur = con.cursor()

    cur.execute(
        """
        INSERT OR IGNORE INTO users
        (user_id, username, coins, messages, caught)
        VALUES (?, ?, 100, 0, 0)
        """,
        (
            user.id,
            user.username or "",
        ),
    )

    cur.execute(
        """
        UPDATE users
        SET username = ?
        WHERE user_id = ?
        """,
        (
            user.username or "",
            user.id,
        ),
    )

    con.commit()
    con.close()


# =========================
# RANDOM CARD ID
# =========================

def generate_card_id():

    con = get_db()
    cur = con.cursor()

    while True:

        card_id = random.randint(1000, 9999)

        cur.execute(
            "SELECT 1 FROM active_cards WHERE card_id = ?",
            (card_id,),
        )

        if cur.fetchone():
            continue

        cur.execute(
            "SELECT 1 FROM collection WHERE card_id = ?",
            (card_id,),
        )

        if cur.fetchone():
            continue

        con.close()
        return card_id


# =========================
# MAIN MENU
# =========================

def main_menu():

    keyboard = [

        [
            InlineKeyboardButton(
                "📚 Collection",
                callback_data="collection"
            ),
            InlineKeyboardButton(
                "📊 Stats",
                callback_data="stats"
            ),
        ],

        [
            InlineKeyboardButton(
                "💰 Balance",
                callback_data="balance"
            ),
            InlineKeyboardButton(
                "🏆 Top 10",
                callback_data="top"
            ),
        ],

        [
            InlineKeyboardButton(
                "🎁 Daily",
                callback_data="daily"
            ),
            InlineKeyboardButton(
                "🛒 Shop ⭐",
                callback_data="shop"
            ),
        ],

        [
            InlineKeyboardButton(
                "🎁 Gifts",
                callback_data="gifts"
            ),
            InlineKeyboardButton(
                "❓ Help",
                callback_data="help"
            ),
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


# =========================
# SHOP KEYBOARD
# =========================

def shop_keyboard():

    keyboard = []

    for key, item in SHOP_ITEMS.items():

        keyboard.append([
            InlineKeyboardButton(
                f"{item['name']} — ⭐ {item['stars']}",
                callback_data=f"buy:{key}",
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            "🎁 My Gifts",
            callback_data="gifts",
        )
    ])

    keyboard.append([
        InlineKeyboardButton(
            "⬅️ Back",
            callback_data="start",
        )
    ])

    return InlineKeyboardMarkup(keyboard)

# =========================
# SHOP COMMAND
# =========================

async def shop(update: Update, context: ContextTypes.DEFAULT_TYPE):

    ensure_user(update.effective_user)

    text = """
🛒 <b>Telegram Stars Shop</b>

⭐ Telegram Stars နဲ့ Item ဝယ်နိုင်ပါတယ်။

🎁 Small Gift — ⭐ 10
💎 Rare Gift — ⭐ 30
👑 Legendary Gift — ⭐ 75
🎟️ Card Ticket — ⭐ 100

အောက်က Button ကိုနှိပ်ပြီး ဝယ်ယူပါ။
"""

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=shop_keyboard(),
    )


# =========================
# SEND TELEGRAM STARS INVOICE
# =========================

async def send_shop_invoice(
    query,
    context,
    item_key,
):

    item = SHOP_ITEMS.get(item_key)

    if not item:

        await query.answer(
            "❌ Item မတွေ့ပါဘူး။",
            show_alert=True,
        )

        return

    user_id = query.from_user.id

    payload = f"shop:{item_key}:{user_id}"

    await context.bot.send_invoice(

        chat_id=query.message.chat_id,

        title=item["name"],

        description=(
            f"{item['name']} ဝယ်ယူရန် "
            f"{item['stars']} Telegram Stars လိုအပ်ပါတယ်။"
        ),

        payload=payload,

        currency="XTR",

        prices=[
            {
                "label": item["name"],
                "amount": item["stars"],
            }
        ],

        # Telegram Stars အတွက် provider token မလိုပါ
        provider_token="",
    )

    await query.answer()


# =========================
# PRE-CHECKOUT
# =========================

async def precheckout_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.pre_checkout_query

    if not query:
        return

    payload = query.invoice_payload

    # Only our shop payment
    if not payload.startswith("shop:"):

        await query.answer(
            ok=False,
            error_message="❌ Invalid payment.",
        )

        return

    parts = payload.split(":")

    if len(parts) != 3:

        await query.answer(
            ok=False,
            error_message="❌ Invalid shop item.",
        )

        return

    item_key = parts[1]

    try:
        payload_user_id = int(parts[2])
    except ValueError:

        await query.answer(
            ok=False,
            error_message="❌ Invalid user.",
        )

        return

    item = SHOP_ITEMS.get(item_key)

    if not item:

        await query.answer(
            ok=False,
            error_message="❌ Item မတွေ့ပါဘူး။",
        )

        return

    # Check user
    if query.from_user.id != payload_user_id:

        await query.answer(
            ok=False,
            error_message="❌ Payment user မကိုက်ပါ။",
        )

        return

    # Check currency
    if query.currency != "XTR":

        await query.answer(
            ok=False,
            error_message="❌ Telegram Stars payment only.",
        )

        return

    # Check amount
    if query.total_amount != item["stars"]:

        await query.answer(
            ok=False,
            error_message="❌ Payment amount မမှန်ပါ။",
        )

        return

    # Everything OK
    await query.answer(ok=True)


# =========================
# SUCCESSFUL PAYMENT
# =========================

async def successful_payment_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    payment = update.message.successful_payment

    if not payment:
        return

    payload = payment.invoice_payload

    if not payload.startswith("shop:"):
        return

    parts = payload.split(":")

    if len(parts) != 3:
        return

    item_key = parts[1]

    try:
        payload_user_id = int(parts[2])
    except ValueError:
        return

    user_id = update.effective_user.id

    # Payload user must match payer
    if payload_user_id != user_id:
        return

    item = SHOP_ITEMS.get(item_key)

    if not item:
        return

    # Verify Telegram Stars
    if payment.currency != "XTR":
        return

    if payment.total_amount != item["stars"]:
        return

    con = get_db()
    cur = con.cursor()

    try:

        # Prevent duplicate processing
        cur.execute(
            """
            SELECT id
            FROM star_payments
            WHERE telegram_payment_charge_id = ?
            """,
            (
                payment.telegram_payment_charge_id,
            ),
        )

        if cur.fetchone():

            con.close()

            await update.message.reply_text(
                "ℹ️ ဒီ Payment ကို အရင် Process လုပ်ပြီးသားပါ။"
            )

            return

        # Add item to Gifts
        cur.execute(
            """
            INSERT INTO gifts
            (
                user_id,
                item_key,
                item_name,
                quantity
            )
            VALUES (?, ?, ?, 1)

            ON CONFLICT(user_id, item_key)
            DO UPDATE SET
                quantity = quantity + 1
            """,
            (
                user_id,
                item_key,
                item["name"],
            ),
        )

        # Save payment
        cur.execute(
            """
            INSERT INTO star_payments
            (
                user_id,
                payload,
                item_key,
                stars,
                telegram_payment_charge_id,
                provider_payment_charge_id
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                payload,
                item_key,
                payment.total_amount,
                payment.telegram_payment_charge_id,
                payment.provider_payment_charge_id,
            ),
        )

        # Purchase history
        cur.execute(
            """
            INSERT INTO shop_purchases
            (
                user_id,
                item_key,
                item_name,
                stars,
                quantity,
                telegram_payment_charge_id,
                provider_payment_charge_id
            )
            VALUES (?, ?, ?, ?, 1, ?, ?)
            """,
            (
                user_id,
                item_key,
                item["name"],
                payment.total_amount,
                payment.telegram_payment_charge_id,
                payment.provider_payment_charge_id,
            ),
        )

        con.commit()

    except Exception as e:

        con.rollback()
        con.close()

        print(
            "Payment processing error:",
            e,
        )

        await update.message.reply_text(
            "❌ Payment process မှာ Error ဖြစ်သွားပါတယ်။"
        )

        return

    con.close()

    await update.message.reply_text(
        f"""
✅ <b>Payment Successful!</b>

{item['name']}

⭐ Paid: <b>{payment.total_amount} Stars</b>

🎁 Item ကို Gifts Inventory ထဲ ထည့်ပြီးပါပြီ။

📦 <code>/gifts</code>
နဲ့ ကြည့်နိုင်ပါတယ်။
""",
        parse_mode="HTML",
    )


# =========================
# GIFTS COMMAND
# =========================

async def gifts(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    ensure_user(update.effective_user)

    user_id = update.effective_user.id

    con = get_db()
    cur = con.cursor()

    cur.execute(
        """
        SELECT item_key, item_name, quantity
        FROM gifts
        WHERE user_id = ?
        AND quantity > 0
        ORDER BY item_key
        """,
        (user_id,),
    )

    rows = cur.fetchall()

    con.close()

    if not rows:

        await update.message.reply_text(
            """
🎁 <b>My Gifts</b>

လက်ရှိ Gift မရှိသေးပါဘူး။

⭐ <code>/shop</code>
ကနေ ဝယ်ယူနိုင်ပါတယ်။
""",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🛒 Shop ⭐",
                        callback_data="shop",
                    )
                ]
            ]),
        )

        return

    lines = [
        "🎁 <b>My Gifts</b>",
        "",
    ]

    for row in rows:

        lines.append(
            f"{row['item_name']} × "
            f"<b>{row['quantity']}</b>"
        )

    lines.extend([
        "",
        "🎁 Gift ပို့ရန်:",
        "<code>/give @username small</code>",
        "",
        "သို့မဟုတ် User message ကို Reply လုပ်ပြီး:",
        "<code>/give small</code>",
    ])

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🛒 Shop ⭐",
                    callback_data="shop",
                )
            ]
        ]),
    )


# =========================
# GIVE GIFT
# =========================

async def give(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    sender = update.effective_user

    ensure_user(sender)

    args = context.args

    recipient_id = None
    item_key = None

    # -------------------------
    # Reply mode
    # /give small
    # -------------------------

    if update.message.reply_to_message:

        replied_user = (
            update.message.reply_to_message.from_user
        )

        if replied_user:

            recipient_id = replied_user.id

            if args:
                item_key = args[0].lower()

    # -------------------------
    # Username mode
    # /give @username small
    # -------------------------

    else:

        if len(args) >= 2:

            username = (
                args[0]
                .lstrip("@")
                .lower()
            )

            item_key = args[1].lower()

            con = get_db()
            cur = con.cursor()

            cur.execute(
                """
                SELECT user_id
                FROM users
                WHERE LOWER(username) = ?
                LIMIT 1
                """,
                (username,),
            )

            row = cur.fetchone()

            con.close()

            if row:
                recipient_id = row["user_id"]

    # -------------------------
    # Usage
    # -------------------------

    if not recipient_id or not item_key:

        await update.message.reply_text(
            """
🎁 <b>Gift ပို့နည်း</b>

User message ကို Reply လုပ်ပြီး:

<code>/give small</code>

သို့မဟုတ်:

<code>/give @username small</code>

Available:

<code>small</code>
<code>rare</code>
<code>legendary</code>
<code>ticket</code>
""",
            parse_mode="HTML",
        )

        return

    # -------------------------
    # Cannot gift yourself
    # -------------------------

    if recipient_id == sender.id:

        await update.message.reply_text(
            "❌ ကိုယ့်ကိုယ်ကို Gift ပို့လို့မရပါဘူး။"
        )

        return

    # -------------------------
    # Check item
    # -------------------------

    if item_key not in SHOP_ITEMS:

        await update.message.reply_text(
            "❌ ဒီ Gift အမျိုးအစား မရှိပါဘူး။"
        )

        return

    item = SHOP_ITEMS[item_key]

    con = get_db()
    cur = con.cursor()

    try:

        cur.execute("BEGIN IMMEDIATE")

        # Sender inventory
        cur.execute(
            """
            SELECT quantity
            FROM gifts
            WHERE user_id = ?
            AND item_key = ?
            """,
            (
                sender.id,
                item_key,
            ),
        )

        sender_item = cur.fetchone()

        if (
            not sender_item
            or sender_item["quantity"] <= 0
        ):

            con.rollback()
            con.close()

            await update.message.reply_text(
                f"❌ သင့်မှာ {item['name']} မရှိပါဘူး။"
            )

            return

        # Recipient exists?
        cur.execute(
            """
            SELECT user_id
            FROM users
            WHERE user_id = ?
            """,
            (recipient_id,),
        )

        recipient = cur.fetchone()

        if not recipient:

            con.rollback()
            con.close()

            await update.message.reply_text(
                "❌ ဒီ User က Bot ကို မသုံးဖူးသေးပါဘူး။"
            )

            return

        # Remove sender gift
        cur.execute(
            """
            UPDATE gifts
            SET quantity = quantity - 1
            WHERE user_id = ?
            AND item_key = ?
            AND quantity > 0
            """,
            (
                sender.id,
                item_key,
            ),
        )

        # Add recipient gift
        cur.execute(
            """
            INSERT INTO gifts
            (
                user_id,
                item_key,
                item_name,
                quantity
            )
            VALUES (?, ?, ?, 1)

            ON CONFLICT(user_id, item_key)
            DO UPDATE SET
                quantity = quantity + 1
            """,
            (
                recipient_id,
                item_key,
                item["name"],
            ),
        )

        con.commit()

    except Exception as e:

        con.rollback()
        con.close()

        print(
            "Gift transfer error:",
            e,
        )

        await update.message.reply_text(
            "❌ Gift ပို့တဲ့အချိန် Error ဖြစ်သွားပါတယ်။"
        )

        return

    con.close()

    await update.message.reply_text(
        f"""
🎁 <b>Gift Sent!</b>

{item['name']}

✅ Gift 1 ခု ပို့ပြီးပါပြီ။
""",
        parse_mode="HTML",
    )

    # Notify recipient
    try:

        await context.bot.send_message(
            chat_id=recipient_id,
            text=f"""
🎁 <b>You received a Gift!</b>

{item['name']}

👤 From:
{html.escape(sender.first_name or "User")}

📦 <code>/gifts</code>
""",
            parse_mode="HTML",
        )

    except Exception:
        pass


# =========================
# SHOP CALLBACK
# =========================

async def shop_button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    data = query.data

    if data == "shop":

        await query.answer()

        await query.edit_message_text(
            """
🛒 <b>Telegram Stars Shop</b>

⭐ Item ရွေးပြီး Telegram Stars နဲ့ ဝယ်ပါ။
""",
            parse_mode="HTML",
            reply_markup=shop_keyboard(),
        )

        return

    if data.startswith("buy:"):

        item_key = data.split(":", 1)[1]

        await send_shop_invoice(
            query,
            context,
            item_key,
        )

        return


# =========================
# GIFTS CALLBACK
# =========================

async def gifts_button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    con = get_db()
    cur = con.cursor()

    cur.execute(
        """
        SELECT item_name, quantity
        FROM gifts
        WHERE user_id = ?
        AND quantity > 0
        ORDER BY item_key
        """,
        (user_id,),
    )

    rows = cur.fetchall()

    con.close()

    if not rows:

        text = """
🎁 <b>My Gifts</b>

Gift မရှိသေးပါဘူး။
"""

    else:

        lines = [
            "🎁 <b>My Gifts</b>",
            "",
        ]

        for row in rows:

            lines.append(
                f"{row['item_name']} × "
                f"<b>{row['quantity']}</b>"
            )

        text = "\n".join(lines)

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🛒 Shop ⭐",
                    callback_data="shop",
                )
            ]
        ]),
    )

# =========================
# TELEGRAM MENU BUTTON
# =========================

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

        BotCommand("help", "❓ Help"),
    ]

    await application.bot.set_my_commands(commands)

    print("✅ Telegram Menu Button updated!")


# =========================
# DAILY
# =========================

async def daily(update: Update, context: ContextTypes.DEFAULT_TYPE):

    ensure_user(update.effective_user)

    await update.message.reply_text(
        """
🎁 <b>Daily Reward</b>

Daily Reward system ကို
နောက်ပိုင်းမှာ cooldown + reward
စနစ်အပြည့်ထည့်နိုင်ပါတယ်။
""",
        parse_mode="HTML",
    )


# =========================
# HELP
# =========================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        """
❓ <b>Eren Character Bot Help</b>

🎴 <b>Character</b>

<code>/guess Name</code>
→ Character Catch

Card ကို Reply လုပ်ပြီး:

<code>.n</code>
→ Character Name ကြည့်ရန်

<code>/card 3356</code>
→ Card ID ကြည့်ရန်


📚 <b>Collection</b>

<code>/collection</code>
→ Collection

<code>/harem</code>
→ Harem

<code>/stats</code>
→ Stats

<code>/balance</code>
→ Coins

<code>/top</code>
→ Top 10


⭐ <b>Stars Shop</b>

<code>/shop</code>
→ Telegram Stars နဲ့ Item ဝယ်ရန်

<code>/gifts</code>
→ ဝယ်ထားတဲ့ Gifts

User message ကို Reply လုပ်ပြီး:

<code>/give small</code>

သို့မဟုတ်:

<code>/give @username small</code>

→ Gift ပို့ရန်


🎁 <code>/daily</code>
→ Daily Reward
""",
        parse_mode="HTML",
    )


# =========================
# INLINE BUTTON HANDLER
# =========================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    data = query.data

    # =====================
    # SHOP
    # =====================

    if data == "shop" or data.startswith("buy:"):

        await shop_button_handler(
            update,
            context,
        )

        return

    # =====================
    # GIFTS
    # =====================

    if data == "gifts":

        await gifts_button_handler(
            update,
            context,
        )

        return

    # =====================
    # START
    # =====================

    if data == "start":

        await query.answer()

        ensure_user(query.from_user)

        await query.edit_message_text(
            """
🎴 <b>Eren Character Bot</b>

Anime Character တွေကို Catch လုပ်ပြီး
Collection စုနိုင်ပါတယ်။

🎯 Character ပေါ်လာရင်:

<code>/guess Character Name</code>

Name မသိရင် Card ကို Reply လုပ်ပြီး:

<code>.n</code>

ရိုက်ပါ။
""",
            parse_mode="HTML",
            reply_markup=main_menu(),
        )

        return

    # =====================
    # COLLECTION
    # =====================

    if data == "collection":

        await query.answer()

        await query.message.reply_text(
            "📚 Collection\n\n<code>/collection</code>",
            parse_mode="HTML",
        )

        return

    # =====================
    # STATS
    # =====================

    if data == "stats":

        await query.answer()

        await query.message.reply_text(
            "📊 Stats\n\n<code>/stats</code>",
            parse_mode="HTML",
        )

        return

    # =====================
    # BALANCE
    # =====================

    if data == "balance":

        await query.answer()

        await query.message.reply_text(
            "💰 Balance\n\n<code>/balance</code>",
            parse_mode="HTML",
        )

        return

    # =====================
    # TOP
    # =====================

    if data == "top":

        await query.answer()

        await query.message.reply_text(
            "🏆 Top 10\n\n<code>/top</code>",
            parse_mode="HTML",
        )

        return

    # =====================
    # DAILY
    # =====================

    if data == "daily":

        await query.answer()

        await query.message.reply_text(
            "🎁 Daily Reward\n\n<code>/daily</code>",
            parse_mode="HTML",
        )

        return

    # =====================
    # HELP
    # =====================

    if data == "help":

        await query.answer()

        await query.message.reply_text(
            "❓ Help\n\n<code>/help</code>",
            parse_mode="HTML",
        )

        return

    await query.answer()


# =========================
# ERROR HANDLER
# =========================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):

    print(
        "❌ Bot Error:",
        context.error,
    )


# =========================
# START
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    ensure_user(user)

    text = (
        "🎴 <b>Eren Character Bot</b>\n\n"
        f"ဟယ်လို 👋 <b>{html.escape(user.first_name or 'User')}</b>!\n\n"
        "Anime Character တွေကို Catch လုပ်ပြီး Collection စုနိုင်ပါတယ်။ 🎴\n\n"
        "📌 <b>Commands</b>\n"
        "• /guess Name — Character ဖမ်းရန်\n"
        "• /collection — Collection ကြည့်ရန်\n"
        "• /harem — Harem ကြည့်ရန်\n"
        "• /stats — Stats ကြည့်ရန်\n"
        "• /balance — Coin ကြည့်ရန်\n"
        "• /top — Top 10\n"
        "• /daily — Daily Reward\n"
        "• /shop — Telegram Stars Shop ⭐\n"
        "• /gifts — ကိုယ့် Gifts ကြည့်ရန်\n"
        "• /give — Gift ပေးရန်\n"
        "• /name — Character Name\n"
        "• /card ID — Card ကြည့်ရန်\n"
        "• /help — Help\n\n"
        "🎯 Group ထဲမှာ Message 20 ခုတိုင်း Character ပေါ်လာပါမယ်။"
    )

    if update.message:
        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=main_menu()
        )


# =========================
# MAIN
# =========================

def main():

    if not BOT_TOKEN:
        print("❌ BOT_TOKEN မတွေ့ပါဘူး။")
        return

    # =========================
    # DATABASE
    # =========================

    init_db()

    print("🤖 Eren Character Bot is starting...")

    # =========================
    # APPLICATION
    # =========================

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # =========================
    # BASIC COMMANDS
    # =========================

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CommandHandler("daily", daily)
    )

    app.add_handler(
        CommandHandler("shop", shop)
    )

    app.add_handler(
        CommandHandler("gifts", gifts)
    )

    app.add_handler(
        CommandHandler("give", give)
    )

    app.add_handler(
        CommandHandler("help", help_command)
    )

    # =========================
    # CHARACTER COMMANDS
    # =========================

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
        CommandHandler("card", card_info)
    )

    # =========================
    # .n CHARACTER NAME
    # =========================

    app.add_handler(
        MessageHandler(
            filters.Regex(r"^\.n$"),
            reveal_name
        )
    )

    # =========================
    # ⭐ TELEGRAM STARS
    # =========================

    app.add_handler(
        PreCheckoutQueryHandler(
            precheckout_callback
        )
    )

    # =========================
    # ⭐ SUCCESSFUL PAYMENT
    # =========================

    app.add_handler(
        MessageHandler(
            filters.SUCCESSFUL_PAYMENT,
            successful_payment_callback
        )
    )

    # =========================
    # INLINE BUTTONS
    # =========================

    app.add_handler(
        CallbackQueryHandler(
            button_handler
        )
    )

    # =========================
    # NORMAL GROUP MESSAGES
    # =========================

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            count_messages
        )
    )

    # =========================
    # ERROR HANDLER
    # =========================

    app.add_error_handler(
        error_handler
    )

    # =========================
    # START BOT
    # =========================

    print("✅ Bot is running!")

    app.run_polling(
        drop_pending_updates=True
    )


# =========================
# RUN
# =========================

if __name__ == "__main__":
    main()
