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
TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "").strip()
ADMIN_PHONE = os.getenv("ADMIN_PHONE", "+998933213532").strip()
DB_DIR = os.getenv("DB_DIR", "/data").strip()

ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_RAW.split(",") if x.strip().isdigit()]

if not TOKEN or not ADMIN_IDS:
    raise ValueError("BOT_TOKEN yoki ADMIN_IDS .env da topilmadi!")

# ----------------- LOG -----------------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("shop-bot")

# ----------------- PATHS (deploy friendly) -----------------
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

# ----------------- Helpers -----------------
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

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

def cart_rows(user_id: int):
    cur.execute("""
        SELECT c.product_id, COALESCE(c.size,'-') as size, c.qty, p.name, p.price
        FROM cart c
        JOIN products p ON p.id=c.product_id
        WHERE c.user_id=?
        ORDER BY p.id DESC
    """, (user_id,))
    return cur.fetchall()

def calc_cart_total(rows):
    return sum(int(r[4]) * int(r[2]) for r in rows)

def ensure_nav(context: ContextTypes.DEFAULT_TYPE):
    if "nav" not in context.user_data or not isinstance(context.user_data["nav"], list):
        context.user_data["nav"] = []

def nav_push(context: ContextTypes.DEFAULT_TYPE, view: str, data: dict | None = None):
    ensure_nav(context)
    context.user_data["nav"].append({"view": view, "data": data or {}})

def nav_pop(context: ContextTypes.DEFAULT_TYPE):
    ensure_nav(context)
    if context.user_data["nav"]:
        context.user_data["nav"].pop()

def nav_top(context: ContextTypes.DEFAULT_TYPE):
    ensure_nav(context)
    if not context.user_data["nav"]:
        return None
    return context.user_data["nav"][-1]

def clear_state(context: ContextTypes.DEFAULT_TYPE):
    keys = [
        "state",
        "tmp_name", "tmp_price", "tmp_has_sizes", "tmp_sizes",
        "edit_pid",
        "pending_pid", "pending_size", "pending_origin",
    ]
    for k in keys:
        context.user_data.pop(k, None)

# ----------------- UI (Reply Keyboard) -----------------
def main_menu_kb(is_admin_user: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        ["🛍 Mahsulotlar", "🛒 Savatcha"],
        ["ℹ️ Info", "📞 Contact"],
    ]
    if is_admin_user:
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

def back_btn() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Orqaga", callback_data="U_BACK")]])

def back_to_admin_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Admin panel", callback_data="A_HOME")]])

# ----------------- States -----------------
U_REG_NAME = "U_REG_NAME"
U_REG_PHONE = "U_REG_PHONE"
U_WAIT_QTY = "U_WAIT_QTY"  # size tanlangandan keyin son so'rash

A_ADD_HAS_SIZES = "A_ADD_HAS_SIZES"
A_ADD_NAME = "A_ADD_NAME"
A_ADD_PRICE = "A_ADD_PRICE"
A_ADD_SIZES = "A_ADD_SIZES"
A_ADD_PHOTO = "A_ADD_PHOTO"

A_EDIT_NAME = "A_EDIT_NAME"
A_EDIT_PRICE = "A_EDIT_PRICE"
A_EDIT_SIZES = "A_EDIT_SIZES"
A_EDIT_PHOTO = "A_EDIT_PHOTO"

A_BC_TEXT = "A_BC_TEXT"

# ----------------- Commands -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    # Admin
    if is_admin(uid):
        clear_state(context)
        await update.message.reply_text(
            "👋 Admin sifatida kirdingiz.",
            reply_markup=main_menu_kb(is_admin_user=True)
        )
        await update.message.reply_text("👑 Admin panel:", reply_markup=admin_panel_inline())
        return

    # User
    user = get_user(uid)
    if not user:
        clear_state(context)
        context.user_data["state"] = U_REG_NAME
        await update.message.reply_text(
            "Assalomu alaykum! Ro‘yxatdan o‘tish uchun ismingizni kiriting:",
            reply_markup=ReplyKeyboardRemove()
        )
        return

    await update.message.reply_text("Xush kelibsiz 👋", reply_markup=main_menu_kb(False))

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    clear_state(context)
    await update.message.reply_text("✅ Bekor qilindi.", reply_markup=main_menu_kb(True))
    await update.message.reply_text("👑 Admin panel:", reply_markup=admin_panel_inline())

# ----------------- Registration -----------------
async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if is_admin(uid):
        return

    state = context.user_data.get("state")
    if state != U_REG_PHONE:
        return

    phone = update.message.contact.phone_number
    name = context.user_data.get("tmp_name", "User")
    now = datetime.utcnow().isoformat()

    cur.execute(
        "INSERT OR REPLACE INTO users(user_id,name,phone,created_at) VALUES (?,?,?,?)",
        (uid, name, phone, now)
    )
    conn.commit()

    clear_state(context)
    # muhim: contact tugmasi qolib ketmasin
    await update.message.reply_text("✅ Ro‘yxatdan o‘tdingiz!", reply_markup=ReplyKeyboardRemove())
    await update.message.reply_text("Menyudan foydalaning 👇", reply_markup=main_menu_kb(False))

# ----------------- User menu + Admin text flow -----------------
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = (update.message.text or "").strip()
    state = context.user_data.get("state")

    # USER REG FLOW
    if state == U_REG_NAME and (not is_admin(uid)):
        if len(text) < 2:
            await update.message.reply_text("Ism juda qisqa. Qaytadan kiriting:")
            return
        context.user_data["tmp_name"] = text
        context.user_data["state"] = U_REG_PHONE
        await update.message.reply_text("Telefon raqamingizni yuboring:", reply_markup=contact_request_kb())
        return

    # USER QTY FLOW (size tanlangandan keyin son so'raymiz)
    if state == U_WAIT_QTY and (not is_admin(uid)):
        qty_txt = text
        if not qty_txt.isdigit():
            await update.message.reply_text("❌ Son faqat raqam bo‘lishi kerak. Masalan: 3", reply_markup=back_btn())
            return
        qty = int(qty_txt)
        if qty <= 0:
            await update.message.reply_text("❌ Son 1 dan katta bo‘lsin.", reply_markup=back_btn())
            return

        pid = context.user_data.get("pending_pid")
        size = context.user_data.get("pending_size", "-")
        origin = context.user_data.get("pending_origin", "CATALOG")

        if not pid:
            clear_state(context)
            await update.message.reply_text("⚠️ Xatolik. Qaytadan urinib ko‘ring.", reply_markup=main_menu_kb(False))
            return

        await cart_add_qty(uid, int(pid), size, qty)
        clear_state(context)

        await update.message.reply_text("✅ Savatchaga qo‘shildi.", reply_markup=main_menu_kb(False))

        # foydalanuvchini oldingi oynaga qaytaramiz (talab bo'yicha back tugmasi ishlaydi)
        # bu yerda avtomatik qaytarib yubormaymiz — user back bosib qaytadi.
        return

    # ADMIN FLOWS
    if is_admin(uid):
        handled = await admin_text_flow(update, context)
        if handled:
            return

    # USER MENU
    if text == "🛍 Mahsulotlar":
        await show_catalog_list(update, context, push=True)
        return

    if text == "🛒 Savatcha":
        await show_cart_list(update, context, push=True)
        return

    if text == "ℹ️ Info":
        await update.message.reply_text(
            "🏗 Qurilish materiallari buyurtma boti.\n"
            "🛍 Mahsulotlar — katalog\n"
            "🛒 Savatcha — buyurtma va tasdiqlash\n"
            "📞 Contact — aloqa"
        )
        return

    if text == "📞 Contact":
        await update.message.reply_text(f"📞 Aloqa: {ADMIN_PHONE}")
        return

    if text == "👑 Admin panel" and is_admin(uid):
        await update.message.reply_text("👑 Admin panel:", reply_markup=admin_panel_inline())
        return

    await update.message.reply_text("Menyudan tanlang 👇", reply_markup=main_menu_kb(is_admin(uid)))

# ----------------- Catalog list (1 post) -----------------
async def show_catalog_list(update_or_qmsg, context: ContextTypes.DEFAULT_TYPE, push: bool):
    products = list_products()
    if not products:
        if hasattr(update_or_qmsg, "message"):
            await update_or_qmsg.message.reply_text("Hozircha mahsulotlar yo‘q.")
        else:
            await update_or_qmsg.reply_text("Hozircha mahsulotlar yo‘q.")
        return

    if push:
        nav_push(context, "CATALOG", {})

    # 1 ta postda ro'yxat + inline tugmalar
    lines = ["🛍 Mahsulotlar ro‘yxati:"]
    kb = []
    for (pid, name, price, has_sizes, sizes, photo_id) in products:
        lines.append(f"• {name}")

        kb.append([InlineKeyboardButton(f"🔎 {name}", callback_data=f"U_PROD|{pid}|CATALOG")])

    kb.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="U_BACK")])

    text = "\n".join(lines)
    if hasattr(update_or_qmsg, "message"):
        await update_or_qmsg.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update_or_qmsg.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))

# ----------------- Product detail -----------------
async def show_product_detail(q_or_msg, context: ContextTypes.DEFAULT_TYPE, pid: int, origin: str, push: bool):
    p = product_by_id(pid)
    if not p:
        if hasattr(q_or_msg, "message"):
            await q_or_msg.message.reply_text("Mahsulot topilmadi.", reply_markup=back_btn())
        else:
            await q_or_msg.reply_text("Mahsulot topilmadi.", reply_markup=back_btn())
        return

    (pid, name, price, has_sizes, sizes, photo_id) = p

    caption = f"📦 {name}\n💰 {money(price)} so'm\n"
    if has_sizes and sizes:
        caption += f"📐 O‘lchamlar: {sizes}\n"
        caption += "\nO‘lchamni tanlang 👇 (keyin sonini kiritasiz)"
    else:
        caption += "📐 O‘lchamsiz\n\nSavatchaga qo‘shish uchun davom eting 👇 (keyin sonini kiritasiz)"

    caption += f"\n\n📞 Aloqa: {ADMIN_PHONE}"

    kb = []

    if has_sizes and sizes:
        for s in [x.strip() for x in sizes.split(",") if x.strip()]:
            kb.append([InlineKeyboardButton(f"📐 {s}", callback_data=f"U_SIZE|{pid}|{s}|{origin}")])
    else:
        kb.append([InlineKeyboardButton("➕ Savatchaga", callback_data=f"U_SIZE|{pid}|-|{origin}")])

    kb.append([InlineKeyboardButton("🛒 Savatcha", callback_data="U_CART")])
    kb.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="U_BACK")])

    if push:
        nav_push(context, "PRODUCT", {"pid": pid, "origin": origin})

    if photo_id:
        # yangi detail post
        if hasattr(q_or_msg, "message"):
            await q_or_msg.message.reply_photo(photo=photo_id, caption=caption, reply_markup=InlineKeyboardMarkup(kb))
        else:
            await q_or_msg.reply_photo(photo=photo_id, caption=caption, reply_markup=InlineKeyboardMarkup(kb))
    else:
        if hasattr(q_or_msg, "message"):
            await q_or_msg.message.reply_text(caption, reply_markup=InlineKeyboardMarkup(kb))
        else:
            await q_or_msg.reply_text(caption, reply_markup=InlineKeyboardMarkup(kb))

# ----------------- Cart: list (1 post) -----------------
async def show_cart_list(update_or_qmsg, context: ContextTypes.DEFAULT_TYPE, push: bool):
    uid = update_or_qmsg.from_user.id if hasattr(update_or_qmsg, "from_user") else update_or_qmsg.effective_user.id
    rows = cart_rows(uid)
    if not rows:
        if hasattr(update_or_qmsg, "message"):
            await update_or_qmsg.message.reply_text("🛒 Savatcha bo‘sh.", reply_markup=back_btn())
        else:
            await update_or_qmsg.reply_text("🛒 Savatcha bo‘sh.", reply_markup=back_btn())
        return

    if push:
        nav_push(context, "CART", {})

    total = calc_cart_total(rows)
    lines = ["🛒 Savatcha ro‘yxati:"]
    kb = []

    for (pid, size, qty, name, price) in rows:
        size_txt = f" ({size})" if size != "-" else ""
        line_total = int(price) * int(qty)
        lines.append(f"• {name}{size_txt} × {qty} = {money(line_total)} so'm")

        # "linkli" tugma: bosilganda detal posti ochiladi
        kb.append([InlineKeyboardButton(f"🔎 {name}{size_txt}", callback_data=f"U_PROD|{pid}|CART")])

    lines.append(f"\n💰 Jami: {money(total)} so'm")

    kb.append([InlineKeyboardButton("✅ Buyurtmani tasdiqlash", callback_data="U_CONFIRM")])
    kb.append([InlineKeyboardButton("🧹 Savatchani tozalash", callback_data="U_CLEAR_CART")])
    kb.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="U_BACK")])

    text = "\n".join(lines)
    if hasattr(update_or_qmsg, "message"):
        await update_or_qmsg.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update_or_qmsg.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))

# ----------------- Cart DB operations (qty manual) -----------------
async def cart_add_qty(user_id: int, product_id: int, size: str, qty_to_add: int):
    size_val = None if size == "-" else size

    cur.execute("""
        SELECT qty FROM cart
        WHERE user_id=? AND product_id=? AND COALESCE(size,'-')=COALESCE(?, '-')
    """, (user_id, product_id, size_val))
    row = cur.fetchone()

    if row:
        cur.execute("""
            UPDATE cart SET qty=qty+?
            WHERE user_id=? AND product_id=? AND COALESCE(size,'-')=COALESCE(?, '-')
        """, (qty_to_add, user_id, product_id, size_val))
    else:
        cur.execute("INSERT INTO cart(user_id,product_id,size,qty) VALUES (?,?,?,?)",
                    (user_id, product_id, size_val, qty_to_add))
    conn.commit()

# ----------------- Order confirm -----------------
async def confirm_order(user_id: int, context: ContextTypes.DEFAULT_TYPE, reply_target):
    user = get_user(user_id)
    if not user:
        await reply_target.reply_text("❗ Avval /start qilib ro‘yxatdan o‘ting.", reply_markup=back_btn())
        return

    rows = cart_rows(user_id)
    if not rows:
        await reply_target.reply_text("🛒 Savatcha bo‘sh.", reply_markup=back_btn())
        return

    total = calc_cart_total(rows)
    items = []
    lines = []

    for (pid, size, qty, name, price) in rows:
        items.append({"product_id": pid, "name": name, "size": None if size == "-" else size,
                      "qty": qty, "price": price})
        size_txt = f" ({size})" if size != "-" else ""
        lines.append(f"• {name}{size_txt} × {qty} = {money(price*qty)} so'm")

    now = datetime.utcnow().isoformat()
    cur.execute(
        "INSERT INTO orders(user_id, items_json, total, created_at) VALUES (?,?,?,?)",
        (user_id, json.dumps(items, ensure_ascii=False), int(total), now)
    )
    cur.execute("DELETE FROM cart WHERE user_id=?", (user_id,))
    conn.commit()

    await reply_target.reply_text("✅ Buyurtma qabul qilindi! Tez orada siz bilan bog‘lanamiz.", reply_markup=back_btn())

    name, phone = user
    admin_text = (
        "📥 YANGI BUYURTMA\n\n"
        f"👤 Mijoz: {name}\n"
        f"📞 Tel: {phone}\n\n"
        "🧾 Buyurtma:\n" + "\n".join(lines) + "\n\n"
        f"💰 Jami: {money(total)} so'm"
    )

    for aid in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=aid, text=admin_text)
        except Exception:
            pass

# ----------------- Admin manage -----------------
async def admin_manage_products(q, context: ContextTypes.DEFAULT_TYPE):
    products = list_products()
    if not products:
        await q.message.reply_text("Mahsulotlar yo‘q.", reply_markup=back_to_admin_inline())
        return

    for (pid, name, price, has_sizes, sizes, photo_id) in products:
        sz = sizes if (has_sizes and sizes) else "o‘lchamsiz"
        text = f"#{pid} • {name}\n💰 {money(price)} so'm\n📐 {sz}"
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Tahrirlash", callback_data=f"A_EDIT|{pid}")],
            [InlineKeyboardButton("❌ O‘chirish", callback_data=f"A_DEL_DO|{pid}")],
            [InlineKeyboardButton("⬅️ Admin panel", callback_data="A_HOME")]
        ])
        await q.message.reply_text(text, reply_markup=markup)

# ----------------- Admin stats (text chart) -----------------
def make_bar(value: int, max_value: int, width: int = 18) -> str:
    if max_value <= 0:
        return "▰"
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
        await q.message.reply_text(text, reply_markup=back_to_admin_inline())
        return

    max_qty = max(v for _, v in top)
    text += "📈 Top mahsulotlar:\n"
    for name, qty in top:
        text += f"{make_bar(qty, max_qty)}  {qty}  — {name}\n"

    await q.message.reply_text(text, reply_markup=back_to_admin_inline())

# ----------------- Admin flows (text/photo/broadcast/edit) -----------------
async def admin_text_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    uid = update.effective_user.id
    if not is_admin(uid):
        return False

    state = context.user_data.get("state")
    text = (update.message.text or "").strip()

    # ADD PRODUCT
    if state == A_ADD_NAME:
        if len(text) < 2:
            await update.message.reply_text("Nom juda qisqa. Qayta kiriting:", reply_markup=back_to_admin_inline())
            return True
        context.user_data["tmp_name"] = text
        context.user_data["state"] = A_ADD_PRICE
        await update.message.reply_text("💰 Narxni kiriting (faqat son):", reply_markup=back_to_admin_inline())
        return True

    if state == A_ADD_PRICE:
        if not text.isdigit():
            await update.message.reply_text("❌ Narx faqat raqam bo‘lsin. Masalan: 120000", reply_markup=back_to_admin_inline())
            return True
        context.user_data["tmp_price"] = int(text)
        has_sizes = int(context.user_data.get("tmp_has_sizes", 0))
        if has_sizes == 1:
            context.user_data["state"] = A_ADD_SIZES
            await update.message.reply_text("📐 O‘lchamlarni kiriting (vergul bilan). Masalan: 10x10, 20x20", reply_markup=back_to_admin_inline())
        else:
            context.user_data["state"] = A_ADD_PHOTO
            await update.message.reply_text("🖼 Mahsulot rasmini yuboring (photo):", reply_markup=back_to_admin_inline())
        return True

    if state == A_ADD_SIZES:
        sizes = text
        if sizes == "-" or sizes.strip() == "":
            context.user_data["tmp_has_sizes"] = 0
            context.user_data["tmp_sizes"] = None
        else:
            context.user_data["tmp_sizes"] = sizes
        context.user_data["state"] = A_ADD_PHOTO
        await update.message.reply_text("🖼 Mahsulot rasmini yuboring (photo):", reply_markup=back_to_admin_inline())
        return True

    # EDIT FIELDS (text)
    if state == A_EDIT_NAME:
        pid = context.user_data.get("edit_pid")
        if not pid:
            await update.message.reply_text("⚠️ Avval mahsulot tanlang.", reply_markup=back_to_admin_inline())
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
            await update.message.reply_text("⚠️ Avval mahsulot tanlang.", reply_markup=back_to_admin_inline())
            clear_state(context)
            return True
        if not text.isdigit():
            await update.message.reply_text("❌ Narx faqat son bo‘lishi kerak.", reply_markup=back_to_admin_inline())
            return True
        cur.execute("UPDATE products SET price=? WHERE id=?", (int(text), pid))
        conn.commit()
        clear_state(context)
        await update.message.reply_text("✅ Narx yangilandi.", reply_markup=back_to_admin_inline())
        return True

    if state == A_EDIT_SIZES:
        pid = context.user_data.get("edit_pid")
        if not pid:
            await update.message.reply_text("⚠️ Avval mahsulot tanlang.", reply_markup=back_to_admin_inline())
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
        photo = update.message.photo[-1]
        file_id = photo.file_id

        name = context.user_data.get("tmp_name")
        price = context.user_data.get("tmp_price")
        has_sizes = int(context.user_data.get("tmp_has_sizes", 0))
        sizes = context.user_data.get("tmp_sizes")

        if not name or price is None:
            await update.message.reply_text("⚠️ Noto‘g‘ri holat. /cancel qiling.", reply_markup=back_to_admin_inline())
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
            await update.message.reply_text("⚠️ Avval mahsulot tanlang.", reply_markup=back_to_admin_inline())
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
    await update.message.reply_text(f"📢 Broadcast natija: ✅{ok} / ❌{fail}", reply_markup=back_to_admin_inline())

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
    await update.message.reply_text(f"📢 Broadcast (rasm) natija: ✅{ok} / ❌{fail}", reply_markup=back_to_admin_inline())

# ----------------- Callbacks -----------------
async def cb_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    data = q.data or ""

    if data == "noop":
        return

    # ---------- ADMIN ----------
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
                [InlineKeyboardButton("⬅️ Admin panel", callback_data="A_HOME")],
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
                [InlineKeyboardButton("❌ O‘chirish", callback_data=f"A_DEL_DO|{pid}")],
                [InlineKeyboardButton("⬅️ Orqaga", callback_data="A_MANAGE")],
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
                context.user_data["state"] = A_EDIT_NAME
                await q.message.reply_text("✏️ Yangi nomini kiriting:", reply_markup=back_to_admin_inline())
                return

            if field == "price":
                context.user_data["state"] = A_EDIT_PRICE
                await q.message.reply_text("💰 Yangi narxni kiriting (faqat son):", reply_markup=back_to_admin_inline())
                return

            if field == "sizes":
                context.user_data["state"] = A_EDIT_SIZES
                await q.message.reply_text("📐 O‘lchamlarni kiriting (vergul bilan). O‘lchamsiz qilish uchun: -", reply_markup=back_to_admin_inline())
                return

            if field == "photo":
                context.user_data["state"] = A_EDIT_PHOTO
                await q.message.reply_text("🖼 Yangi rasm yuboring (photo):", reply_markup=back_to_admin_inline())
                return

        if data.startswith("A_DEL_DO|"):
            pid = int(data.split("|")[1])
            cur.execute("DELETE FROM products WHERE id=?", (pid,))
            cur.execute("DELETE FROM cart WHERE product_id=?", (pid,))
            conn.commit()
            clear_state(context)
            await q.message.reply_text("✅ Mahsulot o‘chirildi.", reply_markup=back_to_admin_inline())
            return

        if data == "A_BC":
            clear_state(context)
            context.user_data["state"] = A_BC_TEXT
            await q.message.reply_text("📢 Broadcast uchun matn kiriting (yoki rasm yuboring):", reply_markup=back_to_admin_inline())
            return

        if data == "A_STATS":
            await send_stats(q, context)
            return

        return

    # ---------- USER ----------
    if data == "U_CART":
        await show_cart_list(q, context, push=True)
        return

    if data == "U_BACK":
        await handle_user_back(q, context)
        return

    if data.startswith("U_PROD|"):
        # U_PROD|pid|ORIGIN
        parts = data.split("|", 2)
        pid = int(parts[1])
        origin = parts[2] if len(parts) > 2 else "CATALOG"
        await show_product_detail(q, context, pid, origin=origin, push=True)
        return

    if data.startswith("U_SIZE|"):
        # U_SIZE|pid|size|origin -> tanlagandan keyin son so'raymiz
        _, pid, size, origin = data.split("|", 3)
        pid = int(pid)
        context.user_data["pending_pid"] = pid
        context.user_data["pending_size"] = size
        context.user_data["pending_origin"] = origin
        context.user_data["state"] = U_WAIT_QTY

        # nav: qty oynasi ham view sifatida kiritamiz (back ishlashi uchun)
        nav_push(context, "QTY", {"pid": pid, "origin": origin})

        await q.message.reply_text(
            "🔢 Nechta dona kerak? Sonini yozib yuboring (masalan: 3)",
            reply_markup=back_btn()
        )
        return

    if data == "U_CLEAR_CART":
        cur.execute("DELETE FROM cart WHERE user_id=?", (uid,))
        conn.commit()
        await q.message.reply_text("🧹 Savatcha tozalandi.", reply_markup=back_btn())
        return

    if data == "U_CONFIRM":
        await confirm_order(uid, context, q.message)
        return

# ----------------- User BACK handler -----------------
async def handle_user_back(q, context: ContextTypes.DEFAULT_TYPE):
    # current view ni olib tashlaymiz, keyingisini ko'rsatamiz
    # agar bo'sh qolsa — main menu ga qaytamiz
    nav_pop(context)
    top = nav_top(context)

    if not top:
        # main menu
        await q.message.reply_text("🏠 Bosh menyu", reply_markup=main_menu_kb(is_admin(q.from_user.id)))
        return

    view = top["view"]
    data = top.get("data", {})

    if view == "CATALOG":
        # push=False, chunki back orqali qaytyapmiz
        await show_catalog_list(q.message, context, push=False)
        return

    if view == "CART":
        await show_cart_list(q.message, context, push=False)
        return

    if view == "PRODUCT":
        pid = int(data.get("pid", 0))
        origin = data.get("origin", "CATALOG")
        await show_product_detail(q.message, context, pid, origin=origin, push=False)
        return

    if view == "QTY":
        # qty view dan back bosilsa — origin ga qaytadi
        origin = data.get("origin", "CATALOG")
        clear_state(context)
        # QTY view ni olib tashlash uchun nav_pop allaqachon bo'ldi, endi top qayta render qiladi.
        # shu yerda hech narsa qilmaymiz, yana bir back bosmaslik uchun:
        # Nav stackda QTY dan oldingi view qolgan bo'ladi, uni render qilamiz:
        top2 = nav_top(context)
        if not top2:
            await q.message.reply_text("🏠 Bosh menyu", reply_markup=main_menu_kb(False))
            return
        # top2 ni render:
        if top2["view"] == "CATALOG":
            await show_catalog_list(q.message, context, push=False)
        elif top2["view"] == "CART":
            await show_cart_list(q.message, context, push=False)
        elif top2["view"] == "PRODUCT":
            pid2 = int(top2["data"].get("pid", 0))
            origin2 = top2["data"].get("origin", "CATALOG")
            await show_product_detail(q.message, context, pid2, origin=origin2, push=False)
        else:
            await q.message.reply_text("🏠 Bosh menyu", reply_markup=main_menu_kb(False))
        return

    # fallback
    await q.message.reply_text("🏠 Bosh menyu", reply_markup=main_menu_kb(False))

# ----------------- Main -----------------
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))

    app.add_handler(CallbackQueryHandler(cb_router))

    app.add_handler(MessageHandler(filters.CONTACT, contact_handler))
    app.add_handler(MessageHandler(filters.PHOTO, admin_photo_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_handler))

    log.info("Bot running. DB at %s | Admins: %s", DB_PATH, ADMIN_IDS)
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
