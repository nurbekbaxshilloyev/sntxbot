import sqlite3
import logging
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

TOKEN = "8555754554:AAG0rx5aBcbL9Xa37QZsLza-6EPw1UEfB-0"
ADMIN_ID = 5374047798
ADMIN_PHONE = "+998933213532"

logging.basicConfig(level=logging.INFO)

# ================= DATABASE =================
db = sqlite3.connect("shop.db", check_same_thread=False)
cur = db.cursor()

cur.execute("""CREATE TABLE IF NOT EXISTS users(
id INTEGER PRIMARY KEY, name TEXT, phone TEXT)""")

cur.execute("""CREATE TABLE IF NOT EXISTS products(
id INTEGER PRIMARY KEY AUTOINCREMENT,
name TEXT, price INTEGER, sizes TEXT, photo TEXT)""")

cur.execute("""CREATE TABLE IF NOT EXISTS orders(
id INTEGER PRIMARY KEY AUTOINCREMENT,
user_id INTEGER, text TEXT)""")

db.commit()

CARTS = {}

# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    cur.execute("SELECT 1 FROM users WHERE id=?", (uid,))
    if not cur.fetchone():
        await update.message.reply_text("Ismingizni kiriting:")
        context.user_data["reg"] = "name"
        return
    await main_menu(update)

async def main_menu(update):
    kb = [
        [InlineKeyboardButton("🛍 Mahsulotlar", callback_data="products")],
        [InlineKeyboardButton("🛒 Savatcha", callback_data="cart")]
    ]
    if update.effective_user.id == ADMIN_ID:
        kb.append([InlineKeyboardButton("🧑‍💼 Admin panel", callback_data="admin")])
    await update.message.reply_text(
        "🏗 Qurilish materiallari botiga xush kelibsiz",
        reply_markup=InlineKeyboardMarkup(kb)
    )

# ================= REGISTRATION =================
async def register(update: Update, context):
    step = context.user_data.get("reg")

    if step == "name":
        context.user_data["name"] = update.message.text
        kb = [[KeyboardButton("📞 Telefon raqamni yuborish", request_contact=True)]]
        await update.message.reply_text(
            "Telefon raqamingizni yuboring:",
            reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True, one_time_keyboard=True)
        )
        context.user_data["reg"] = "phone"

    elif step == "phone":
        cur.execute(
            "INSERT OR REPLACE INTO users VALUES (?,?,?)",
            (update.effective_user.id, context.user_data["name"],
             update.message.contact.phone_number)
        )
        db.commit()
        context.user_data.clear()
        await update.message.reply_text("✅ Ro‘yxatdan o‘tdingiz")
        await main_menu(update)

# ================= PRODUCTS =================
async def products(update, context):
    q = update.callback_query
    await q.answer()
    cur.execute("SELECT id,name,price FROM products")
    rows = cur.fetchall()
    if not rows:
        await q.edit_message_text("Mahsulotlar yo‘q")
        return
    kb = [[InlineKeyboardButton(f"{n} - {p} so'm", callback_data=f"view_{i}")]
          for i, n, p in rows]
    kb.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="back")])
    await q.edit_message_text("🛍 Mahsulotlar:", reply_markup=InlineKeyboardMarkup(kb))

async def view_product(update, context):
    q = update.callback_query
    await q.answer()
    pid = int(q.data.split("_")[1])
    cur.execute("SELECT name,price,sizes,photo FROM products WHERE id=?", (pid,))
    name, price, sizes, photo = cur.fetchone()
    text = f"📦 {name}\n💰 {price} so'm\n📐 {sizes or 'O‘lchamsiz'}\n\n📞 {ADMIN_PHONE}"

    kb = []
    if sizes:
        for s in sizes.split(","):
            kb.append([InlineKeyboardButton(s, callback_data=f"add_{pid}_{s}")])
    else:
        kb.append([InlineKeyboardButton("🛒 Savatchaga", callback_data=f"add_{pid}_-")])
    kb.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="products")])

    await q.message.reply_photo(photo, caption=text,
                               reply_markup=InlineKeyboardMarkup(kb))

# ================= CART =================
async def add_cart(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    _, pid, size = q.data.split("_")
    CARTS.setdefault(uid, {})
    CARTS[uid][(pid, size)] = CARTS[uid].get((pid, size), 0) + 1
    await q.answer("Savatchaga qo‘shildi ✅", show_alert=True)

async def cart(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    cart = CARTS.get(uid)
    if not cart:
        await q.edit_message_text("Savatcha bo‘sh")
        return
    text = "🛒 Savatcha:\n"
    for (pid, size), qty in cart.items():
        cur.execute("SELECT name FROM products WHERE id=?", (pid,))
        name = cur.fetchone()[0]
        text += f"{name} ({size}) x{qty}\n"
    kb = [
        [InlineKeyboardButton("✅ Tasdiqlash", callback_data="confirm")],
        [InlineKeyboardButton("⬅️ Orqaga", callback_data="back")]
    ]
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

async def confirm(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    cur.execute("INSERT INTO orders(user_id,text) VALUES (?,?)",
                (uid, str(CARTS.get(uid))))
    db.commit()
    CARTS[uid] = {}
    await q.edit_message_text("✅ Buyurtma qabul qilindi")
    await context.bot.send_message(ADMIN_ID, "📦 Yangi buyurtma mavjud")

# ================= ADMIN =================
async def admin(update, context):
    q = update.callback_query
    await q.answer()
    kb = [
        [InlineKeyboardButton("✏️ Mahsulot tahrirlash", callback_data="edit_list")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="broadcast")],
        [InlineKeyboardButton("⬅️ Orqaga", callback_data="back")]
    ]
    await q.edit_message_text("🧑‍💼 Admin panel", reply_markup=InlineKeyboardMarkup(kb))

# ---------- EDIT PRODUCTS ----------
async def edit_list(update, context):
    q = update.callback_query
    await q.answer()
    cur.execute("SELECT id,name FROM products")
    kb = [[InlineKeyboardButton(n, callback_data=f"edit_{i}")]
          for i, n in cur.fetchall()]
    kb.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="admin")])
    await q.edit_message_text("Tahrirlash:", reply_markup=InlineKeyboardMarkup(kb))

async def edit_product(update, context):
    q = update.callback_query
    await q.answer()
    pid = int(q.data.split("_")[1])
    context.user_data["edit"] = pid
    kb = [
        [InlineKeyboardButton("📝 Nom", callback_data="edit_name")],
        [InlineKeyboardButton("💰 Narx", callback_data="edit_price")],
        [InlineKeyboardButton("❌ O‘chirish", callback_data="delete")],
    ]
    await q.edit_message_text("Nimani o‘zgartiramiz?", reply_markup=InlineKeyboardMarkup(kb))

async def edit_field(update, context):
    q = update.callback_query
    await q.answer()
    context.user_data["field"] = q.data
    await q.message.reply_text("Yangi qiymatni kiriting:")

async def save_edit(update, context):
    if "field" not in context.user_data:
        return
    pid = context.user_data["edit"]
    field = context.user_data.pop("field")
    value = update.message.text
    col = "name" if field == "edit_name" else "price"
    cur.execute(f"UPDATE products SET {col}=? WHERE id=?", (value, pid))
    db.commit()
    await update.message.reply_text("✅ Saqlandi")

# ---------- DELETE ----------
async def delete(update, context):
    q = update.callback_query
    await q.answer()
    pid = context.user_data.get("edit")
    cur.execute("DELETE FROM products WHERE id=?", (pid,))
    db.commit()
    await q.edit_message_text("❌ O‘chirildi")

# ---------- BROADCAST ----------
async def broadcast(update, context):
    q = update.callback_query
    await q.answer()
    context.user_data["broadcast"] = True
    await q.message.reply_text("📢 Yuboriladigan xabarni yozing:")

async def send_broadcast(update, context):
    if not context.user_data.get("broadcast"):
        return
    cur.execute("SELECT id FROM users")
    users = cur.fetchall()
    count = 0
    for (uid,) in users:
        try:
            await update.bot.send_message(uid, update.message.text)
            count += 1
        except:
            pass
    context.user_data.pop("broadcast")
    await update.message.reply_text(f"✅ Xabar {count} foydalanuvchiga yuborildi")

# ================= MAIN =================
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.CONTACT | filters.TEXT, register))
    app.add_handler(MessageHandler(filters.TEXT, save_edit))
    app.add_handler(MessageHandler(filters.TEXT, send_broadcast))

    app.add_handler(CallbackQueryHandler(products, pattern="^products$"))
    app.add_handler(CallbackQueryHandler(view_product, pattern="^view_"))
    app.add_handler(CallbackQueryHandler(add_cart, pattern="^add_"))
    app.add_handler(CallbackQueryHandler(cart, pattern="^cart$"))
    app.add_handler(CallbackQueryHandler(confirm, pattern="^confirm$"))

    app.add_handler(CallbackQueryHandler(admin, pattern="^admin$"))
    app.add_handler(CallbackQueryHandler(edit_list, pattern="^edit_list$"))
    app.add_handler(CallbackQueryHandler(edit_product, pattern="^edit_"))
    app.add_handler(CallbackQueryHandler(edit_field, pattern="^edit_name|edit_price$"))
    app.add_handler(CallbackQueryHandler(delete, pattern="^delete$"))
    app.add_handler(CallbackQueryHandler(broadcast, pattern="^broadcast$"))

    app.add_handler(CallbackQueryHandler(start, pattern="^back$"))

    print("✅ BOT ISHGA TUSHDI")
    app.run_polling()

if __name__ == "__main__":
    main()
