"""Import completed legacy JSONL sessions into SQLite without deleting files."""

import argparse
import itertools
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from .models import BoundingBox, positive_seconds
from .storage import append_record, connect_database


def import_session(source: Path, directory: Path) -> bool:
    with source.open(encoding="utf-8") as stream:
        records = (json.loads(line) for line in stream if line.strip())
        first = next(records, None)
        if not isinstance(first, dict) or first.get("type") != "start":
            raise ValueError("session must begin with a start record")
        session_id = first.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id must be a nonempty string")
        BoundingBox.parse(first.get("bbox"))
        positive_seconds(first.get("interval_seconds"))
        with closing(connect_database(directory)) as connection, connection:
            if connection.execute(
                "SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone():
                return False
            last_type = None
            for record in itertools.chain((first,), records):
                if (
                    not isinstance(record, dict)
                    or record.get("session_id") != session_id
                    or record.get("schema_version") != 1
                    or last_type == "stop"
                ):
                    raise ValueError("invalid session record or unsupported schema")
                append_record(connection, record)
                last_type = record["type"]
            if last_type != "stop":
                raise ValueError("session is incomplete; no final stop record (nothing imported)")
            connection.execute(
                "UPDATE sessions SET source_file = ? WHERE session_id = ?",
                (str(source), session_id),
            )
        return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-dir", type=Path, default=os.getenv("LOG_DIR", "data"))
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args(argv)
    imported = skipped = failed = 0
    for file in args.files:
        try:
            if import_session(file, args.log_dir):
                imported += 1
            else:
                skipped += 1
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
            failed += 1
            print(f"Could not import {file}: {exc}")
    print(f"Imported {imported}; already present {skipped}; failed {failed}. Originals kept.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
