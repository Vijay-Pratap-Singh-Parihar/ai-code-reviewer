import enum
from typing import Any

import sqlalchemy as sa


def pg_enum(enum_cls: type[enum.Enum], **kwargs: Any) -> sa.Enum:
    """A Postgres ENUM column type that stores `.value`, not `.name`.

    SQLAlchemy's default `sa.Enum(SomeEnum)` persists the member *name*
    (e.g. "HIGH"). Every enum in this codebase is a `StrEnum` whose value is
    already the wire format used by Pydantic/JSON (e.g. "high"), so storing
    the name instead would make raw SQL queries and any direct DB inspection
    use a different vocabulary than the API. This keeps them identical.
    """
    return sa.Enum(enum_cls, values_callable=lambda cls: [e.value for e in cls], **kwargs)
