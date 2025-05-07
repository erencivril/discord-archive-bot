# Database interaction module (using SQLAlchemy ORM)
import os
from sqlalchemy import create_engine, Column, Integer, String, Text, BigInteger, DateTime, UniqueConstraint, JSON
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.sql import func
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv('DATABASE_URL') # e.g., "postgresql://user:password@host:port/database"

if not DATABASE_URL:
    print("Error: DATABASE_URL not found in .env file.")
    # Consider falling back to SQLite for local development?
    # DATABASE_URL = "sqlite:///./discord_archive.db"
    # engine = create_engine(DATABASE_URL)
    exit() # Or handle appropriately
else:
    engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Define the AppLog table for application logging
class AppLog(Base):
    __tablename__ = 'app_logs'

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    level = Column(String(20), nullable=False, default="INFO")  # e.g., INFO, WARNING, ERROR
    event_type = Column(String(100), nullable=False)  # e.g., "attachment_sent", "message_deleted"
    message = Column(Text, nullable=False)
    extra = Column(JSON, nullable=True)  # Optional: store extra info as JSON

# Define the Message table
class Message(Base):
    __tablename__ = 'messages'

    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(BigInteger, unique=True, nullable=False, index=True)
    guild_id = Column(BigInteger, nullable=False, index=True)
    channel_id = Column(BigInteger, nullable=False)
    author_id = Column(BigInteger, nullable=False)
    author_name = Column(String(255)) # Store author name for context if needed
    content = Column(Text, nullable=False)
    timestamp = Column(DateTime, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint('message_id', name='uq_message_id'),)

# Define the Attachment table
class Attachment(Base):
    __tablename__ = 'attachments'

    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(BigInteger, nullable=False, index=True) # Foreign key relationship could be added
    attachment_id = Column(BigInteger, unique=True, nullable=False, index=True)
    url = Column(Text, nullable=False)
    filename = Column(String(255))
    content_type = Column(String(100)) # e.g., 'image/png', 'video/mp4'
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint('attachment_id', name='uq_attachment_id'),)


# Function to initialize the database (create tables)
def init_db():
    Base.metadata.create_all(bind=engine)
    print("Database tables created (if they didn't exist).")

# --- Functions for interacting with the database ---

def add_message(db_session, msg_data):
    """Adds a message and its attachments to the database, ensuring uniqueness."""
    # Check if message already exists
    existing_message = db_session.query(Message).filter(Message.message_id == msg_data['message_id']).first()
    if existing_message:
        # print(f"Message {msg_data['message_id']} already exists. Skipping.")
        return False # Indicate skipped

    # Add message
    new_message = Message(
        message_id=msg_data['message_id'],
        guild_id=msg_data['guild_id'],
        channel_id=msg_data['channel_id'],
        author_id=msg_data['author_id'],
        author_name=msg_data['author_name'],
        content=msg_data['content'],
        timestamp=msg_data['timestamp']
    )
    db_session.add(new_message)

    # Add attachments
    for att_data in msg_data.get('attachments', []):
         # Check if attachment already exists
        existing_attachment = db_session.query(Attachment).filter(Attachment.attachment_id == att_data['attachment_id']).first()
        if not existing_attachment:
            new_attachment = Attachment(
                message_id=msg_data['message_id'], # Link back to the message
                attachment_id=att_data['attachment_id'],
                url=att_data['url'],
                filename=att_data['filename'],
                content_type=att_data['content_type']
            )
            db_session.add(new_attachment)
        # else:
            # print(f"Attachment {att_data['attachment_id']} already exists. Skipping.")

    try:
        db_session.commit()
        return True # Indicate success
    except Exception as e:
        db_session.rollback()
        print(f"Error adding message {msg_data['message_id']}: {e}")
        return False # Indicate failure

def get_random_message(db_session):
    """Fetches a random Message object from the database."""
    # Note: Efficiently getting a random row can depend on DB size and engine.
    # This is a common approach but might be slow on very large tables.
    # Consider alternatives like indexed random sampling if performance is critical.
    return db_session.query(Message).order_by(func.random()).first()

def get_random_attachment(db_session):
    """Fetches a random Attachment object from the database."""
    return db_session.query(Attachment).order_by(func.random()).first()

def get_recent_messages_for_context(db_session, limit=50):
    """Fetches recent messages to potentially use as context for the AI."""
    messages = db_session.query(Message.author_name, Message.content)\
                         .order_by(Message.timestamp.desc())\
                         .limit(limit)\
                         .all()
    # Format for AI context (e.g., "User1: message\nUser2: another message")
    context = "\n".join([f"{name}: {content}" for name, content in reversed(messages)])
    return context


def delete_message(db_session, message_id_to_delete):
    """Deletes a message and its associated attachments by message_id."""
    try:
        # Delete attachments first (if any)
        db_session.query(Attachment).filter(Attachment.message_id == message_id_to_delete).delete()
        # Delete the message
        deleted_count = db_session.query(Message).filter(Message.message_id == message_id_to_delete).delete()
        db_session.commit()
        return deleted_count > 0 # Return True if a message was deleted
    except Exception as e:
        db_session.rollback()
        print(f"Error deleting message {message_id_to_delete}: {e}")
        return False

def log_app_event(db_session, level, event_type, message, extra=None):
    """Log an application event to the AppLog table."""
    log_entry = AppLog(
        level=level,
        event_type=event_type,
        message=message,
        extra=extra
    )
    db_session.add(log_entry)
    # Commit should happen in the calling scope

# Example usage (can be run directly to initialize DB)
if __name__ == "__main__":
    print("Initializing database...")
    init_db()
    print("Database initialization complete.")
