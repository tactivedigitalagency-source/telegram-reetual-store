"""SQLite transactions for the Telegram Dropship Store MVP.

All money is integer Rupiah. Timestamps are UTC ISO 8601 strings.
"""
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Jakarta")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def database_path():
    return os.getenv("DATABASE_PATH", "./data/store.sqlite3")


def connect():
    db = sqlite3.connect(database_path(), timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA busy_timeout = 15000")
    return db


def init():
    Path(database_path()).parent.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.execute("PRAGMA journal_mode = WAL")
        db.executescript((Path(__file__).parent / "schema.sql").read_text(encoding="utf-8"))


@contextmanager
def tx():
    db = connect()
    try:
        db.execute("BEGIN IMMEDIATE")
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def read_one(sql, params=()):
    with connect() as db:
        return db.execute(sql, params).fetchone()


def read_all(sql, params=()):
    with connect() as db:
        return db.execute(sql, params).fetchall()


def ensure_user(user_id):
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO users(telegram_id) VALUES (?)", (user_id,))


def confirm_age(user_id):
    ensure_user(user_id)
    with connect() as db:
        db.execute("UPDATE users SET age_confirmed_at=?,updated_at=? WHERE telegram_id=?", (now(), now(), user_id))


def is_adult_confirmed(user_id):
    r = read_one("SELECT age_confirmed_at FROM users WHERE telegram_id=?", (user_id,))
    return bool(r and r["age_confirmed_at"])


def add_product(sku, name, price, stock, description, category="Umum"):
    if not sku or not name or price <= 0 or stock < 0:
        raise ValueError("SKU/nama harus ada, harga >0 dan stok >=0")
    with connect() as db:
        cur = db.execute("INSERT INTO products(sku,name,price_rp,stock_qty,description,category) VALUES (?,?,?,?,?,?)", (sku, name, price, stock, description, category))
        return cur.lastrowid


def list_products():
    return read_all("SELECT *,stock_qty-reserved_qty AS available FROM products WHERE is_active=1 AND stock_qty>reserved_qty ORDER BY id DESC LIMIT 30")


def get_product(product_id):
    return read_one("SELECT *,stock_qty-reserved_qty AS available FROM products WHERE id=?", (product_id,))


def update_product_stock(product_id, qty, actor):
    if qty < 0:
        raise ValueError("Stok tidak boleh negatif")
    with tx() as db:
        row = db.execute("SELECT stock_qty,reserved_qty FROM products WHERE id=?", (product_id,)).fetchone()
        if not row:
            raise ValueError("Produk tidak ditemukan")
        if qty < row["reserved_qty"]:
            raise ValueError(f"Stok baru tidak boleh lebih kecil dari stok terkunci ({row['reserved_qty']})")
        db.execute("UPDATE products SET stock_qty=?,updated_at=? WHERE id=?", (qty, now(), product_id))
        if row["stock_qty"] != qty:
            db.execute("INSERT INTO inventory_events(product_id,event_type,qty,actor_user_id,note) VALUES (?,'ADJUST',?,?,?)", (product_id,abs(qty-row["stock_qty"]),actor,f"Stok {row['stock_qty']} -> {qty}"))


def update_price(product_id, price):
    if price <= 0:
        raise ValueError("Harga harus positif")
    with connect() as db:
        if db.execute("UPDATE products SET price_rp=?,updated_at=? WHERE id=?", (price, now(), product_id)).rowcount != 1:
            raise ValueError("Produk tidak ditemukan")


def set_photo(product_id, file_id):
    with connect() as db:
        if db.execute("UPDATE products SET photo_file_id=? WHERE id=?", (file_id, product_id)).rowcount != 1:
            raise ValueError("Produk tidak ditemukan")


def toggle_product(product_id):
    with connect() as db:
        if db.execute("UPDATE products SET is_active=1-is_active WHERE id=?", (product_id,)).rowcount != 1:
            raise ValueError("Produk tidak ditemukan")


def cart_add(user_id, product_id, delta):
    with tx() as db:
        product = db.execute("SELECT is_active,stock_qty-reserved_qty AS available FROM products WHERE id=?", (product_id,)).fetchone()
        if not product or not product["is_active"]:
            raise ValueError("Produk tidak tersedia")
        old = db.execute("SELECT qty FROM cart_items WHERE user_id=? AND product_id=?", (user_id,product_id)).fetchone()
        qty = (old["qty"] if old else 0) + delta
        if qty > product["available"]:
            raise ValueError("Stok tidak mencukupi")
        if qty <= 0:
            db.execute("DELETE FROM cart_items WHERE user_id=? AND product_id=?", (user_id,product_id))
        else:
            db.execute("INSERT INTO cart_items(user_id,product_id,qty) VALUES(?,?,?) ON CONFLICT(user_id,product_id) DO UPDATE SET qty=excluded.qty", (user_id,product_id,qty))
        return max(qty,0)


def cart_clear(user_id):
    with connect() as db:
        db.execute("DELETE FROM cart_items WHERE user_id=?", (user_id,))


def get_cart(user_id):
    return read_all("SELECT c.product_id,c.qty,p.name,p.price_rp,p.is_active,p.stock_qty-p.reserved_qty AS available FROM cart_items c JOIN products p ON p.id=c.product_id WHERE c.user_id=? ORDER BY c.product_id", (user_id,))


def queue_notice(db, user_id, message, photo=None):
    data = {"text": message}
    if photo:
        data["photo"] = photo
    db.execute("INSERT INTO notification_outbox(destination_user_id,type,payload_json) VALUES (?,?,?)", (user_id, "PHOTO" if photo else "TEXT", json.dumps(data,ensure_ascii=False)))


def create_order(user_id, address, key, admin_id, deadline_minutes=120):
    """Atomically reserve every item; duplicate request returns the same invoice."""
    if not key or len(key) > 100:
        raise ValueError("Permintaan invoice tidak valid")
    for f in ("name","phone","address","city","province"):
        if not (address.get(f) or "").strip():
            raise ValueError(f"Data {f} wajib diisi")
    if len(address["phone"]) < 8 or len(address["phone"]) > 20:
        raise ValueError("Nomor HP tidak valid")
    if deadline_minutes < 10:
        raise ValueError("Batas pembayaran terlalu singkat")
    with tx() as db:
        old = db.execute("SELECT * FROM orders WHERE idempotency_key=? AND user_id=?", (key,user_id)).fetchone()
        if old:
            return old
        cart = db.execute("SELECT c.product_id,c.qty,p.name,p.price_rp FROM cart_items c JOIN products p ON c.product_id=p.id WHERE c.user_id=? ORDER BY c.product_id", (user_id,)).fetchall()
        if not cart:
            raise ValueError("Keranjang kosong")
        subtotal = 0
        for r in cart:
            updated = db.execute("UPDATE products SET reserved_qty=reserved_qty+? WHERE id=? AND is_active=1 AND stock_qty-reserved_qty>=?", (r["qty"],r["product_id"],r["qty"])).rowcount
            if updated != 1:
                raise ValueError(f"Stok produk {r['name']} tidak cukup. Keranjang tidak berubah.")
            subtotal += r["price_rp"]*r["qty"]
        deadline = (datetime.now(timezone.utc)+timedelta(minutes=deadline_minutes)).isoformat(timespec="seconds")
        cur = db.execute("""INSERT INTO orders(order_no,user_id,idempotency_key,recipient_name,recipient_phone,address_line,city,province,subdistrict,postal_code,subtotal_rp,shipping_fee_rp,total_rp,status,payment_deadline)
         VALUES (?,?,?,?,?,?,?,?,?,?,?,0,?,'AWAITING_PAYMENT',?)""", (
         f"TMP-{uuid.uuid4().hex}",user_id,key,address["name"],address["phone"],address["address"],address["city"],address["province"],address.get("subdistrict",""),address.get("postal",""),subtotal,subtotal,deadline))
        order_id = cur.lastrowid
        order_no = f"TDS-{datetime.now(TZ):%y%m%d}-{order_id:06d}"
        db.execute("UPDATE orders SET order_no=? WHERE id=?", (order_no,order_id))
        for r in cart:
            db.execute("INSERT INTO order_items(order_id,product_id,product_name,qty,unit_price_rp) VALUES (?,?,?,?,?)", (order_id,r["product_id"],r["name"],r["qty"],r["price_rp"]))
            db.execute("INSERT INTO inventory_events(product_id,order_id,event_type,qty,actor_user_id) VALUES (?,?,'RESERVE',?,?)", (r["product_id"],order_id,r["qty"],user_id))
        db.execute("DELETE FROM cart_items WHERE user_id=?", (user_id,))
        for r in cart:
            remain = db.execute("SELECT stock_qty-reserved_qty AS remaining FROM products WHERE id=?",(r["product_id"],)).fetchone()["remaining"]
            if remain <= 3:
                queue_notice(db,admin_id,f"🔔 Stok lokal menipis: {r['name']} (#{r['product_id']}), tersisa {remain} tersedia.")
        db.execute("INSERT INTO order_events(order_id,to_status,actor_user_id,reason) VALUES (?,'AWAITING_PAYMENT',?,'Invoice otomatis')", (order_id,user_id))
        queue_notice(db,admin_id,f"🆕 Invoice baru {order_no}: Rp{subtotal:,} (belum bayar)")
        return db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()


def get_order(order_id, user_id=None):
    if user_id is None:
        return read_one("SELECT * FROM orders WHERE id=?", (order_id,))
    return read_one("SELECT * FROM orders WHERE id=? AND user_id=?", (order_id,user_id))


def get_order_items(order_id):
    return read_all("SELECT * FROM order_items WHERE order_id=? ORDER BY id", (order_id,))


def user_orders(user_id):
    return read_all("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10", (user_id,))


def active_unpaid_orders(user_id):
    return read_all("SELECT * FROM orders WHERE user_id=? AND status='AWAITING_PAYMENT' ORDER BY id DESC LIMIT 10", (user_id,))


def _event(db, order_id, before, after, actor, reason):
    db.execute("INSERT INTO order_events(order_id,from_status,to_status,actor_user_id,reason) VALUES (?,?,?,?,?)", (order_id,before,after,actor,reason))


def _release(db, order_id, actor):
    for r in db.execute("SELECT product_id,qty FROM order_items WHERE order_id=?",(order_id,)).fetchall():
        db.execute("UPDATE products SET reserved_qty=reserved_qty-? WHERE id=? AND reserved_qty>=?", (r["qty"],r["product_id"],r["qty"]))
        db.execute("INSERT INTO inventory_events(product_id,order_id,event_type,qty,actor_user_id) VALUES (?,?,'RELEASE',?,?)",(r["product_id"],order_id,r["qty"],actor))


def cancel_unpaid(order_id, user_id):
    with tx() as db:
        o = db.execute("SELECT * FROM orders WHERE id=? AND user_id=?",(order_id,user_id)).fetchone()
        if not o or o["status"]!="AWAITING_PAYMENT":
            raise ValueError("Hanya invoice yang belum dibayar dan belum diperiksa yang bisa dibatalkan")
        _release(db,order_id,user_id)
        db.execute("UPDATE orders SET status='CANCELLED',updated_at=? WHERE id=?",(now(),order_id))
        _event(db,order_id,"AWAITING_PAYMENT","CANCELLED",user_id,"Pembeli membatalkan")


def expire_orders():
    count=0
    with tx() as db:
        for o in db.execute("SELECT id,user_id,order_no FROM orders WHERE status='AWAITING_PAYMENT' AND payment_deadline<=?",(now(),)).fetchall():
            _release(db,o["id"],None)
            db.execute("UPDATE orders SET status='EXPIRED',updated_at=? WHERE id=?",(now(),o["id"]))
            _event(db,o["id"],"AWAITING_PAYMENT","EXPIRED",None,"Batas bayar habis")
            queue_notice(db,o["user_id"],f"⌛ Invoice {o['order_no']} kedaluwarsa. Stok reservasi telah dilepas.")
            count+=1
    return count


def submit_proof(order_id, user_id, photo_file, admin_id):
    with tx() as db:
        o = db.execute("SELECT * FROM orders WHERE id=? AND user_id=?",(order_id,user_id)).fetchone()
        if not o or o["status"] not in ("AWAITING_PAYMENT","PAYMENT_REVIEW"):
            raise ValueError("Pesanan ini tidak bisa menerima bukti. Bila sudah transfer setelah kedaluwarsa, hubungi admin.")
        if o["status"]=="AWAITING_PAYMENT" and o["payment_deadline"] <= now():
            raise ValueError("Tenggat sudah lewat. Hubungi admin bila dana sudah ditransfer.")
        db.execute("INSERT INTO payment_proofs(order_id,uploaded_by,telegram_file_id) VALUES (?,?,?)",(order_id,user_id,photo_file))
        if o["status"]!="PAYMENT_REVIEW":
            db.execute("UPDATE orders SET status='PAYMENT_REVIEW',updated_at=? WHERE id=?",(now(),order_id))
            _event(db,order_id,o["status"],"PAYMENT_REVIEW",user_id,"Bukti dibagikan")
        queue_notice(db,admin_id,f"🔎 Bukti pembayaran {o['order_no']} (ID {order_id}) Rp{o['total_rp']:,}. Periksa mutasi rekening dahulu!\nGunakan /bayar {order_id} setelah dana benar-benar diterima atau /tolak {order_id}.",photo_file)


def verify_payment(order_id, admin_id):
    with tx() as db:
        o=db.execute("SELECT * FROM orders WHERE id=?",(order_id,)).fetchone()
        if not o or o["status"]!="PAYMENT_REVIEW":
            raise ValueError("Pesanan bukan dalam status review pembayaran")
        p=db.execute("SELECT id FROM payment_proofs WHERE order_id=? AND review_status='PENDING' ORDER BY id DESC LIMIT 1",(order_id,)).fetchone()
        if not p:
            raise ValueError("Bukti pembayaran belum ada")
        for item in db.execute("SELECT product_id,qty FROM order_items WHERE order_id=?",(order_id,)).fetchall():
            affected = db.execute("UPDATE products SET stock_qty=stock_qty-?,reserved_qty=reserved_qty-? WHERE id=? AND stock_qty>=? AND reserved_qty>=?",(item["qty"],item["qty"],item["product_id"],item["qty"],item["qty"])).rowcount
            if not affected:
                raise ValueError("Stok lokal tidak konsisten; tinjau manual")
            db.execute("INSERT INTO inventory_events(product_id,order_id,event_type,qty,actor_user_id) VALUES (?,?,'SALE',?,?)",(item["product_id"],order_id,item["qty"],admin_id))
        db.execute("UPDATE payment_proofs SET review_status='ACCEPTED',reviewed_by=?,reviewed_at=? WHERE id=?",(admin_id,now(),p["id"]))
        db.execute("UPDATE orders SET status='PAID',paid_at=?,payment_verified_at=?,updated_at=? WHERE id=?",(now(),now(),now(),order_id))
        _event(db,order_id,"PAYMENT_REVIEW","PAID",admin_id,"Mutasi dana diverifikasi manual")
        queue_notice(db,o["user_id"],f"✅ Pembayaran {o['order_no']} telah diverifikasi. Pesanan akan diproses.")


def reject_payment(order_id, admin_id):
    with tx() as db:
        o=db.execute("SELECT * FROM orders WHERE id=?",(order_id,)).fetchone()
        if not o or o["status"]!="PAYMENT_REVIEW":
            raise ValueError("Status tidak mengizinkan penolakan bukti")
        db.execute("UPDATE payment_proofs SET review_status='REJECTED',reviewed_by=?,reviewed_at=? WHERE order_id=? AND review_status='PENDING'",(admin_id,now(),order_id))
        # Return to awaiting payment with a NEW grace window; expiry will safely release stock later.
        new_deadline=(datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat(timespec="seconds")
        db.execute("UPDATE orders SET status='AWAITING_PAYMENT',payment_deadline=?,updated_at=? WHERE id=?",(new_deadline,now(),order_id))
        _event(db,order_id,"PAYMENT_REVIEW","AWAITING_PAYMENT",admin_id,"Bukti ditolak; 30 menit tambahan")
        queue_notice(db,o["user_id"],f"⚠️ Bukti pembayaran {o['order_no']} belum dapat diverifikasi. Periksa transfer dan hubungi admin bila dana sudah terkirim.")


def mark_processing(order_id, admin_id):
    return _simple_transition(order_id,admin_id,{"PAID"},"PROCESSING","Pesanan diteruskan ke supplier")


def mark_completed(order_id,admin_id):
    return _simple_transition(order_id,admin_id,{"SHIPPED"},"COMPLETED","Penerimaan telah diperiksa admin")


def mark_issue(order_id,admin_id,reason):
    if not reason.strip(): raise ValueError("Alasan wajib diisi")
    return _simple_transition(order_id,admin_id,{"PAID","PROCESSING","SHIPPED","COMPLETED"},"ISSUE",reason)


def _simple_transition(order_id,admin_id,allowed,to_status,reason):
    with tx() as db:
        o=db.execute("SELECT * FROM orders WHERE id=?",(order_id,)).fetchone()
        if not o or o["status"] not in allowed:
            raise ValueError("Transisi status tidak diizinkan")
        db.execute("UPDATE orders SET status=?,updated_at=?,completed_at=CASE WHEN ?='COMPLETED' THEN ? ELSE completed_at END WHERE id=?",(to_status,now(),to_status,now(),order_id))
        _event(db,order_id,o["status"],to_status,admin_id,reason)
        queue_notice(db,o["user_id"],f"📦 Pesanan {o['order_no']} sekarang: {to_status}. {reason}")


def mark_shipped(order_id,admin_id,courier,tracking):
    if not courier or not tracking: raise ValueError("Kurir dan resi wajib")
    with tx() as db:
        o=db.execute("SELECT * FROM orders WHERE id=?",(order_id,)).fetchone()
        if not o or o["status"] not in ("PAID","PROCESSING","SHIPPED"):
            raise ValueError("Status tidak bisa diubah menjadi dikirim")
        db.execute("UPDATE orders SET status='SHIPPED',courier=?,tracking_no=?,shipped_at=?,updated_at=? WHERE id=?",(courier,tracking,now(),now(),order_id))
        _event(db,order_id,o["status"],"SHIPPED",admin_id,"Resi diinput/dikoreksi")
        queue_notice(db,o["user_id"],f"🚚 Pesanan {o['order_no']} dikirim. Kurir: {courier}, resi: {tracking}")


def request_refund(order_id,admin_id,amount,reason):
    if amount <= 0 or not reason.strip(): raise ValueError("Nominal/alasan tidak valid")
    with tx() as db:
        o=db.execute("SELECT * FROM orders WHERE id=?",(order_id,)).fetchone()
        if not o or o["status"] not in ("PAID","PROCESSING","ISSUE","SHIPPED"):
            raise ValueError("Pesanan tidak memenuhi syarat refund manual")
        if amount != o["total_rp"]: raise ValueError("MVP hanya mendukung refund penuh; tangani refund parsial di luar alur otomatis")
        if db.execute("SELECT 1 FROM refunds WHERE order_id=? AND status IN ('PENDING','SENT')",(order_id,)).fetchone():
            raise ValueError("Refund sudah tercatat")
        db.execute("INSERT INTO refunds(order_id,amount_rp,reason,status,processed_by) VALUES (?,?,?,'PENDING',?)",(order_id,amount,reason,admin_id))
        db.execute("UPDATE orders SET status='REFUND_PENDING',updated_at=? WHERE id=?",(now(),order_id))
        _event(db,order_id,o["status"],"REFUND_PENDING",admin_id,reason)
        queue_notice(db,o["user_id"],f"↩️ Pengembalian dana {o['order_no']} Rp{amount:,} sedang diproses manual oleh admin.")


def complete_refund(order_id,admin_id,reference):
    if len(reference.strip()) < 3: raise ValueError("Referensi transfer refund wajib diisi")
    with tx() as db:
        o=db.execute("SELECT * FROM orders WHERE id=?",(order_id,)).fetchone()
        r=db.execute("SELECT * FROM refunds WHERE order_id=? AND status='PENDING' ORDER BY id DESC LIMIT 1",(order_id,)).fetchone()
        if not o or o["status"]!="REFUND_PENDING" or not r:
            raise ValueError("Tidak ada refund tertunda")
        db.execute("UPDATE refunds SET status='SENT',transfer_reference=?,completed_at=?,processed_by=? WHERE id=?",(reference,now(),admin_id,r["id"]))
        db.execute("UPDATE orders SET status='REFUNDED',updated_at=? WHERE id=?",(now(),order_id))
        _event(db,order_id,"REFUND_PENDING","REFUNDED",admin_id,f"Transfer manual ref: {reference}")
        queue_notice(db,o["user_id"],f"✅ Pengembalian dana {o['order_no']} sebesar Rp{r['amount_rp']:,} telah dikirim.")


def create_ticket(user_id,text,admin_id):
    if not text.strip(): raise ValueError("Pertanyaan kosong")
    with tx() as db:
        c=db.execute("INSERT INTO support_tickets(user_id,topic) VALUES (?,?)",(user_id,text[:1000]))
        queue_notice(db,admin_id,f"💬 Tiket #{c.lastrowid} dari Telegram ID {user_id}:\n{text[:1000]}")
        return c.lastrowid


def pending_notifications():
    return read_all("SELECT * FROM notification_outbox WHERE status IN ('PENDING','FAILED') AND next_attempt_at<=? AND attempts<5 ORDER BY id LIMIT 20",(now(),))


def notification_result(note_id,success):
    with connect() as db:
        if success:
            db.execute("UPDATE notification_outbox SET status='SENT',sent_at=?,attempts=attempts+1 WHERE id=?",(now(),note_id))
        else:
            nexttry=(datetime.now(timezone.utc)+timedelta(minutes=2)).isoformat(timespec="seconds")
            db.execute("UPDATE notification_outbox SET status='FAILED',attempts=attempts+1,next_attempt_at=? WHERE id=?",(nexttry,note_id))


def report_data():
    day=datetime.now(TZ).date().isoformat()
    # Rolling 24h; SQLite datetime() handles both default SQL and ISO-8601 timestamps.
    since=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat(timespec="seconds")
    with connect() as db:
        new=db.execute("SELECT count(*) as n FROM orders WHERE datetime(created_at)>=datetime(?)",(since,)).fetchone()["n"]
        paid=db.execute("SELECT count(*) as n,COALESCE(sum(total_rp),0) as s FROM orders WHERE datetime(paid_at)>=datetime(?)",(since,)).fetchone()
        overdue=db.execute("SELECT count(*) as n FROM orders WHERE status IN ('PAYMENT_REVIEW','PAID','ISSUE','REFUND_PENDING')").fetchone()["n"]
        low=db.execute("SELECT count(*) as n FROM products WHERE is_active=1 AND stock_qty-reserved_qty<=3").fetchone()["n"]
    return f"📊 Laporan 24 jam terakhir ({day} WIB)\nInvoice: {new}\nBayar terkonfirmasi: {paid['n']}\nOmzet dibayar: Rp{paid['s']:,}\nPerlu tindakan: {overdue}\nProduk stok tipis: {low}"


def claim_daily_report(local_day):
    with connect() as db:
        cur=db.execute("INSERT OR IGNORE INTO report_runs(local_report_date) VALUES (?)",(local_day,))
        return cur.rowcount == 1


def enqueue_daily_report(local_day, admin_id):
    """Create the report marker and outbox message atomically."""
    report = report_data()
    with tx() as db:
        cur=db.execute("INSERT OR IGNORE INTO report_runs(local_report_date) VALUES (?)",(local_day,))
        if cur.rowcount != 1:
            return False
        queue_notice(db,admin_id,report)
        return True