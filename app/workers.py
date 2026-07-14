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
            DockerOps.exec_command(self.container, ["chmod", "644", container_tmp], user="0")

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


class ExportDowngradeWorker(QThread):
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
            export_dir = "{}/{}_export_{}".format(
                self.container_backup_dir, self.database,
                datetime.datetime.now().strftime("%Y%m%d%H%M%S")
            )

            self.progress.emit("Mendapatkan daftar tabel...", 5)
            tables = sql.get_user_tables(self.database)
            if not tables:
                self.finished.emit(False, "Tidak ada tabel user di database '{}'.".format(self.database))
                return

            self.progress.emit("Membuat direktori export di container...", 10)
            DockerOps.mkdir(self.container, export_dir)

            self.progress.emit("Menggenerate schema SQL...", 15)
            schema_file, data_files = sql.export_for_downgrade(self.database, tables, export_dir)

            total = len(data_files)
            for i, (schema, table, csv_path) in enumerate(data_files):
                pct = 20 + int((i / total) * 60)
                self.progress.emit(
                    "Mengexport data tabel [{0}].[{1}]... ({2}/{3})".format(
                        schema, table, i + 1, total
                    ), pct
                )

            self.progress.emit("Menyalin hasil export ke lokal...", 85)
            os.makedirs(self.local_dir, exist_ok=True)

            DockerOps.copy_from_container(self.container, schema_file,
                os.path.join(self.local_dir, os.path.basename(schema_file)))

            for schema, table, csv_path in data_files:
                local_csv = os.path.join(self.local_dir, os.path.basename(csv_path))
                DockerOps.copy_from_container(self.container, csv_path, local_csv)

            self.progress.emit("Membersihkan file temporary...", 95)
            try:
                DockerOps.remove_file(self.container, schema_file)
                for _, _, csv_path in data_files:
                    DockerOps.remove_file(self.container, csv_path)
                DockerOps.exec_command(self.container, ["rmdir", export_dir])
            except DockerExecError:
                pass

            self.progress.emit(
                "Export selesai! Tersimpan di: {}".format(self.local_dir), 100
            )
            self.finished.emit(True, self.local_dir)

        except DockerExecError as e:
            self.finished.emit(False, str(e))
        except Exception as e:
            self.finished.emit(False, "Error: {}".format(str(e)))


class ImportDowngradeWorker(QThread):
    progress = pyqtSignal(str, int)
    finished = pyqtSignal(bool, str)

    def __init__(self, container, password, database, import_dir, container_backup_dir):
        super().__init__()
        self.container = container
        self.password = password
        self.database = database
        self.import_dir = import_dir
        self.container_backup_dir = container_backup_dir

    def run(self):
        try:
            sql = SqlOps(self.container, self.password)

            schema_file = None
            data_files = []

            for f in os.listdir(self.import_dir):
                if f.endswith("_schema.sql"):
                    schema_file = f
                elif f.endswith(".tsv"):
                    parts = f.replace(".tsv", "").split(".", 1)
                    if len(parts) == 2:
                        data_files.append((parts[0], parts[1], f))

            if not schema_file:
                self.finished.emit(False, "File schema (*_schema.sql) tidak ditemukan di folder.")
                return
            if not data_files:
                self.finished.emit(False, "Tidak ada file data (*.tsv) di folder.")
                return

            import_container_dir = "{}/{}_import_{}".format(
                self.container_backup_dir, self.database,
                datetime.datetime.now().strftime("%Y%m%d%H%M%S")
            )
            DockerOps.mkdir(self.container, import_container_dir)

            self.progress.emit("Menyalin file ke container...", 10)
            local_schema = os.path.join(self.import_dir, schema_file)
            container_schema = "{}/{}".format(import_container_dir, schema_file)
            DockerOps.copy_to_container(self.container, local_schema, container_schema)

            container_csv_files = []
            for schema, table, csv_file in data_files:
                local_csv = os.path.join(self.import_dir, csv_file)
                container_csv = "{}/{}".format(import_container_dir, csv_file)
                DockerOps.copy_to_container(self.container, local_csv, container_csv)
                container_csv_files.append((schema, table, container_csv))

            self.progress.emit("Membuat database '{}'...".format(self.database), 25)
            try:
                sql._run_sqlcmd("DROP DATABASE IF EXISTS [{}]".format(self.database))
            except DockerExecError:
                pass
            sql._run_sqlcmd("CREATE DATABASE [{}]".format(self.database), timeout=60)

            self.progress.emit("Menjalankan schema SQL...", 35)
            sql.run_sql_script(container_schema)

            total = len(container_csv_files)
            for i, (schema, table, csv_path) in enumerate(container_csv_files):
                pct = 40 + int((i / total) * 50)
                self.progress.emit(
                    "Mengimport data tabel [{0}].[{1}]... ({2}/{3})".format(
                        schema, table, i + 1, total
                    ), pct
                )
                sql._run_bcp([
                    "{}.[{}].[{}]".format(self.database, schema, table),
                    "in", csv_path, "-c", "-E",
                ], timeout=600)

            self.progress.emit("Membersihkan file temporary...", 95)
            try:
                for _, _, csv_path in container_csv_files:
                    DockerOps.remove_file(self.container, csv_path)
                DockerOps.remove_file(self.container, container_schema)
                DockerOps.exec_command(self.container, ["rmdir", import_container_dir])
            except DockerExecError:
                pass

            self.progress.emit(
                "Import database '{}' selesai!".format(self.database), 100
            )
            self.finished.emit(True, self.database)

        except DockerExecError as e:
            self.finished.emit(False, str(e))
        except Exception as e:
            self.finished.emit(False, "Error: {}".format(str(e)))
