# SQL Server Docker Manager

Aplikasi GUI desktop untuk mengelola database SQL Server yang berjalan di container Docker. Mendukung backup, restore, copy, dan hapus database tanpa perlu membuka terminal.

![Platform](https://img.shields.io/badge/platform-Linux-blue)
![Python](https://img.shields.io/badge/python-3.8+-blue)
![PyQt5](https://img.shields.io/badge/PyQt5-5.15+-green)

## Fitur

- **Backup Database** — Backup database SQL Server ke file `.bak` di folder lokal
- **Restore Database** — Restore database dari file `.bak` lokal ke container (auto-detect logical files)
- **Copy Database** — Copy database dalam satu container dengan nama baru (backup + restore otomatis)
- **Hapus Database** — Hapus database dari container dengan konfirmasi ketik ulang nama
- **Installer Bawaan** — Jalankan sekali, aplikasi terinstall ke sistem
- **Multi Container** — Deteksi otomatis semua container SQL Server yang berjalan
- **Progress & Log** — Progress bar dan log real-time selama proses backup/restore/copy

## Tangkapan Layar

<img width="760" alt="Aplikasi" src="https://github.com/user-attachments/assets/ab6ca5b8-5f20-4f9a-a9b8-2a6a17185e16" />

## Persyaratan Sistem

- **OS**: Linux x86_64 (Arch, EndeavourOS, Manjaro, Fedora, Ubuntu 22.04+, Debian 12+)
- **Docker**: Docker Engine sudah terinstall dan berjalan
- **Container**: SQL Server container sudah berjalan (image `mcr.microsoft.com/mssql/server`)

## Instalasi

### Opsi 1 — Unduh Executable (Recommended)
Unduh file executable di https://github.com/wicky14/SQLServerDocker/releases
klik 2x pada file unduhan
atau
**Install:**
```bash
wget https://github.com/wicky14/SQLServerDocker/releases/latest/download/MSSQL-Docker-Manager
chmod +x MSSQL-Docker-Manager
./MSSQL-Docker-Manager
# -> Klik "Ya" untuk install ke ~/.local/
# -> Aplikasi muncul di menu (KDE/GNOME)
# -> File download bisa dihapus
```

**Uninstall:** jalankan ulang file download, klik "Ya" untuk hapus.

**Update:** hapus dulu via uninstall, lalu jalankan download baru.

### Opsi 2 — Jalankan dari Source

```bash
git clone https://github.com/wicky14/SQLServerDocker.git
cd SQLServerDocker
pip install -r requirements.txt
python main.py
```

### Opsi 3 — Build Executable Sendiri

```bash
./build.sh
# Hasil: dist/MSSQL-Docker-Manager
```

## Cara Penggunaan

### 1. Koneksi ke Container

1. Aplikasi akan otomatis mendeteksi container SQL Server yang berjalan
2. Pilih container dari dropdown
3. Masukkan **password SA** SQL Server
4. Klik **Connect**
5. Daftar database akan muncul setelah koneksi berhasil

### 2. Backup Database

1. Pilih database dari list
2. Klik **Backup Database**
3. Pilih lokasi penyimpanan file `.bak`
4. Tunggu hingga selesai (progress bar + log)
5. File backup tersimpan di folder yang dipilih

### 3. Restore Database

1. Klik **Restore Database**
2. Pilih file `.bak` dari folder lokal
3. Aplikasi akan membaca struktur file backup
4. Konfirmasi nama database (bisa diganti)
5. Klik **Restore Sekarang**

### 4. Copy Database

1. Pilih database sumber dari list
2. Klik **Copy Database**
3. Masukkan nama database baru
4. Proses backup + restore otomatis berjalan
5. Database baru muncul di list

### 5. Hapus Database

1. Pilih database dari list
2. Klik **Hapus Database**
3. Ketik ulang nama database untuk konfirmasi
4. Database akan dihapus permanen

## Konfigurasi

File `config.json` dibuat otomatis di:
- **Setelah install**: `~/.config/sqlserver-docker-manager/config.json`
- **Jalankan dari source**: direktori proyek

```json
{
    "backup_dir": "~/backups/mssql",
    "containers": [
        {
            "name": "sql1",
            "sa_password": "",
            "container_backup_dir": "/var/opt/mssql/backup"
        }
    ]
}
```

| Field | Deskripsi |
|---|---|
| `backup_dir` | Folder default untuk menyimpan file backup |
| `containers` | Daftar container yang dikenal |
| `name` | Nama container Docker |
| `sa_password` | Password SA SQL Server (disimpan setelah connect) |
| `container_backup_dir` | Direktori backup sementara di dalam container |

> **Catatan**: Gunakan `config.example.json` sebagai template.

## Struktur Proyek

```
SQLServerDocker/
├── main.py                 # Entry point aplikasi
├── config.example.json     # Contoh konfigurasi
├── requirements.txt        # Dependensi Python
├── build.sh                # Script build executable
├── .gitignore              # Ignore config.json, dist/, build/
├── app/
│   ├── __init__.py
│   ├── icon/
│   │   └── icon.png        # Icon aplikasi
│   ├── installer.py        # Install/uninstall wizard
│   ├── main_window.py      # GUI PyQt5
│   ├── docker_ops.py       # Operasi Docker (exec, cp, ps)
│   ├── sql_ops.py          # Operasi sqlcmd (backup, restore, dll)
│   └── workers.py          # QThread untuk background task
└── dist/
    └── MSSQL-Docker-Manager # Executable (hasil build, di .gitignore)
```

## Troubleshooting

**"Docker tidak ditemukan"**
Pastikan Docker Engine sudah terinstall dan service berjalan:
```bash
sudo systemctl start docker
```

**"sqlcmd tidak ditemukan di container"**
Pastikan container menggunakan image resmi Microsoft SQL Server:
```bash
docker pull mcr.microsoft.com/mssql/server:2022-latest
```

**"Koneksi gagal"**
- Pastikan container sedang berjalan: `docker ps`
- Pastikan password SA benar
- Untuk container baru, password SA ditentukan saat pertama kali container dibuat

## Lisensi

[MIT](LICENSE)
