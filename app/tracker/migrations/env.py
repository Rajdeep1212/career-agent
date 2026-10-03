"""Alembic environment for the tracker database. Run through app.tracker.store.upgrade() and downgrade()."""
from alembic import context

from app.tracker import models, store

url = context.config.get_main_option("sqlalchemy.url")

with store.engine(url).connect() as connection:
    context.configure(connection=connection, target_metadata=models.Base.metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()
    connection.commit()
