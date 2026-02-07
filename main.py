import os
import logging
from dotenv import load_dotenv

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ================== ENV ==================
load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
ADMIN_PHONE = os.getenv("ADMIN_PHONE")

if not TOKEN or not ADMIN_ID:
    raise ValueError("BOT_TOKEN yoki ADMIN_ID .env faylda topilmadi")

# ================== LOG ==================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# ================== /start ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    keyboard = [
        [KeyboardButton("📞 Telefon raqamni yuborish", request_contact=True)]
    ]
    reply_kb = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

    await update.message.reply_text(
        f"Assalomu alaykum {user.first_name} 👋\n\n"
        "Botdan foydalanish uchun telefon raqamingizni yuboring:",
        reply_markup=reply_kb,
    )

# ================== CONTACT ==================
async def get_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    user = update.effective_user

    # bu joyda keyinchalik SQLite ga yoziladi
    context.user_data["phone"] = contact.phone_number
    context.user_data["name"] = user.first_name

    main_menu = ReplyKeyboardMarkup(
        [
            ["🧱 Mahsulotlar", "🛒 Savatcha"],
            ["ℹ️ Yordam"]
        ],
        resize_keyboard=True,
    )

    await update.message.reply_text(
        "✅ Ro‘yxatdan o‘tdingiz!\n\nAsosiy menyu:",
        reply_markup=main_menu,
    )

# ================== ADMIN PANEL ==================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Siz admin emassiz")
        return

    keyboard = [
        [InlineKeyboardButton("➕ Mahsulot qo‘shish", callback_data="add_product")],
        [InlineKeyboardButton("✏️ Mahsulotlarni tahrirlash", callback_data="edit_products")],
        [InlineKeyboardButton("📊 Buyurtma statistikasi", callback_data="stats")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="broadcast")],
    ]

    await update.message.reply_text(
        "👑 Admin panel:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

# ================== CALLBACK ==================
async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "add_product":
        await query.message.reply_text("🧱 Mahsulot qo‘shish (keyin to‘liq qo‘shiladi)")
    elif query.data == "edit_products":
        await query.message.reply_text("✏️ Mahsulotlarni tahrirlash paneli")
    elif query.data == "stats":
        await query.message.reply_text("📊 Buyurtma statistikasi")
    elif query.data == "broadcast":
        await query.message.reply_text("📢 Broadcast rejimi yoqildi")

# ================== TEXT HANDLER ==================
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "🧱 Mahsulotlar":
        await update.message.reply_text(
            "📦 Mahsulotlar ro‘yxati (tez orada)\n\n"
            f"📞 Aloqa: {ADMIN_PHONE}"
        )

    elif text == "🛒 Savatcha":
        await update.message.reply_text("🛒 Savatchangiz bo‘sh")

    elif text == "ℹ️ Yordam":
        await update.message.reply_text(
            "Bu bot qurilish materiallari buyurtma qilish uchun mo‘ljallangan."
        )

# ================== MAIN ==================
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))

    app.add_handler(MessageHandler(filters.CONTACT, get_contact))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(CallbackQueryHandler(callbacks))

    print("🤖 Bot ishga tushdi...")
    app.run_polling()

if __name__ == "__main__":
    main()
