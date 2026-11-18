"""Pick the book provider and library catalogue from the environment."""
import os
from app.books.fixture import FixtureProvider, fixture_catalog


def _choice(name):
    return os.getenv(name, 'none').strip().lower() or 'none'


def configure_sources(app):
    """BOOK_PROVIDER / LIBRARY_CATALOG = fixture | none. Values already set in app.config are kept.

    fixture is development only: outside debug mode it is refused and the source stays unavailable (503).
    No real external provider or DLS import exists yet, so any other value is a startup error.
    """
    for key, factory in (('BOOK_PROVIDER', FixtureProvider), ('LIBRARY_CATALOG', fixture_catalog)):
        if app.config.get(key) is not None:
            continue
        choice = _choice(key)
        if choice == 'none':
            app.config[key] = None
        elif choice == 'fixture':
            if app.debug:
                app.config[key] = factory()
                app.logger.warning('%s=fixture: development sample data is active (not a real source)', key)
            else:
                app.config[key] = None
                app.logger.error('%s=fixture is refused outside debug mode; source stays unavailable', key)
        else:
            raise RuntimeError(f'Unsupported {key}={choice!r}: only fixture or none are implemented')
