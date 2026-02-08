import os
import sqlite3
from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

# ---------- ENV ----------
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
ADMIN_PHONE = os.getenv("ADMIN_PHONE")

# ---------- DB ----------
# Use absolute path for database to avoid file not found errors in production
db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "database.db")
conn = sqlite3.connect(db_path, check_same_thread=False)
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY,
    name TEXT,
    phone TEXT
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS products(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    price INTEGER,
    image TEXT
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS cart(
    user_id INTEGER,
    product_id INTEGER
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS orders(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    items TEXT
)
""")
conn.commit()

# ---------- START ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    if uid == ADMIN_ID:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Mahsulot qo‘shish", callback_data="add_product")],
            [InlineKeyboardButton("📦 Mahsulotlarni boshqarish", callback_data="edit_products")],
            [InlineKeyboardButton("📢 Broadcast", callback_data="broadcast")],
            [InlineKeyboardButton("📊 Buyurtma statistikasi", callback_data="stats")]
        ])
        await update.message.reply_text("👑 ADMIN PANEL", reply_markup=kb)
        return

    cur.execute("SELECT * FROM users WHERE id=?", (uid,))
    if not cur.fetchone():
        context.user_data["register"] = "name"
        await update.message.reply_text("👤 Ismingizni kiriting:")
        return

    await show_products(update)

# ---------- USER REGISTER ----------
async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data.get("register")

    if step == "name":
        context.user_data["name"] = update.message.text
        context.user_data["register"] = "phone"
        kb = ReplyKeyboardMarkup(
            [[KeyboardButton("📞 Raqam yuborish", request_contact=True)]],
            resize_keyboard=True
        )
        await update.message.reply_text("📱 Telefon raqamingiz:", reply_markup=kb)

    elif step == "phone":
        phone = update.message.contact.phone_number
        cur.execute(
            "INSERT INTO users VALUES (?,?,?)",
            (update.effective_user.id, context.user_data["name"], phone)
        )
        conn.commit()
        context.user_data.clear()
        await update.message.reply_text("✅ Ro‘yxatdan o‘tdingiz")
        await show_products(update)

# ---------- SHOW PRODUCTS ----------
async def show_products(update: Update):
    cur.execute("SELECT * FROM products")
    products = cur.fetchall()

    if not products:
        await update.message.reply_text("📦 Mahsulotlar yo‘q")
        return

    for p in products:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 Savatchaga qo‘shish", callback_data=f"addcart_{p[0]}")]
        ])
        await update.message.reply_photo(
            photo=p[3],
            caption=f"📦 {p[1]}\n💰 {p[2]} so‘m\n📞 {ADMIN_PHONE}",
            reply_markup=kb
        )

# ---------- CALLBACK ----------
async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    # ADD TO CART
    if data.startswith("addcart_"):
        pid = int(data.split("_")[1])
        cur.execute("INSERT INTO cart VALUES (?,?)", (q.from_user.id, pid))
        conn.commit()
        await q.message.reply_text("✅ Savatchaga qo‘shildi")

    # ADMIN ADD PRODUCT
    elif data == "add_product":
        context.user_data["add"] = "name"
        await q.message.reply_text("📦 Mahsulot nomi:")

    elif data == "edit_products":
        cur.execute("SELECT * FROM products")
        for p in cur.fetchall():
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✏️ Tahrirlash", callback_data=f"edit_{p[0]}"),
                    InlineKeyboardButton("❌ O‘chirish", callback_data=f"del_{p[0]}")
                ]
            ])
            await q.message.reply_text(f"{p[1]} — {p[2]} so‘m", reply_markup=kb)

    elif data.startswith("del_"):
        pid = int(data.split("_")[1])
        cur.execute("DELETE FROM products WHERE id=?", (pid,))
        conn.commit()
        await q.message.reply_text("❌ O‘chirildi")

    elif data.startswith("edit_"):
        context.user_data["edit_id"] = int(data.split("_")[1])
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Nomi", callback_data="ename")],
            [InlineKeyboardButton("💰 Narx", callback_data="eprice")],
            [InlineKeyboardButton("🖼 Rasm", callback_data="eimage")]
        ])
        await q.message.reply_text("Nimani tahrirlaysiz?", reply_markup=kb)

    elif data in ["ename", "eprice", "eimage"]:
        context.user_data["edit_field"] = data
        await q.message.reply_text("✍️ Yangi qiymat yuboring")

    elif data == "broadcast":
        context.user_data["broadcast"] = True
        await q.message.reply_text("📢 Xabar matnini yuboring")

    elif data == "stats":
        cur.execute("SELECT COUNT(*) FROM orders")
        await q.message.reply_text(f"📊 Buyurtmalar soni: {cur.fetchone()[0]}")

# ---------- ADMIN TEXT ----------
async def admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        return

    # ADD PRODUCT FLOW
    if context.user_data.get("add") == "name":
        context.user_data["pname"] = update.message.text
        context.user_data["add"] = "price"
        await update.message.reply_text("💰 Narxi:")

    elif context.user_data.get("add") == "price":
        context.user_data["price"] = int(update.message.text)
        context.user_data["add"] = "image"
        await update.message.reply_text("🖼 Rasm URL:")

    elif context.user_data.get("add") == "image":
        cur.execute(
            "INSERT INTO products(name,price,image) VALUES (?,?,?)",
            (context.user_data["pname"], context.user_data["price"], update.message.text)
        )
        conn.commit()
        context.user_data.clear()
        await update.message.reply_text("✅ Mahsulot qo‘shildi")

    # EDIT PRODUCT
    elif "edit_id" in context.user_data:
        pid = context.user_data["edit_id"]
        field = context.user_data["edit_field"]

        if field == "ename":
            cur.execute("UPDATE products SET name=? WHERE id=?", (update.message.text, pid))
        elif field == "eprice":
            cur.execute("UPDATE products SET price=? WHERE id=?", (int(update.message.text), pid))
        elif field == "eimage":
            cur.execute("UPDATE products SET image=? WHERE id=?", (update.message.text, pid))

        conn.commit()
        context.user_data.clear()
        await update.message.reply_text("✅ Yangilandi")

    # BROADCAST
    elif context.user_data.get("broadcast"):
        cur.execute("SELECT id FROM users")
        for u in cur.fetchall():
            try:
                await update.get_bot().send_message(u[0], update.message.text)
            except:
                pass
        context.user_data.clear()
        await update.message.reply_text("📢 Yuborildi")

# ---------- MAIN ----------
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.CONTACT, register))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, register))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_text))

    app.run_polling()

if __name__ == "__main__":
    main()
