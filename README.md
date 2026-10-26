# Tmap — Tick Map: Likuiditas L2 dari Tick MT5

> **Peta limit order book (ala Bookmap) yang direkonstruksi dari aliran tick MetaTrader 5 melalui EA Bridge.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Tests](https://img.shields.io/badge/tests-12%20passed-brightgreen.svg)]()

---

## 📌 Konsep Utama

Broker forex/CFD seperti **MetaTrader 5 tidak menyediakan order book L2 asli**. Tmap mengatasi ini dengan cara:

1. **EA Bridge (`TmapBridge.mq5`)** menempel di terminal MT5 dan menyedot setiap tick harga + volume + snapshot depth, lalu melemparkannya lewat **socket TCP lokal** ke mesin Python.
2. **Rekonstruksi L2 DOM** — karena depth MT5 terbatas/palsu, Tmap membangun *cache order book level-2 sendiri* dengan merekam dan mengagregasi stream tick secara persisten: setiap level harga diperbarui berdasarkan delta volume dan interaksi bid/ask yang teramati.
3. **Heatmap Likuiditas ala Bookmap** — riwayat kedalaman tiap level harga dipetakan sebagai pita warna (gelap → terang = likuiditas menebal/menipis) sepanjang sumbu waktu, sehingga *resting liquidity*, *spoof walls*, dan *absorption* terlihat jelas.

Ditambah lapisan analitik order-flow: **footprint imbalance, net delta, CVD, RVOL, dan Volume Profile (POC/VAH/VAL)**.

---

## 🏛️ Arsitektur & Struktur Direktori

Pipeline berorientasi event dengan pemisahan tegas: **ingestion → analytics → presentation**.

```
Tmap/
├── README.md                 # Dokumentasi utama
├── run_demo.py               # Launcher demo dengan feed sintetis (tanpa broker)
├── pyproject.toml            # Metadata package
├── requirements.txt          # Dependensi
├── tmap/
│   ├── core/                 # Engine inti
│   │   ├── types.py          # Tick, OrderBookLevel, MarketDepth, VolumeBar
│   │   ├── engine.py         # OrderFlowEngine: akumulasi O(1), bar sealing, bounded buffer
│   │   ├── config.py         # EngineConfig: tick size, limiter, rolling window
│   │   └── events.py         # EventBus pub/sub (decoupling)
│   ├── network/              # Lapisan data
│   │   ├── base.py           # Interface BaseFeed
│   │   ├── mt5_feed.py       # MT5TCPFeed — klien TCP untuk EA bridge (auto-reconnect)
│   │   └── mock_feed.py      # Simulator tick/depth realistis (random-walk + Poisson)
│   ├── analytics/            # Modul kuantitatif
│   │   ├── orderflow.py      # Diagonal footprint imbalance & net delta %
│   │   ├── rvol.py           # Relative Volume & regime classifier
│   │   └── vbp.py            # Volume by Price: POC, VAH, VAL
│   ├── ui/                   # Presentasi (PySide6, opsional)
│   │   ├── app.py            # TmapMainWindow
│   │   ├── theme.py          # Design tokens dark terminal
│   │   ├── contracts.py      # Protokol IChartView / IControlPanel
│   │   ├── DESIGN_GUIDE.md   # Panduan untuk desainer UI
│   │   └── components/       # Canvas (heatmap siap dikembangkan), control panel, status bar
│   └── utils/                # Logger thread-safe & helper matematika (safe_div, clamp)
└── tests/                    # 12 unit tests (engine, analytics, feed, UI contracts)
```

---

## 🔌 Alur Data MT5 → Tmap

```
MT5 Terminal ──(tick-by-tick)──> TmapBridge.mq5 (EA)
        │
        ▼  JSON lines via TCP 127.0.0.1:5555
MT5TCPFeed (tmap/network/mt5_feed.py)
        │  on_tick / on_depth callbacks
        ▼
OrderFlowEngine (akumulasi O(1), rekonstruksi L2 cache)
        │  EventBus
        ├── Analytics: footprint, delta, CVD, RVOL, VBP
        └── UI ChartCanvas: price line + heatmap likuiditas + DOM
```

**Format packet EA bridge** (satu baris JSON per pesan):

```json
{"type":"tick","ts":1730000000.123,"price":2650.25,"volume":3.0,"side":"BUY","bid":2650.20,"ask":2650.30}
{"type":"depth","ts":1730000000.456,"bids":{"2650.20":12.0,"2650.10":40.0},"asks":{"2650.30":8.0,"2650.40":55.0}}
```

---

## ⚡ Instalasi & Penggunaan

```bash
git clone https://github.com/amfitrite/Tmap.git
cd Tmap
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # Linux/macOS
pip install -r requirements.txt
```

### Demo (tanpa MT5)
```bash
python run_demo.py
```
Memutar feed sintetis berkecepatan tinggi — cocok untuk menguji UI dan heatmap.

### Live dari MT5
1. Pasang `TmapBridge.mq5` di chart instrumen yang diinginkan di terminal MT5 (izinkan *WebRequest/socket* sesuai protokol bridge).
2. Jalankan Tmap — feed otomatis menghubungkan `127.0.0.1:5555` dengan reconnect loop.

---

## 🛡️ Prinsip Emas Arsitektur

1. **Non-blocking GUI** — socket & kalkulasi di thread background; UI hanya merender.
2. **Akumulasi tick $O(1)$** — tanpa loop ke riwayat saat tick masuk.
3. **Memori bounded** — histori disimpan pada `deque(maxlen=...)`, stabil berhari-hari.
4. **Separation of concerns** — `analytics`/`network` tidak menyentuh GUI; `ui` tidak menghitung matematika berat di `paintEvent`.
5. **Epsilon guards** — semua pembagian memakai `safe_div`.

---

## 🧪 Testing

```bash
python -m unittest discover -s tests
# Ran 12 tests ... OK
```

---

## 🗺️ Roadmap

- [x] Core engine tick→bar O(1) + EventBus
- [x] MT5 TCP feed client (auto-reconnect)
- [x] Mock feed untuk development offline
- [x] Analytics: footprint imbalance, RVOL, VBP
- [x] **L2 DOM cache** — rekonstruksi order book sintetis dari stream tick (sweep / touch / decay, `tmap/core/dom_cache.py`)
- [ ] **Heatmap likuiditas ala Bookmap** di ChartCanvas (pita warna waktu×harga)
- [ ] EA `TmapBridge.mq5` resmi + dokumentasi setup
- [ ] Replay sesi dari file tick log

---

## 📄 Lisensi

MIT License — silakan gunakan, modifikasi, dan kembangkan sesuai kebutuhan.
