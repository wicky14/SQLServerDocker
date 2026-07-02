import re
import os
import datetime

from .docker_ops import DockerOps, DockerExecError


SQLCMD_PATHS = [
    "/opt/mssql-tools18/bin/sqlcmd",
    "/opt/mssql-tools/bin/sqlcmd",
]

SYSTEM_DBS = {"master", "model", "msdb", "tempdb"}


class SqlOps:

    def __init__(self, container_name, password):
        self.container = container_name
        self.password = password
        self.sqlcmd_path = None
        self.sqlcmd_flags = []

    def _detect_sqlcmd(self):
        if self.sqlcmd_path:
            return
        for path in SQLCMD_PATHS:
            if DockerOps.check_file_exists(self.container, path):
                self.sqlcmd_path = path
                break
        if not self.sqlcmd_path:
            raise DockerExecError(
                "sqlcmd tidak ditemukan di container '{}'. "
                "Pastikan container SQL Server menggunakan image "
                "dari mcr.microsoft.com/mssql/server.".format(self.container)
            )
        if "mssql-tools18" in self.sqlcmd_path:
            self.sqlcmd_flags = ["-C"]
        else:
            self.sqlcmd_flags = []

    def _run_sqlcmd(self, query, timeout=120, extra_flags=None):
        self._detect_sqlcmd()
        cmd = [
            self.sqlcmd_path,
            "-S", "localhost",
            "-U", "sa",
            "-P", self.password,
            "-Q", query,
            "-W",
        ] + self.sqlcmd_flags
        if extra_flags:
            cmd.extend(extra_flags)
        return DockerOps.exec_command(self.container, cmd, timeout=timeout)

    def test_connection(self):
        try:
            self._run_sqlcmd("SELECT 1 AS ok", timeout=15)
            return True
        except DockerExecError:
            return False

    def list_databases(self):
        output = self._run_sqlcmd(
            "SET NOCOUNT ON; SELECT name FROM sys.databases "
            "WHERE name NOT IN ('master','model','msdb','tempdb') "
            "AND state = 0 ORDER BY name",
            extra_flags=["-s", "|", "-h", "-1"]
        )
        dbs = []
        for line in output.strip().split("\n"):
            line = line.strip()
            if line:
                dbs.append(line)
        return dbs

    def backup_database(self, database, backup_dir_in_container, backup_filename=None):
        if backup_filename:
            backup_file = "{}/{}".format(backup_dir_in_container, backup_filename)
        else:
            backup_file = "{}/{}.bak".format(backup_dir_in_container, database)
        query = (
            "BACKUP DATABASE [{}] TO DISK = N'{}' "
            "WITH NOFORMAT, NOINIT, "
            "NAME = N'{}-Full', SKIP, NOREWIND, NOUNLOAD, STATS = 10"
        ).format(database, backup_file, database)
        self._run_sqlcmd(query, timeout=600)
        return backup_file

    def get_filelistonly(self, bak_path_in_container):
        output = self._run_sqlcmd(
            "RESTORE FILELISTONLY FROM DISK = N'{}'".format(bak_path_in_container),
            extra_flags=["-s", "|"]
        )
        files = self._parse_filelistonly_delimited(output)
        if not files:
            raise DockerExecError(
                "Gagal membaca struktur file backup.\n"
                "Path: {}\n\n"
                "Output sqlcmd:\n{}".format(bak_path_in_container, output)
            )
        return files

    def _parse_filelistonly_delimited(self, output):
        files = []
        for line in output.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            if "LogicalName" in line or line.startswith("---") or line.startswith("-"):
                continue
            parts = line.split("|")
            if len(parts) < 3:
                continue
            logical_name = parts[0].strip()
            physical_name = parts[1].strip()
            ftype = parts[2].strip()
            if logical_name and physical_name:
                files.append({
                    "logical_name": logical_name,
                    "physical_name": physical_name,
                    "type": ftype
                })
        return files

    def restore_database(self, database, bak_path_in_container,
                         logical_files, timeout=600):
        with_clauses = []
        for f in logical_files:
            ext = ".mdf" if f["type"] == "D" else ".ndf" if f["type"] == "S" else ".ldf"
            new_phys = "/var/opt/mssql/data/{}{}".format(database, ext)
            if f["type"] == "S":
                new_phys = "/var/opt/mssql/data/{}_{}".format(
                    database, os.path.basename(f["physical_name"])
                )
            with_clauses.append(
                'MOVE "{}" TO "{}"'.format(f["logical_name"], new_phys)
            )

        with_str = ", ".join(with_clauses)
        query = (
            "RESTORE DATABASE [{}] FROM DISK = N'{}' "
            "WITH {}, REPLACE, STATS = 10"
        ).format(database, bak_path_in_container, with_str)

        self._run_sqlcmd(query, timeout=timeout)

    def drop_database(self, database):
        self._run_sqlcmd(
            "DROP DATABASE IF EXISTS [{}]".format(database)
        )

    def copy_database(self, source_db, new_db, backup_dir_in_container):
        ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        bak_filename = "temp_copy_{}_{}.bak".format(source_db, ts)
        bak_path = "{}/{}".format(backup_dir_in_container, bak_filename)

        DockerOps.mkdir(self.container, backup_dir_in_container)

        query = (
            "BACKUP DATABASE [{}] TO DISK = N'{}' "
            "WITH NOFORMAT, NOINIT, "
            "NAME = N'{}-Copy', SKIP, NOREWIND, NOUNLOAD, STATS = 10"
        ).format(source_db, bak_path, source_db)
        self._run_sqlcmd(query, timeout=600)

        files = self.get_filelistonly(bak_path)
        if not files:
            raise DockerExecError("Tidak dapat membaca struktur database '{}'".format(source_db))

        self.restore_database(new_db, bak_path, files)

        try:
            DockerOps.remove_file(self.container, bak_path)
        except DockerExecError:
            pass

        return new_db
