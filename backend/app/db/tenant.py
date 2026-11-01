"""Connection management for the one-database-per-school design.

Each school has its own PostgreSQL database and its own database role.  Pools are created lazily,
kept small, and the number of cached pools is capped (least recently used are disposed).
"""
import threading
from collections import OrderedDict

from sqlalchemy import create_engine
from sqlalchemy.engine import URL, Engine
from sqlalchemy.orm import Session, sessionmaker

from app.security import SecretBox


def tenant_url(reg, box: SecretBox) -> URL:
    return URL.create("postgresql+psycopg", username=reg.db_role, password=box.decrypt(reg.password_ciphertext),
                      host=reg.host, port=reg.port, database=reg.db_name)


class TenantEngines:
    def __init__(self, settings, box: SecretBox):
        self.settings, self.box = settings, box
        self._engines: "OrderedDict[str, tuple[Engine, sessionmaker]]" = OrderedDict()
        self._lock = threading.Lock()

    def _key(self, reg) -> str:
        return f"{reg.school_id}:{reg.db_name}"

    def _entry(self, reg):
        key = self._key(reg)
        with self._lock:
            hit = self._engines.get(key)
            if hit:
                self._engines.move_to_end(key)
                return hit
            engine = create_engine(tenant_url(reg, self.box), pool_size=self.settings.tenant_pool_size,
                                   max_overflow=self.settings.tenant_max_overflow, pool_pre_ping=True,
                                   pool_recycle=1800)
            entry = (engine, sessionmaker(engine, expire_on_commit=False))
            self._engines[key] = entry
            while len(self._engines) > self.settings.tenant_max_engines:
                _, (old, _) = self._engines.popitem(last=False)
                old.dispose()
            return entry

    def engine(self, reg) -> Engine:
        return self._entry(reg)[0]

    def session(self, reg) -> Session:
        return self._entry(reg)[1]()

    def forget(self, school_id) -> None:
        with self._lock:
            for key in [k for k in self._engines if k.startswith(f"{school_id}:")]:
                self._engines.pop(key)[0].dispose()

    def dispose_all(self) -> None:
        with self._lock:
            for engine, _ in self._engines.values():
                engine.dispose()
            self._engines.clear()
