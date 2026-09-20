from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base. Every model registers on this so Alembic
    autogenerate can see them via `target_metadata = Base.metadata`.
    """
