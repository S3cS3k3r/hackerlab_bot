from sqlalchemy import Column, Integer, String, ForeignKey, UniqueConstraint, create_engine
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()


class Chat(Base):
    __tablename__ = "chats"
    id = Column(Integer, primary_key=True)
    chat_id = Column(String, unique=True, index=True)
    users = relationship("MonitoredUser", back_populates="chat", cascade="all, delete-orphan")


class MonitoredUser(Base):
    __tablename__ = "monitored_users"
    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, ForeignKey("chats.id"))
    username = Column(String)
    last_rating = Column(Integer)
    chat = relationship("Chat", back_populates="users")
    __table_args__ = (UniqueConstraint("chat_id", "username", name="chat_username_uc"),)


def get_engine(db_path: str = "sqlite:///data.db"):
    return create_engine(db_path, connect_args={"check_same_thread": False})


def init_db(db_path: str = "sqlite:///data.db"):
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)