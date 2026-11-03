from logging.config import fileConfig
from alembic import context
from sqlalchemy import engine_from_config, pool
from app.config import get_settings, normalize_database_url
from app.database import Base
import app.models
config=context.config
if config.config_file_name:fileConfig(config.config_file_name,disable_existing_loggers=False)
config.set_main_option('sqlalchemy.url',normalize_database_url(get_settings().database_url).replace('%','%%'))
target_metadata=Base.metadata

def run_migrations_offline():
    context.configure(url=config.get_main_option('sqlalchemy.url'),target_metadata=target_metadata,literal_binds=True,compare_type=True)
    with context.begin_transaction():context.run_migrations()

def run_migrations_online():
    supplied_connection=config.attributes.get('connection')
    if supplied_connection is not None:
        context.configure(connection=supplied_connection,target_metadata=target_metadata,compare_type=True)
        with context.begin_transaction():context.run_migrations()
        return
    connectable=engine_from_config(config.get_section(config.config_ini_section),prefix='sqlalchemy.',poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection,target_metadata=target_metadata,compare_type=True)
        with context.begin_transaction():context.run_migrations()
    connectable.dispose()
if context.is_offline_mode():run_migrations_offline()
else:run_migrations_online()
