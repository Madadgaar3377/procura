"""Read-only application DB probe. Never prints URLs, secrets or raw errors."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.config import get_settings, normalize_database_url
from app.database import engine
from app.database_diagnostics import classify, error_metadata


def main():
    configured = make_url(normalize_database_url(get_settings().database_url))
    report = {'database': engine.dialect.name, 'effective_sslmode': engine.url.query.get('sslmode'),
              'ca_source': 'system' if engine.url.query.get('sslrootcert') == 'system' else 'configured_file' if engine.url.query.get('sslrootcert') else 'driver_default',
              'alembic_ssl_configuration_differs': any(configured.query.get(key) != engine.url.query.get(key) for key in ('sslmode', 'sslrootcert')),
              'probe': 'SELECT 1', 'writes_performed': False}
    try:
        with engine.connect() as connection:
            connection.execute(text('SELECT 1'))
        report['connected'] = True
    except Exception as error:  # noqa: BLE001 - diagnostic must not print raw driver exceptions
        report.update(connected=False, **classify(error), **error_metadata(error))
    print(json.dumps(report, indent=2))
    return 0 if report['connected'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
