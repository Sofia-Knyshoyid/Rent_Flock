from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, Text, DateTime, ForeignKey, JSON
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_data_dir = os.path.join(BASE_DIR, "data")
os.makedirs(_data_dir, exist_ok=True)
DB_PATH = os.path.join(_data_dir, "rentflock.db")
if not os.access(_data_dir, os.W_OK):
    DB_PATH = "/tmp/rentflock.db"

engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
Base = declarative_base()
SessionLocal = sessionmaker(bind=engine)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    email = Column(String(120), unique=True, nullable=False)
    role = Column(String(20), nullable=False)  # renter / buddy
    verification_status = Column(String(20), default="unverified")  # unverified / verified
    rating = Column(Float, default=5.0)
    completed_viewings = Column(Integer, default=0)
    attendance_rate = Column(Float, default=100.0)
    neighborhood = Column(String(100), default="")
    available = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    requests = relationship("BuddyRequest", back_populates="renter", foreign_keys="BuddyRequest.renter_id")
    matches = relationship("Match", back_populates="buddy", foreign_keys="Match.buddy_id")


class BuddyRequest(Base):
    __tablename__ = "buddy_requests"

    id = Column(Integer, primary_key=True)
    renter_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    location = Column(String(200), nullable=False)
    date = Column(String(50), nullable=False)
    time = Column(String(20), nullable=False)
    duration = Column(Integer, default=30)  # minutes
    reward = Column(Float, default=20.0)
    notes = Column(Text, default="")
    status = Column(String(30), default="open")  # open / matched / completed / cancelled
    created_at = Column(DateTime, default=datetime.utcnow)

    renter = relationship("User", back_populates="requests", foreign_keys=[renter_id])
    match = relationship("Match", back_populates="request", uselist=False)
    report = relationship("ViewingReport", back_populates="request", uselist=False)


class Match(Base):
    __tablename__ = "matches"

    id = Column(Integer, primary_key=True)
    request_id = Column(Integer, ForeignKey("buddy_requests.id"), nullable=False)
    buddy_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(String(30), default="confirmed")  # confirmed / completed / cancelled
    created_at = Column(DateTime, default=datetime.utcnow)

    request = relationship("BuddyRequest", back_populates="match")
    buddy = relationship("User", back_populates="matches", foreign_keys=[buddy_id])


class ListingAnalysis(Base):
    __tablename__ = "listing_analyses"

    id = Column(Integer, primary_key=True)
    renter_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    listing_text = Column(Text, nullable=False)
    analysis_result = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)


class ViewingReport(Base):
    __tablename__ = "viewing_reports"

    id = Column(Integer, primary_key=True)
    request_id = Column(Integer, ForeignKey("buddy_requests.id"), nullable=False)
    checklist_data = Column(JSON)
    notes = Column(Text, default="")
    summary = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    request = relationship("BuddyRequest", back_populates="report")


def init_db():
    """Create tables and seed demo data."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    Base.metadata.create_all(engine)

    session = SessionLocal()
    try:
        # Only seed if empty
        if session.query(User).count() == 0:
            # Demo Renter
            renter = User(
                name="Anna",
                email="anna@demo.com",
                role="renter",
                verification_status="verified",
                rating=5.0,
                neighborhood="Krowodrza"
            )
            # Demo Buddies
            marta = User(
                name="Marta",
                email="marta@demo.com",
                role="buddy",
                verification_status="verified",
                rating=4.92,
                completed_viewings=37,
                attendance_rate=98.0,
                neighborhood="Krowodrza",
                available=True
            )
            kuba = User(
                name="Kuba",
                email="kuba@demo.com",
                role="buddy",
                verification_status="verified",
                rating=4.85,
                completed_viewings=22,
                attendance_rate=95.0,
                neighborhood="Stare Miasto",
                available=True
            )
            ola = User(
                name="Ola",
                email="ola@demo.com",
                role="buddy",
                verification_status="verified",
                rating=4.78,
                completed_viewings=15,
                attendance_rate=100.0,
                neighborhood="Podgórze",
                available=True
            )
            session.add_all([renter, marta, kuba, ola])
            session.commit()
            print("✓ Demo data seeded")
    finally:
        session.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()