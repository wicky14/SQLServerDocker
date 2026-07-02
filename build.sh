#!/bin/bash
# Build script untuk SQL Server Docker Manager
# Menghasilkan executable satu file

set -e

# Auto virtual environment
if [ ! -d "venv" ]; then
    echo "Membuat virtual environment..."
    python -m venv venv
fi
source venv/bin/activate

echo "Memeriksa dependensi..."
pip install -r requirements.txt -q

echo "Membersihkan build sebelumnya..."
rm -rf build dist *.spec

echo "Membangun executable dengan PyInstaller..."
pyinstaller \
    --onefile \
    --windowed \
    --name "MSSQL-Docker-Manager" \
    --add-data "config.json:." \
    --add-data "app/icon/icon.png:app/icon" \
    --distpath dist \
    main.py

echo ""
echo "Build selesai!"
echo "Executable: dist/MSSQL-Docker-Manager"
echo ""
echo "Cara menjalankan:"
echo "  ./dist/MSSQL-Docker-Manager"
echo ""
echo "Atau install ke sistem:"
echo "  sudo cp dist/MSSQL-Docker-Manager /usr/local/bin/"
