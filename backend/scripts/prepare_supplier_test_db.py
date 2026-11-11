"""Prepare a named test database in the existing loopback-only test cluster."""
import psycopg

with psycopg.connect(host='127.0.0.1', port=55440, user='supplier_test', dbname='postgres', autocommit=True) as db:
    if not db.execute("SELECT 1 FROM pg_database WHERE datname='supplier_import_test'").fetchone():
        db.execute('CREATE DATABASE supplier_import_test')
    print('Local supplier_import_test database ready')
