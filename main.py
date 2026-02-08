import os
import json
import sqlite3
import logging
from datetime import datetime

from dotenv import load_dotenv


from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ----------------- ENV -----------------
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
ADMIN_PHONE = os.getenv("ADMIN_PHONE", "+998933213532")
DB_DIR = os.getenv("DB_DIR", "/data")

if not TOKEN or ADMIN_ID == 0:
    raise ValueError("BOT_TOKEN yoki ADMIN_ID .env da topilmadi!")

# ----------------- LOG -----------------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("shop-bot")

# ----------------- PATHS (deploy friendly) -----------------
# Shitob/Nix muhitida app papkalari read-only bo'lishi mumkin.
# /data bo'lmasa /tmp ishlatamiz.
def get_writable_dir(preferred: str) -> str:
    try:
        os.makedirs(preferred, exist_ok=True)
        test_path = os.path.join(preferred, ".write_test")
        with open(test_path, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(test_path)
        return preferred
    except Exception:
        os.makedirs("/tmp", exist_ok=True)
        return "/tmp"

WRITABLE_DIR = get_writable_dir(DB_DIR)
DB_PATH = os.path.join(WRITABLE_DIR, "shop.db")
CHART_PATH = os.path.join(WRITABLE_DIR, "stats.png")

# ----------------- DB -----------------
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS users (
  user_id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  phone TEXT NOT NULL,
  created_at TEXT NOT NULL
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  price INTEGER NOT NULL,
  has_sizes INTEGER NOT NULL DEFAULT 0,
  sizes TEXT DEFAULT NULL,
  photo_file_id TEXT DEFAULT NULL,
  created_at TEXT NOT NULL
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS cart (
  user_id INTEGER NOT NULL,
  product_id INTEGER NOT NULL,
  size TEXT DEFAULT NULL,
  qty INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (user_id, product_id, size)
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  items_json TEXT NOT NULL,
  total INTEGER NOT NULL,
  created_at TEXT NOT NULL
)
""")

conn.commit()

# ----------------- UI (Reply Keyboard) -----------------
def main_menu_kb(is_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        ["🛍 Mahsulotlar", "🛒 Savatcha"],
        ["ℹ️ Info", "📞 Contact"],
    ]
    if is_admin:
        rows.append(["👑 Admin panel"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def contact_request_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📞 Raqam yuborish", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

# ----------------- UI (Inline) -----------------
def admin_panel_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Mahsulot qo‘shish", callback_data="A_ADD")],
        [InlineKeyboardButton("📦 Mahsulotlarni boshqarish", callback_data="A_MANAGE")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="A_BC")],
        [InlineKeyboardButton("📊 Statistika", callback_data="A_STATS")],
    ])

def back_to_admin_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Admin panel", callback_data="A_HOME")]])

# ----------------- Helpers -----------------
def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

def money(n: int) -> str:
    return f"{n:,}".replace(",", " ")

def get_user(user_id: int):
    cur.execute("SELECT name, phone FROM users WHERE user_id=?", (user_id,))
    return cur.fetchone()

def product_by_id(pid: int):
    cur.execute("SELECT id,name,price,has_sizes,sizes,photo_file_id FROM products WHERE id=?", (pid,))
    return cur.fetchone()

def list_products():
    cur.execute("SELECT id,name,price,has_sizes,sizes,photo_file_id FROM products ORDER BY id DESC")
    return cur.fetchall()

def cart_items(user_id: int):
    cur.execute("""
        SELECT c.product_id, c.size, c.qty, p.name, p.price
        FROM cart c
        JOIN products p ON p.id=c.product_id
        WHERE c.user_id=?
        ORDER BY p.id DESC
    """, (user_id,))
    return cur.fetchall()

def calc_cart_total(rows):
    return sum(int(r[4]) * int(r[2]) for r in rows)

def clear_state(context: ContextTypes.DEFAULT_TYPE):
    # faqat admin/user flow state tozalaydi (broadcast, add product, edit etc.)
    keys = [
        "state", "tmp_name", "tmp_price", "tmp_has_sizes", "tmp_sizes", "tmp_photo",
        "edit_pid", "edit_field", "bc_mode"
    ]
    for k in keys:
        context.user_data.pop(k, None)

# ----------------- States -----------------
# User registration:
U_REG_NAME = "U_REG_NAME"
U_REG_PHONE = "U_REG_PHONE"

# Admin add product:
A_ADD_HAS_SIZES = "A_ADD_HAS_SIZES"
A_ADD_NAME = "A_ADD_NAME"
A_ADD_PRICE = "A_ADD_PRICE"
A_ADD_SIZES = "A_ADD_SIZES"
A_ADD_PHOTO = "A_ADD_PHOTO"

# Admin broadcast:
A_BC_TEXT = "A_BC_TEXT"
A_BC_PHOTO = "A_BC_PHOTO"

# Admin edit:
A_EDIT_CHOOSE = "A_EDIT_CHOOSE"     # not used as state (inline)
A_EDIT_NAME = "A_EDIT_NAME"
A_EDIT_PRICE = "A_EDIT_PRICE"
A_EDIT_SIZES = "A_EDIT_SIZES"
A_EDIT_PHOTO = "A_EDIT_PHOTO"
A_DELETE_CONFIRM = "A_DELETE_CONFIRM"

# ----------------- Commands -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    # Admin: foydalanuvchi menyusi ham bo'lsin, lekin admin panel ham chiqadi
    if is_admin(uid):
        clear_state(context)
        await update.message.reply_text(
            "👋 Admin sifatida kirdingiz. Pastdagi menyudan foydalaning.",
            reply_markup=main_menu_kb(is_admin=True)
        )
        await update.message.reply_text("👑 Admin panel:", reply_markup=admin_panel_inline())
        return

    # User: ro'yxatdan o'tganmi?
    user = get_user(uid)
    if not user:
        clear_state(context)
        context.user_data["state"] = U_REG_NAME
        await update.message.reply_text(
            "Assalomu alaykum! Ro‘yxatdan o‘tish uchun ismingizni kiriting:",
            reply_markup=ReplyKeyboardRemove()
        )
        return

    await update.message.reply_text(
        "Xush kelibsiz 👋",
        reply_markup=main_menu_kb(is_admin=False)
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Admin uchun flow ni bekor qilish
    if not is_admin(update.effective_user.id):
        return
    clear_state(context)
    await update.message.reply_text("✅ Bekor qilindi.", reply_markup=main_menu_kb(is_admin=True))
    await update.message.reply_text("👑 Admin panel:", reply_markup=admin_panel_inline())

# ----------------- Reply menu handlers -----------------
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = (update.message.text or "").strip()

    # Registration flow text kirsa shu yerga ham keladi, state asosida ajratamiz.
    state = context.user_data.get("state")

    # ---------- USER REG ----------
    if state == U_REG_NAME and not is_admin(uid):
        name = text
        if len(name) < 2:
            await update.message.reply_text("Ism juda qisqa. Qaytadan kiriting:")
            return
        context.user_data["tmp_name"] = name
        context.user_data["state"] = U_REG_PHONE
        await update.message.reply_text(
            "Telefon raqamingizni yuboring (tugma orqali):",
            reply_markup=contact_request_kb()
        )
        return

    # Admin matn flows (add/edit/broadcast) shu yerda ham bo'ladi:
    if is_admin(uid):
        handled = await admin_text_flow(update, context)
        if handled:
            return

    # Oddiy user menyu buyruqlari:
    if text == "🛍 Mahsulotlar":
        await show_products_list(update, context)
        return

    if text == "🛒 Savatcha":
        await show_cart(update, context)
        return

    if text == "ℹ️ Info":
        await update.message.reply_text(
            "🏗 Qurilish materiallari buyurtma bot.\n"
            "🛍 Mahsulotlar — katalog\n"
            "🛒 Savatcha — buyurtmani ko‘rish va tasdiqlash\n"
            "📞 Contact — aloqa"
        )
        return

    if text == "📞 Contact":
        await update.message.reply_text(f"📞 Aloqa uchun: {ADMIN_PHONE}")
        return

    if text == "👑 Admin panel" and is_admin(uid):
        await update.message.reply_text("👑 Admin panel:", reply_markup=admin_panel_inline())
        return

    # Aks holda:
    await update.message.reply_text("Menyudan tanlang 👇", reply_markup=main_menu_kb(is_admin=is_admin(uid)))

async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    contact = update.message.contact
    state = context.user_data.get("state")

    if is_admin(uid):
        # admin registratsiya qilmaydi (kerak bo'lsa ham), e'tiborsiz
        return

    if state != U_REG_PHONE:
        # ro'yxatdan o'tish emas -> ignore
        return

    phone = contact.phone_number
    name = context.user_data.get("tmp_name", "User")
    now = datetime.utcnow().isoformat()

    cur.execute(
        "INSERT OR REPLACE INTO users(user_id,name,phone,created_at) VALUES (?,?,?,?)",
        (uid, name, phone, now)
    )
    conn.commit()

    clear_state(context)
    # eng muhim: contact tugmasi doimiy qolib ketmasin!
    await update.message.reply_text("✅ Ro‘yxatdan o‘tdingiz!", reply_markup=ReplyKeyboardRemove())
    await update.message.reply_text("Menyudan foydalaning:", reply_markup=main_menu_kb(is_admin=False))

# ----------------- Products (User) -----------------
async def show_products_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    products = list_products()
    if not products:
        await update.message.reply_text("Hozircha mahsulotlar yo‘q.")
        return

    # har bir mahsulotni rasm+caption qilib yuboramiz, pastida inline tugmalar
    for (pid, name, price, has_sizes, sizes, photo_id) in products:
        caption = (
            f"📦 {name}\n"
            f"💰 {money(price)} so'm\n"
        )
        if has_sizes and sizes:
            caption += f"📐 O‘lchamlar: {sizes}\n"
        else:
            caption += "📐 O‘lchamsiz\n"
        caption += f"\n📞 Aloqa: {ADMIN_PHONE}"

        if has_sizes and sizes:
            btns = []
            for s in [x.strip() for x in sizes.split(",") if x.strip()]:
                btns.append([InlineKeyboardButton(f"➕ {s}", callback_data=f"C_ADD|{pid}|{s}")])
            btns.append([InlineKeyboardButton("🛒 Savatcha", callback_data="C_VIEW")])
            markup = InlineKeyboardMarkup(btns)
        else:
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Savatchaga", callback_data=f"C_ADD|{pid}|-")],
                [InlineKeyboardButton("🛒 Savatcha", callback_data="C_VIEW")]
            ])

        if photo_id:
            await update.message.reply_photo(photo=photo_id, caption=caption, reply_markup=markup)
        else:
            await update.message.reply_text(caption, reply_markup=markup)

# ----------------- Cart (User) -----------------
async def show_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    rows = cart_items(uid)
    if not rows:
        await update.message.reply_text("🛒 Savatcha bo‘sh.")
        return

    total = calc_cart_total(rows)
    lines = ["🛒 Savatcha:"]
    keyboard = []

    for (pid, size, qty, name, price) in rows:
        size_txt = f" ({size})" if size and size != "-" else ""
        lines.append(f"• {name}{size_txt} × {qty} = {money(price*qty)} so'm")

        keyboard.append([
            InlineKeyboardButton("➖", callback_data=f"C_DEC|{pid}|{size or '-'}"),
            InlineKeyboardButton(f"{qty} dona", callback_data="noop"),
            InlineKeyboardButton("➕", callback_data=f"C_INC|{pid}|{size or '-'}"),
        ])
        keyboard.append([
            InlineKeyboardButton("🗑 O‘chirish", callback_data=f"C_DEL|{pid}|{size or '-'}")
        ])

    lines.append(f"\n💰 Jami: {money(total)} so'm")

    keyboard.append([InlineKeyboardButton("✅ Buyurtmani tasdiqlash", callback_data="C_CONFIRM")])
    keyboard.append([InlineKeyboardButton("🧹 Savatchani tozalash", callback_data="C_CLEAR")])

    await update.message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard))

# ----------------- Callback router -----------------
async def cb_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    uid = q.from_user.id
    data = q.data or ""

    # noop
    if data == "noop":
        return

    # ----- Admin callbacks -----
    if is_admin(uid):
        if data == "A_HOME":
            clear_state(context)
            await q.message.reply_text("👑 Admin panel:", reply_markup=admin_panel_inline())
            return

        if data == "A_ADD":
            clear_state(context)
            context.user_data["state"] = A_ADD_HAS_SIZES
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("📐 O‘lchamli", callback_data="A_ADD_SZ|1")],
                [InlineKeyboardButton("📦 O‘lchamsiz", callback_data="A_ADD_SZ|0")],
                [InlineKeyboardButton("❌ Bekor", callback_data="A_HOME")],
            ])
            await q.message.reply_text("Mahsulot o‘lchamlimi?", reply_markup=markup)
            return

        if data.startswith("A_ADD_SZ|"):
            has_sz = int(data.split("|")[1])
            context.user_data["tmp_has_sizes"] = has_sz
            context.user_data["state"] = A_ADD_NAME
            await q.message.reply_text("📦 Mahsulot nomini kiriting:", reply_markup=back_to_admin_inline())
            return

        if data == "A_MANAGE":
            clear_state(context)
            await admin_manage_products(q, context)
            return

        if data.startswith("A_EDIT|"):
            pid = int(data.split("|")[1])
            context.user_data["edit_pid"] = pid
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("✏️ Nomi", callback_data="A_EF|name")],
                [InlineKeyboardButton("💰 Narx", callback_data="A_EF|price")],
                [InlineKeyboardButton("📐 O‘lchamlar", callback_data="A_EF|sizes")],
                [InlineKeyboardButton("🖼 Rasm", callback_data="A_EF|photo")],
                [InlineKeyboardButton("❌ O‘chirish", callback_data="A_DEL_CONFIRM")],
                [InlineKeyboardButton("⬅️ Orqaga", callback_data="A_MANAGE")]
            ])
            await q.message.reply_text("Nimani tahrirlaysiz?", reply_markup=markup)
            return

        if data.startswith("A_EF|"):
            field = data.split("|")[1]
            pid = context.user_data.get("edit_pid")
            if not pid:
                await q.message.reply_text("⚠️ Avval mahsulot tanlang.", reply_markup=back_to_admin_inline())
                return

            if field == "name":
                context.user_data["edit_field"] = "name"
                context.user_data["state"] = A_EDIT_NAME
                await q.message.reply_text("✏️ Yangi nomini kiriting:")
                return
            if field == "price":
                context.user_data["edit_field"] = "price"
                context.user_data["state"] = A_EDIT_PRICE
                await q.message.reply_text("💰 Yangi narxni kiriting (faqat son):")
                return
            if field == "sizes":
                context.user_data["edit_field"] = "sizes"
                context.user_data["state"] = A_EDIT_SIZES
                await q.message.reply_text("📐 O‘lchamlarni kiriting (vergul bilan). O‘lchamsiz qilish uchun: -")
                return
            if field == "photo":
                context.user_data["edit_field"] = "photo"
                context.user_data["state"] = A_EDIT_PHOTO
                await q.message.reply_text("🖼 Yangi rasm yuboring (photo):")
                return

        if data == "A_DEL_CONFIRM":
            pid = context.user_data.get("edit_pid")
            if not pid:
                await q.message.reply_text("⚠️ Avval mahsulot tanlang.", reply_markup=back_to_admin_inline())
                return
            p = product_by_id(pid)
            if not p:
                await q.message.reply_text("Mahsulot topilmadi.", reply_markup=back_to_admin_inline())
                return
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Ha, o‘chirish", callback_data=f"A_DEL_DO|{pid}")],
                [InlineKeyboardButton("❌ Yo‘q", callback_data=f"A_EDIT|{pid}")]
            ])
            await q.message.reply_text(f"❗ '{p[1]}' mahsulotini o‘chiraymi?", reply_markup=markup)
            return

        if data.startswith("A_DEL_DO|"):
            pid = int(data.split("|")[1])
            cur.execute("DELETE FROM products WHERE id=?", (pid,))
            cur.execute("DELETE FROM cart WHERE product_id=?", (pid,))
            conn.commit()
            clear_state(context)
            await q.message.reply_text("✅ Mahsulot o‘chirildi.")
            await admin_manage_products(q, context)
            return

        if data == "A_BC":
            clear_state(context)
            context.user_data["state"] = A_BC_TEXT
            context.user_data["bc_mode"] = "text"
            await q.message.reply_text("📢 Broadcast uchun matn kiriting (yoki rasm yuborsangiz ham bo‘ladi):", reply_markup=back_to_admin_inline())
            return

        if data == "A_STATS":
            await send_stats(q, context)
            return

        # default
        await q.message.reply_text("⚠️ Noma'lum admin buyruq.", reply_markup=back_to_admin_inline())
        return

    # ----- User callbacks (Cart) -----
    if data == "C_VIEW":
        await show_cart_from_callback(q, context)
        return

    if data.startswith("C_ADD|"):
        _, pid, size = data.split("|")
        await cart_add(uid, int(pid), size)
        await q.message.reply_text("✅ Savatchaga qo‘shildi.")
        return

    if data.startswith("C_INC|"):
        _, pid, size = data.split("|")
        await cart_inc(uid, int(pid), size)
        await show_cart_from_callback(q, context)
        return

    if data.startswith("C_DEC|"):
        _, pid, size = data.split("|")
        await cart_dec(uid, int(pid), size)
        await show_cart_from_callback(q, context)
        return

    if data.startswith("C_DEL|"):
        _, pid, size = data.split("|")
        await cart_del(uid, int(pid), size)
        await show_cart_from_callback(q, context)
        return

    if data == "C_CLEAR":
        cur.execute("DELETE FROM cart WHERE user_id=?", (uid,))
        conn.commit()
        await q.message.reply_text("🧹 Savatcha tozalandi.")
        return

    if data == "C_CONFIRM":
        await confirm_order(q, context)
        return

    await q.message.reply_text("⚠️ Noma'lum buyruq.")

async def show_cart_from_callback(q, context):
    # callbackdan kelganda ham cart ko'rsatish
    dummy_update = None
    # textni q.message ga chiqaramiz
    uid = q.from_user.id
    rows = cart_items(uid)
    if not rows:
        await q.message.reply_text("🛒 Savatcha bo‘sh.")
        return

    total = calc_cart_total(rows)
    lines = ["🛒 Savatcha:"]
    keyboard = []
    for (pid, size, qty, name, price) in rows:
        size_txt = f" ({size})" if size and size != "-" else ""
        lines.append(f"• {name}{size_txt} × {qty} = {money(price*qty)} so'm")
        keyboard.append([
            InlineKeyboardButton("➖", callback_data=f"C_DEC|{pid}|{size or '-'}"),
            InlineKeyboardButton(f"{qty} dona", callback_data="noop"),
            InlineKeyboardButton("➕", callback_data=f"C_INC|{pid}|{size or '-'}"),
        ])
        keyboard.append([InlineKeyboardButton("🗑 O‘chirish", callback_data=f"C_DEL|{pid}|{size or '-'}")])

    lines.append(f"\n💰 Jami: {money(total)} so'm")
    keyboard.append([InlineKeyboardButton("✅ Buyurtmani tasdiqlash", callback_data="C_CONFIRM")])
    keyboard.append([InlineKeyboardButton("🧹 Savatchani tozalash", callback_data="C_CLEAR")])

    await q.message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard))

async def cart_add(user_id: int, product_id: int, size: str):
    if size == "-":
        size_val = None
    else:
        size_val = size

    cur.execute("""
        SELECT qty FROM cart WHERE user_id=? AND product_id=? AND COALESCE(size,'-')=COALESCE(?, '-')
    """, (user_id, product_id, size_val))
    row = cur.fetchone()

    if row:
        cur.execute("""
            UPDATE cart SET qty=qty+1
            WHERE user_id=? AND product_id=? AND COALESCE(size,'-')=COALESCE(?, '-')
        """, (user_id, product_id, size_val))
    else:
        cur.execute("INSERT INTO cart(user_id,product_id,size,qty) VALUES (?,?,?,1)", (user_id, product_id, size_val))

    conn.commit()

async def cart_inc(user_id: int, product_id: int, size: str):
    size_val = None if size == "-" else size
    cur.execute("""
        UPDATE cart SET qty=qty+1
        WHERE user_id=? AND product_id=? AND COALESCE(size,'-')=COALESCE(?, '-')
    """, (user_id, product_id, size_val))
    conn.commit()

async def cart_dec(user_id: int, product_id: int, size: str):
    size_val = None if size == "-" else size
    cur.execute("""
        SELECT qty FROM cart
        WHERE user_id=? AND product_id=? AND COALESCE(size,'-')=COALESCE(?, '-')
    """, (user_id, product_id, size_val))
    row = cur.fetchone()
    if not row:
        return
    qty = int(row[0])
    if qty <= 1:
        await cart_del(user_id, product_id, size)
    else:
        cur.execute("""
            UPDATE cart SET qty=qty-1
            WHERE user_id=? AND product_id=? AND COALESCE(size,'-')=COALESCE(?, '-')
        """, (user_id, product_id, size_val))
        conn.commit()

async def cart_del(user_id: int, product_id: int, size: str):
    size_val = None if size == "-" else size
    cur.execute("""
        DELETE FROM cart
        WHERE user_id=? AND product_id=? AND COALESCE(size,'-')=COALESCE(?, '-')
    """, (user_id, product_id, size_val))
    conn.commit()

# ----------------- Order confirm -----------------
async def confirm_order(q, context):
    uid = q.from_user.id
    user = get_user(uid)
    if not user:
        await q.message.reply_text("❗ Avval /start qilib ro‘yxatdan o‘ting.")
        return

    rows = cart_items(uid)
    if not rows:
        await q.message.reply_text("🛒 Savatcha bo‘sh.")
        return

    total = calc_cart_total(rows)
    items = []
    lines = []
    for (pid, size, qty, name, price) in rows:
        items.append({"product_id": pid, "name": name, "size": size, "qty": qty, "price": price})
        size_txt = f" ({size})" if size and size != "-" else ""
        lines.append(f"• {name}{size_txt} × {qty} = {money(price*qty)} so'm")

    # Save order
    now = datetime.utcnow().isoformat()
    cur.execute(
        "INSERT INTO orders(user_id, items_json, total, created_at) VALUES (?,?,?,?)",
        (uid, json.dumps(items, ensure_ascii=False), int(total), now)
    )
    # Clear cart
    cur.execute("DELETE FROM cart WHERE user_id=?", (uid,))
    conn.commit()

    await q.message.reply_text("✅ Buyurtma qabul qilindi! Tez orada siz bilan bog‘lanamiz.")

    # Send to admin
    name, phone = user
    admin_text = (
        "📥 YANGI BUYURTMA\n\n"
        f"👤 Mijoz: {name}\n"
        f"📞 Tel: {phone}\n\n"
        "🧾 Buyurtma:\n" + "\n".join(lines) + "\n\n"
        f"💰 Jami: {money(total)} so'm"
    )
    await context.bot.send_message(chat_id=ADMIN_ID, text=admin_text)

# ----------------- Admin manage list -----------------
async def admin_manage_products(q, context):
    products = list_products()
    if not products:
        await q.message.reply_text("Mahsulotlar yo‘q.", reply_markup=back_to_admin_inline())
        return

    # ro'yxatni ixcham qilib chiqaramiz
    for (pid, name, price, has_sizes, sizes, photo_id) in products:
        sz = sizes if (has_sizes and sizes) else "o‘lchamsiz"
        text = f"#{pid} • {name}\n💰 {money(price)} so'm\n📐 {sz}"
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Tahrirlash", callback_data=f"A_EDIT|{pid}")],
            [InlineKeyboardButton("❌ O‘chirish", callback_data=f"A_DEL_DO|{pid}")],
        ])
        await q.message.reply_text(text, reply_markup=markup)

# ----------------- Admin flows: text/photo -----------------
async def admin_text_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Admin matn yuborganida state bo'yicha ishlaydi. True qaytarsa - ishlov berildi."""
    uid = update.effective_user.id
    if not is_admin(uid):
        return False

    state = context.user_data.get("state")
    text = (update.message.text or "").strip()

    # ADD PRODUCT
    if state == A_ADD_NAME:
        if len(text) < 2:
            await update.message.reply_text("Nom juda qisqa. Qayta kiriting:")
            return True
        context.user_data["tmp_name"] = text
        context.user_data["state"] = A_ADD_PRICE
        await update.message.reply_text("💰 Narxni kiriting (faqat son):")
        return True

    if state == A_ADD_PRICE:
        if not text.isdigit():
            await update.message.reply_text("❌ Narx faqat raqam bo‘lishi kerak. Masalan: 120000")
            return True
        context.user_data["tmp_price"] = int(text)
        has_sizes = int(context.user_data.get("tmp_has_sizes", 0))
        if has_sizes == 1:
            context.user_data["state"] = A_ADD_SIZES
            await update.message.reply_text("📐 O‘lchamlarni kiriting (vergul bilan). Masalan: 10x10, 20x20, 50x50")
        else:
            context.user_data["state"] = A_ADD_PHOTO
            await update.message.reply_text("🖼 Mahsulot rasmini yuboring (photo):")
        return True

    if state == A_ADD_SIZES:
        sizes = text
        if sizes == "-" or sizes.strip() == "":
            # baribir o'lchamsiz qilamiz
            context.user_data["tmp_has_sizes"] = 0
            context.user_data["tmp_sizes"] = None
        else:
            context.user_data["tmp_sizes"] = sizes
        context.user_data["state"] = A_ADD_PHOTO
        await update.message.reply_text("🖼 Mahsulot rasmini yuboring (photo):")
        return True

    # EDIT PRODUCT (text fields)
    if state == A_EDIT_NAME:
        pid = context.user_data.get("edit_pid")
        if not pid:
            await update.message.reply_text("⚠️ edit_pid yo‘q. Qayta tanlang.", reply_markup=back_to_admin_inline())
            clear_state(context)
            return True
        cur.execute("UPDATE products SET name=? WHERE id=?", (text, pid))
        conn.commit()
        clear_state(context)
        await update.message.reply_text("✅ Nomi yangilandi.", reply_markup=back_to_admin_inline())
        return True

    if state == A_EDIT_PRICE:
        pid = context.user_data.get("edit_pid")
        if not pid:
            await update.message.reply_text("⚠️ edit_pid yo‘q. Qayta tanlang.", reply_markup=back_to_admin_inline())
            clear_state(context)
            return True
        if not text.isdigit():
            await update.message.reply_text("❌ Narx faqat son bo‘lishi kerak.")
            return True
        cur.execute("UPDATE products SET price=? WHERE id=?", (int(text), pid))
        conn.commit()
        clear_state(context)
        await update.message.reply_text("✅ Narx yangilandi.", reply_markup=back_to_admin_inline())
        return True

    if state == A_EDIT_SIZES:
        pid = context.user_data.get("edit_pid")
        if not pid:
            await update.message.reply_text("⚠️ edit_pid yo‘q. Qayta tanlang.", reply_markup=back_to_admin_inline())
            clear_state(context)
            return True
        if text == "-" or text.strip() == "":
            cur.execute("UPDATE products SET has_sizes=0, sizes=NULL WHERE id=?", (pid,))
        else:
            cur.execute("UPDATE products SET has_sizes=1, sizes=? WHERE id=?", (text, pid))
        conn.commit()
        clear_state(context)
        await update.message.reply_text("✅ O‘lchamlar yangilandi.", reply_markup=back_to_admin_inline())
        return True

    # BROADCAST (text)
    if state == A_BC_TEXT:
        # text yuborsa broadcast qilamiz
        await do_broadcast_text(update, context, text)
        clear_state(context)
        await update.message.reply_text("✅ Broadcast yuborildi.", reply_markup=back_to_admin_inline())
        return True

    return False

async def admin_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        return

    state = context.user_data.get("state")

    # ADD PRODUCT PHOTO
    if state == A_ADD_PHOTO:
        photo = update.message.photo[-1]  # eng katta
        file_id = photo.file_id

        name = context.user_data.get("tmp_name")
        price = context.user_data.get("tmp_price")
        has_sizes = int(context.user_data.get("tmp_has_sizes", 0))
        sizes = context.user_data.get("tmp_sizes")

        if not name or price is None:
            await update.message.reply_text("⚠️ Noto‘g‘ri holat. Qayta boshlang (/cancel).", reply_markup=back_to_admin_inline())
            clear_state(context)
            return

        now = datetime.utcnow().isoformat()
        cur.execute(
            "INSERT INTO products(name,price,has_sizes,sizes,photo_file_id,created_at) VALUES (?,?,?,?,?,?)",
            (name, int(price), has_sizes, sizes if has_sizes else None, file_id, now)
        )
        conn.commit()
        clear_state(context)
        await update.message.reply_text("✅ Mahsulot qo‘shildi!", reply_markup=back_to_admin_inline())
        return

    # EDIT PHOTO
    if state == A_EDIT_PHOTO:
        pid = context.user_data.get("edit_pid")
        if not pid:
            await update.message.reply_text("⚠️ edit_pid yo‘q. Qayta tanlang.", reply_markup=back_to_admin_inline())
            clear_state(context)
            return

        photo = update.message.photo[-1]
        file_id = photo.file_id
        cur.execute("UPDATE products SET photo_file_id=? WHERE id=?", (file_id, pid))
        conn.commit()
        clear_state(context)
        await update.message.reply_text("✅ Rasm yangilandi.", reply_markup=back_to_admin_inline())
        return

    # BROADCAST PHOTO
    if state == A_BC_TEXT:
        # admin rasm yuborsa, caption bilan ham broadcast qilamiz
        cap = update.message.caption or ""
        await do_broadcast_photo(update, context, update.message.photo[-1].file_id, cap)
        clear_state(context)
        await update.message.reply_text("✅ Broadcast (rasm) yuborildi.", reply_markup=back_to_admin_inline())
        return

async def do_broadcast_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    cur.execute("SELECT user_id FROM users")
    users = cur.fetchall()
    ok, fail = 0, 0
    for (uid,) in users:
        try:
            await context.bot.send_message(uid, text)
            ok += 1
        except Exception:
            fail += 1
    await update.message.reply_text(f"📢 Broadcast natija: ✅{ok} / ❌{fail}")

async def do_broadcast_photo(update: Update, context: ContextTypes.DEFAULT_TYPE, file_id: str, caption: str):
    cur.execute("SELECT user_id FROM users")
    users = cur.fetchall()
    ok, fail = 0, 0
    for (uid,) in users:
        try:
            await context.bot.send_photo(uid, photo=file_id, caption=caption[:1024])
            ok += 1
        except Exception:
            fail += 1
    await update.message.reply_text(f"📢 Broadcast (rasm) natija: ✅{ok} / ❌{fail}")

# ----------------- Admin stats (text) -----------------
def make_bar(value: int, max_value: int, width: int = 18) -> str:
    if max_value <= 0:
        return "▱" * 1
    filled = int((value / max_value) * width)
    filled = max(1, min(width, filled))
    return "▰" * filled + "▱" * (width - filled)


async def send_stats(q, context: ContextTypes.DEFAULT_TYPE):
    cur.execute("SELECT COUNT(*) FROM users")
    users_count = int(cur.fetchone()[0])

    cur.execute("SELECT COUNT(*), COALESCE(SUM(total),0) FROM orders")
    orders_count, revenue = cur.fetchone()
    orders_count = int(orders_count)
    revenue = int(revenue or 0)

    cur.execute("SELECT items_json FROM orders ORDER BY id DESC LIMIT 2000")
    rows = cur.fetchall()

    counts = {}
    for (items_json,) in rows:
        try:
            items = json.loads(items_json)
            for it in items:
                nm = it.get("name", "Unknown")
                qty = int(it.get("qty", 1))
                counts[nm] = counts.get(nm, 0) + qty
        except Exception:
            continue

    top = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:8]

    text = (
        "📊 Statistika\n\n"
        f"👤 Foydalanuvchilar: {users_count}\n"
        f"🧾 Buyurtmalar: {orders_count}\n"
        f"💰 Jami tushum: {money(revenue)} so'm\n\n"
    )

    if not top:
        text += "Grafik uchun hali buyurtmalar yetarli emas."
        await q.message.reply_text(text)
        return

    max_qty = max(v for _, v in top)
    text += "📈 Top mahsulotlar:\n"
    for name, qty in top:
        text += f"{make_bar(qty, max_qty)}  {qty}  — {name}\n"

    await q.message.reply_text(text)

# ----------------- Admin edit state setters (text) -----------------
async def admin_set_edit_state(update: Update, context: ContextTypes.DEFAULT_TYPE, state_name: str):
    context.user_data["state"] = state_name

# ----------------- Photo (user ignore) -----------------
async def user_photo_ignore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # oddiy user rasm yuborsa (ro'yxatdan o'tish / menu) - e'tiborsiz
    if is_admin(update.effective_user.id):
        return
    await update.message.reply_text("Rasm qabul qilinmadi. Menyudan foydalaning 👇", reply_markup=main_menu_kb(False))

# ----------------- Main -----------------
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    # commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))

    # callbacks (inline)
    app.add_handler(CallbackQueryHandler(cb_router))

    # contact for registration
    app.add_handler(MessageHandler(filters.CONTACT, contact_handler))

    # photos (admin add/edit/broadcast)
    app.add_handler(MessageHandler(filters.PHOTO, admin_photo_handler))
    app.add_handler(MessageHandler(filters.PHOTO, user_photo_ignore))

    # text menu + admin flows
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_handler))

    log.info("Bot running. DB at %s", DB_PATH)
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
