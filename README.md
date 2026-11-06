# Telegram Dropship Store (MVP v1.1)

Bot katalog Telegram Python untuk toko dropship satu-admin dengan checkout B (invoice otomatis), **ongkir pembeli Rp0**, reservasi stok lokal atomik, pembayaran manual, dan refund manual. Ini adalah implementasi MVP untuk diuji dengan **data simulasi** sebelum digunakan menerima pesanan riil.

## Perlu disiapkan sebelum dijalankan
1. Token bot dari [@BotFather](https://t.me/BotFather).
2. Numeric `ADMIN_USER_ID` Telegram (dapat diperoleh melalui `/myid` setelah bot berjalan dengan ID sementara milikmu, atau melalui bot info terpercaya).
3. Rekening/QRIS merchant yang sesuai kategori produk yang dijual; isikan instruksi **melalui environment**, bukan hardcode.
4. Pilih `SUPPORTED_PROVINCES` yang benar-benar dapat dikirim supplier. Default contoh `Jawa Timur`.
5. Verifikasi izin/kepatuhan kategori produk, foto, syarat kurir, dan kebijakan penyedia pembayaran. Gerbang 18+ hanya konfirmasi mandiri.

## Menjalankan lokal (Windows PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
python main.py
```

Isi `BOT_TOKEN`, `ADMIN_USER_ID`, `PAYMENT_INSTRUCTIONS`, dan wilayah. Aplikasi akan membuat DB SQLite sendiri. Untuk menguji logika transaksi tanpa token Telegram:

```powershell
python -m unittest discover -s tests -v
```

## Deploy Railway (satu proses saja)
1. Buat **repo GitHub baru** khusus proyek ini; upload seluruh isi folder kecuali `.env`, `data/`, dan file SQLite.
2. Hubungkan GitHub ke Railway → **New Project → Deploy from GitHub Repo**.
3. Add **Volume** dengan mount path `/app/data` (jangan skip: SQLite harus persisten).
4. Railway → **Variables** set:
   - `BOT_TOKEN` = token BotFather (secret; jangan kirim lewat chat).
   - `ADMIN_USER_ID` = numeric Telegram ID milik admin.
   - `DATABASE_PATH` = `/app/data/store.sqlite3`.
   - `PAYMENT_TIMEOUT_MINUTES` = `120` (ubah sesuai kebutuhan).
   - `SUPPORTED_PROVINCES` = daftar wilayah, dipisahkan koma; contoh `Jawa Timur,Jawa Tengah`.
   - `PAYMENT_INSTRUCTIONS` = instruksi transfer/QRIS sah dari penyedia pembayaran (teks).
   - `TZ` = `Asia/Jakarta`.
5. Deployment membaca `railway.json` dan Dockerfile, start `python main.py`. Bot menggunakan **long polling** sehingga tidak perlu domain.
6. Pastikan service memiliki **tepat 1 replica**. Jangan jalankan bot lokal dan Railway bersamaan memakai token yang sama.
7. Periksa Deploy Logs. Test `/start`, `/admin`, tambah produk dummy, checkout, upload bukti dummy, admin hanya menyetujui setelah pengecekan simulasi, input resi, refund.
8. Atur backup SQLite **ke luar Railway Volume** secara aman. Volume tidak menggantikan offsite backup. Jangan simpan database pelanggan di GitHub.

Jika Railway belum terhubung ke ChatGPT, hubungkan melalui **Settings → Plugins** untuk pengelolaan deployment; aktivasi membutuhkan persetujuan akun kamu.

## Perintah admin

`/admin`, `/panduan`, `/tambah SKU | Nama | Harga | Stok | Deskripsi | Kategori`, `/stok ID JUMLAH`, `/harga ID HARGA`, `/nonaktif ID`, foto dengan caption `/foto ID`, `/detail ID`, `/bayar ID`, `/tolak ID`, `/proses ID`, `/resi ID KURIR NOMOR`, `/selesai ID`, `/masalah ID ALASAN`, `/refund ID JUMLAH ALASAN`, `/refundselesai ID REF_TRANSFER`, `/rekap`.

## Penting
- Bot ini tidak memiliki sinkronisasi stok supplier; sebelum mengirim barang admin harus memeriksa stok sungguhan.
- Bukti transfer **tidak** cukup untuk menyatakan pesanan dibayar. Admin harus memeriksa mutasi/settlement pembayaran.
- Refund dilakukan di luar bot; `/refundselesai` hanya untuk pencatatan **setelah** uang dikirim.
- Jika stok supplier kosong setelah bayar, gunakan `/masalah ID ...` lalu `/refund ID JUMLAH ALASAN` bila sesuai kesepakatan.
- Semua data pemesan harus diperlakukan rahasia. Pertimbangkan enkripsi backup, retensi data, pembatasan akses, dan audit keamanan sebelum go-live.
- Laporan otomatis dikirim setelah 21.00 WIB dan paling banyak sekali per hari; saat bot mati laporan akan tertunda.
- Setelah menjalankan bot, lakukan uji end-to-end di staging. Belum ada integrasi bot atau deployment yang dilakukan dari file ini.

## Struktur
- `main.py`: handler buyer/admin dan scheduler.
- `db.py`: semua operasi SQLite, transaksi stok, status, audit, notifikasi.
- `schema.sql`: database dari PRD final v1.1.
- `tests/`: uji invarians stok, idempotensi invoice, keamanan akses order, pembayaran dan refund.
- `PRD.md`: spesifikasi yang disetujui.