from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base. SQLAlchemy models (Stage 1) register on this
    so Alembic autogenerate can see them via `target_metadata = Base.metadata`.
    """
