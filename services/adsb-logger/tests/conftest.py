import json
import sqlite3
from contextlib import closing

import pytest


@pytest.fixture
def read_records():
    def read(database, session_id=None):
        with closing(sqlite3.connect(database)) as connection:
            if session_id is None:
                rows = connection.execute("SELECT payload_json FROM records ORDER BY id")
            else:
                rows = connection.execute(
                    "SELECT payload_json FROM records WHERE session_id = ? ORDER BY id",
                    (session_id,),
                )
            return [json.loads(row[0]) for row in rows]

    return read
