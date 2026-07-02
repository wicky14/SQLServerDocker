import os
import sys
import shutil
import subprocess

from PyQt5.QtWidgets import QMessageBox


INSTALL_PREFIX = os.path.expanduser("~/.local")
BIN_DIR = os.path.join(INSTALL_PREFIX, "bin")
APP_DIR = os.path.join(INSTALL_PREFIX, "share", "applications")
ICON_DIRS = [
    os.path.join(INSTALL_PREFIX, "share", "icons", "hicolor", "scalable", "apps"),
    os.path.join(INSTALL_PREFIX, "share", "icons", "hicolor", "256x256", "apps"),
    os.path.join(INSTALL_PREFIX, "share", "pixmaps"),
]
CONFIG_DIR = os.path.expanduser("~/.config/sqlserver-docker-manager")
INSTALLED_BIN = os.path.join(BIN_DIR, "MSSQL-Docker-Manager")
INSTALLED_DESKTOP = os.path.join(APP_DIR, "sqlserver-docker-manager.desktop")


def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), relative_path)


def is_installed():
    return os.path.exists(INSTALLED_BIN) and os.path.exists(INSTALLED_DESKTOP)


def is_running_from_install():
    return os.path.abspath(sys.executable) == os.path.abspath(INSTALLED_BIN)


def handle():
    if not getattr(sys, 'frozen', False):
        return False

    if not is_installed():
        reply = QMessageBox.question(
            None, "Install SQL Server Docker Manager",
            "Aplikasi akan diinstall ke:\n"
            "  ~/.local/bin/\n"
            "  ~/.local/share/applications/\n"
            "  ~/.local/share/icons/\n\n"
            "Lanjutkan install?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return False

        _install()
        QMessageBox.information(
            None, "Install Berhasil",
            "SQL Server Docker Manager berhasil diinstall.\n\n"
            "Buka dari menu aplikasi (Kickoff/Overview)\n"
            "atau jalankan perintah:\n"
            "  MSSQL-Docker-Manager\n\n"
            "File download ini sudah tidak diperlukan."
        )
        return True

    if is_running_from_install():
        return False

    reply = QMessageBox.question(
        None, "SQL Server Docker Manager",
        "Aplikasi sudah terinstall.\n\n"
        "Ingin menghapus dari sistem?",
        QMessageBox.Yes | QMessageBox.No
    )
    if reply == QMessageBox.Yes:
        _uninstall()
        QMessageBox.information(
            None, "Uninstall Berhasil",
            "SQL Server Docker Manager berhasil dihapus dari sistem."
        )
        return True
    return False


def _install():
    os.makedirs(BIN_DIR, exist_ok=True)
    os.makedirs(APP_DIR, exist_ok=True)
    for d in ICON_DIRS:
        os.makedirs(d, exist_ok=True)

    shutil.copy2(sys.executable, INSTALLED_BIN)
    os.chmod(INSTALLED_BIN, 0o755)

    icon_src = resource_path("app/icon/icon.png")
    if os.path.exists(icon_src):
        for d in ICON_DIRS:
            shutil.copy2(icon_src, os.path.join(d, "sqlserver-docker-manager.png"))

    with open(INSTALLED_DESKTOP, "w") as f:
        f.write("[Desktop Entry]\n")
        f.write("Type=Application\n")
        f.write("Name=SQL Server Docker Manager\n")
        f.write("Comment=Kelola database SQL Server di Docker\n")
        f.write("Exec={}\n".format(INSTALLED_BIN))
        f.write("Icon=sqlserver-docker-manager\n")
        f.write("Terminal=false\n")
        f.write("Categories=Utility;Database;\n")
        f.write("StartupWMClass=MSSQL-Docker-Manager\n")

    try:
        subprocess.run(["xdg-desktop-menu", "forceupdate"],
                       capture_output=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    for d in ICON_DIRS:
        if os.path.isdir(d):
            try:
                subprocess.run(["gtk-update-icon-cache", os.path.dirname(os.path.dirname(d))],
                               capture_output=True, timeout=10)
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass


def _uninstall():
    if os.path.exists(INSTALLED_BIN):
        os.remove(INSTALLED_BIN)
    if os.path.exists(INSTALLED_DESKTOP):
        os.remove(INSTALLED_DESKTOP)
    for d in ICON_DIRS:
        ip = os.path.join(d, "sqlserver-docker-manager.png")
        if os.path.exists(ip):
            os.remove(ip)
    if os.path.exists(CONFIG_DIR):
        shutil.rmtree(CONFIG_DIR)

    try:
        subprocess.run(["xdg-desktop-menu", "forceupdate"],
                       capture_output=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
