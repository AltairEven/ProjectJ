from alembic import context

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("use the simulation storage entry; no arbitrary database URL is accepted")
context.configure(connection=connection, target_metadata=None)
with context.begin_transaction():
    context.run_migrations()
