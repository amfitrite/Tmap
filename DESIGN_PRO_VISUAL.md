# Rancangan Perbaikan Visual "Pro-Grade" — Tmap

> Target rasa visual: **Bookmap** (heatmap likuiditas) + **Exocharts/Jigsaw** (footprint & DOM) +
> **TradingView** (kualitas rendering candle, tipografi, chrome gelap). Semua perubahan murni
> kosmetik/UX — tidak mengubah logika engine. Prinsip: *dark-first, high-DPI, 60fps, satu bahasa desain.*

---

## Audit Kondisi Saat Ini (state `aecb657`)

| Komponen | Status | Gap vs platform pro |
|---|---|---|
| `theme.py` | ✅ token warna lengkap | hardcoded di banyak widget; tanpa skala spacing/typografi; tanpa light/dark switch |
| `base_canvas.py` (candle) | ✅ geometri TradingView-style | tanpa gradient body, antialiasing belum dijamin, grid putus-putus kasar, ruler tanpa tick mark |
| Heatmap overlay | ⚠️ berfungsi | palet 4-stop linear terasa "flat"; tanpa fade ke latar; resolusi tile ≠ device pixel |
| `dom_panel.py` (limit order) | ⚠️ dasar | bar tanpa gradasi, angka rata kiri, tanpa imbalance meter, font bukan tabular |
| `control_panel.py` / toolbar | ⚠️ standar Qt | ikon emoji/glyph mentah, chip brand polos, tanpa hover state halus |
| `status_bar.py` | ⚠️ teks polos | tanpa segmen ber-ikon, tanpa indikator koneksi hidup |
| Menu klik-kanan | ✅ fitur lengkap | style QSS menu masih default-ish, submenu TF tanpa preview mini |
| Tipografi | ❌ campur | angka chart memakai font default → kolom tidak sejajar (masalah klasik "terlihat murah") |
| Animasi/transisi | ❌ tidak ada | panel muncul/hilang instan; heatmap rebuild tanpa progres |
| HiDPI/Windows scaling | ❌ belum diurus | di laptop 125%/150% scaling garis 1px jadi blur (penyebab #1 kesan "murah") |

---

## FASE 1 — Fondasi Desain Token (½ hari) — *dampak terbesar, risiko kecil*

### 1.1 Perluas `tmap/ui/theme.py` menjadi design-token tunggal
```python
@dataclass(frozen=True)
class ThemeColors: ...          # (sudah ada) + tambah:
    bg_chart_gutter: str       # area price scale lebih gelap 1 step dari chart
    bull_candle_grad_top/bottom, bear_candle_grad_top/bottom   # gradient body
    heat_stops: tuple          # ramp heatmap 8-stop perceptual (lihat 2.3)
    focus_ring: str            # outline aksesibilitas biru lembut

SPACING  = {xs:4, sm:8, md:12, lg:16, xl:24}     # semua layout pakai kelipatan 4px
TYPO     = {mono:"Cascadia Mono/Consolas", ui:"Segoe UI", size:{...}}
RADIUS   = {panel:10, chip:6, menu:8}            # sudut konsisten ala app modern
ELEVATION= [bayangan 3 level untuk popup/menu/tooltip]
```
**Aturan:** hasil `grep QColor("#` di seluruh `ui/` harus **nol** — semua warna lewat `THEME`.

### 1.2 Tabular figures di semua angka (fix cepat, dampak besar)
- Chart axis, DOM ladder, status bar, snapshot panel → `font.setFeature("tnum", True)` / pilih
  `Consolas`/`Cascadia Mono` agar digit **sejajar vertikal**. Angka yang bergoyang tiap tick adalah
  ciri paling mudah dikenali dari aplikasi "non-pro".

### 1.3 High-DPI & crisp lines (Windows)
- Di `app.py` sebelum `QApplication`: set `Qt.AA_EnableHighDpiScaling`, `AA_UseHighDpiPixmaps`.
- Renderer: gambar hanya pada **pixel-snap** (`x + 0.5` untuk pen 1px), aktifkan
  `Antialiasing|TextAntialiasing|SmoothPixmapTransform`. Garis 1px tajam = sinyal "mahal".

---

## FASE 2 — Chart Canvas Premium (1–1,5 hari)

### 2.1 Candle & wick
- Body: **gradient vertikal halus** (top→bottom ±8% luminance) solid untuk up, hollow tipis untuk doji.
- Wick: 1px persis di tengah body (snap integer), warna = body @70% opacity (bukan abu-abu datar).
- Volume profile mini di bawah chart (opsi menu): POC garis emas, VAH/VAL shaded 12%.

### 2.2 Grid & price scale ala TradingView
- Ganti grid penuh dengan **tick marks pendek (4px)** di gutter kanan + garis horizontal sangat lemah
  (`grid_line` @40% alpha); garis vertikal hanya di batas menit/jam (bukan per bar).
- Gutter harga: latar `bg_chart_gutter`, pemisah 1px `border_subtle`, label 11px `axis_text`.
- Garis harga terakhir: putus-putus + **badge harga** rounded-rect (sudah ada — rapikan radius & padding).

### 2.3 Heatmap likuiditas — bagian paling "Bookmap"
- **Ramp 8-stop perceptual** (bukan 4-stop): bid `#0a1f2e → #0e4d64 → #12a4a4 → #7fffd4`,
  ask `#2e1a0a → #7a3b0e → #f59e0b → #ffe08a`; intensitas via **sqrt/log scaling** agar dinding
  kecil tetap terlihat dan mega-wall tidak saturasi putih.
- Render tile ke **QImage sekali per build**, tempel dengan `drawImage(rect)` + `SmoothTransformation`
  → heatmap *silky* saat zoom/pan, tanpa moiré kotak-kotak.
- **Fade layer**: gradien transparansi 8% di tepi kanan heatmap agar menyatu dengan candle, bukan tambalan.
- Saat rebuild history: tampilkan **progress bar tipis emas** di bawah toolbar (lihat 4.2).

### 2.4 Crosshair pro
- Crosshair putus-putus abu lembut + **dua badge** (harga & waktu) berlatar dark-pill, mengikuti kursor;
  snap ke OHLC saat dekat candle; hide saat kursor keluar canvas.

---

## FASE 3 — DOM / Limit Order Panel ala Bookmap+Jigsaw (1 hari)

- Layout **two-sided ladder**: kolom bid (kiri, hijau) | spread gap | kolom ask (kanan, merah) —
  bukan daftar campuran. Bar likuiditas dari **tengah melebar ke dua arah** (mirroring) dengan
  gradient per-bar (volume tinggi → warna lebih jenuh).
- **Imbalance highlight**: sel di mana bid ≥ 3× ask bertetangga → border cyan 1px (pola Jigsaw).
- Header panel: nama simbol + **imbalance meter** horizontal (proportion bid vs ask, ala Bookmap).
- Penanda tetap: `T#` wall (ikon 🧱 diganti pill emas kecil "T"), `S#` sweep (pill merah), konsisten radius.
- Update bar dengan **animasi width 80ms ease-out** (QPropertyAnimation) — dinding tumbuh/memudar
  terasa hidup, bukan berkedip.
- Scroll otomatis jaga posisi harga-tengah; tombol pin untuk mengunci view.

---

## FASE 4 — Chrome Aplikasi (toolbar, panel, status bar) (1 hari)

### 4.1 Toolbar & brand
- Tinggi 44px, pemisah bawah 1px; grup kontrol diberi jarak `SPACING.lg`; button/icon toggle dengan
  **hover state** (bg lighten 6%) & checked-state accent, radius 6px.
- Chip brand: `● XAUUSD · M5` dengan dot status **berdenyut** (green=live MT5, amber=synthetic, red=offline).
- Ikon: ganti emoji dengan set monokrom SVG (Lucide/Tabler, gratis) diwarnai via THEME → konsisten DPI.

### 4.2 Progress & feedback
- `QProgressBar` custom 2px strip emas tak-berguna-indeterminate untuk load history/rebuild heatmap
  (slot sinyal `_HistoryLoadWorker.progress`).
- Toast kecil kanan-bawah ("Loaded 10 800 ticks · Synthetic") auto-fade 3 dtk — bukan hanya log.

### 4.3 Status bar ber-segmen
- Segmen ber-ikon: 🔌 bridge state · ⏱ latency tick · 📦 bars sealed · 🧠 cache size — dipisah
  separator 1px, font mono 11px, warna `text_secondary`; angka live rata kanan (tabular).

### 4.4 Menu klik-kanan
- QSS ulang: bg `bg_panel`, radius 8px, elevation shadow (Fase 1.4), item height 30px, checkmark
  accent cyan, submenu TF menampilkan contoh pola bar mini.

---

## FASE 5 — Performa Rendering (½–1 hari)

- **Dirty-region update**: `update()` hanya pada rect terdampak (harga baru / bar baru), bukan full repaint.
- Cache render: QImage untuk heatmap (2.3) + path candle di-*build* sekali per data-version, digambar
  per frame (bukan recompute geometri tiap paint).
- Throttle repaint ke **60fps** via single-shot coalescer (sudah ada pattern di DOM panel — angkat ke canvas).
- Target: pan/zoom 4 jam data (±15k bar visible window) tetap 60fps di iGPU laptop.

---

## FASE 6 — Polish & Konsistensi (½ hari)

- Empty-state elegan: chart kosong → watermark logo samar + hint "Right-click → Load history".
- Skeleton shimmer 1 dtk saat restore cache sebelum data pertama masuk.
- Uniform cursor pointer pada elemen interaktif; tooltip QSS senada; focus-ring keyboard (a11y).
- Pass akhir: screenshot diff semua panel vs checklist "rasa pro" (tipografi, radius, bayangan, spasi).

---

## Urutan Eksekusi & Estimasi

| Sprint | Isi | Estimasi | Dampak visual |
|---|---|---|---|
| **P1** | Fase 1 (token, tabular, HiDPI) + 2.4 crosshair | 0.5–1 hari | ★★★★★ |
| **P2** | Fase 2 (candle, grid, heatmap silky) | 1–1.5 hari | ★★★★★ |
| **P3** | Fase 3 (ladder two-sided + animasi) | 1 hari | ★★★★ |
| **P4** | Fase 4 (chrome, toast, status bar) | 1 hari | ★★★ |
| **P5** | Fase 5 (performa) | 0.5–1 hari | ★★★ (feels premium) |
| **P6** | Fase 6 (polish) | 0.5 hari | ★★ |

**Total ≈ 4,5–6 hari kerja.** Setiap fase di-commit terpisah + tes regresi visual (screenshot offscreen
di-diff piksel terhadap golden image) agar tidak merusak 65 tes yang sudah hijau.

## Risiko & Mitigasi
- *PySide6 fallback tanpa GUI* (mesin CI/Linux): semua perubahan guarded `HAS_PYSIDE`, golden-image test hanya jalan bila PySide6 tersedia.
- *Animasi lebar bar DOM* bisa membebani saat tick-rate tinggi → matikan animasi bila >20 update/dtk (auto-degrade).
- *Font mono Windows*: fallback stack `Cascadia Mono → Consolas → monospace` agar aman di semua mesin.
