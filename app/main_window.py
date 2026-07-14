import os
import sys
import json
import datetime

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QComboBox, QLineEdit, QPushButton, QListWidget,
    QProgressBar, QTextEdit, QLabel, QGroupBox,
    QMessageBox, QFileDialog, QDialog, QDialogButtonBox, QInputDialog,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QSplitter, QFrame, QCheckBox, QGridLayout, QSizePolicy,
    QApplication, QStyle
)
from PyQt5.QtCore import Qt, QTimer, QSize, QUrl
from PyQt5.QtGui import QFont, QIcon
from PyQt5.QtGui import QDesktopServices

from .workers import (
    ListContainersWorker, ListDatabasesWorker, TestConnectionWorker,
    BackupWorker, LoadFileListWorker, RestoreWorker,
    DropDatabaseWorker, CopyDatabaseWorker,
    ExportDowngradeWorker, ImportDowngradeWorker
)
from .docker_ops import DockerOps, DockerExecError


def _get_config_path():
    xdg = os.path.join(os.path.expanduser("~/.config/sqlserver-docker-manager"), "config.json")
    if not getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        local = os.path.join(exe_dir, "config.json")
        if os.path.exists(local):
            return local
    return xdg


CONFIG_FILE = _get_config_path()


def load_config():
    default = {
        "backup_dir": os.path.expanduser("~/backups/mssql"),
        "containers": [
            {"name": "sql1", "sa_password": "", "container_backup_dir": "/var/opt/mssql/backup"}
        ]
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                cfg = json.load(f)
            cfg.setdefault("backup_dir", default["backup_dir"])
            cfg.setdefault("containers", default["containers"])
            return cfg
        except (json.JSONDecodeError, IOError):
            pass
    return default


def save_config(cfg):
    path = CONFIG_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "w") as f:
            json.dump(cfg, f, indent=4)
    except IOError:
        pass


class BackupDialog(QDialog):
    def __init__(self, parent, container, database, default_dir):
        super().__init__(parent)
        self.container = container
        self.database = database
        self.save_path = os.path.join(
            default_dir,
            "{}_{}.bak".format(database, datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
        )
        self.setWindowTitle("Backup Database")
        self.setMinimumWidth(500)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        info = QLabel(
            "Container: {}   Database: {}".format(self.container, self.database)
        )
        info.setStyleSheet("font-weight: bold;")
        layout.addWidget(info)

        path_layout = QHBoxLayout()
        path_layout.addWidget(QLabel("Simpan ke:"))
        self.path_edit = QLineEdit(self.save_path)
        self.path_edit.setReadOnly(False)
        path_layout.addWidget(self.path_edit)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse)
        path_layout.addWidget(browse_btn)
        layout.addLayout(path_layout)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        cancel_btn = QPushButton("Batal")
        cancel_btn.clicked.connect(self.reject)
        self.backup_btn = QPushButton("Backup Sekarang")
        self.backup_btn.setDefault(True)
        self.backup_btn.clicked.connect(self.accept)
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(self.backup_btn)
        layout.addLayout(btn_layout)

    def _browse(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Simpan Backup", self.save_path, "Backup Files (*.bak)"
        )
        if path:
            self.save_path = path
            self.path_edit.setText(path)


class RestoreConfirmDialog(QDialog):
    def __init__(self, parent, container, bak_file, logical_files, db_name):
        super().__init__(parent)
        self.container = container
        self.bak_file = bak_file
        self.logical_files = logical_files
        self.db_name = db_name
        self.new_db_name = db_name
        self.setWindowTitle("Konfirmasi Restore")
        self.setMinimumWidth(550)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        info = QLabel(
            "Container: {}   File: {}".format(self.container, os.path.basename(self.bak_file))
        )
        info.setStyleSheet("font-weight: bold;")
        layout.addWidget(info)

        layout.addWidget(QLabel("Nama database (bisa diganti):"))
        self.name_edit = QLineEdit(self.db_name)
        layout.addWidget(self.name_edit)

        layout.addWidget(QLabel("File yang akan di-restore:"))
        table = QTableWidget()
        table.setColumnCount(3)
        table.setHorizontalHeaderLabels(["Logical Name", "Physical Path", "Type"])
        table.setRowCount(len(self.logical_files))
        for i, f in enumerate(self.logical_files):
            t = "Data" if f["type"] == "D" else "Log" if f["type"] == "L" else "Secondary"
            table.setItem(i, 0, QTableWidgetItem(f["logical_name"]))
            table.setItem(i, 1, QTableWidgetItem(f["physical_name"]))
            table.setItem(i, 2, QTableWidgetItem(t))
        table.horizontalHeader().setStretchLastSection(True)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(table)

        warning = QLabel(
            "Akan merestore database '{}' ke container '{}'.\n"
            "Database yang sudah ada akan ditimpa.".format(self.db_name, self.container)
        )
        warning.setStyleSheet("color: #cc0000;")
        layout.addWidget(warning)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        cancel_btn = QPushButton("Batal")
        cancel_btn.clicked.connect(self.reject)
        self.restore_btn = QPushButton("Restore Sekarang")
        self.restore_btn.setDefault(True)
        self.restore_btn.clicked.connect(self.accept)
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(self.restore_btn)
        layout.addLayout(btn_layout)

    def get_new_db_name(self):
        return self.name_edit.text().strip()


class DropDatabaseDialog(QDialog):
    def __init__(self, parent, container, database):
        super().__init__(parent)
        self.container = container
        self.database = database
        self.setWindowTitle("Hapus Database")
        self.setMinimumWidth(450)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        info = QLabel(
            "Container: {}   Database: {}".format(self.container, self.database)
        )
        info.setStyleSheet("font-weight: bold;")
        layout.addWidget(info)

        warning = QLabel(
            "PERINGATAN: Tindakan ini akan menghapus database '{}' "
            "secara permanen.\nSemua data akan hilang dan tidak bisa "
            "dikembalikan.".format(self.database)
        )
        warning.setStyleSheet(
            "color: #cc0000; font-weight: bold; padding: 8px; "
            "border: 1px solid #cc0000; border-radius: 4px;"
        )
        warning.setWordWrap(True)
        layout.addWidget(warning)

        layout.addSpacing(8)
        layout.addWidget(
            QLabel("Ketik nama database untuk konfirmasi:")
        )
        self.confirm_input = QLineEdit()
        self.confirm_input.setPlaceholderText("Ketik '{}' di sini...".format(self.database))
        layout.addWidget(self.confirm_input)

        layout.addSpacing(8)
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        cancel_btn = QPushButton("Batal")
        cancel_btn.clicked.connect(self.reject)
        self.hapus_btn = QPushButton("Hapus Database")
        self.hapus_btn.setStyleSheet(
            "background-color: #cc0000; color: white; font-weight: bold;"
        )
        self.hapus_btn.setEnabled(False)
        self.hapus_btn.clicked.connect(self._confirm)
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(self.hapus_btn)
        layout.addLayout(btn_layout)

        self.confirm_input.textChanged.connect(
            lambda t: self.hapus_btn.setEnabled(t.strip() == self.database)
        )

    def _confirm(self):
        if self.confirm_input.text().strip() == self.database:
            self.accept()


class CopyDatabaseDialog(QDialog):
    def __init__(self, parent, container, source_db):
        super().__init__(parent)
        self.container = container
        self.source_db = source_db
        self.new_db = source_db + "_copy"
        self.setWindowTitle("Copy Database")
        self.setMinimumWidth(450)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        info = QLabel(
            "Container: {}   Database sumber: {}".format(self.container, self.source_db)
        )
        info.setStyleSheet("font-weight: bold;")
        layout.addWidget(info)

        layout.addWidget(QLabel("Nama database baru:"))
        self.name_input = QLineEdit(self.new_db)
        layout.addWidget(self.name_input)

        layout.addSpacing(8)
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        cancel_btn = QPushButton("Batal")
        cancel_btn.clicked.connect(self.reject)
        self.copy_btn = QPushButton("Copy Database")
        self.copy_btn.setDefault(True)
        self.copy_btn.clicked.connect(self.accept)
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(self.copy_btn)
        layout.addLayout(btn_layout)

    def get_new_db_name(self):
        return self.name_input.text().strip()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = load_config()
        self.containers = []
        self.databases = []
        self.current_container = None
        self.password = ""
        self._connected = False
        self.container_backup_dir = "/var/opt/mssql/backup"

        self.setWindowTitle("SQL Server Docker Manager")
        self.setMinimumSize(750, 600)

        self._build_ui()
        self._load_config_to_ui()
        self._refresh_containers()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(8)

        conn_group = QGroupBox("Koneksi")
        conn_layout = QHBoxLayout(conn_group)

        conn_layout.addWidget(QLabel("Container:"))
        self.container_combo = QComboBox()
        self.container_combo.setEditable(True)
        self.container_combo.setMinimumWidth(180)
        self.container_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        conn_layout.addWidget(self.container_combo)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self._refresh_containers)
        conn_layout.addWidget(self.refresh_btn)

        conn_layout.addWidget(QLabel("Password SA:"))
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.setMinimumWidth(150)
        conn_layout.addWidget(self.password_input)

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setDefault(True)
        self.connect_btn.clicked.connect(self._connect_container)
        conn_layout.addWidget(self.connect_btn)

        main_layout.addWidget(conn_group)

        content = QHBoxLayout()

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        db_group = QGroupBox("Database")
        db_layout = QVBoxLayout(db_group)
        self.db_list = QListWidget()
        self.db_list.setMinimumWidth(200)
        db_layout.addWidget(self.db_list)
        left_layout.addWidget(db_group)

        content.addWidget(left_widget, 1)

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        action_group = QGroupBox("Aksi")
        action_layout = QVBoxLayout(action_group)

        self.backup_btn = self._make_action_button(
            "Backup Database", "Pilih database lalu klik untuk backup"
        )
        self.backup_btn.clicked.connect(self._do_backup)
        action_layout.addWidget(self.backup_btn)

        self.restore_btn = self._make_action_button(
            "Restore Database", "Pilih file .bak lalu restore ke container"
        )
        self.restore_btn.clicked.connect(self._do_restore)
        action_layout.addWidget(self.restore_btn)

        self.copy_btn = self._make_action_button(
            "Copy Database", "Copy database dalam satu container dengan nama baru"
        )
        self.copy_btn.clicked.connect(self._do_copy_database)
        action_layout.addWidget(self.copy_btn)

        self.export_btn = self._make_action_button(
            "Export Cross-Version",
            "Export database sebagai schema + data (bcp) agar bisa di-restore ke versi SQL lebih rendah"
        )
        self.export_btn.clicked.connect(self._do_export_downgrade)
        action_layout.addWidget(self.export_btn)

        self.import_btn = self._make_action_button(
            "Import Cross-Version",
            "Import database dari hasil Export Cross-Version"
        )
        self.import_btn.clicked.connect(self._do_import_downgrade)
        action_layout.addWidget(self.import_btn)

        self.drop_btn = self._make_action_button(
            "Hapus Database", "Hapus database dari container"
        )
        self.drop_btn.clicked.connect(self._do_drop_database)
        action_layout.addWidget(self.drop_btn)

        right_layout.addWidget(action_group)

        folder_btn = QPushButton("Buka Folder Backup")
        folder_btn.clicked.connect(self._open_backup_folder)
        right_layout.addWidget(folder_btn)

        content.addWidget(right_widget, 1)
        main_layout.addLayout(content)

        progress_group = QGroupBox("Proses")
        progress_layout = QVBoxLayout(progress_group)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        progress_layout.addWidget(self.progress_bar)

        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setMaximumHeight(150)
        self.log_area.setMinimumHeight(80)
        self.log_area.setFont(QFont("Monospace", 9))
        progress_layout.addWidget(self.log_area)

        main_layout.addWidget(progress_group)

        self.status_label = QLabel("Siap. Klik Connect untuk terhubung ke container.")
        self.status_label.setStyleSheet("color: #555;")
        main_layout.addWidget(self.status_label)

    def _make_action_button(self, text, tooltip):
        btn = QPushButton(text)
        btn.setMinimumHeight(48)
        btn.setToolTip(tooltip)
        font = btn.font()
        font.setPointSize(13)
        font.setBold(True)
        btn.setFont(font)
        return btn

    def _load_config_to_ui(self):
        self.password_input.setText("")
        for c in self.config.get("containers", []):
            if c.get("sa_password"):
                idx = self.container_combo.findText(c["name"])
                if idx >= 0:
                    self.password_input.setText(c["sa_password"])
                    break

    def _refresh_containers(self):
        self._log("Mendeteksi container SQL Server...")
        self.status_label.setText("Mendeteksi container...")
        self.container_combo.clear()
        self.worker = ListContainersWorker()
        self.worker.finished.connect(self._on_containers_loaded)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _on_containers_loaded(self, containers):
        self.containers = containers
        known = set()
        for c in self.config.get("containers", []):
            known.add(c["name"])

        if not containers and not known:
            self._log("Tidak ada container SQL Server terdeteksi.")
            self.status_label.setText(
                "Tidak ada container SQL Server berjalan. Jalankan container terlebih dahulu."
            )
            return

        self.container_combo.clear()
        for c in containers:
            self.container_combo.addItem(c["name"])

        for name in known:
            found = any(c["name"] == name for c in containers)
            if not found:
                self.container_combo.addItem("{} (tidak running)".format(name))

        if self.container_combo.count() > 0:
            self.container_combo.setCurrentIndex(0)
            self._load_password_for_container(self.container_combo.currentText())

        self.status_label.setText(
            "Ditemukan {} container SQL Server. Pilih container dan klik Connect.".format(
                len(containers)
            )
        )

    def _load_password_for_container(self, name):
        clean_name = name.replace(" (tidak running)", "")
        for c in self.config.get("containers", []):
            if c["name"] == clean_name:
                if c.get("sa_password"):
                    self.password_input.setText(c["sa_password"])
                self.container_backup_dir = c.get(
                    "container_backup_dir", "/var/opt/mssql/backup"
                )
                return
        self.password_input.setText("")
        self.container_backup_dir = "/var/opt/mssql/backup"

    def _connect_container(self):
        if self._connected:
            self._disconnect_container()
            return

        container = self.container_combo.currentText().strip()
        container = container.replace(" (tidak running)", "")
        password = self.password_input.text().strip()

        if not container:
            QMessageBox.warning(self, "Peringatan", "Pilih atau masukkan nama container.")
            return
        if not password:
            QMessageBox.warning(self, "Peringatan", "Masukkan password SA.")
            return

        self.current_container = container
        self.password = password
        self._log("Menghubungkan ke container '{}'...".format(container))
        self.status_label.setText("Menghubungkan...")
        self.container_combo.setEnabled(False)
        self.password_input.setEnabled(False)
        self.refresh_btn.setEnabled(False)
        self.connect_btn.setText("Connecting...")
        self.connect_btn.setEnabled(False)

        self._save_container_password(container, password)

        self.worker = TestConnectionWorker(container, password)
        self.worker.finished.connect(self._on_connection_result)
        self.worker.start()

    def _disconnect_container(self):
        self.current_container = None
        self.password = ""
        self._connected = False
        self.container_combo.setEnabled(True)
        self.password_input.setEnabled(True)
        self.refresh_btn.setEnabled(True)
        self.connect_btn.setText("Connect")
        self.connect_btn.setEnabled(True)
        self.db_list.clear()
        self.status_label.setText("Terputus.")
        self._log("Terputus dari container.")

    def _save_container_password(self, name, password):
        for c in self.config["containers"]:
            if c["name"] == name:
                c["sa_password"] = password
                c["container_backup_dir"] = self.container_backup_dir
                save_config(self.config)
                return
        self.config["containers"].append({
            "name": name,
            "sa_password": password,
            "container_backup_dir": self.container_backup_dir
        })
        save_config(self.config)

    def _on_connection_result(self, ok, container):
        if ok:
            self._connected = True
            self.connect_btn.setText("Disconnect")
            self.connect_btn.setEnabled(True)
            self._log("Terhubung ke container '{}'.".format(container))
            self.status_label.setText("Terhubung ke '{}'. Mengambil daftar database...".format(container))
            self._load_databases()
        else:
            self.current_container = None
            self.password = ""
            self._connected = False
            self.container_combo.setEnabled(True)
            self.password_input.setEnabled(True)
            self.refresh_btn.setEnabled(True)
            self.connect_btn.setText("Connect")
            self.connect_btn.setEnabled(True)
            self._log("Gagal terhubung ke container '{}'. Periksa password.".format(container))
            self.status_label.setText("Koneksi gagal. Periksa password SA.")
            QMessageBox.warning(
                self, "Koneksi Gagal",
                "Tidak dapat terhubung ke SQL Server di container '{}'.\n"
                "Periksa:\n"
                "1. Container sedang berjalan\n"
                "2. Password SA benar\n"
                "3. Container menggunakan image MSSQL Server".format(container)
            )

    def _load_databases(self):
        self.db_list.clear()
        self.worker = ListDatabasesWorker(self.current_container, self.password)
        self.worker.finished.connect(self._on_databases_loaded)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _on_databases_loaded(self, databases):
        self.databases = databases
        self.db_list.clear()
        if not databases:
            self.db_list.addItem("(Tidak ada database user)")
            self._log("Tidak ada database user di container.")
            self.status_label.setText("Terhubung. Tidak ada database user.")
        else:
            for db in databases:
                self.db_list.addItem(db)
            self._log("{} database ditemukan.".format(len(databases)))
            self.status_label.setText(
                "Terhubung ke '{}'. {} database tersedia.".format(
                    self.current_container, len(databases)
                )
            )

    def _do_backup(self):
        if not self._check_connected():
            return

        selected = self.db_list.selectedItems()
        if not selected:
            QMessageBox.warning(self, "Peringatan", "Pilih database yang akan di-backup.")
            return

        database = selected[0].text()
        if database == "(Tidak ada database user)":
            return

        default_dir = os.path.expanduser(self.config.get("backup_dir", "~/backups/mssql"))
        os.makedirs(default_dir, exist_ok=True)

        dlg = BackupDialog(self, self.current_container, database, default_dir)
        if dlg.exec_() == QDialog.Accepted:
            local_dir = os.path.dirname(dlg.save_path)
            os.makedirs(local_dir, exist_ok=True)

            self._set_processing(True)
            self._log("Memulai backup database '{}'...".format(database))

            self.worker = BackupWorker(
                self.current_container, self.password,
                database, local_dir, self.container_backup_dir
            )
            self.worker.progress.connect(self._on_progress)
            self.worker.finished.connect(self._on_backup_finished)
            self.worker.start()

    def _do_restore(self):
        if not self._check_connected():
            return

        default_dir = os.path.expanduser(self.config.get("backup_dir", "~/backups/mssql"))
        bak_path, _ = QFileDialog.getOpenFileName(
            self, "Pilih File Backup", default_dir, "Backup Files (*.bak)"
        )
        if not bak_path:
            return

        self._log("Membaca struktur file backup '{}'...".format(os.path.basename(bak_path)))
        self._set_processing(True)

        self.worker = LoadFileListWorker(
            self.current_container, self.password, bak_path
        )
        self.worker.finished.connect(self._on_filelist_loaded)
        self.worker.error.connect(self._on_restore_error)
        self.worker.start()
        self._tmp_bak_path = bak_path

    def _on_filelist_loaded(self, files, db_name, container_tmp_path):
        self._set_processing(False)
        self._tmp_container_path = container_tmp_path

        dlg = RestoreConfirmDialog(
            self, self.current_container, self._tmp_bak_path, files, db_name
        )
        if dlg.exec_() == QDialog.Accepted:
            new_db_name = dlg.get_new_db_name()

            reply = QMessageBox.warning(
                self, "Konfirmasi Restore",
                "Yakin ingin merestore database '{}'?\n\n"
                "Database yang sudah ada dengan nama yang sama "
                "akan ditimpa dan tidak bisa dikembalikan.".format(new_db_name),
                QMessageBox.Yes | QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                try:
                    DockerOps.remove_file(self.current_container, container_tmp_path)
                except Exception:
                    pass
                return

            self._log("Memulai restore database '{}'...".format(new_db_name))
            self._set_processing(True)

            self.worker = RestoreWorker(
                self.current_container, self.password,
                container_tmp_path, new_db_name, files
            )
            self.worker.progress.connect(self._on_progress)
            self.worker.finished.connect(self._on_restore_finished)
            self.worker.start()
        else:
            try:
                DockerOps.remove_file(self.current_container, container_tmp_path)
            except Exception:
                pass

    def _on_restore_error(self, msg):
        self._set_processing(False)
        self._log("Error: " + msg)
        self.status_label.setText("Restore gagal.")
        QMessageBox.critical(self, "Error", msg)

    def _do_drop_database(self):
        if not self._check_connected():
            return

        selected = self.db_list.selectedItems()
        if not selected:
            QMessageBox.warning(self, "Peringatan", "Pilih database yang akan dihapus.")
            return

        database = selected[0].text()
        if database == "(Tidak ada database user)":
            return

        dlg = DropDatabaseDialog(self, self.current_container, database)
        if dlg.exec_() == QDialog.Accepted:
            self._set_processing(True)
            self._log("Menghapus database '{}'...".format(database))

            self.worker = DropDatabaseWorker(
                self.current_container, self.password, database
            )
            self.worker.finished.connect(self._on_drop_finished)
            self.worker.start()

    def _do_copy_database(self):
        if not self._check_connected():
            return

        selected = self.db_list.selectedItems()
        if not selected:
            QMessageBox.warning(self, "Peringatan", "Pilih database yang akan di-copy.")
            return

        source_db = selected[0].text()
        if source_db == "(Tidak ada database user)":
            return

        dlg = CopyDatabaseDialog(self, self.current_container, source_db)
        if dlg.exec_() == QDialog.Accepted:
            new_db = dlg.get_new_db_name()
            if not new_db:
                QMessageBox.warning(self, "Peringatan", "Nama database baru tidak boleh kosong.")
                return
            if new_db == source_db:
                QMessageBox.warning(
                    self, "Peringatan",
                    "Nama database baru harus berbeda dari database sumber."
                )
                return

            self._set_processing(True)
            self._log("Mengcopy database '{}' ke '{}'...".format(source_db, new_db))

            self.worker = CopyDatabaseWorker(
                self.current_container, self.password,
                source_db, new_db, self.container_backup_dir
            )
            self.worker.progress.connect(self._on_progress)
            self.worker.finished.connect(self._on_copy_finished)
            self.worker.start()

    def _do_export_downgrade(self):
        if not self._check_connected():
            return

        selected = self.db_list.selectedItems()
        if not selected:
            QMessageBox.warning(self, "Peringatan", "Pilih database yang akan di-export.")
            return

        database = selected[0].text()
        if database == "(Tidak ada database user)":
            return

        default_dir = os.path.expanduser(self.config.get("backup_dir", "~/backups/mssql"))
        export_dir = QFileDialog.getExistingDirectory(
            self, "Pilih folder untuk menyimpan export", default_dir
        )
        if not export_dir:
            return

        db_folder = os.path.join(export_dir, "{}_{}".format(
            database, datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        ))
        os.makedirs(db_folder, exist_ok=True)

        self._set_processing(True)
        self._log("Memulai export cross-version database '{}'...".format(database))

        self.worker = ExportDowngradeWorker(
            self.current_container, self.password,
            database, db_folder, self.container_backup_dir
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_export_downgrade_finished)
        self.worker.start()

    def _on_export_downgrade_finished(self, success, result):
        self._set_processing(False)
        if success:
            self._log("Export cross-version selesai: " + result)
            self.status_label.setText("Export selesai.")
            QMessageBox.information(
                self, "Sukses",
                "Export berhasil!\nFolder: " + result
            )
        else:
            self._log("Export gagal: " + result)
            self.status_label.setText("Export gagal.")
            QMessageBox.critical(self, "Error", result)

    def _do_import_downgrade(self):
        if not self._check_connected():
            return

        default_dir = os.path.expanduser(self.config.get("backup_dir", "~/backups/mssql"))
        import_dir = QFileDialog.getExistingDirectory(
            self, "Pilih folder hasil export", default_dir
        )
        if not import_dir:
            return

        schema_file = None
        for f in os.listdir(import_dir):
            if f.endswith("_schema.sql"):
                schema_file = f
                break

        if not schema_file:
            QMessageBox.warning(self, "Error",
                "Folder harus berisi file *_schema.sql hasil Export Cross-Version.")
            return

        db_name = schema_file.replace("_schema.sql", "")

        new_db_name, ok = QInputDialog.getText(
            self, "Nama Database Baru",
            "Nama database tujuan:", text=db_name
        )
        if not ok or not new_db_name.strip():
            return
        new_db_name = new_db_name.strip()

        self._set_processing(True)
        self._log("Memulai import cross-version database '{}'...".format(new_db_name))

        self.worker = ImportDowngradeWorker(
            self.current_container, self.password,
            new_db_name, import_dir, self.container_backup_dir
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_import_downgrade_finished)
        self.worker.start()

    def _on_import_downgrade_finished(self, success, result):
        self._set_processing(False)
        if success:
            self._log("Import cross-version selesai: " + result)
            self.status_label.setText("Import selesai.")
            QMessageBox.information(
                self, "Sukses",
                "Import database '{}' berhasil!".format(result)
            )
            self._load_databases()
        else:
            self._log("Import gagal: " + result)
            self.status_label.setText("Import gagal.")
            QMessageBox.critical(self, "Error", result)

    def _open_backup_folder(self):
        backup_dir = os.path.expanduser(self.config.get("backup_dir", "~/backups/mssql"))
        os.makedirs(backup_dir, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(backup_dir))

    def _check_connected(self):
        if not self.current_container:
            QMessageBox.warning(
                self, "Peringatan",
                "Belum terhubung ke container.\n"
                "Pilih container dan klik Connect terlebih dahulu."
            )
            return False
        return True

    def _set_processing(self, busy):
        self.backup_btn.setEnabled(not busy)
        self.restore_btn.setEnabled(not busy)
        self.copy_btn.setEnabled(not busy)
        self.export_btn.setEnabled(not busy)
        self.import_btn.setEnabled(not busy)
        self.drop_btn.setEnabled(not busy)
        self.connect_btn.setEnabled(not busy)
        self.progress_bar.setVisible(busy)
        if busy:
            self.progress_bar.setValue(0)

    def _on_progress(self, message, percent):
        self._log(message)
        self.progress_bar.setValue(percent)

    def _on_backup_finished(self, success, result):
        self._set_processing(False)
        if success:
            self._log("Backup selesai: " + result)
            self.status_label.setText("Backup selesai.")
            QMessageBox.information(
                self, "Sukses",
                "Backup berhasil!\nFile: " + result
            )
            self._load_databases()
        else:
            self._log("Backup gagal: " + result)
            self.status_label.setText("Backup gagal.")
            QMessageBox.critical(self, "Error", result)

    def _on_restore_finished(self, success, result):
        self._set_processing(False)
        if success:
            self._log("Restore selesai: " + result)
            self.status_label.setText("Restore selesai.")
            QMessageBox.information(
                self, "Sukses",
                "Restore database '{}' berhasil!".format(result)
            )
            self._load_databases()
        else:
            self._log("Restore gagal: " + result)
            self.status_label.setText("Restore gagal.")
            QMessageBox.critical(self, "Error", result)

    def _on_drop_finished(self, success, result):
        self._set_processing(False)
        if success:
            self._log("Database '{}' berhasil dihapus.".format(result))
            self.status_label.setText("Database dihapus.")
            QMessageBox.information(
                self, "Sukses",
                "Database '{}' berhasil dihapus.".format(result)
            )
            self._load_databases()
        else:
            self._log("Gagal menghapus database: " + result)
            self.status_label.setText("Gagal menghapus database.")
            QMessageBox.critical(self, "Error", result)

    def _on_copy_finished(self, success, result):
        self._set_processing(False)
        if success:
            self._log("Database berhasil di-copy ke '{}'.".format(result))
            self.status_label.setText("Copy database selesai.")
            QMessageBox.information(
                self, "Sukses",
                "Database berhasil di-copy ke '{}'.".format(result)
            )
            self._load_databases()
        else:
            self._log("Gagal mengcopy database: " + result)
            self.status_label.setText("Gagal mengcopy database.")
            QMessageBox.critical(self, "Error", result)

    def _on_error(self, msg):
        self._log("Error: " + msg)
        self.status_label.setText("Terjadi error.")
        QMessageBox.critical(self, "Error", msg)

    def _log(self, message):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_area.append("[{}] {}".format(ts, message))
        max_lines = 500
        doc = self.log_area.document()
        if doc.blockCount() > max_lines:
            cursor = self.log_area.textCursor()
            cursor.movePosition(cursor.Start)
            cursor.movePosition(cursor.Down, cursor.KeepAnchor, doc.blockCount() - max_lines)
            cursor.removeSelectedText()
            cursor.deleteChar()

    def closeEvent(self, event):
        self.config["backup_dir"] = os.path.expanduser(
            self.config.get("backup_dir", "~/backups/mssql")
        )
        save_config(self.config)
        event.accept()
