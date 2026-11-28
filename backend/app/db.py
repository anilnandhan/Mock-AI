from collections.abc import Iterator

from sqlmodel import Session, SQLModel, create_engine

from .config import settings

engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})


def init_db() -> None:
    from . import models  # noqa: F401  (registers tables)

    SQLModel.metadata.create_all(engine)


def get_db() -> Iterator[Session]:
    with Session(engine) as db:
        yield db
