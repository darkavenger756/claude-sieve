import hashlib
import json
import os
import sqlite3
import threading
import time
from typing import Optional


_DB_PATH = os.path.join(
    os.environ.get('XDG_CACHE_HOME', os.path.expanduser('~/.cache')),
    'claude-sieve', 'cache.db',
)
_DEFAULT_TTL = 3600


class SieveCache:
    def __init__(self, db_path: str = '', ttl: int = _DEFAULT_TTL):
        self._lock = threading.Lock()
        self._ttl = ttl
        self._db_path = db_path or _DB_PATH
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute(
            'CREATE TABLE IF NOT EXISTS cache ('
            '  key_hash TEXT PRIMARY KEY,'
            '  compressed TEXT NOT NULL,'
            '  stats TEXT NOT NULL,'
            '  created_at REAL NOT NULL'
            ')'
        )
        self._conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_cache_created ON cache(created_at)'
        )
        self._conn.commit()

    def _key(self, text: str, framework: str) -> str:
        raw = f'{text}:::{framework}'
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()

    def get(self, text: str, framework: str) -> Optional[tuple[str, dict]]:
        key = self._key(text, framework)
        with self._lock:
            row = self._conn.execute(
                'SELECT compressed, stats, created_at FROM cache WHERE key_hash = ?',
                (key,),
            ).fetchone()
        if row is None:
            return None
        compressed, stats_json, created_at = row
        if time.time() - created_at > self._ttl:
            with self._lock:
                self._conn.execute('DELETE FROM cache WHERE key_hash = ?', (key,))
                self._conn.commit()
            return None
        return compressed, json.loads(stats_json)

    def set(self, text: str, framework: str, compressed: str, stats: dict) -> None:
        key = self._key(text, framework)
        with self._lock:
            self._conn.execute(
                'INSERT OR REPLACE INTO cache (key_hash, compressed, stats, created_at) '
                'VALUES (?, ?, ?, ?)',
                (key, compressed, json.dumps(stats), time.time()),
            )
            self._conn.commit()

    def clear_expired(self) -> int:
        with self._lock:
            deleted = self._conn.execute(
                'DELETE FROM cache WHERE created_at < ?',
                (time.time() - self._ttl,),
            ).rowcount
            self._conn.commit()
        return deleted

    def stats(self) -> dict:
        with self._lock:
            total = self._conn.execute('SELECT COUNT(*) FROM cache').fetchone()[0]
            expired = self._conn.execute(
                'SELECT COUNT(*) FROM cache WHERE created_at < ?',
                (time.time() - self._ttl,),
            ).fetchone()[0]
        return {'total_entries': total, 'expired_entries': expired}
