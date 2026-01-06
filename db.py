import os
from pathlib import Path

from sqlalchemy import Column, Integer, String, ForeignKey, UniqueConstraint, create_engine, text
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()


class Chat(Base):
    __tablename__ = "chats"
    id = Column(Integer, primary_key=True)
    chat_id = Column(String, unique=True, index=True)
    tg_username = Column(String)
    first_name = Column(String)
    last_name = Column(String)
    users = relationship("MonitoredUser", back_populates="chat", cascade="all, delete-orphan")


class MonitoredUser(Base):
    __tablename__ = "monitored_users"
    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, ForeignKey("chats.id"))
    username = Column(String)
    last_rating = Column(Integer)
    chat = relationship("Chat", back_populates="users")
    __table_args__ = (UniqueConstraint("chat_id", "username", name="chat_username_uc"),)


def _sqlite_url(path: Path | str) -> str:
    path_str = str(path)
    return f"sqlite:///{path_str}"


def _default_sqlite_path() -> Path:
    env_dir = os.getenv("DB_DIR") or os.getenv("HACKERLAB_BOT_DATA_DIR")
    candidates = []
    if env_dir:
        candidates.append(Path(env_dir).expanduser())
    data_dir = Path("/data")
    if data_dir.exists():
        candidates.append(data_dir)
    xdg_home = os.getenv("XDG_DATA_HOME")
    if xdg_home:
        candidates.append(Path(xdg_home) / "hackerlab_bot")
    candidates.append(Path.home() / ".local" / "share" / "hackerlab_bot")
    candidates.append(Path("/tmp") / "hackerlab_bot")
    for base_dir in candidates:
        try:
            base_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        if os.access(base_dir, os.W_OK):
            return base_dir / "data.db"
    return Path("data.db")


def _resolve_db_url(db_path: str | None = None) -> str:
    if db_path:
        return db_path
    env_url = os.getenv("DB_URL") or os.getenv("DATABASE_URL")
    if env_url:
        return env_url
    env_path = os.getenv("DB_PATH")
    if env_path:
        return _sqlite_url(Path(env_path).expanduser())
    return _sqlite_url(_default_sqlite_path())


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
        if "first_name" not in column_names:
            conn.execute(text("ALTER TABLE chats ADD COLUMN first_name VARCHAR"))
        if "last_name" not in column_names:
            conn.execute(text("ALTER TABLE chats ADD COLUMN last_name VARCHAR"))


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
