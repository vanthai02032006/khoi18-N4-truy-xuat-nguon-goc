from sqlalchemy import create_engine, Integer, String, Index, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session
from sqlalchemy.exc import IntegrityError

class Base(DeclarativeBase):
    pass

class Handover(Base):
    __tablename__ = "handovers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)

    __table_args__ = (
        Index(
            "uq_batch_pending_handover",
            "batch_id",
            unique=True,
            sqlite_where=text("status = 'pending'"),
            postgresql_where=text("status = 'pending'"),
        ),
    )

engine = create_engine("sqlite:///:memory:", echo=False)
Base.metadata.create_all(engine)

with Session(engine) as session:
    # 1. First pending handover
    h1 = Handover(batch_id=101, status="pending")
    session.add(h1)
    session.commit()
    print("1. First pending created successfully")

    # 2. Second pending handover for the SAME batch -> should raise IntegrityError
    try:
        h2 = Handover(batch_id=101, status="pending")
        session.add(h2)
        session.commit()
        print("ERROR: Duplicate pending succeeded, constraint failed!")
    except IntegrityError:
        session.rollback()
        print("2. SUCCESS: Duplicate pending was blocked by DB unique index!")

    # 3. Pending handover for a DIFFERENT batch -> should succeed
    h3 = Handover(batch_id=102, status="pending")
    session.add(h3)
    session.commit()
    print("3. SUCCESS: Pending for different batch succeeded")

    # 4. Accepted handover for the SAME batch 101 -> should succeed (since status is not pending)
    h4 = Handover(batch_id=101, status="accepted")
    session.add(h4)
    session.commit()
    print("4. SUCCESS: Accepted handover for batch 101 succeeded")
