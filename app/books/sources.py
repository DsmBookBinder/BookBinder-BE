"""Pick the book provider and library catalogue from the environment."""
import os
from app.books.fixture import FixtureProvider, fixture_catalog


def _choice(name):
    default = 'kakao' if name == 'BOOK_PROVIDER' and os.getenv('KAKAO_REST_API_KEY', '').strip() else 'none'
    return os.getenv(name, default).strip().lower() or 'none'


def configure_sources(app):
    """Load configured sources. The fixture stays restricted to debug mode."""
    demo_assumption = os.getenv('BOOKBINDER_DEMO_ASSUME_UNOWNED', '').strip().lower() in ('1', 'true')
    if demo_assumption and not app.debug:
        raise RuntimeError('BOOKBINDER_DEMO_ASSUME_UNOWNED is available only in the local development mode')
    app.config['DEMO_ASSUME_UNOWNED'] = demo_assumption
    for key, factory in (('BOOK_PROVIDER', FixtureProvider), ('LIBRARY_CATALOG', fixture_catalog)):
        if app.config.get(key) is not None:
            continue
        choice = _choice(key)
        if choice == 'none':
            app.config[key] = None
        elif key == 'BOOK_PROVIDER' and choice == 'kakao':
            from app.books.kakao import KakaoBookProvider
            try:
                app.config[key] = KakaoBookProvider(os.getenv('KAKAO_REST_API_KEY', ''))
            except ValueError as exc:
                raise RuntimeError('BOOK_PROVIDER=kakao requires KAKAO_REST_API_KEY') from exc
        elif key == 'LIBRARY_CATALOG' and choice == 'dls':
            from app.books.dls import load_dls
            path = os.getenv('DLS_CATALOG_XLSX', '').strip()
            if not path:
                raise RuntimeError('LIBRARY_CATALOG=dls requires DLS_CATALOG_XLSX')
            try:
                app.config[key] = load_dls(path)
            except (OSError, ValueError) as exc:
                raise RuntimeError(f'DLS 소장 자료를 읽을 수 없어요. {exc.strerror if isinstance(exc, OSError) else exc}') from None
        elif choice == 'fixture':
            if app.debug:
                app.config[key] = factory()
                app.logger.warning('%s=fixture: development sample data is active (not a real source)', key)
            else:
                app.config[key] = None
                app.logger.error('%s=fixture is refused outside debug mode; source stays unavailable', key)
        else:
            raise RuntimeError(f'Unsupported source for {key}')
