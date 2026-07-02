import os
import datetime

from PyQt5.QtCore import QThread, pyqtSignal

from .docker_ops import DockerOps, DockerExecError, DockerNotAvailableError
from .sql_ops import SqlOps


class ListContainersWorker(QThread):
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def run(self):
        try:
            if not DockerOps.is_available():
                self.error.emit(
                    "Docker tidak ditemukan. Pastikan Docker sudah terinstall "
                    "dan tersedia di PATH."
                )
                return
            containers = DockerOps.list_sql_containers()
            self.finished.emit(containers)
        except DockerNotAvailableError as e:
            self.error.emit(str(e))
        except Exception as e:
            self.error.emit("Gagal mendeteksi container: {}".format(str(e)))


class ListDatabasesWorker(QThread):
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, container, password):
        super().__init__()
        self.container = container
        self.password = password

    def run(self):
        try:
            sql = SqlOps(self.container, self.password)
            if not sql.test_connection():
                self.error.emit(
                    "Gagal terhubung ke SQL Server di container '{}'.\n"
                    "Periksa password SA.".format(self.container)
                )
                return
            dbs = sql.list_databases()
            self.finished.emit(dbs)
        except DockerExecError as e:
            self.error.emit(str(e))
        except Exception as e:
            self.error.emit("Gagal mengambil daftar database: {}".format(str(e)))


class TestConnectionWorker(QThread):
    finished = pyqtSignal(bool, str)

    def __init__(self, container, password):
        super().__init__()
        self.container = container
        self.password = password

    def run(self):
        try:
            sql = SqlOps(self.container, self.password)
            ok = sql.test_connection()
            self.finished.emit(ok, self.container)
        except DockerExecError as e:
            self.finished.emit(False, self.container)


class BackupWorker(QThread):
    progress = pyqtSignal(str, int)
    finished = pyqtSignal(bool, str)

    def __init__(self, container, password, database, local_dir, container_backup_dir):
        super().__init__()
        self.container = container
        self.password = password
        self.database = database
        self.local_dir = local_dir
        self.container_backup_dir = container_backup_dir

    def run(self):
        try:
            sql = SqlOps(self.container, self.password)
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            bak_filename = "{}_{}.bak".format(self.database, ts)
            local_path = os.path.join(self.local_dir, bak_filename)

            self.progress.emit("Membuat direktori backup di container...", 5)
            DockerOps.mkdir(self.container, self.container_backup_dir)

            self.progress.emit("Menjalankan backup database '{}'...".format(self.database), 20)
            bak_path_in_container = sql.backup_database(
                self.database, self.container_backup_dir, bak_filename
            )

            self.progress.emit(
                "Backup selesai di container. Menyalin ke lokal...", 70
            )
            DockerOps.copy_from_container(
                self.container, bak_path_in_container, local_path
            )

            self.progress.emit("Membersihkan file backup dari container...", 90)
            try:
                DockerOps.remove_file(self.container, bak_path_in_container)
            except DockerExecError:
                pass

            self.progress.emit(
                "Backup selesai! File: {}".format(local_path), 100
            )
            self.finished.emit(True, local_path)

        except DockerExecError as e:
            self.finished.emit(False, str(e))
        except Exception as e:
            self.finished.emit(False, "Error: {}".format(str(e)))


class LoadFileListWorker(QThread):
    finished = pyqtSignal(list, str, str)
    error = pyqtSignal(str)

    def __init__(self, container, password, bak_path):
        super().__init__()
        self.container = container
        self.password = password
        self.bak_path = bak_path

    def run(self):
        try:
            sql = SqlOps(self.container, self.password)
            bak_filename = os.path.basename(self.bak_path)
            container_tmp = "/var/opt/mssql/backup/{}".format(bak_filename)

            DockerOps.mkdir(self.container, "/var/opt/mssql/backup")
            DockerOps.copy_to_container(self.container, self.bak_path, container_tmp)

            files = sql.get_filelistonly(container_tmp)
            if not files:
                self.error.emit(
                    "Tidak dapat membaca struktur file backup. "
                    "Pastikan file .bak valid."
                )
                return

            db_name = files[0]["logical_name"]
            self.finished.emit(files, db_name, container_tmp)

        except DockerExecError as e:
            self.error.emit(str(e))
        except Exception as e:
            self.error.emit("Error membaca file backup: {}".format(str(e)))


class RestoreWorker(QThread):
    progress = pyqtSignal(str, int)
    finished = pyqtSignal(bool, str)

    def __init__(self, container, password, bak_path_in_container,
                 database, logical_files):
        super().__init__()
        self.container = container
        self.password = password
        self.bak_path_in_container = bak_path_in_container
        self.database = database
        self.logical_files = logical_files

    def run(self):
        try:
            sql = SqlOps(self.container, self.password)

            self.progress.emit("Merestore database '{}'...".format(self.database), 20)
            sql.restore_database(
                self.database, self.bak_path_in_container, self.logical_files
            )

            self.progress.emit("Membersihkan file backup dari container...", 90)
            try:
                DockerOps.remove_file(self.container, self.bak_path_in_container)
            except DockerExecError:
                pass

            self.progress.emit(
                "Restore database '{}' selesai!".format(self.database), 100
            )
            self.finished.emit(True, self.database)

        except DockerExecError as e:
            self.finished.emit(False, str(e))
        except Exception as e:
            self.finished.emit(False, "Error: {}".format(str(e)))


class DropDatabaseWorker(QThread):
    finished = pyqtSignal(bool, str)

    def __init__(self, container, password, database):
        super().__init__()
        self.container = container
        self.password = password
        self.database = database

    def run(self):
        try:
            sql = SqlOps(self.container, self.password)
            sql.drop_database(self.database)
            self.finished.emit(True, self.database)
        except DockerExecError as e:
            self.finished.emit(False, str(e))
        except Exception as e:
            self.finished.emit(False, "Error: {}".format(str(e)))


class CopyDatabaseWorker(QThread):
    progress = pyqtSignal(str, int)
    finished = pyqtSignal(bool, str)

    def __init__(self, container, password, source_db, new_db,
                 container_backup_dir):
        super().__init__()
        self.container = container
        self.password = password
        self.source_db = source_db
        self.new_db = new_db
        self.container_backup_dir = container_backup_dir

    def run(self):
        try:
            sql = SqlOps(self.container, self.password)

            self.progress.emit("Membackup database '{}'...".format(self.source_db), 20)
            sql.copy_database(
                self.source_db, self.new_db, self.container_backup_dir
            )

            self.progress.emit(
                "Database '{}' berhasil di-copy ke '{}'.".format(
                    self.source_db, self.new_db
                ), 100
            )
            self.finished.emit(True, self.new_db)

        except DockerExecError as e:
            self.finished.emit(False, str(e))
        except Exception as e:
            self.finished.emit(False, "Error: {}".format(str(e)))
