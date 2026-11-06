"""Telegram Dropship Store MVP v1.1 — polling, one bot instance."""
import json
import logging
import os
import re
import sqlite3
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as K, Update
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
    ConversationHandler, MessageHandler, filters,
)

import db

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log=logging.getLogger(__name__)
ADMIN=int(os.environ.get("ADMIN_USER_ID","0"))
PAYMENT_INSTRUCTIONS=os.environ.get("PAYMENT_INSTRUCTIONS","").replace("\\n","\n")
REGIONS=[x.strip().casefold() for x in os.environ.get("SUPPORTED_PROVINCES","").split(",") if x.strip()]
TIMEOUT=int(os.environ.get("PAYMENT_TIMEOUT_MINUTES","120"))
NAME,PHONE,ADDRESS,KECAMATAN,CITY,PROVINCE,POSTAL,REVIEW=range(8)


def rupiah(value):
    return f"Rp{value:,}".replace(",",".")


def admin_only(update):
    return bool(update.effective_user and update.effective_user.id==ADMIN and update.effective_chat and update.effective_chat.type=="private")


def button(label,callback):
    return B(label,callback_data=callback)


def menu():
    return K([[button("🛍 Katalog","catalog"),button("🛒 Keranjang","cart")],
              [button("📦 Pesanan Saya","orders"),button("💬 Bantuan","support")]])


def age_menu():
    return K([[button("✅ Saya 18+","age:yes")],[button("🚫 Keluar","age:no")]])


def invoice_text(o):
    items=db.get_order_items(o["id"])
    lines=[f"🧾 INVOICE {o['order_no']}",f"Status: {o['status']}",""]
    lines += [f"• {i['product_name']} x{i['qty']} — {rupiah(i['qty']*i['unit_price_rp'])}" for i in items]
    lines += ["",f"Subtotal: {rupiah(o['subtotal_rp'])}","Ongkir pelanggan: GRATIS (Rp0)",f"TOTAL: {rupiah(o['total_rp'])}",f"Penerima: {o['recipient_name']}",f"Kota: {o['city']}, {o['province']}"]
    if o["status"] in ("AWAITING_PAYMENT","PAYMENT_REVIEW"):
        deadline=datetime.fromisoformat(o["payment_deadline"]).astimezone(ZoneInfo("Asia/Jakarta"))
        lines.append("Batas bayar: "+deadline.strftime("%d-%m-%Y %H:%M WIB"))
        if PAYMENT_INSTRUCTIONS:
            lines.append("\nCara pembayaran:\n"+PAYMENT_INSTRUCTIONS)
        else:
            lines.append("\n⚠️ Rekening/QRIS belum diatur. Jangan transfer. Hubungi admin.")
    if o["tracking_no"]:
        lines += [f"Kurir: {o['courier']}",f"Resi: {o['tracking_no']}"]
    return "\n".join(lines)


async def respond(update,text,reply_markup=None):
    if update.callback_query:
        await update.callback_query.message.reply_text(text,reply_markup=reply_markup)
    else:
        await update.effective_message.reply_text(text,reply_markup=reply_markup)


async def start(update:Update, context:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if update.effective_chat.type != "private":
        await respond(update,"Buka bot melalui chat pribadi Telegram untuk menggunakan toko ini.")
        return
    db.ensure_user(uid)
    if admin_only(update):
        await respond(update,"Halo Admin. Ketik /admin untuk antrean pesanan atau /panduan untuk semua perintah.")
    if db.is_adult_confirmed(uid):
        await respond(update,"Selamat datang! Pilih menu:",menu())
    else:
        await respond(update,"Toko ini khusus pelanggan berusia minimal 18 tahun. Dengan menekan tombol, kamu menyatakan sudah berusia 18+ dan setuju data pengiriman digunakan hanya untuk pemrosesan pesanan.",age_menu())


async def myid(update:Update,context):
    await respond(update,f"Telegram user ID kamu: {update.effective_user.id}")


async def cancel_text(update,context):
    context.user_data.pop("proof_order",None)
    context.user_data.pop("support_mode",None)
    await respond(update,"Aksi dibatalkan. Gunakan /start untuk kembali.")
    return ConversationHandler.END


async def checkout_entry(update:Update,context):
    q=update.callback_query
    await q.answer()
    if update.effective_chat.type != "private":
        await respond(update,"Gunakan chat pribadi dengan bot.")
        return ConversationHandler.END
    if not db.is_adult_confirmed(q.from_user.id):
        await respond(update,"Konfirmasi usia lewat /start lebih dulu.")
        return ConversationHandler.END
    if not PAYMENT_INSTRUCTIONS:
        await respond(update,"Pembayaran belum dikonfigurasi oleh admin. Checkout dinonaktifkan sementara.")
        return ConversationHandler.END
    if not REGIONS:
        await respond(update,"Wilayah layanan belum diatur admin. Checkout belum tersedia.")
        return ConversationHandler.END
    if not db.get_cart(q.from_user.id):
        await respond(update,"Keranjang masih kosong.")
        return ConversationHandler.END
    context.user_data["checkout"]={"key":uuid.uuid4().hex}
    await respond(update,"Checkout (gratis ongkir). Masukkan NAMA penerima atau /cancel untuk membatalkan:")
    return NAME


async def checkout_name(update,context):
    val=update.message.text.strip()
    if len(val)<2 or len(val)>100:
        await respond(update,"Nama 2-100 karakter. Coba lagi:")
        return NAME
    context.user_data["checkout"]["name"]=val
    await respond(update,"Nomor HP penerima (contoh 081234567890):")
    return PHONE


async def checkout_phone(update,context):
    val=update.message.text.strip()
    if not re.fullmatch(r"\+?[0-9\s-]{8,20}",val):
        await respond(update,"Nomor HP tidak valid. Isi angka dan boleh menggunakan + di awal:")
        return PHONE
    context.user_data["checkout"]["phone"]=val
    await respond(update,"Alamat jalan / dusun / RT / RW / nomor rumah (selengkap mungkin):")
    return ADDRESS


async def checkout_address(update,context):
    val=update.message.text.strip()
    if len(val)<10 or len(val)>500:
        await respond(update,"Alamat minimal 10 karakter (maksimal 500). Coba lagi:")
        return ADDRESS
    context.user_data["checkout"]["address"]=val
    await respond(update,"Kecamatan:")
    return KECAMATAN


async def checkout_kecamatan(update,context):
    val=update.message.text.strip()
    if len(val)<2 or len(val)>100:
        await respond(update,"Kecamatan tidak valid, ulangi:")
        return KECAMATAN
    context.user_data["checkout"]["subdistrict"]=val
    await respond(update,"Kabupaten / Kota:")
    return CITY


async def checkout_city(update,context):
    val=update.message.text.strip()
    if len(val)<2 or len(val)>100:
        await respond(update,"Kota tidak valid, ulangi:")
        return CITY
    context.user_data["checkout"]["city"]=val
    await respond(update,"Provinsi:")
    return PROVINCE


async def checkout_province(update,context):
    val=update.message.text.strip()
    if val.casefold() not in REGIONS and "*" not in REGIONS:
        await respond(update,"Maaf, provinsi tersebut belum termasuk cakupan pengiriman. Wilayah yang dilayani: "+", ".join(REGIONS)+". Gunakan /cancel untuk keluar atau masukkan provinsi yang benar:")
        return PROVINCE
    context.user_data["checkout"]["province"]=val
    await respond(update,"Kode pos (5 digit):")
    return POSTAL


async def checkout_postal(update,context):
    val=update.message.text.strip()
    if not re.fullmatch(r"[0-9]{5}",val):
        await respond(update,"Kode pos 5 digit. Coba lagi:")
        return POSTAL
    d=context.user_data["checkout"]
    d["postal"]=val
    cart=db.get_cart(update.effective_user.id)
    total=sum(x["qty"]*x["price_rp"] for x in cart)
    lines=[f"{x['name']} x{x['qty']} = {rupiah(x['qty']*x['price_rp'])}" for x in cart]
    await respond(update,"Periksa ringkasan sebelum membuat invoice:\n"+"\n".join(lines)+f"\nOngkir: GRATIS\nTotal sementara: {rupiah(total)}\nNama: {d['name']}\nHP: {d['phone']}\nAlamat: {d['address']}, {d['subdistrict']}, {d['city']}, {d['province']} {d['postal']}\n\nHarga dan stok akan diperiksa ulang saat invoice dibuat.",K([[button("🧾 Buat Invoice","invoice:create"),button("❌ Batal","invoice:cancel")]]))
    return REVIEW


async def checkout_confirm(update,context):
    q=update.callback_query
    await q.answer()
    if q.data=="invoice:cancel":
        context.user_data.pop("checkout",None)
        await respond(update,"Checkout dibatalkan; isi keranjang tetap tersimpan.",menu())
        return ConversationHandler.END
    data=context.user_data.get("checkout")
    if not data:
        await respond(update,"Sesi checkout hilang. Buka keranjang lagi.")
        return ConversationHandler.END
    try:
        o=db.create_order(q.from_user.id,data,data["key"],ADMIN,TIMEOUT)
        await respond(update,invoice_text(o),K([[button("📸 Kirim bukti bayar",f"proof:{o['id']}"),button("📦 Pesanan",f"order:{o['id']}")]]))
        context.user_data.pop("checkout",None)
        return ConversationHandler.END
    except ValueError as err:
        await respond(update,f"Invoice belum dibuat: {err}. Pilih kembali atau /cancel.")
        return REVIEW


async def on_callback(update:Update,context):
    q=update.callback_query
    await q.answer()
    uid=q.from_user.id
    if update.effective_chat.type != "private":
        await respond(update,"Gunakan chat pribadi dengan bot.")
        return
    data=q.data or ""
    if data=="age:no":
        await respond(update,"Akses ditutup. Katalog hanya untuk usia 18+.")
        return
    if data=="age:yes":
        db.confirm_age(uid)
        await respond(update,"Konfirmasi diterima. Pilih menu:",menu())
        return
    if data.startswith("adm:"):
        if not admin_only(update):
            await respond(update,"Akses admin ditolak.")
            return
        try:
            _,action,idstr=data.split(":")
            order_id=int(idstr)
            if action=="verify":
                await respond(update,f"KONFIRMASI: apakah dana untuk pesanan #{order_id} BENAR-BENAR masuk ke rekening? Jangan hanya melihat foto bukti.",K([[button("✅ Dana sudah masuk",f"adm:verify2:{order_id}")]]))
            elif action=="verify2":
                db.verify_payment(order_id,uid)
                await respond(update,f"Pembayaran #{order_id} telah diverifikasi.")
            elif action=="reject":
                db.reject_payment(order_id,uid)
                await respond(update,f"Bukti #{order_id} ditolak. Tenggat baru +30 menit.")
            elif action=="processing":
                db.mark_processing(order_id,uid)
                await respond(update,f"Pesanan #{order_id} diproses.")
            else:
                await respond(update,"Tombol admin tidak diketahui.")
        except (ValueError,IndexError) as e:
            await respond(update,f"Tidak berhasil: {e}")
        return
    if not db.is_adult_confirmed(uid):
        await respond(update,"Konfirmasi usia terlebih dahulu melalui /start.",age_menu())
        return
    try:
        if data=="menu":
            await respond(update,"Menu utama:",menu())
        elif data=="catalog":
            rows=db.list_products()
            if not rows:
                await respond(update,"Katalog belum tersedia. Silakan cek lagi nanti.",menu())
            else:
                keys=[[button(f"#{r['id']} {r['name'][:29]} — {rupiah(r['price_rp'])}",f"p:{r['id']}")] for r in rows]
                keys.append([button("🛒 Keranjang","cart")])
                await respond(update,"🛍 Katalog produk (stok lokal):",K(keys))
        elif data.startswith("p:"):
            product=db.get_product(int(data.split(":")[1]))
            if not product or not product["is_active"]:
                raise ValueError("Produk tidak tersedia")
            txt=f"{product['name']}\n{product['description']}\nHarga: {rupiah(product['price_rp'])}\nStok tersedia (lokal): {product['available']}"
            markup=K([[button("🛒 Tambah",f"a:{product['id']}")],[button("⬅️ Katalog","catalog")]])
            if product["photo_file_id"]:
                await q.message.reply_photo(product["photo_file_id"],caption=txt[:1024],reply_markup=markup)
            else:
                await respond(update,txt,markup)
        elif data.startswith(("a:","r:")):
            action, product_id=data.split(":")
            qty=db.cart_add(uid,int(product_id),1 if action=="a" else -1)
            await respond(update,f"Keranjang diperbarui. Jumlah produk ini: {qty}",K([[button("🛒 Buka keranjang","cart")],[button("⬅️ Katalog","catalog")]]))
        elif data=="cart":
            rows=db.get_cart(uid)
            if not rows:
                await respond(update,"Keranjang kosong.",K([[button("🛍 Katalog","catalog")]]))
            else:
                total=sum(r["price_rp"]*r["qty"] for r in rows)
                lines=[f"{r['name']} x{r['qty']} = {rupiah(r['price_rp']*r['qty'])}" for r in rows]
                buttons=[]
                for r in rows:
                    buttons.append([button("➖",f"r:{r['product_id']}"),button(f"{r['name'][:20]}",f"p:{r['product_id']}"),button("➕",f"a:{r['product_id']}")])
                buttons += [[button("🧹 Kosongkan","clear")],[button("✅ Checkout","checkout")]]
                await respond(update,"🛒 Keranjang:\n"+"\n".join(lines)+f"\n\nTotal: {rupiah(total)}\nOngkir: GRATIS",K(buttons))
        elif data=="clear":
            db.cart_clear(uid)
            await respond(update,"Keranjang dikosongkan.",menu())
        elif data=="orders":
            rows=db.user_orders(uid)
            if not rows:
                await respond(update,"Belum ada pesanan.",menu())
            else:
                await respond(update,"📦 10 pesanan terakhir:",K([[button(f"{r['order_no']} ({r['status']})",f"order:{r['id']}")] for r in rows]))
        elif data.startswith("order:"):
            oid=int(data.split(":")[1]); o=db.get_order(oid,uid)
            if not o: raise ValueError("Pesanan tidak ditemukan")
            buttons=[]
            if o["status"] in ("AWAITING_PAYMENT","PAYMENT_REVIEW"):
                buttons.append([button("📸 Kirim bukti",f"proof:{oid}")])
            if o["status"]=="AWAITING_PAYMENT":
                buttons.append([button("❌ Batalkan invoice",f"cancelorder:{oid}")])
            buttons.append([button("💬 Bantuan","support")])
            await respond(update,invoice_text(o),K(buttons))
        elif data.startswith("proof:"):
            oid=int(data.split(":")[1]); o=db.get_order(oid,uid)
            if not o or o["status"] not in ("AWAITING_PAYMENT","PAYMENT_REVIEW"):
                raise ValueError("Pesanan tidak dapat menerima bukti")
            context.user_data["proof_order"]=oid
            await respond(update,f"Kirim FOTO bukti transfer untuk {o['order_no']}. Admin hanya akan mengonfirmasi setelah dana masuk. Ketik /cancel untuk membatalkan unggahan.")
        elif data.startswith("cancelorder:"):
            db.cancel_unpaid(int(data.split(":")[1]),uid)
            await respond(update,"Invoice dibatalkan dan stok lokal dilepas.",menu())
        elif data=="support":
            context.user_data["support_mode"]=True
            await respond(update,"Tuliskan pertanyaan/masalah pesanan. Pesan akan diteruskan ke admin. /cancel untuk batal.")
        else:
            await respond(update,"Pilihan tidak dikenal. Gunakan /start.")
    except (ValueError,OverflowError) as e:
        await respond(update,f"Tidak berhasil: {e}")


async def on_photo(update:Update,context):
    uid=update.effective_user.id
    if admin_only(update) and (update.message.caption or "").startswith("/foto "):
        try:
            oid=int(update.message.caption.split()[1])
            db.set_photo(oid,update.message.photo[-1].file_id)
            await respond(update,f"Foto produk #{oid} disimpan.")
        except (ValueError,IndexError) as e:
            await respond(update,f"Format: foto dengan caption /foto ID. {e}")
        return
    oid=context.user_data.get("proof_order")
    if not oid or not db.is_adult_confirmed(uid):
        await respond(update,"Untuk mengirim bukti, pilih pesanan lalu tekan 'Kirim bukti bayar'.")
        return
    try:
        db.submit_proof(oid,uid,update.message.photo[-1].file_id,ADMIN)
        context.user_data.pop("proof_order",None)
        await respond(update,"Bukti diterima dan masuk antrean verifikasi admin. Status BELUM dibayar sampai dana dikonfirmasi.",menu())
    except ValueError as e:
        await respond(update,str(e))


async def on_text(update:Update,context):
    if context.user_data.get("support_mode") and db.is_adult_confirmed(update.effective_user.id):
        context.user_data.pop("support_mode",None)
        ticket=db.create_ticket(update.effective_user.id,update.message.text,ADMIN)
        await respond(update,f"Tiket bantuan #{ticket} dikirim ke admin.",menu())


async def admin_home(update,context):
    if not admin_only(update):
        await respond(update,"Tidak diizinkan.")
        return
    rows=db.read_all("SELECT id,order_no,status,total_rp FROM orders WHERE status IN ('PAYMENT_REVIEW','PAID','PROCESSING','ISSUE','REFUND_PENDING') ORDER BY id DESC LIMIT 12")
    lines=[f"#{r['id']} {r['order_no']} — {r['status']} — {rupiah(r['total_rp'])}" for r in rows]
    await respond(update,"🛠 ADMIN — antrean tindakan:\n"+("\n".join(lines) if lines else "Tidak ada antrean")+"\n\n/panduan untuk daftar perintah.")
    for r in rows:
        if r["status"]=="PAYMENT_REVIEW":
            await respond(update,f"Review pembayaran #{r['id']}: periksa mutasi di rekening, bukan hanya screenshot.",K([[button("✅ Cek konfirmasi",f"adm:verify:{r['id']}"),button("❌ Tolak bukti",f"adm:reject:{r['id']}")]]))
        elif r["status"]=="PAID":
            await respond(update,f"Pesanan #{r['id']} telah dibayar.",K([[button("📦 Diproses supplier",f"adm:processing:{r['id']}")]]))


async def panduan(update,context):
    if not admin_only(update): return
    await respond(update,"""🛠 PERINTAH ADMIN (semua via chat pribadi)
/tambah SKU | Nama | harga | stok | deskripsi | kategori
/stok ID JUMLAH
/harga ID RUPIAH
/nonaktif ID (toggle aktif/nonaktif)
Kirim foto produk dengan caption: /foto ID
/detail ID (alamat lengkap untuk fulfillment)
/bayar ID (HANYA bila mutasi dana telah masuk)
/tolak ID (tolak bukti + perpanjang deadline 30 menit)
/proses ID (pesan ke supplier)
/resi ID KURIR NOMOR_RESI
/selesai ID
/masalah ID ALASAN
/refund ID JUMLAH ALASAN
/refundselesai ID REFERENSI_TRANSFER (HANYA setelah benar-benar kirim dana)
/rekap
/myid""")


async def admin_action(update,context):
    if not admin_only(update):
        await respond(update,"Akses admin ditolak.")
        return
    command=update.message.text.split()[0].split('@')[0][1:]
    args=update.message.text.partition(" ")[2].strip()
    try:
        if command=="tambah":
            fields=[f.strip() for f in args.split("|")]
            if len(fields)<5: raise ValueError("Format: /tambah SKU | Nama | Harga | Stok | Deskripsi | Kategori(optional)")
            product_id=db.add_product(fields[0],fields[1],int(fields[2]),int(fields[3]),fields[4],fields[5] if len(fields)>5 else "Umum")
            await respond(update,f"Produk #{product_id} ditambahkan. Kirim foto dengan caption /foto {product_id}")
        elif command=="stok":
            idval,qty=map(int,args.split(maxsplit=1))
            db.update_product_stock(idval,qty,update.effective_user.id)
            await respond(update,f"Stok #{idval} menjadi {qty}")
        elif command=="harga":
            idval,price=map(int,args.split(maxsplit=1))
            db.update_price(idval,price)
            await respond(update,f"Harga #{idval} diperbarui menjadi {rupiah(price)}")
        elif command=="nonaktif":
            db.toggle_product(int(args))
            await respond(update,"Status produk diubah.")
        elif command=="detail":
            o=db.get_order(int(args))
            if not o: raise ValueError("Pesanan tidak ditemukan")
            await respond(update,invoice_text(o)+f"\n\nALAMAT FULFILLMENT (RAHASIA):\n{ o['recipient_name'] }\n{o['recipient_phone']}\n{o['address_line']}, {o['subdistrict']}, {o['city']}, {o['province']} {o['postal_code']}")
        elif command=="bayar":
            db.verify_payment(int(args),update.effective_user.id)
            await respond(update,"✅ Status PAID. Pastikan verifikasi dana sudah benar.")
        elif command=="tolak":
            db.reject_payment(int(args),update.effective_user.id)
            await respond(update,"Bukti ditolak; invoice aktif +30 menit.")
        elif command=="proses":
            db.mark_processing(int(args),update.effective_user.id)
            await respond(update,"Status PROCESSING.")
        elif command=="resi":
            oid,courier,tracking=args.split(maxsplit=2)
            db.mark_shipped(int(oid),update.effective_user.id,courier,tracking)
            await respond(update,"Resi disimpan dan pemberitahuan diantrikan.")
        elif command=="selesai":
            db.mark_completed(int(args),update.effective_user.id)
            await respond(update,"Pesanan selesai.")
        elif command=="masalah":
            oid,reason=args.split(maxsplit=1)
            db.mark_issue(int(oid),update.effective_user.id,reason)
            await respond(update,"Kasus ditandai bermasalah.")
        elif command=="refund":
            oid,amount,reason=args.split(maxsplit=2)
            db.request_refund(int(oid),update.effective_user.id,int(amount),reason)
            await respond(update,"Refund PENDING. Transfer sungguhan dilakukan secara manual, kemudian /refundselesai.")
        elif command=="refundselesai":
            oid,reference=args.split(maxsplit=1)
            db.complete_refund(int(oid),update.effective_user.id,reference)
            await respond(update,"Refund dicatat sebagai SENT dan pembeli diberi tahu.")
        elif command=="rekap":
            await respond(update,db.report_data())
    except (ValueError,sqlite3.Error) as e:
        await respond(update,f"Gagal: {e}. Cek /panduan.")
    except Exception as e:
        log.exception("Unexpected admin error")
        await respond(update,"Terjadi gangguan. Cek log server.")


async def expire_job(context):
    try:
        db.expire_orders()
    except Exception:
        log.exception("Expiry job error")


async def notification_job(context):
    for note in db.pending_notifications():
        try:
            payload=json.loads(note["payload_json"])
            if payload.get("photo"):
                await context.bot.send_photo(chat_id=note["destination_user_id"],photo=payload["photo"],caption=payload["text"][:1024])
            else:
                await context.bot.send_message(chat_id=note["destination_user_id"],text=payload["text"][:4000])
            db.notification_result(note["id"],True)
        except Exception:
            db.notification_result(note["id"],False)
            log.warning("Notification %s delivery failed",note["id"])


async def daily_report(context):
    local=datetime.now(ZoneInfo("Asia/Jakarta"))
    if local.hour>=21:
        db.enqueue_daily_report(local.date().isoformat(),ADMIN)


async def report_backlog(context):
    try:
        rows=db.read_all("SELECT count(*) n FROM orders WHERE status IN ('PAYMENT_REVIEW','PAID','ISSUE','REFUND_PENDING')")
        if rows[0]["n"]:
            log.info("Orders needing action: %s",rows[0]["n"])
    except Exception:
        log.exception("Backlog logging error")


async def error_handler(update,context):
    log.error("Handler failed (%s)",type(context.error).__name__)
    if update and update.effective_message:
        try: await update.effective_message.reply_text("Ada masalah sementara. Silakan ulangi /start atau hubungi admin.")
        except Exception: pass


def run():
    global ADMIN, PAYMENT_INSTRUCTIONS, REGIONS, TIMEOUT
    token=os.environ.get("BOT_TOKEN","")
    if not token:
        raise SystemExit("BOT_TOKEN belum disetel. Salin .env.example ke .env dan isi token dari BotFather.")
    if ADMIN<=0:
        raise SystemExit("ADMIN_USER_ID belum disetel (Telegram numeric user ID).")
    if TIMEOUT<10:
        raise SystemExit("PAYMENT_TIMEOUT_MINUTES harus >=10")
    db.init()
    app=Application.builder().token(token).build()
    checkout=ConversationHandler(
        entry_points=[CallbackQueryHandler(checkout_entry,pattern=r"^checkout$")],
        states={NAME:[MessageHandler(filters.TEXT & ~filters.COMMAND,checkout_name)],
                PHONE:[MessageHandler(filters.TEXT & ~filters.COMMAND,checkout_phone)],
                ADDRESS:[MessageHandler(filters.TEXT & ~filters.COMMAND,checkout_address)],
                KECAMATAN:[MessageHandler(filters.TEXT & ~filters.COMMAND,checkout_kecamatan)],
                CITY:[MessageHandler(filters.TEXT & ~filters.COMMAND,checkout_city)],
                PROVINCE:[MessageHandler(filters.TEXT & ~filters.COMMAND,checkout_province)],
                POSTAL:[MessageHandler(filters.TEXT & ~filters.COMMAND,checkout_postal)],
                REVIEW:[CallbackQueryHandler(checkout_confirm,pattern=r"^invoice:(create|cancel)$")]},
        fallbacks=[CommandHandler("cancel",cancel_text)],allow_reentry=True)
    app.add_handler(checkout)
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("myid",myid))
    app.add_handler(CommandHandler("admin",admin_home))
    app.add_handler(CommandHandler("panduan",panduan))
    for command in ("tambah","stok","harga","nonaktif","detail","bayar","tolak","proses","resi","selesai","masalah","refund","refundselesai","rekap"):
        app.add_handler(CommandHandler(command,admin_action))
    app.add_handler(CommandHandler("cancel",cancel_text))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE,on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,on_text))
    app.add_error_handler(error_handler)
    app.job_queue.run_repeating(expire_job,interval=60,first=3)
    app.job_queue.run_repeating(notification_job,interval=30,first=5)
    app.job_queue.run_repeating(daily_report,interval=300,first=15)
    app.job_queue.run_repeating(report_backlog,interval=300,first=25)
    log.info("Starting Telegram dropship bot; polling with single instance")
    app.run_polling(drop_pending_updates=False,allowed_updates=Update.ALL_TYPES)


if __name__=="__main__":
    run()