# Tmap — Next-Gen Market Microstructure & Order Flow Terminal

> **High-Performance Python Market Microstructure Engine & Visual Terminal**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Tests](https://img.shields.io/badge/tests-12%20passed-brightgreen.svg)]()

---

## 📌 Tentang Tmap

**Tmap** adalah sistem analisis kuantitatif dan terminal visualisasi mikrostruktur pasar (*market microstructure & order flow*) generasi baru. Sistem ini dirancang untuk membaca pergerakan transaksi eksekusi (*tick-by-tick*), kedalaman limit order book (*DOM / Depth of Market*), volume footprint, delta imbalance, serta Cumulative Volume Delta (CVD) secara *real-time*.

Dibangun dengan arsitektur **Decoupled Event-Driven Pipeline**, Tmap memisahkan secara tegas antara lapisan penerimaan data (*data ingestion*), kalkulasi matematika kuantitatif (*analytics*), dan antarmuka visual grafis (*presentation layer*). Arsitektur ini memungkinkan developer dan desainer antarmuka (UI/UX) untuk bekerja sama secara independen tanpa saling mengganggu stabilitas logika inti.

---

## 🏛️ Arsitektur Proyek & Struktur Direktori

```
Tmap/
├── README.md                 # Dokumentasi utama proyek & panduan integrasi
├── run_demo.py               # Launcher cepat demo interaktif dengan feed sintetis
├── pyproject.toml            # Konfigurasi build sistem dan metadata package
├── requirements.txt          # Dependensi Python
├── .gitignore                # Aturan ignore git standar Python/Qt/IDE
├── tmap/                     # Package Python Inti
│   ├── __init__.py           # Versi dan public exports
│   ├── core/                 # Engine Inti & Pipeline Data
│   │   ├── __init__.py
│   │   ├── types.py          # Data types: Tick, VolumeBar, MarketDepth, MarketSnapshot
│   │   ├── engine.py         # OrderFlowEngine: Akumulasi O(1), Sealing Bar, Bounded Buffer
│   │   ├── config.py         # EngineConfig: Setting tick size, limiter, dan rolling window
│   │   └── events.py         # EventBus: Pub/Sub decoupled broker
│   ├── analytics/            # Modul Kuantitatif & Order Flow
│   │   ├── __init__.py
│   │   ├── orderflow.py      # Diagonal Footprint Imbalance & Net Delta %
│   │   ├── rvol.py           # Relative Volume (RVOL) & Regime Classifier
│   │   └── vbp.py            # Volume by Price (Volume Profile, POC, VAH, VAL)
│   ├── network/              # Layer Komunikasi Data & Feeds
│   │   ├── __init__.py
│   │   ├── base.py           # BaseFeed interface
│   │   ├── mock_feed.py      # Simulator tick & DOM realistis (Brownian random-walk + Poisson)
│   │   └── mt5_feed.py       # Client TCP bridge untuk EA MetaTrader 5
│   ├── ui/                   # Layer Presentasi Visual & UI
│   │   ├── __init__.py
│   │   ├── DESIGN_GUIDE.md   # Panduan lengkap untuk desainer UI kustom
│   │   ├── contracts.py      # Interface contracts / protocols (IChartView, IControlPanel)
│   │   ├── theme.py          # Design tokens: palet warna cyberpunk/dark terminal & QSS
│   │   ├── app.py            # Window aplikasi utama (TmapMainWindow)
│   │   └── components/       # Komponen visual modular
│   │       ├── base_canvas.py   # Kanvas render (QPainter / OpenGL ready, pan & zoom)
│   │       ├── control_panel.py # Top bar kontrol feed & navigasi
│   │       └── status_bar.py    # Bottom bar indikator performa & tick count
│   └── utils/                # Helper Matematika & Logging
│       ├── __init__.py
│       ├── logger.py         # Thread-safe logger
│       └── math_helpers.py   # Safe division, LERP, clamp, frame-rate normalize
└── tests/                    # Unit Tests
    ├── __init__.py
    ├── test_engine.py        # Pengujian akumulasi O(1), buffer limits, bar sealing
    ├── test_analytics.py     # Pengujian Imbalance, RVOL, CVD, Volume Profile
    ├── test_feed.py          # Pengujian mock feed streaming & callbacks
    └── test_ui_contracts.py  # Pengujian protokol UI & token tema
```

---

## 🎨 Panduan Khusus: Kolaborasi Desain UI

Bagian ini ditujukan khusus bagi **rekan desainer/pengembang antarmuka** yang ingin mengisi dan menerapkan desain buatan sendiri ke dalam Tmap.

### 1. Di Mana Saya Harus Meletakkan Desain Saya?
Seluruh kode tampilan visual berada di dalam folder **`tmap/ui/`**:
- **Desain Kanvas & Grafik Utama**: Ubah atau gantikan `tmap/ui/components/base_canvas.py`.
- **Desain Panel Kontrol / Toolbar**: Ubah `tmap/ui/components/control_panel.py`.
- **Warna, Font, & Gaya Visual**: Ubah palet token di `tmap/ui/theme.py`.
- **Panduan Desain Terperinci**: Buka file [`tmap/ui/DESIGN_GUIDE.md`](tmap/ui/DESIGN_GUIDE.md) untuk mempelajari struktur data visual secara mendalam.

### 2. Bagaimana Tampilan Menerima Data dari Engine?
Kanvas visual mengimplementasikan kontrak `IChartView` (`tmap/ui/contracts.py`). Kamu cukup menerima event callback yang sudah disediakan:
```python
def on_tick(self, tick: Tick) -> None:
    # Dipanggil setiap kali ada transaksi baru (price, volume, side)
    ...

def on_bar_updated(self, bar: VolumeBar) -> None:
    # Dipanggil saat bar aktif bergerak
    ...

def on_bar_sealed(self, bar: VolumeBar) -> None:
    # Dipanggil saat bar selesai terbentuk lengkap dengan footprint profile
    ...

def on_depth_updated(self, depth: MarketDepth) -> None:
    # Dipanggil saat limit order book (bids/asks) berubah
    ...
```

### 3. Menguji Desain Langsung (Tanpa Perlu Akun Broker)
Cukup jalankan:
```bash
python run_demo.py
```
Sistem akan langsung memutar data pasar sintetis berkecepatan tinggi dengan random-walk harga realistis dan kedalaman buku pesanan, sehingga kamu bisa mengecek keindahan desain, animasi, dan layout kapan saja!

---

## 🛡️ 5 Prinsip Emas Arsitektur Tmap

Semua kontribusi kode wajib mematuhi 5 aturan inti ini:

1. **Non-Blocking GUI Thread**: Seluruh socket jaringan dan stream kalkulasi data berjalan di thread background terpisah. UI thread murni hanya bertugas merender grafik.
2. **Operasi Tick Bersifat $O(1)$**: Setiap tick yang masuk langsung diakumulasi ke dalam bar aktif dalam waktu konstan $O(1)$ tanpa perulangan (*looping*) pada riwayat masa lalu.
3. **Memori Bounded (Zero Memory Leaks)**: Seluruh koleksi historis dibatasi menggunakan `collections.deque(maxlen=...)` agar RAM aplikasi tetap stabil meskipun berjalan berhari-hari.
4. **Pemisahan Tanggung Jawab Mutlak (*Separation of Concerns*)**: Modul `analytics` dan `network` tidak boleh menyentuh visual `QPainter`/GUI; modul `ui` tidak boleh melakukan kalkulasi matematika berat di dalam `paintEvent`.
5. **Epsilon Guards**: Semua operasi pembagian rasio volume dan delta wajib menggunakan guard epsilon (`safe_div`) untuk mencegah `ZeroDivisionError`.

---

## ⚡ Instalasi & Penggunaan

### 1. Kloning Repository
```bash
git clone https://github.com/amfitrite/Tmap.git
cd Tmap
```

### 2. Buat Virtual Environment (Direkomendasikan)
```bash
python -m venv .venv
# Di Windows:
.venv\Scripts\activate
# Di Linux/macOS:
source .venv/bin/activate
```

### 3. Pasang Dependensi
```bash
pip install -r requirements.txt
```

### 4. Jalankan Aplikasi
```bash
python run_demo.py
```

---

## 🧪 Menjalankan Unit Tests

Seluruh fungsionalitas inti telah dilengkapi dengan unit test otomatis:

```bash
python -m unittest discover -s tests
```

Output:
```text
Ran 12 tests in 0.155s

OK
```

---

## 📄 Lisensi

Proyek ini dirilis di bawah lisensi **MIT License**. Silakan gunakan, modifikasi, dan kembangkan sesuai kebutuhan tim.
