"""Append-only event log in SQLite. The log is the source of truth; state is a fold of it."""
import json
import sqlite3


class EventStore:
    def __init__(self, path=":memory:"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS events (world TEXT, seq INTEGER, day INTEGER, hour INTEGER,"
            " t INTEGER, type TEXT, actor TEXT, data TEXT, PRIMARY KEY (world, seq))"
        )
        self.db.commit()

    def clear(self):
        self.db.execute("DELETE FROM events")
        self.db.commit()

    def append(self, ev):
        self.db.execute(
            "INSERT INTO events VALUES (?,?,?,?,?,?,?,?)",
            (ev["world"], ev["seq"], ev["day"], ev["hour"], ev["t"], ev["type"], ev["actor"],
             json.dumps(ev["data"])),
        )

    def commit(self):
        self.db.commit()

    def read(self, world, until_day=None, day=None):
        sql, args = "SELECT world, seq, day, hour, t, type, actor, data FROM events WHERE world=?", [world]
        if until_day is not None:
            sql += " AND day<=?"
            args.append(until_day)
        if day is not None:
            sql += " AND day=?"
            args.append(day)
        rows = self.db.execute(sql + " ORDER BY seq", args).fetchall()
        keys = ("world", "seq", "day", "hour", "t", "type", "actor")
        return [{**dict(zip(keys, r[:7])), "data": json.loads(r[7])} for r in rows]
