import re
import os
import datetime
import tempfile

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

    def _get_bcp_path(self):
        if not self.sqlcmd_path:
            self._detect_sqlcmd()
        return self.sqlcmd_path.replace("sqlcmd", "bcp")

    def _run_bcp(self, args, timeout=300):
        bcp = self._get_bcp_path()
        cmd = [bcp] + args + ["-S", "localhost", "-U", "sa", "-P", self.password]
        if "mssql-tools18" in bcp:
            cmd.append("-C")
        return DockerOps.exec_command(self.container, cmd, timeout=timeout)

    def get_user_tables(self, database):
        output = self._run_sqlcmd(
            "USE [{}]; SET NOCOUNT ON; SELECT TABLE_SCHEMA, TABLE_NAME "
            "FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE = 'BASE TABLE' "
            "ORDER BY TABLE_SCHEMA, TABLE_NAME".format(database),
            extra_flags=["-s", "|", "-h", "-1"],
            timeout=30
        )
        tables = []
        for line in output.strip().split("\n"):
            line = line.strip()
            if not line or "|" not in line:
                continue
            parts = line.split("|")
            if len(parts) >= 2:
                tables.append((parts[0].strip(), parts[1].strip()))
        return tables

    def get_column_info(self, database, schema, table):
        query = (
            "USE [{}]; SET NOCOUNT ON; SELECT "
            "c.COLUMN_NAME, c.DATA_TYPE, c.CHARACTER_MAXIMUM_LENGTH, "
            "c.IS_NULLABLE, c.NUMERIC_PRECISION, c.NUMERIC_SCALE, c.ORDINAL_POSITION, "
            "COLUMNPROPERTY(OBJECT_ID('['+c.TABLE_SCHEMA+'].['+c.TABLE_NAME+']'), c.COLUMN_NAME, 'IsIdentity') "
            "FROM INFORMATION_SCHEMA.COLUMNS c "
            "WHERE c.TABLE_SCHEMA = '{}' AND c.TABLE_NAME = '{}' "
            "ORDER BY c.ORDINAL_POSITION"
        ).format(database, schema, table)
        output = self._run_sqlcmd(query, extra_flags=["-s", "|", "-h", "-1"], timeout=30)
        columns = []
        for line in output.strip().split("\n"):
            line = line.strip()
            if not line or "|" not in line:
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 7:
                columns.append({
                    "name": parts[0],
                    "data_type": parts[1],
                    "max_length": parts[2],
                    "nullable": parts[3],
                    "precision": parts[4],
                    "scale": parts[5],
                    "ordinal": parts[6],
                    "identity": len(parts) > 7 and parts[7] == "1"
                })
        return columns

    def get_primary_key(self, database, schema, table):
        query = (
            "USE [{}]; SET NOCOUNT ON; SELECT c.COLUMN_NAME "
            "FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc "
            "JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE c "
            "ON tc.CONSTRAINT_NAME = c.CONSTRAINT_NAME "
            "WHERE tc.TABLE_SCHEMA = '{}' AND tc.TABLE_NAME = '{}' "
            "AND tc.CONSTRAINT_TYPE = 'PRIMARY KEY' "
            "ORDER BY c.ORDINAL_POSITION"
        ).format(database, schema, table)
        output = self._run_sqlcmd(query, extra_flags=["-s", "|", "-h", "-1"], timeout=30)
        cols = []
        for line in output.strip().split("\n"):
            line = line.strip()
            if line:
                cols.append(line)
        return cols

    def _build_create_table_sql(self, database, schema, table):
        columns = self.get_column_info(database, schema, table)
        pk_cols = self.get_primary_key(database, schema, table)

        parts = []
        for col in columns:
            type_str = col["data_type"]
            if type_str in ("varchar", "nvarchar", "char", "nchar", "varbinary"):
                ml = col["max_length"]
                if ml == "-1":
                    type_str += "(MAX)"
                elif ml and ml != "None":
                    type_str += "({})".format(ml)
            elif type_str in ("decimal", "numeric"):
                if col["precision"] and col["precision"] != "None":
                    type_str += "({}, {})".format(col["precision"], col["scale"] or "0")

            col_def = "    [{}] {}".format(col["name"], type_str)
            if col["identity"] == True or col["identity"] == "1":
                col_def += " IDENTITY(1,1)"
            if col["nullable"] == "NO":
                col_def += " NOT NULL"
            else:
                col_def += " NULL"
            parts.append(col_def)

        if pk_cols:
            pk_str = ", ".join("[{}]".format(c) for c in pk_cols)
            parts.append("    CONSTRAINT [PK_{}_{}] PRIMARY KEY ({})".format(
                table, schema, pk_str
            ))

        return "CREATE TABLE [{}].[{}] (\n{}\n);\nGO\n".format(
            schema, table, ",\n".join(parts)
        )

    def _write_file_to_container(self, content, container_path):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sql", delete=False) as f:
            f.write(content)
            local_tmp = f.name
        try:
            DockerOps.copy_to_container(self.container, local_tmp, container_path)
        finally:
            os.unlink(local_tmp)

    def export_for_downgrade(self, database, tables, export_dir_in_container):
        schema_sql = ""
        for schema, table in tables:
            schema_sql += self._build_create_table_sql(database, schema, table)

        schema_file = "{}/{}_schema.sql".format(export_dir_in_container, database)
        self._write_file_to_container(schema_sql, schema_file)

        data_files = []
        for schema, table in tables:
            csv_file = "{}/{}.{}.tsv".format(export_dir_in_container, schema, table)
            self._run_bcp([
                "{}.[{}].[{}]".format(database, schema, table),
                "out", csv_file, "-c",
            ], timeout=600)
            data_files.append((schema, table, csv_file))

        return schema_file, data_files

    def run_sql_script(self, script_path):
        self._detect_sqlcmd()
        cmd = [
            self.sqlcmd_path,
            "-S", "localhost",
            "-U", "sa",
            "-P", self.password,
            "-i", script_path,
            "-b"
        ] + self.sqlcmd_flags
        return DockerOps.exec_command(self.container, cmd, timeout=600)

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
