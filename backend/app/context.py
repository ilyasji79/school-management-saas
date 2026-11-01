from contextlib import contextmanager

from app.config import Settings
from app.db.control import make_control
from app.db.tenant import TenantEngines
from app.security import SecretBox


class AppContext:
    """Everything long-lived: settings, control-plane engine, per-school pools, secret box."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.box = SecretBox(settings.secrets_key)
        self.control_engine, self._control_factory = make_control(settings)
        self.tenants = TenantEngines(settings, self.box)
        self.verified_schools: set[str] = set()

    @contextmanager
    def control(self):
        session = self._control_factory()
        try:
            yield session
        finally:
            session.close()

    def close(self):
        self.tenants.dispose_all()
        self.control_engine.dispose()
