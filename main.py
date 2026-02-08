import os
import sqlite3
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)

# ================== ENV ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

# ================== DB ==================
conn = sqlite3.connect("shop.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    price INTEGER
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS cart (
    user_id INTEGER,
    product_id INTEGER,
    qty INTEGER
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    total INTEGER
)
""")

conn.commit()

# ================== START ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cursor.execute("INSERT OR IGNORE INTO users VALUES (?)", (user_id,))
    conn.commit()

    if user_id == ADMIN_ID:
        keyboard = [
            [InlineKeyboardButton("➕ Mahsulot qo‘shish", callback_data="add_product")],
            [InlineKeyboardButton("📦 Mahsulotlar", callback_data="admin_products")],
            [InlineKeyboardButton("📢 Broadcast", callback_data="broadcast")],
            [InlineKeyboardButton("📊 Buyurtma statistikasi", callback_data="stats")]
        ]
        await update.message.reply_text(
            "🛠 Admin panel",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        keyboard = [
            [InlineKeyboardButton("🛍 Mahsulotlar", callback_data="view_products")],
            [InlineKeyboardButton("🛒 Savatcha", callback_data="view_cart")]
        ]
        await update.message.reply_text(
            "Xush kelibsiz 👋",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

# ================== ADMIN ADD PRODUCT ==================
async def add_product_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data["state"] = "ADD_NAME"
    await update.callback_query.message.reply_text("📦 Mahsulot nomini kiriting:")

# ================== BROADCAST ==================
async def broadcast_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data["state"] = "BROADCAST"
    await update.callback_query.message.reply_text("📢 Matnni kiriting:")

# ================== ADMIN PRODUCTS ==================
async def admin_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()

    cursor.execute("SELECT * FROM products")
    products = cursor.fetchall()

    keyboard = []
    for p in products:
        keyboard.append([
            InlineKeyboardButton(
                f"{p[1]} - {p[2]} so‘m",
                callback_data=f"edit_{p[0]}"
            )
        ])

    await update.callback_query.message.reply_text(
        "📦 Mahsulotlar:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ================== EDIT PRODUCT ==================
async def edit_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    pid = int(query.data.split("_")[1])
    context.user_data["edit_pid"] = pid
    context.user_data["state"] = "EDIT_NAME"

    await query.message.reply_text("✏️ Yangi nomini kiriting:")

# ================== USER PRODUCTS ==================
async def view_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()

    cursor.execute("SELECT * FROM products")
    products = cursor.fetchall()

    keyboard = []
    for p in products:
        keyboard.append([
            InlineKeyboardButton(
                f"{p[1]} ({p[2]} so‘m)",
                callback_data=f"addcart_{p[0]}"
            )
        ])

    await update.callback_query.message.reply_text(
        "🛍 Mahsulotlar:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ================== ADD TO CART ==================
async def add_to_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    pid = int(query.data.split("_")[1])
    uid = query.from_user.id

    cursor.execute(
        "SELECT qty FROM cart WHERE user_id=? AND product_id=?",
        (uid, pid)
    )
    row = cursor.fetchone()

    if row:
        cursor.execute(
            "UPDATE cart SET qty=qty+1 WHERE user_id=? AND product_id=?",
            (uid, pid)
        )
    else:
        cursor.execute(
            "INSERT INTO cart VALUES (?, ?, 1)",
            (uid, pid)
        )
    conn.commit()

    await query.message.reply_text("✅ Savatchaga qo‘shildi")

# ================== VIEW CART ==================
async def view_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid = update.effective_user.id

    cursor.execute("""
    SELECT p.name, p.price, c.qty
    FROM cart c
    JOIN products p ON p.id=c.product_id
    WHERE c.user_id=?
    """, (uid,))
    rows = cursor.fetchall()

    if not rows:
        await update.callback_query.message.reply_text("🛒 Savatcha bo‘sh")
        return

    total = 0
    text = "🛒 Savatcha:\n"
    for r in rows:
        total += r[1] * r[2]
        text += f"{r[0]} x{r[2]} = {r[1]*r[2]}\n"

    text += f"\n💰 Jami: {total}"

    keyboard = [
        [InlineKeyboardButton("✅ Buyurtmani tasdiqlash", callback_data="confirm_order")]
    ]

    await update.callback_query.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ================== CONFIRM ORDER ==================
async def confirm_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    cursor.execute("""
    SELECT p.price, c.qty
    FROM cart c JOIN products p ON p.id=c.product_id
    WHERE c.user_id=?
    """, (uid,))
    rows = cursor.fetchall()

    total = sum(p*q for p, q in rows)

    cursor.execute("INSERT INTO orders (user_id, total) VALUES (?, ?)", (uid, total))
    cursor.execute("DELETE FROM cart WHERE user_id=?", (uid,))
    conn.commit()

    await query.message.reply_text("🎉 Buyurtma qabul qilindi!")

# ================== ADMIN TEXT HANDLER ==================
async def admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    state = context.user_data.get("state")

    if state == "ADD_NAME":
        context.user_data["name"] = update.message.text
        context.user_data["state"] = "ADD_PRICE"
        await update.message.reply_text("💰 Narxini kiriting:")
        return

    if state == "ADD_PRICE":
        cursor.execute(
            "INSERT INTO products (name, price) VALUES (?, ?)",
            (context.user_data["name"], int(update.message.text))
        )
        conn.commit()
        context.user_data.clear()
        await update.message.reply_text("✅ Mahsulot qo‘shildi")
        return

    if state == "EDIT_NAME":
        context.user_data["new_name"] = update.message.text
        context.user_data["state"] = "EDIT_PRICE"
        await update.message.reply_text("💰 Yangi narx:")
        return

    if state == "EDIT_PRICE":
        cursor.execute(
            "UPDATE products SET name=?, price=? WHERE id=?",
            (
                context.user_data["new_name"],
                int(update.message.text),
                context.user_data["edit_pid"]
            )
        )
        conn.commit()
        context.user_data.clear()
        await update.message.reply_text("✏️ Tahrirlandi")
        return

    if state == "BROADCAST":
        cursor.execute("SELECT user_id FROM users")
        for (uid,) in cursor.fetchall():
            try:
                await context.bot.send_message(uid, update.message.text)
            except:
                pass
        context.user_data.clear()
        await update.message.reply_text("📢 Yuborildi")

# ================== MAIN ==================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    app.add_handler(CallbackQueryHandler(add_product_cb, pattern="add_product"))
    app.add_handler(CallbackQueryHandler(admin_products, pattern="admin_products"))
    app.add_handler(CallbackQueryHandler(edit_product, pattern="edit_"))
    app.add_handler(CallbackQueryHandler(broadcast_cb, pattern="broadcast"))

    app.add_handler(CallbackQueryHandler(view_products, pattern="view_products"))
    app.add_handler(CallbackQueryHandler(add_to_cart, pattern="addcart_"))
    app.add_handler(CallbackQueryHandler(view_cart, pattern="view_cart"))
    app.add_handler(CallbackQueryHandler(confirm_order, pattern="confirm_order"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_text))

    app.run_polling()

if __name__ == "__main__":
    main()
