import os

from sqlalchemy import Column, Integer, String, ForeignKey, UniqueConstraint, create_engine, text
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()


class Chat(Base):
    __tablename__ = "chats"
    id = Column(Integer, primary_key=True)
    chat_id = Column(String, unique=True, index=True)
    tg_username = Column(String)
    users = relationship("MonitoredUser", back_populates="chat", cascade="all, delete-orphan")


class MonitoredUser(Base):
    __tablename__ = "monitored_users"
    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, ForeignKey("chats.id"))
    username = Column(String)
    last_rating = Column(Integer)
    chat = relationship("Chat", back_populates="users")
    __table_args__ = (UniqueConstraint("chat_id", "username", name="chat_username_uc"),)


def _resolve_db_url(db_path: str | None = None) -> str:
    if db_path:
        return db_path
    env_url = os.getenv("DB_URL") or os.getenv("DATABASE_URL")
    return env_url or "sqlite:///data.db"


def _ensure_schema(engine) -> None:
    with engine.begin() as conn:
        exists = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='chats'")
        ).fetchone()
        if not exists:
            return
        columns = conn.execute(text("PRAGMA table_info(chats)")).fetchall()
        column_names = {row[1] for row in columns}
        if "tg_username" not in column_names:
            conn.execute(text("ALTER TABLE chats ADD COLUMN tg_username VARCHAR"))


def get_engine(db_path: str | None = None):
    db_url = _resolve_db_url(db_path)
    connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
    return create_engine(db_url, connect_args=connect_args)


def init_db(db_path: str | None = None):
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    if engine.dialect.name == "sqlite":
        _ensure_schema(engine)
    return sessionmaker(bind=engine)
