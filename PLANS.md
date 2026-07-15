# Rencana Fitur

## 0. Integrasi Main Window

Dua tombol baru di grid Aksi: **Query** + **Table Browser**.

### Layout Aksi (final)

```
┌─ Aksi ─────────────────────────────────────┐
│ [Backup]        [Restore]                   │  row 0
│ [Export]        [Import]                    │  row 1
│ [Copy]          [Drop]                      │  row 2
│ [Query]         [Table Browser]             │  row 3
└────────────────────────────────────────────┘
```

### Behaviour

| Tombol | Disabled saat | Terbuka saat klik |
|--------|--------------|-------------------|
| `[Query]` | `not self._connected` | `QueryDialog(self, ...)` modal |
| `[Table Browser]` | `not self._connected` | `TableBrowserDialog(self, ...)` modal |

### Update di `main_window.py`

- `action_layout.addWidget(self.query_btn, 3, 0)`
- `action_layout.addWidget(self.table_browser_btn, 3, 1)`
- Set `setEnabled(False)` awal; enable di `_connect_completed` bersama tombol lain
- `_open_query_dialog` → `QueryDialog(self, container_name, password)`
- `_open_table_browser` → `TableBrowserDialog(self, container_name, password)`

---

## 1. Query Runner

Dialog SQL editor dengan fitur lengkap.

### Layout

```
┌───────────── SQL Query ────────────────────────────┐
│ [+] [📂] [💾] [⟳]   DB: [GaplekDB ▼]        [▶] │
├─┬──────────────────────────────────────────────────┤
│ │ SELECT                                            │
│ │   i.Nama, i.Harga                                 │
│ │ FROM                                              │
│ │   Item i                                          │
│ │ WHERE                                             │
│ │   i.Harga > 10000                                 │
│ │ ORDER BY                                          │
│ │   i.Harga DESC                                    │
│ │ ── auto-complete ────────────                     │
│ │ SELECT / FROM / WHERE / JOIN / AND / OR            │
│ └───[F5 / Ctrl+Enter ▶ Run]────────────────────────┤
│                                                      │
│ ✅ 42 rows returned (0.23s)                         │
│                                                      │
│ ┌────┬──────────┬───────┬──────┬───────────────────┐│
│ │ ID │ Nama     │ Harga │ Stok │ ...               ││
│ ├────┼──────────┼───────┼──────┼───────────────────┤│
│ │ 1  │ Beras    │ 12000 │ 500  │                   ││
│ │ 2  │ Gula     │ 14000 │ 200  │                   ││
│ └────┴──────────┴───────┴──────┴───────────────────┘│
└──────────────────────────────────────────────────────┘
```

### Tab System

- Setiap tab = `QSplitter(QPlainTextEdit (editor), QTableWidget (result))`
- Tab bar di kiri/vertical (atau kotak biasa)
- Bisa buka banyak query bersamaan
- Close tab → konfirmasi jika ada perubahan (dirty flag)

### Toolbar

| Icon | Action |
|------|--------|
| `+` | New tab |
| `📂` | Open .sql file → isi editor tab aktif |
| `💾` | Save as → simpan isi editor tab aktif |
| `⟳` | Refresh metadata (reload daftar tabel/kolom untuk completer) |
| Combo | Database picker (isi dari container yang terhubung) |
| `▶` | Run query (shortcut: F5 / Ctrl+Enter) |

### SqlHighlighter

Syntax highlight untuk `QPlainTextEdit`:
- **Keywords** (biru): SELECT, FROM, WHERE, JOIN, ON, AND, OR, INSERT, UPDATE, DELETE, SET, INTO, VALUES, CREATE, ALTER, DROP, INDEX, VIEW, PROCEDURE, FUNCTION, TRIGGER, AS, IN, NOT, NULL, IS, LIKE, BETWEEN, INNER, LEFT, RIGHT, OUTER, CROSS, HAVING, GROUP, ORDER, BY, ASC, DESC, TOP, DISTINCT, CASE, WHEN, THEN, ELSE, END, BEGIN, COMMIT, ROLLBACK, DECLARE, SET, PRINT, RETURN, EXEC, IF, ELSE, WHILE, EXISTS, UNION, ALL, WITH, CAST, CONVERT, COALESCE, NULLIF
- **Strings** (merah tua): `'...'`, `N'...'`
- **Comments** (hijau): `-- ...`, `/* ... */`
- **Numbers** (oranye): integer dan float literals
- Case-insensitive

### SqlCompleter

- **Trigger**: `Ctrl+Space` manual, atau jeda ketik 500ms (opsional)
- **Model**: `QCompleter` dengan `QStringListModel`
- **Mode 1** — SQL keywords: muncul saat awal baris atau setelah spasi di luar FROM/JOIN/WHERE
- **Mode 2** — Table/column names: muncul setelah FROM, JOIN, WHERE, ON, ORDER BY, GROUP BY
- Metadata tabel/kolom di-load saat dialog dibuka (query `INFORMATION_SCHEMA.TABLES` + `INFORMATION_SCHEMA.COLUMNS`)
- Popup bisa dipilih dengan ↑↓ + Enter, atau dicancel dengan Escape

### Result Grid

- `QTableWidget` read-only, column count dan header sesuai hasil query
- Auto-resize kolom (stretch)
- Row count dibatasi display (misal 10rb, ada label "Showing first 10000 rows")

### Info Bar

- **Success**: `"✅ 42 rows returned (0.23s)"`
- **Error**: `"❌ Msg 208, Level 16, State 1, Line 1: Invalid object name 'foo'."` (merah)
- **No rows**: `"✅ Query executed successfully (0.02s)"`
- **Timeout**: `"❌ Query timeout (> 60s)"` — jalankan di thread terpisah dengan timeout

### Edge Cases

| Situasi | Handling |
|---------|----------|
| Empty editor | Disable tombol Run |
| Query error | Info bar merah, grid kosong |
| No result set | Info bar sukses, grid kosong |
| >10k rows | Limit FETCH NEXT di SQL + label peringatan |
| Query >60s | timeout, info bar merah |
| Container disconnect | Dialog tertutup (detect di run query) |
| Tab close with changes | Confirm dialog "Save changes?" |
| Multi-statement (USE, GO) | Split on GO, execute per batch |

### Komponen

| Komponen | File | Estimasi |
|----------|------|----------|
| SqlHighlighter | `app/sql_highlighter.py` | 2-3 jam |
| SqlCompleter | `app/query_dialog.py` (inline) | 4-6 jam |
| QueryTab (editor + result + splitter) | `app/query_dialog.py` | 2 jam |
| QueryDialog (tab widget + toolbar) | `app/query_dialog.py` | 3-4 jam |
| Integrasi main_window | `app/main_window.py` | 30 menit |
| **Total** | | **~12-16 jam** |

---

## 2. Table Browser

Dialog eksplorasi objek database secara visual.

### Layout

```
┌─────────── Table Browser ──────────── [GaplekDB] ───┐
│                                                        │
│ ┌─ Explorer ────────────┐  ┌─ Preview ───────────────┐│
│ │                        │  │                         ││
│ │  📋 Tables (20)       │  │ WHERE [______________]  ││
│ │    ├─ Adjustment       │  │ ORDER [______________]  ││
│ │    ├─ Item             │  │              [⟳ Load]  ││
│ │    └─ ...              │  │                         ││
│ │                        │  │ ┌──┬──────┬──────┬───┐ ││
│ │  👁 Views (2)         │  │ │ID│Nama↑ │Harga↓│...│ ││
│ │    ├─ getStock         │  │ ├──┼──────┼──────┼───┤ ││
│ │    └─ getStockFull     │  │ │1 │Beras │12000 │   │ ││
│ │                        │  │ │2 │Gula  │14000 │   │ ││
│ │  ⚙ Procedures (30)    │  │ └──┴──────┴──────┴───┘ ││
│ │                        │  │ 42 rows (0.02s)       ││
│ │  🔧 Functions (5)     │  │ [📥 CSV] [📥 XLSX]    ││
│ │                        │  └────────────────────────┘│
│ │  🚀 Triggers (0)      │                           │
│ └────────────────────────┘                           │
└──────────────────────────────────────────────────────┘
```

### Object Tree (kiri)

- `QTreeWidget` dengan root nodes: Tables, Views, Procedures, Functions, Triggers
- Tiap root punya count di label: `Tables (20)`
- Query `INFORMATION_SCHEMA.ROUTINES` untuk SP + function + trigger definition
- Loading dilakukan sekali saat dialog dibuka. Refresh manual via konteks menu atau tombol.

### Behaviour per tipe klik

| Object | Panel Preview | WHERE/ORDER bar | Export |
|--------|--------------|-----------------|--------|
| **Table** | Data grid (`QTableWidget`) | ✅ Aktif | ✅ CSV / XLSX |
| **View** | Data grid (`QTableWidget`) | ✅ Aktif | ✅ CSV / XLSX |
| **SP** | Definisi read-only (`QPlainTextEdit`) | ❌ Disabled / Hidden | ❌ |
| **Function** | Definisi read-only (`QPlainTextEdit`) | ❌ Disabled / Hidden | ❌ |
| **Trigger** | Definisi read-only (`QPlainTextEdit`) | ❌ Disabled / Hidden | ❌ |

### WHERE / ORDER BY

- Input teks bebas, contoh:
  - `WHERE`: `NamaBarang LIKE '%beras%' AND Harga > 10000`
  - `ORDER`: `Harga DESC, Nama ASC`
- Kosongkan untuk SELECT semua
- Header grid bisa diklik → update ORDER BY + reload otomatis
- Tombol `⟳ Load` untuk reload manual

### SQL yang dihasilkan

```sql
SELECT * FROM [dbo].[Item]
WHERE NamaBarang LIKE '%beras%'
ORDER BY Harga DESC
OFFSET 0 ROWS FETCH NEXT 100 ROWS ONLY;
```

### Sort Header

- First click → ASC (↑)
- Second click → DESC (↓)
- Jika kolom sudah ada di ORDER BY, update direction
- Jika kolom baru, tambah ke ORDER BY (ganti yang lama)
- Auto-reload setelah perubahan

### Export

- **CSV**: langsung dari QTableWidget → `.csv`, pakai `csv.writer` (no dependency)
- **XLSX**: pakai `openpyxl` → perlu tambah ke `requirements.txt`

### Edge Cases

| Situasi | Handling |
|---------|----------|
| Table/view dengan 0 rows | Grid kosong, "0 rows" |
| Column names dengan spasi/special chars | Wrap di `[]` di SQL |
| SP/func/trigger besar | QPlainTextEdit scroll saja, tanpa limit |
| WHERE injection | Tidak ada — user sendiri yang nulis WHERE, SQL mentah |
| Container disconnect | Dialog tertutup (detect di load/sort/export) |
| Export cancel | File dialog cancel → no-op |

### Komponen

| Komponen | File | Estimasi |
|----------|------|----------|
| Dialog utama + tree + grid | `app/table_browser.py` | 4-5 jam |
| WHERE/ORDER input + sort header | — (dalam dialog) | 1 jam |
| Definisi panel (SP/Func/Trigger) | — (dalam dialog) | 1 jam |
| Export CSV | — (dalam dialog) | 30 menit |
| Export XLSX (openpyxl) | — (dalam dialog) | 1 jam |
| Integrasi main_window | `app/main_window.py` | 30 menit |
| **Total** | | **~8-10 jam** |

---

## 3. File Plan

| File | Tindakan | Isi |
|------|----------|-----|
| `app/sql_highlighter.py` | **Baru** | QSyntaxHighlighter subclass, SQL keyword/string/number/comment rules |
| `app/query_dialog.py` | **Baru** | QueryDialog (QDialog), SqlCompleter, tab system, result grid, toolbar, info bar |
| `app/table_browser.py` | **Baru** | TableBrowserDialog, QTreeWidget explorer, preview panel, WHERE/ORDER bar, export CSV/XLSX |
| `app/main_window.py` | **Edit** | +2 tombol di grid Aksi row 3, slot `_open_query_dialog`, `_open_table_browser`, disabled state |
| `requirements.txt` | **Edit** | + `openpyxl` untuk XLSX export |

---

## 4. Prioritas

1. **Query Runner** (~12-16 jam) — fitur paling berdampak
2. **Table Browser** (~8-10 jam) — pelengkap visual

Keduanya independen — bisa dikerjakan terpisah.
