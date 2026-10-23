"""One-time initial copy of a real FUNAAB ONE SQLite database to PostgreSQL.

Set SOURCE_SQLITE_PATH and DATABASE_URL. Run only after verifying a backup and
only against an empty target database. This script does not delete the source.
"""
import os
from pathlib import Path
from sqlalchemy import MetaData, Table, create_engine, inspect, text
from app import app, db

source_path = Path(os.environ.get("SOURCE_SQLITE_PATH", ""))
if not source_path.is_file() or source_path.stat().st_size == 0:
    raise SystemExit("SOURCE_SQLITE_PATH must point to a non-empty copy of your actual SQLite database.")
source_engine = create_engine(f"sqlite:///{source_path.resolve()}")
source_metadata = MetaData()
source_metadata.reflect(bind=source_engine)
source_tables = set(source_metadata.tables)
if not source_tables:
    raise SystemExit("The source SQLite file has no tables; refusing to migrate.")

with app.app_context():
    if db.engine.dialect.name != "postgresql":
        raise SystemExit("DATABASE_URL must point to the Supabase PostgreSQL target database.")
    db.create_all()
    target_tables = set(inspect(db.engine).get_table_names())
    common = [t for t in db.metadata.sorted_tables if t.name in source_tables and t.name in target_tables]
    if not common:
        raise SystemExit("No matching application tables found between source and target.")
    existing = {t.name: db.session.execute(text(f'SELECT COUNT(*) FROM "{t.name}"')).scalar_one() for t in common}
    populated = [name for name, count in existing.items() if count]
    if populated:
        raise SystemExit("Target database is not empty; refusing to duplicate/overwrite data in: " + ", ".join(populated))

    with source_engine.connect() as src_conn:
        with db.engine.begin() as dest_conn:
            for target_table in common:
                source_table = source_metadata.tables[target_table.name]
                target_columns = {col.name for col in target_table.columns}
                source_columns = {col.name for col in source_table.columns}
                shared_columns = target_columns & source_columns
                rows = []
                for row in src_conn.execute(source_table.select()).mappings():
                    rows.append({key: row[key] for key in shared_columns if key in row})
                if rows:
                    dest_conn.execute(target_table.insert(), rows)

            # Align integer primary-key sequences after explicit ID inserts.
            for table in common:
                pk_cols = list(table.primary_key.columns)
                if len(pk_cols) != 1:
                    continue
                try:
                    is_integer_pk = pk_cols[0].type.python_type is int
                except (AttributeError, NotImplementedError):
                    is_integer_pk = False
                if not is_integer_pk:
                    continue
                col = pk_cols[0].name
                table_name = table.name
                dest_conn.execute(text(
                    f"SELECT setval(pg_get_serial_sequence(:table_name, :column_name), "
                    f"COALESCE((SELECT MAX(\"{col}\") FROM \"{table_name}\"), 1), "
                    f"EXISTS (SELECT 1 FROM \"{table_name}\") )"
                ), {"table_name": table_name, "column_name": col})

print("Migration copy finished. Tables copied:", ", ".join(t.name for t in common))
print("Verify row counts and test login/feed/messages before switching production traffic.")
