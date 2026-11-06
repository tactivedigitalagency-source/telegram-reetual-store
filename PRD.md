# PRD FINAL v1.1 — Telegram Dropship Store

**Tanggal:** 6 November 2026  
**Status:** Spesifikasi implementasi MVP (final untuk pengembangan; bukan bukti sistem telah berjalan)  
**Platform:** Telegram saja; Python `python-telegram-bot` v21.x, SQLite, Railway  
**Model:** 1 toko, 1 admin, dropship, pembayaran manual  
**Keputusan final pemilik:** (B) invoice otomatis langsung setelah checkout; **ongkir pelanggan Rp0**; **stok dikunci secara lokal**; **refund manual**.

## 1. Tujuan dan cakupan

Pembeli usia 18+ dapat melihat katalog produk yang diizinkan, mengelola keranjang, checkout, mendapat invoice langsung, membayar via transfer/QRIS yang diterima merchant, mengirim bukti bayar, serta melihat status dan resi di Telegram. Admin mengelola produk, stok lokal, verifikasi pembayaran, pengiriman, masalah pesanan, refund manual, dan rekap harian melalui bot.

**Dalam MVP:** gerbang usia, katalog, keranjang, checkout otomatis, ongkir Rp0 bagi pelanggan di area layanan, reservasi stok lokal atomik, invoice unik dan batas bayar, upload bukti, verifikasi manual, admin queue, fulfillment supplier manual, resi, bantuan, refund manual, audit, dan laporan 21.00 WIB.  
**Di luar MVP:** API supplier, gateway/verifikasi pembayaran otomatis, COD, dashboard web, multi-admin, promo, retur/refund otomatis, dan real-time tracking kurir.

## 2. Aturan bisnis yang dikunci

| ID | Aturan | Keputusan |
|---|---|---|
| BR-01 | Model checkout | **B**: invoice langsung setelah checkout berhasil |
| BR-02 | Biaya kirim dibebankan ke pelanggan | **Rp0**, ditampilkan jelas sebagai `Gratis Ongkir` |
| BR-03 | Biaya kurir sebenarnya | Ditanggung owner; dicatat sebagai biaya internal jika diketahui |
| BR-04 | Wilayah pengiriman | Checkout hanya untuk wilayah yang dilayani supplier/kurir; daftar wilayah dapat dikonfigurasi admin |
| BR-05 | Stok tersedia | `stock_qty - reserved_qty`; stok katalog adalah catatan lokal, bukan sinkronisasi supplier |
| BR-06 | Stok terkunci | Saat invoice dibuat, secara atomik; **bukan** ketika produk masuk keranjang |
| BR-07 | Batas bayar | **Usulan default 120 menit** dari invoice; dapat dikonfigurasi, belum merupakan keputusan eksplisit owner |
| BR-08 | Pembayaran sah | Hanya setelah admin memeriksa dana masuk; foto bukti saja tidak cukup |
| BR-09 | Jika supplier kosong setelah dibayar | Admin tandai masalah; tawarkan solusi yang disetujui pelanggan atau **refund manual** |
| BR-10 | Status refund selesai | Baru `REFUNDED` setelah admin memastikan dana pengembalian terkirim |
| BR-11 | Akses admin | Telegram numeric user ID yang di-whitelist di environment |
| BR-12 | Perhitungan Rupiah | Seluruh nominal dalam integer Rupiah, bukan float |

**Implikasi gratis ongkir:** kebijakan ini gratis bagi pembeli, bukan berarti kurir gratis. Owner harus memasukkan ongkir nyata dan harga pokok supplier dalam analisis laba, membatasi cakupan kirim jika margin tidak mencukupi, dan tidak meminta selisih ongkir setelah invoice diterbitkan.

## 3. Aktor, akses, dan privasi

- **Pembeli:** hanya bertransaksi melalui chat privat bot, melihat pesanan miliknya sendiri, mengirim bukti bayar, membuka bantuan.
- **Admin:** mengelola produk, status, pengiriman, pembayaran, tiket, refund, dan laporan; validasi ID server-side di setiap callback/perintah.
- **Supplier/kurir:** pihak operasional di luar sistem; admin membagikan hanya data pengiriman yang diperlukan.
- **18+:** pengguna menyatakan berusia 18+ sebelum melihat katalog; tombol ini bukan verifikasi identitas/usia yang kuat.
- **Kepatuhan:** hanya tampilkan barang dan materi visual yang sesuai hukum, izin/ketentuan kategori produk, aturan Telegram dan pihak pembayaran; jangan mengasumsikan seluruh kategori produk dewasa diperbolehkan.
- **Privasi:** jangan kirim data alamat ke grup publik atau menaruhnya di log biasa; konfigurasi retensi dan penghapusan; gunakan `file_id` foto Telegram alih-alih menyimpan berkas lokal bila memungkinkan.

## 4. Alur utama pembeli (happy path)

1. `/start` → gerbang 18+ + pemberitahuan privasi → menu utama.
2. `🛍 Katalog` → kategori → detail (foto netral, nama, harga, deskripsi, stok lokal).
3. `➕ Keranjang` → ubah jumlah, hapus item, tinjau subtotal.
4. `✅ Checkout` → nama, telepon, alamat terstruktur, wilayah kirim → ringkasan akhir dengan `Ongkir Rp0`.
5. `🧾 Buat Invoice` → transaksi SQLite: idempotency key diverifikasi, harga di-snapshot, stok semua item direservasi atomik, order dibuat, keranjang dikosongkan, invoice unik ditampilkan.
6. Invoice menampilkan `ORDER-...`, item, subtotal, ongkir Rp0, total, metode bayar, dan tenggat yang eksplisit.
7. `📸 Kirim Bukti Bayar` → pilih order aktif → foto dan metadata transaksi diteruskan hanya ke admin → status `PAYMENT_REVIEW`.
8. Admin cocokkan mutasi/QRIS dengan nominal/order → `✅ Konfirmasi Dana Masuk` → status `PAID`. Pada transaksi atomik, stok lokal berkurang dan reservasi dilepas.
9. Admin teruskan pesanan ke supplier secara manual → `PROCESSING` → kurir/resi → `SHIPPED` → `COMPLETED` setelah penerimaan terkonfirmasi/pengecekan admin.
10. Pembeli dapat memantau `📦 Pesanan Saya` dan `💬 Bantuan` kapan pun.

**Catatan operasional:** sistem tidak menjanjikan supplier pasti punya stok. Risiko stok supplier habis setelah pembayaran tetap ada dan ditangani dengan alur exception.

## 5. Menu dan tombol Telegram

### 5.1 Pembeli

| Halaman | Tombol/aksi |
|---|---|
| Gerbang usia | `✅ Saya 18+`, `🚫 Keluar` |
| Menu utama | `🛍 Katalog`, `🛒 Keranjang`, `📦 Pesanan Saya`, `💬 Bantuan` |
| Katalog | `📂 Kategori`, `👁 Detail`, `⬅️ Kembali` |
| Detail produk | `➖`, `➕`, `🛒 Tambah ke Keranjang`, `⬅️ Katalog` |
| Keranjang | `➕`, `➖`, `🗑 Hapus`, `🧹 Kosongkan`, `✅ Checkout` |
| Checkout | `✏️ Ubah Alamat`, `🧾 Buat Invoice`, `❌ Batal` |
| Invoice | `📸 Kirim Bukti Bayar`, `📦 Lihat Pesanan`, `💬 Bantuan`, `❌ Batalkan jika belum bayar` |
| Pesanan saya | `📄 Detail`, `📦 Resi`, `💬 Hubungi Admin`, `⬅️ Menu` |

Semua aksi dipetakan pada callback ID pendek, misalnya `cat:12`, `cart:add:25`, `ord:42`, dan diperiksa ulang terhadap identitas pengguna dan status terbaru di database. Jangan percaya harga, kuantitas, atau role dari payload tombol.

### 5.2 Admin

| Menu | Tombol/aksi |
|---|---|
| Beranda `/admin` | `🆕 Order Baru`, `💳 Cek Pembayaran`, `📦 Proses Supplier`, `🚚 Input Resi`, `⚠️ Masalah`, `↩️ Refund`, `📊 Laporan`, `🗂 Produk` |
| Produk | `➕ Tambah`, `📸 Foto`, `✏️ Edit Harga`, `📦 Set Stok`, `⏸ Nonaktifkan`, `🔔 Stok Menipis` |
| Bukti bayar | `✅ Konfirmasi Dana Masuk`, `❌ Tolak Bukti`, `🔎 Perlu Ditinjau` |
| Pesanan dibayar | `📦 Mulai Proses`, `⚠️ Supplier Kosong`, `🚚 Tambah Resi` |
| Masalah | `💬 Hubungi Pembeli`, `↩️ Ajukan Refund`, `✅ Tutup Kasus` |
| Refund | `💰 Catat Refund`, `✅ Tandai Dana Terkirim` |

Perintah teks alternatif: `/admin`, `/tambah`, `/foto`, `/stok`, `/harga`, `/nonaktif`, `/status`, `/resi`, `/refund`, `/rekap`, `/bantuan`. Seluruh perubahan administratif harus dicatat di audit trail.

## 6. State machine pesanan

| Status | Makna | Transisi sah utama |
|---|---|---|
| `AWAITING_PAYMENT` | Invoice terbit; stok lokal direservasi | `PAYMENT_REVIEW`, `EXPIRED`, `CANCELLED` |
| `PAYMENT_REVIEW` | Bukti masuk; admin mengecek dana | `PAID`, `AWAITING_PAYMENT` (bukti ditolak), `ISSUE` |
| `PAID` | Dana masuk terverifikasi; stok lokal dikonsumsi | `PROCESSING`, `ISSUE`, `REFUND_PENDING` |
| `PROCESSING` | Supplier memproses | `SHIPPED`, `ISSUE`, `REFUND_PENDING` |
| `SHIPPED` | Resi masuk | `COMPLETED`, `ISSUE` |
| `COMPLETED` | Pesanan selesai | `ISSUE` hanya dengan alasan audit/layanan pelanggan |
| `EXPIRED` | Invoice habis; reservasi dilepas | `ISSUE` jika pembayaran terlambat ditemukan |
| `CANCELLED` | Dibatalkan sebelum dibayar | `ISSUE` jika kemudian ditemukan dana masuk |
| `ISSUE` | Masalah stok/pembayaran/pengiriman | `PROCESSING`, `REFUND_PENDING`, atau status sah lain oleh admin dengan audit |
| `REFUND_PENDING` | Pengembalian dana manual ditangani | `REFUNDED`, `ISSUE` jika gagal |
| `REFUNDED` | Dana refund dipastikan terkirim | Terminal; perubahan hanya koreksi audit khusus |

**Jangan gunakan satu kolom status sebagai satu-satunya sumber kebenaran uang.** Simpan payment proofs, keputusan verifikasi, refund dan audit secara terpisah. Pembayaran terlambat pada invoice `EXPIRED` tidak boleh langsung mengubahnya ke `PAID` tanpa pengecekan ulang stok/rekonsiliasi.

## 7. Aturan stok dan transaksi atomik

**Model stok lokal:**

- `stock_qty`: estimasi jumlah unit yang masih bisa dialokasikan menurut catatan owner, setelah penjualan terverifikasi.
- `reserved_qty`: unit yang direservasi oleh invoice belum dibayar.
- `available_qty = stock_qty - reserved_qty`; wajib tidak negatif.
- Keranjang tidak mengubah stok. Invoice sukses menambah `reserved_qty`.
- Checkout multi-item dijalankan sebagai satu transaksi `BEGIN IMMEDIATE`; jika 1 item tidak tersedia, seluruh checkout dibatalkan dan tidak ada invoice parsial.
- Saat `PAYMENT_REVIEW` → `PAID`, untuk setiap item `stock_qty -= qty` dan `reserved_qty -= qty` dalam transaksi yang sama; verifikasi idempotent.
- Invoice `AWAITING_PAYMENT` yang kedaluwarsa/dibatalkan mengurangi `reserved_qty` tepat satu kali.
- Invoice `PAYMENT_REVIEW` **tidak otomatis kedaluwarsa** saat bukti sedang diperiksa; sistem mengingatkan admin bila review melewati SLA. Jika bukti ditolak, admin memilih tenggat baru atau membatalkan sesuai konfirmasi dana.
- Admin tidak boleh menetapkan `stock_qty < reserved_qty`. Jika supplier ternyata kosong, nonaktifkan produk, selesaikan reservasi aktif, dan koreksi stok setelahnya.
- Setelah pesanan terbayar ternyata gagal dipenuhi, **jangan** otomatis menambahkan stok saat refund karena bisa menghasilkan stok fiktif. Admin melakukan rekonsiliasi stok supplier secara manual.
- Setiap gerakan stok dicatat di `inventory_events` dengan jenis `RESERVE`, `RELEASE`, `SALE`, `ADJUST`.

## 8. Pengecualian dan pemulihan

| Kondisi | Tindakan wajib |
|---|---|
| Tombol checkout diklik dua kali | Gunakan `idempotency_key` unik; kembalikan invoice yang sama |
| Dua pelanggan membeli unit terakhir bersamaan | Reservasi atomik; hanya satu checkout sukses |
| Stok kosong saat checkout | Tolak checkout, pertahankan keranjang, arahkan ke katalog |
| Pelanggan di luar area kirim | Tolak invoice, minta ganti alamat atau hubungi admin |
| Tenggat pembayaran lewat tanpa bukti | `EXPIRED`, lepaskan reservasi, notifikasi pembeli |
| Bukti bayar masuk sebelum tenggat | `PAYMENT_REVIEW`; jangan lepas stok sampai diselesaikan admin |
| Bukti palsu / nominal tak cocok | `ISSUE` atau tolak bukti; status `PAID` dilarang tanpa dana nyata |
| Transfer diterima setelah order kedaluwarsa | Buat tiket rekonsiliasi; admin memutuskan invoice pengganti dengan stok baru atau refund |
| Supplier kosong setelah bayar | Nonaktifkan produk bila perlu, beri tahu pembeli, tawarkan pemenuhan alternatif yang disetujui atau refund manual |
| Pembayaran ganda | Audit kedua pembayaran, refund kelebihan manual |
| Refund manual | Admin konfirmasi nominal dan tujuan sah, kirim lewat kanal pembayaran yang tepat, simpan referensi transaksi dan waktu; `REFUNDED` hanya setelah dana dikirim |
| Resi salah / pengiriman bermasalah | Admin koreksi dengan jejak audit; pembeli menerima notifikasi |
| Pengguna memblokir bot / pesan Telegram gagal | Simpan ke `notification_outbox`, retry terbatas, jangan mengulangi perubahan bisnis |
| Restart/deploy saat order aktif | Status, deadline, dan outbox dibaca dari database; scheduler melakukan catch-up |

**Refund MVP:** satu refund penuh per pesanan sebagai jalur utama. Refund parsial, jika diperlukan, dibuat sebagai kasus manual khusus dan harus dicatat; otomatisasi refund ditunda. Tidak ada refund tanpa verifikasi admin. Hindari menyimpan nomor rekening lengkap tanpa kebutuhan dan kontrol tambahan; simpan identitas transaksi minimum/referensi refund.

## 9. Struktur database SQLite

Skema SQL pendamping: `schema_v1_1.sql`.

| Tabel | Fungsi penting |
|---|---|
| `users` | Telegram user ID, konfirmasi 18+, waktu daftar |
| `products` | SKU, nama, kategori, harga, stok lokal, reservasi, foto, status aktif |
| `cart_items` | Item keranjang per pembeli |
| `orders` | Invoice ID, pembeli, alamat, subtotal, ongkir pelanggan = 0, total, tenggat, status, kurir/resi, biaya kirim owner, waktu |
| `order_items` | Snapshot nama, kuantitas, harga saat invoice terbit, harga pokok opsional |
| `payment_proofs` | Telegram `file_id`, order, waktu upload, review, reviewer |
| `refunds` | Jumlah refund, status, alasan, referensi pembayaran balik |
| `inventory_events` | Buku audit perubahan stok |
| `order_events` | Perpindahan status dan alasan |
| `support_tickets` | Komplain atau masalah pengiriman/pembayaran |
| `notification_outbox` | Pengiriman notifikasi dengan retry yang aman |
| `report_runs` | Menjamin rekap harian tidak terkirim berkali-kali |

**Indeks:** `orders(user_id,created_at)`, `orders(status,created_at)`, `payment_proofs(order_id)`, `notification_outbox(status,next_attempt_at)`. Aktifkan `PRAGMA foreign_keys=ON`, `journal_mode=WAL`, dan `busy_timeout` yang wajar; semua operasi stok dan status transaksi menggunakan commit/rollback.

## 10. Pekerjaan otomatis

| Jadwal | Aksi |
|---|---|
| Setiap 1 menit (konfigurabel) | Scan invoice `AWAITING_PAYMENT` yang kedaluwarsa; release reservasi secara idempotent |
| Setiap 5 menit | Alert pesanan `PAYMENT_REVIEW` yang terlalu lama dan order `PAID` belum ditangani |
| Setiap 1 menit | Retry notifikasi tertunda dengan batas percobaan |
| Setiap hari 21.00 `Asia/Jakarta` | Rekap order, pembayaran terverifikasi, penjualan, biaya kirim owner jika diketahui, stok menipis, kasus terbuka |
| Setiap hari | Backup SQLite konsisten ke tempat terpisah; uji pemulihan berkala |

Semua job harus aman dijalankan ulang setelah restart tanpa pengurangan stok atau perubahan status ganda.

## 11. Struktur proyek Python

```text
telegram-dropship-store/
├── main.py
├── config.py
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
├── bot/
│   ├── keyboards.py
│   ├── callbacks.py
│   ├── buyer_handlers.py
│   ├── admin_handlers.py
│   └── conversations.py
├── services/
│   ├── checkout.py
│   ├── inventory.py
│   ├── payments.py
│   ├── refunds.py
│   ├── orders.py
│   ├── notifications.py
│   └── reports.py
├── storage/
│   ├── db.py
│   ├── migrations.py
│   └── schema_v1_1.sql
├── jobs/
│   └── scheduler.py
└── tests/
    ├── test_checkout.py
    ├── test_stock_race.py
    ├── test_payment.py
    ├── test_refund.py
    └── test_access_control.py
```

**Dependency utama:** `python-telegram-bot[job-queue]>=21.0,<22`, `aiosqlite`, `python-dotenv` untuk lokal, dan timezone data bila diperlukan. Gunakan `Application.run_polling()` dengan satu instance bot; tidak diperlukan domain publik untuk long polling.

## 12. Environment dan deployment Railway

### Variabel lingkungan

```dotenv
BOT_TOKEN=<telegram_bot_token>
ADMIN_USER_ID=<numeric_telegram_id>
DATABASE_PATH=/app/data/store.sqlite3
TIMEZONE=Asia/Jakarta
PAYMENT_TIMEOUT_MINUTES=120
LOW_STOCK_THRESHOLD=3
PAYMENT_METHODS=TRANSFER,QRIS
PAYMENT_BANK_NAME=<nama_bank>
PAYMENT_ACCOUNT_NAME=<nama_penerima>
PAYMENT_ACCOUNT_NUMBER=<nomor_rekening>
PAYMENT_QRIS_FILE_ID=<optional_telegram_file_id>
SUPPORT_TEXT=Hubungi admin melalui menu Bantuan
```

Nilai bertanda `<...>` merupakan placeholder, bukan kredensial riil. Jangan commit `.env` atau token ke GitHub. Parameter wilayah layanan harus dikonfigurasi sebelum pembayaran nyata dibuka.

### Langkah Railway

1. Buat repo GitHub berisi source code, `requirements.txt`, dan migrasi database; **jangan** masukkan token/berkas SQLite lokal.
2. Di Railway, buat project baru → deploy dari GitHub repo.
3. Tambahkan **Volume** dan mount ke **`/app/data`**. Database wajib disimpan di volume, bukan filesystem ephemeral.
4. Di Railway Variables, isi variabel lingkungan di atas (kecuali placeholder yang tidak digunakan).
5. Pastikan **start command** `python main.py` dan startup menjalankan migrasi skema sebelum polling.
6. Jalankan **1 replica saja** untuk polling dan SQLite; jangan menjalankan bot yang sama bersamaan di komputer lokal dan Railway.
7. Deploy, periksa log, uji `/start`, 18+ gate, admin ID, checkout, invoice, bukti bayar, verifikasi, dan order selesai dengan data simulasi.
8. Atur backup terpisah dari volume dan uji restore; jalankan uji re-deploy ketika ada invoice tertunda.
9. Uji secara terbatas dengan transaksi riil hanya setelah kebijakan produk, pembayaran, pengiriman, keamanan, refund, dan supplier sudah diverifikasi.

**Catatan:** Volume bertahan antar-deploy, tetapi backup di volume yang sama belum merupakan backup off-site.

## 13. Skenario uji/acceptance criteria

- [ ] Pengguna tanpa konfirmasi 18+ tidak bisa melihat katalog.
- [ ] Admin action ditolak untuk non-admin, termasuk callback yang dimanipulasi.
- [ ] Pembeli hanya bisa membuka order miliknya sendiri.
- [ ] Invoice selalu menampilkan ongkir Rp0 dan total sama dengan jumlah item.
- [ ] Checkout gagal di wilayah non-layanan sebelum invoice terbit.
- [ ] Checkout membuat invoice tanpa menunggu admin.
- [ ] Dua checkout pada stok terakhir tidak oversell.
- [ ] Klik checkout ganda tidak membuat invoice/reservasi ganda.
- [ ] Gagal satu item pada keranjang multi-item menyebabkan rollback seluruh checkout.
- [ ] Invoice kedaluwarsa melepaskan reservasi sekali saja.
- [ ] Bukti bayar masuk memblokir expiry otomatis dan memicu review admin.
- [ ] Bukti bayar palsu tidak dapat memicu status `PAID`.
- [ ] Konfirmasi pembayaran kedua kali tidak mengurangi stok lagi.
- [ ] Transfer terlambat setelah `EXPIRED` masuk rekonsiliasi, bukan otomatis menjadi terbayar.
- [ ] Supplier stok kosong pasca-bayar masuk alur masalah/refund manual.
- [ ] `REFUNDED` hanya setelah admin mencatat dana balik benar-benar terkirim.
- [ ] Edit stok admin tidak melanggar `stock_qty >= reserved_qty`.
- [ ] Input dan koreksi resi dikirim ke pembeli dan terekam di audit.
- [ ] Restart/deploy tidak menghapus data invoice, deadline, atau bukti bayar.
- [ ] Kegagalan notifikasi tidak menggandakan perubahan status atau stok.
- [ ] Rekap pukul 21.00 WIB tidak terkirim ganda dalam satu hari.
- [ ] Backup SQLite dapat dipulihkan pada database pengujian.

## 14. Metrik keberhasilan

- Checkout selesai per minggu; persentase invoice terbayar; waktu rata-rata pembayaran diverifikasi.
- Persentase order `PAID` yang berhasil diserahkan ke supplier; persentase order gagal karena supplier kosong; waktu hingga resi dikirim.
- Nilai dan jumlah refund; SLA penyelesaian refund manual.
- Omzet terverifikasi, HPP supplier, biaya kirim yang ditanggung owner, serta estimasi laba kotor jika data biaya lengkap.
- Repeat purchase 30 hari, rasio pengaduan, dan akurasi stok lokal.

## 15. Asumsi sebelum go-live

1. Supplier telah menyetujui prosedur dropship, wilayah kirim, kecepatan proses, dan pembebanan biaya kirim.
2. Harga produk cukup untuk menutup biaya supplier serta **ongkir yang ditanggung owner**.
3. Media pembayaran benar-benar dapat digunakan untuk kategori produk yang dijual.
4. Kategori produk/foto/deskripsi telah diperiksa terhadap regulasi dan kebijakan platform yang relevan.
5. Owner siap memeriksa mutasi pembayaran, mengirim pesanan ke supplier, dan mengeksekusi refund manual.
6. Batas waktu 120 menit, cakupan wilayah pengiriman, dan jadwal dukungan adalah default rancangan yang harus diatur sebelum aktivasi publik.

**Hasil final:** MVP siap menjadi dasar implementasi Python Telegram Bot + SQLite + Railway, dengan checkout B, ongkir gratis bagi pelanggan, reservasi stok lokal, dan refund manual.