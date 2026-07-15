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

    def _run_sqlcmd(self, query, timeout=120, extra_flags=None, database=None):
        self._detect_sqlcmd()
        cmd = [
            self.sqlcmd_path,
            "-S", "localhost",
            "-U", "sa",
            "-P", self.password,
            "-Q", query,
            "-W",
        ] + self.sqlcmd_flags
        if database:
            cmd.extend(["-d", database])
        if extra_flags:
            cmd.extend(extra_flags)
        env = None
        if "mssql-tools18" in self.sqlcmd_path:
            env = {"TRUSTSERVERCERTIFICATE": "yes", "SQLCMDENCRYPT": "optional"}
        output = DockerOps.exec_command(self.container, cmd, timeout=timeout, env=env)
        lines = [l for l in output.split("\n") if "Changed database context" not in l]
        return "\n".join(lines)

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
        if "mssql-tools18" in bcp:
            ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S%f")
            dsn_name = "MSSQLTrusted"
            dsn_file = "/tmp/bcp_odbc_{}.ini".format(ts)
            dsn_content = "[{}]\nDriver=ODBC Driver 18 for SQL Server\nServer=localhost,1433\nTrustServerCertificate=yes\n".format(dsn_name)
            DockerOps.exec_command(self.container, [
                "bash", "-c",
                "cat > '{}' << 'EOF'\n{}\nEOF".format(dsn_file, dsn_content)
            ])
            cmd = [bcp] + args + ["-D", "-S", dsn_name, "-U", "sa", "-P", self.password]
            return DockerOps.exec_command(self.container, cmd, timeout=timeout,
                env={"ODBCINI": dsn_file})
        cmd = [bcp] + args + ["-S", "localhost", "-U", "sa", "-P", self.password]
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

    def _get_indexes(self, database):
        q = """
        USE [{}]; SET NOCOUNT ON;
        SELECT OBJECT_SCHEMA_NAME(i.object_id), OBJECT_NAME(i.object_id),
            i.name, i.type_desc, i.is_unique, i.has_filter,
            ISNULL(i.filter_definition, '')
        FROM sys.indexes i
        WHERE i.is_primary_key = 0 AND i.is_unique_constraint = 0
          AND i.name IS NOT NULL AND OBJECTPROPERTY(i.object_id, 'IsUserTable') = 1
        ORDER BY OBJECT_SCHEMA_NAME(i.object_id), OBJECT_NAME(i.object_id), i.index_id
        """.format(database)
        output = self._run_sqlcmd(q, extra_flags=["-s", "|", "-h", "-1"], timeout=30)
        indexes = []
        for line in output.strip().split("\n"):
            line = line.strip()
            if not line or "|" not in line:
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 7:
                indexes.append({
                    "schema": parts[0], "table": parts[1], "name": parts[2],
                    "type": parts[3], "unique": parts[4] == "1",
                    "filtered": parts[5] == "1", "filter_def": parts[6]
                })
        return indexes

    def _get_index_columns(self, database):
        q = """
        USE [{}]; SET NOCOUNT ON;
        SELECT OBJECT_SCHEMA_NAME(ic.object_id), OBJECT_NAME(ic.object_id),
            i.name, c.name, ic.key_ordinal, ic.is_included_column, ic.is_descending_key
        FROM sys.index_columns ic
        JOIN sys.indexes i ON ic.object_id = i.object_id AND ic.index_id = i.index_id
        JOIN sys.columns c ON ic.object_id = c.object_id AND ic.column_id = c.column_id
        WHERE i.is_primary_key = 0 AND i.is_unique_constraint = 0
          AND i.name IS NOT NULL AND OBJECTPROPERTY(ic.object_id, 'IsUserTable') = 1
        ORDER BY OBJECT_SCHEMA_NAME(ic.object_id), OBJECT_NAME(ic.object_id), i.name, ic.key_ordinal
        """.format(database)
        output = self._run_sqlcmd(q, extra_flags=["-s", "|", "-h", "-1"], timeout=30)
        cols = []
        for line in output.strip().split("\n"):
            line = line.strip()
            if not line or "|" not in line:
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 7:
                cols.append({
                    "schema": parts[0], "table": parts[1], "index": parts[2],
                    "column": parts[3], "ordinal": int(parts[4]),
                    "included": parts[5] == "1", "descending": parts[6] == "1"
                })
        return cols

    def _build_indexes_sql(self, database):
        indexes = self._get_indexes(database)
        if not indexes:
            return ""
        index_cols = self._get_index_columns(database)
        col_map = {}
        for c in index_cols:
            key = (c["schema"], c["table"], c["index"])
            col_map.setdefault(key, []).append(c)

        sql = "\n-- Indexes\n"
        for idx in indexes:
            key = (idx["schema"], idx["table"], idx["name"])
            cols = col_map.get(key, [])
            key_cols = [c for c in cols if not c["included"]]
            inc_cols = [c for c in cols if c["included"]]

            key_cols.sort(key=lambda x: x["ordinal"])
            key_str = ", ".join(
                "[{}] {}".format(c["column"], "DESC" if c["descending"] else "")
                for c in key_cols
            )

            unique_str = "UNIQUE " if idx["unique"] else ""
            clustered_str = ""
            if idx["type"] == "CLUSTERED":
                clustered_str = "CLUSTERED "
            elif idx["type"] == "NONCLUSTERED":
                clustered_str = "NONCLUSTERED "

            inc_str = ""
            if inc_cols:
                inc_str = "\nINCLUDE ({})".format(
                    ", ".join("[{}]".format(c["column"]) for c in inc_cols)
                )

            filter_str = ""
            if idx["filtered"] and idx["filter_def"]:
                filter_str = "\nWHERE {}".format(idx["filter_def"])

            sql += "CREATE {}{}INDEX [{}] ON [{}].[{}] ({}){} {};\nGO\n".format(
                unique_str, clustered_str, idx["name"],
                idx["schema"], idx["table"], key_str,
                inc_str, filter_str
            )
        return sql

    def _get_foreign_keys(self, database):
        q = """
        USE [{}]; SET NOCOUNT ON;
        SELECT OBJECT_SCHEMA_NAME(fk.parent_object_id), OBJECT_NAME(fk.parent_object_id),
            fk.name,
            OBJECT_SCHEMA_NAME(fk.referenced_object_id), OBJECT_NAME(fk.referenced_object_id),
            fk.delete_referential_action, fk.update_referential_action
        FROM sys.foreign_keys fk
        WHERE OBJECTPROPERTY(fk.parent_object_id, 'IsUserTable') = 1
        ORDER BY OBJECT_SCHEMA_NAME(fk.parent_object_id), OBJECT_NAME(fk.parent_object_id), fk.name
        """.format(database)
        output = self._run_sqlcmd(q, extra_flags=["-s", "|", "-h", "-1"], timeout=30)
        fks = []
        for line in output.strip().split("\n"):
            line = line.strip()
            if not line or "|" not in line:
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 7:
                fks.append({
                    "schema": parts[0], "table": parts[1], "name": parts[2],
                    "ref_schema": parts[3], "ref_table": parts[4],
                    "delete_action": int(parts[5]), "update_action": int(parts[6])
                })
        return fks

    def _get_foreign_key_columns(self, database):
        q = """
        USE [{}]; SET NOCOUNT ON;
        SELECT OBJECT_SCHEMA_NAME(fkc.parent_object_id), OBJECT_NAME(fkc.parent_object_id),
            OBJECT_NAME(fkc.constraint_object_id),
            c1.name, c2.name
        FROM sys.foreign_key_columns fkc
        JOIN sys.columns c1 ON fkc.parent_object_id = c1.object_id AND fkc.parent_column_id = c1.column_id
        JOIN sys.columns c2 ON fkc.referenced_object_id = c2.object_id AND fkc.referenced_column_id = c2.column_id
        ORDER BY OBJECT_SCHEMA_NAME(fkc.parent_object_id), OBJECT_NAME(fkc.parent_object_id),
            OBJECT_NAME(fkc.constraint_object_id), fkc.constraint_column_id
        """.format(database)
        output = self._run_sqlcmd(q, extra_flags=["-s", "|", "-h", "-1"], timeout=30)
        cols = []
        for line in output.strip().split("\n"):
            line = line.strip()
            if not line or "|" not in line:
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 5:
                cols.append({
                    "schema": parts[0], "table": parts[1], "fk": parts[2],
                    "parent_col": parts[3], "ref_col": parts[4]
                })
        return cols

    def _build_foreign_keys_sql(self, database):
        fks = self._get_foreign_keys(database)
        if not fks:
            return ""
        fk_cols = self._get_foreign_key_columns(database)
        col_map = {}
        for c in fk_cols:
            key = (c["schema"], c["table"], c["fk"])
            col_map.setdefault(key, []).append(c)

        action_map = {0: "NO ACTION", 1: "CASCADE", 2: "SET NULL", 3: "SET DEFAULT"}
        sql = "\n-- Foreign Keys\n"
        for fk in fks:
            key = (fk["schema"], fk["table"], fk["name"])
            cols = col_map.get(key, [])
            parent_cols = ", ".join("[{}]".format(c["parent_col"]) for c in cols)
            ref_cols = ", ".join("[{}]".format(c["ref_col"]) for c in cols)
            del_act = action_map.get(fk["delete_action"], "NO ACTION")
            upd_act = action_map.get(fk["update_action"], "NO ACTION")
            sql += (
                "ALTER TABLE [{}].[{}] WITH CHECK ADD CONSTRAINT [{}] "
                "FOREIGN KEY ({}) REFERENCES [{}].[{}] ({}) "
                "ON DELETE {} ON UPDATE {};\nGO\n"
            ).format(fk["schema"], fk["table"], fk["name"],
                     parent_cols, fk["ref_schema"], fk["ref_table"], ref_cols,
                     del_act, upd_act)
        return sql

    def _get_defaults(self, database):
        q = """
        USE [{}]; SET NOCOUNT ON;
        SELECT OBJECT_SCHEMA_NAME(dc.parent_object_id), OBJECT_NAME(dc.parent_object_id),
            c.name, dc.name, dc.definition
        FROM sys.default_constraints dc
        JOIN sys.columns c ON dc.parent_object_id = c.object_id AND dc.parent_column_id = c.column_id
        WHERE OBJECTPROPERTY(dc.parent_object_id, 'IsUserTable') = 1
        ORDER BY OBJECT_SCHEMA_NAME(dc.parent_object_id), OBJECT_NAME(dc.parent_object_id), dc.name
        """.format(database)
        output = self._run_sqlcmd(q, extra_flags=["-s", "|", "-h", "-1"], timeout=30)
        defaults = []
        for line in output.strip().split("\n"):
            line = line.strip()
            if not line or "|" not in line:
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 5:
                defaults.append({
                    "schema": parts[0], "table": parts[1],
                    "column": parts[2], "name": parts[3], "definition": parts[4]
                })
        return defaults

    def _build_defaults_sql(self, database):
        defaults = self._get_defaults(database)
        if not defaults:
            return ""
        sql = "\n-- Default Constraints\n"
        for d in defaults:
            sql += (
                "ALTER TABLE [{}].[{}] ADD CONSTRAINT [{}] "
                "DEFAULT {} FOR [{}];\nGO\n"
            ).format(d["schema"], d["table"], d["name"], d["definition"], d["column"])
        return sql

    def _get_check_constraints(self, database):
        q = """
        USE [{}]; SET NOCOUNT ON;
        SELECT OBJECT_SCHEMA_NAME(cc.parent_object_id), OBJECT_NAME(cc.parent_object_id),
            cc.name, cc.definition
        FROM sys.check_constraints cc
        WHERE OBJECTPROPERTY(cc.parent_object_id, 'IsUserTable') = 1
        ORDER BY OBJECT_SCHEMA_NAME(cc.parent_object_id), OBJECT_NAME(cc.parent_object_id), cc.name
        """.format(database)
        output = self._run_sqlcmd(q, extra_flags=["-s", "|", "-h", "-1"], timeout=30)
        checks = []
        for line in output.strip().split("\n"):
            line = line.strip()
            if not line or "|" not in line:
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 4:
                checks.append({
                    "schema": parts[0], "table": parts[1],
                    "name": parts[2], "definition": parts[3]
                })
        return checks

    def _build_check_constraints_sql(self, database):
        checks = self._get_check_constraints(database)
        if not checks:
            return ""
        sql = "\n-- Check Constraints\n"
        for c in checks:
            sql += (
                "ALTER TABLE [{}].[{}] WITH CHECK ADD CONSTRAINT [{}] "
                "CHECK ({});\nGO\n"
            ).format(c["schema"], c["table"], c["name"], c["definition"])
        return sql

    def _get_modules(self, database):
        q = """
        USE [{}]; SET NOCOUNT ON;
        SELECT o.type, OBJECT_SCHEMA_NAME(o.object_id), o.name,
            ISNULL(OBJECT_SCHEMA_NAME(o.parent_object_id), ''),
            ISNULL(OBJECT_NAME(o.parent_object_id), '')
        FROM sys.sql_modules m
        JOIN sys.objects o ON m.object_id = o.object_id
        WHERE o.type IN ('V', 'P', 'FN', 'TF', 'IF', 'TR')
          AND o.is_ms_shipped = 0
        ORDER BY CASE o.type
            WHEN 'V' THEN 1 WHEN 'FN' THEN 2 WHEN 'IF' THEN 3
            WHEN 'TF' THEN 4 WHEN 'P' THEN 5 WHEN 'TR' THEN 6 ELSE 7
        END, OBJECT_SCHEMA_NAME(o.object_id), o.name
        """.format(database)
        output = self._run_sqlcmd(q, extra_flags=["-s", "|", "-h", "-1"], timeout=30)
        modules = []
        for line in output.strip().split("\n"):
            line = line.strip()
            if not line or "|" not in line:
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 5:
                modules.append({
                    "type": parts[0], "schema": parts[1], "name": parts[2],
                    "parent_schema": parts[3], "parent_object": parts[4]
                })
        return modules

    def _get_module_definition(self, database, schema, name, type_char):
        self._detect_sqlcmd()
        q = """
        SET TEXTSIZE 2147483647; SET NOCOUNT ON;
        SELECT m.definition
        FROM sys.sql_modules m
        JOIN sys.objects o ON m.object_id = o.object_id
        WHERE OBJECT_SCHEMA_NAME(o.object_id) = '{}'
          AND o.name = '{}' AND o.type = '{}'
        """.format(schema, name, type_char)
        cmd = [
            self.sqlcmd_path,
            "-S", "localhost",
            "-U", "sa",
            "-P", self.password,
            "-d", database,
            "-Q", q,
            "-y", "0",
        ] + self.sqlcmd_flags
        env = None
        if "mssql-tools18" in self.sqlcmd_path:
            env = {"TRUSTSERVERCERTIFICATE": "yes", "SQLCMDENCRYPT": "optional"}
        output = DockerOps.exec_command(self.container, cmd, timeout=30, env=env)
        lines = [l.strip() for l in output.split("\n")
                 if l.strip() and "Changed database context" not in l
                 and l.strip() != "definition" and not l.strip().startswith("---")
                 and not re.match(r"^\(\d+ rows? affected\)$", l.strip())]
        return "\n".join(lines)

    def _build_schema_sql(self, database, tables):
        sql = "-- Schema untuk database: [{}]\n-- Generated: {}\n\n".format(
            database, datetime.datetime.now().isoformat()
        )
        for schema, table in tables:
            sql += self._build_create_table_sql(database, schema, table)
        sql += self._build_indexes_sql(database)
        sql += self._build_defaults_sql(database)
        sql += self._build_check_constraints_sql(database)
        sql += self._build_foreign_keys_sql(database)
        modules = self._get_modules(database)
        for type_list, label in [(['V'],"Views"), (['FN','IF','TF'],"Functions"),
                                  (['P'],"Stored Procedures"), (['TR'],"Triggers")]:
            subset = [m for m in modules if m["type"] in type_list]
            if not subset:
                continue
            sql += "\n-- {}\n".format(label)
            for m in subset:
                try:
                    defn = self._get_module_definition(database, m["schema"], m["name"], m["type"])
                    if defn:
                        if not defn.strip().upper().startswith("CREATE"):
                            defn = "CREATE [{}].[{}] AS\n{}".format(m["schema"], m["name"], defn)
                        sql += defn + "\nGO\n"
                except DockerExecError as e:
                    sql += "-- GAGAL: [{}].[{}] - {}\n".format(m["schema"], m["name"], str(e)[:80])
                except Exception as e:
                    sql += "-- GAGAL: [{}].[{}] - {}\n".format(m["schema"], m["name"], str(e)[:80])
        return sql

    def _write_file_to_container(self, content, container_path):
        DockerOps.write_text_file(self.container, content.encode("utf-8"), container_path)

    def export_for_downgrade(self, database, tables, export_dir_in_container):
        schema_sql = self._build_schema_sql(database, tables)

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

    def run_sql_script(self, script_path, database=None):
        self._detect_sqlcmd()
        cmd = [
            self.sqlcmd_path,
            "-S", "localhost",
            "-U", "sa",
            "-P", self.password,
            "-i", script_path,
            "-W",
        ] + self.sqlcmd_flags
        if database:
            cmd.extend(["-d", database])
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
