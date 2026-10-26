# 🎨 Panduan Desain & Integrasi UI — Tmap

Halo! Dokumen ini dibuat khusus untuk mempermudah kamu mendesain dan mengkustomisasi tampilan visual **Tmap** tanpa perlu pusing memikirkan logika kalkulasi matematika atau koneksi socket di baliknya.

---

## 🏗️ Filosofi Desain: *Decoupled Presentation Layer*

Semua logika inti (kalkulasi delta, order book, aggregasi tick O(1), dan koneksi data) sudah terisolasi rapi di dalam package `tmap.core`, `tmap.analytics`, dan `tmap.network`.

Tugas modul `tmap.ui` murni adalah **menerima data yang sudah jadi dan merendernya seindah dan sehalus mungkin**.

```
[ Market Data Feed ]
        ↓ (Tick / Depth)
[ OrderFlowEngine (O(1)) ]
        ↓ (Events)
[ EventBus: TICK, BAR_UPDATED, BAR_SEALED, DEPTH_UPDATED ]
        ↓
[ Custom UI View / Kanvas Desain Kamu ]
```

---

## 📁 Struktur Folder UI

Semua pekerjaan visual kamu berada di folder ini:

```
tmap/ui/
├── DESIGN_GUIDE.md         # Panduan ini
├── theme.py                # Token warna, typography, dan stylesheet QSS
├── contracts.py            # Protokol/Interface kontrak antarmuka
├── components/             # Widget-widget individual
│   ├── base_canvas.py      # Kanvas render (bisa diubah ke QPainter / OpenGL)
│   ├── control_panel.py    # Toolbar / panel kontrol atas
│   └── status_bar.py       # Baris indikator status bawah
└── app.py                  # Window utama (MainWindow) penyusun layout
```

---

## 🎨 1. Menyesuaikan Warna & Tema (`theme.py`)

Kamu bisa mengubah seluruh palet warna di `tmap/ui/theme.py`:

```python
from tmap.ui.theme import ThemeColors

# Contoh: Sesuaikan token warna
my_palette = ThemeColors(
    bg_main="#0b0e14",         # Latar belakang kanvas
    bull_green="#00FF88",      # Warna buyer / agresi beli
    bear_red="#FF0055",        # Warna seller / agresi jual
    cyan_info="#00E5FF",       # Garis CVD & indikator delta
    gold_highlight="#FFD700",  # Point of Control (POC) & Whale trades
)
```

Jika kamu ingin menggunakan styling CSS/QSS yang lebih kompleks (seperti efek glassmorphism, rounded corners, atau shadow), cukup edit string `QSS_STYLESHEET` di file tersebut.

---

## 🖌️ 2. Membuat Desain Kanvas Kustom (`components/base_canvas.py`)

Kanvas utama mengimplementasikan protokol `IChartView` dari `contracts.py`.

Setiap kali data pasar bergerak, engine akan memanggil method ini secara otomatis:

| Method | Kapan Dipanggil? | Data yang Diterima |
|---|---|---|
| `on_tick(tick)` | Setiap kali terjadi transaksi eksekusi | `Tick(price, volume, side, bid, ask)` |
| `on_bar_updated(bar)` | Bar yang sedang aktif mengalami perubahan | `VolumeBar(open, high, low, close, delta, ...)` |
| `on_bar_sealed(bar)` | Bar selesai terbentuk (mencapai batas tick) | `VolumeBar` lengkap dengan footprint profile |
| `on_depth_updated(depth)`| Buku pesanan limit (DOM) berubah | `MarketDepth(bids, asks, spread)` |

### Contoh Menggambar di `paintEvent`:
```python
from PySide6.QtGui import QPainter, QColor, QPen
from PySide6.QtWidgets import QWidget

class MyCustomCanvas(QWidget):
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # 1. Gambar latar belakang
        painter.fillRect(self.rect(), QColor("#0D0F12"))

        # 2. Gambar garis harga saat ini
        # ... logika visual kamu di sini ...

        painter.end()
```

---

## 🚀 3. Menguji Desain Kamu Secara Langsung

Kamu tidak butuh akun broker MetaTrader aktif untuk melihat desainmu berjalan! Jalankan demo dengan feed data sintetis:

```bash
python run_demo.py
```

Sistem akan otomatis menghasilkan transaksi tick pasar realistis (random-walk + spread + orderbook DOM) dengan kecepatan tinggi, sehingga kamu bisa langsung mengecek kehalusan animasi, warna font, layout, dan responsivitas interaksinya!

---

## 💡 Tips untuk Performa Visual Maksimal

1. **Gunakan Double-Buffering / Offscreen Pixmap**: Jika menggambar heatmap tebal dengan ribuan sel, gambar ke `QPixmap` terlebih dahulu lalu blit ke layar saat `paintEvent`.
2. **Gunakan `normalize_lerp`**: Gunakan fungsi helper dari `tmap.utils.math_helpers.normalize_lerp` untuk animasi kamera yang mulus baik di layar 60 Hz maupun 120+ Hz ProMotion.
3. **Jangan Lakukan Query Berat di `paintEvent`**: Semua data bar dan tick sudah tersimpan di `self.engine.bars`. Cukup baca dan gambar.
