"""
Simple rule-based matching engine for RentFlock.
Creates PENDING matches that require buddy confirmation.
"""

from database import SessionLocal, User, BuddyRequest, Match, BuddyIgnore
from datetime import datetime, timedelta


def find_best_buddy(request_id: int, exclude_buddy_ids: list = None):
    session = SessionLocal()
    try:
        request = session.query(BuddyRequest).filter(BuddyRequest.id == request_id).first()
        if not request:
            return None

        exclude = set(exclude_buddy_ids or [])
        now = datetime.utcnow()
        ignores = (
            session.query(BuddyIgnore)
            .filter(BuddyIgnore.renter_id == request.renter_id, BuddyIgnore.until > now)
            .all()
        )
        for ig in ignores:
            exclude.add(ig.buddy_id)

        query = (
            session.query(User)
            .filter(
                User.role == "buddy",
                User.verification_status == "verified",
                User.available == True,
            )
        )
        if exclude:
            query = query.filter(~User.id.in_(list(exclude)))

        buddies = query.all()
        if not buddies:
            return None

        def score(buddy: User) -> float:
            s = buddy.rating * 10
            s += min(buddy.completed_viewings * 0.5, 20)
            if buddy.neighborhood and request.location:
                if buddy.neighborhood.lower() in request.location.lower() or \
                   request.location.lower() in buddy.neighborhood.lower():
                    s += 15
            s += (buddy.attendance_rate / 100) * 5
            return s

        ranked = sorted(buddies, key=score, reverse=True)
        best = ranked[0]

        match = Match(request_id=request.id, buddy_id=best.id, status="pending")
        request.status = "pending"
        session.add(match)
        session.commit()
        session.refresh(match)
        session.refresh(best)

        return {
            "match_id": match.id,
            "buddy": {
                "id": best.id,
                "name": best.name,
                "rating": best.rating,
                "completed_viewings": best.completed_viewings,
                "attendance_rate": best.attendance_rate,
                "neighborhood": best.neighborhood,
                "verification_status": best.verification_status,
            },
            "request": {
                "id": request.id,
                "location": request.location,
                "date": request.date,
                "time": request.time,
                "duration": request.duration,
                "reward": request.reward,
            },
            "status": "pending",
            "created_at": match.created_at.isoformat() if match.created_at else None,
        }
    finally:
        session.close()


def confirm_match(match_id: int, buddy_id: int):
    session = SessionLocal()
    try:
        match = session.query(Match).filter(
            Match.id == match_id, Match.buddy_id == buddy_id, Match.status == "pending"
        ).first()
        if not match:
            return False
        match.status = "confirmed"
        match.confirmed_at = datetime.utcnow()
        match.request.status = "matched"
        session.commit()
        return True
    finally:
        session.close()


def decline_match(match_id: int, buddy_id: int):
    session = SessionLocal()
    try:
        match = session.query(Match).filter(
            Match.id == match_id, Match.buddy_id == buddy_id, Match.status == "pending"
        ).first()
        if not match:
            return False
        match.status = "expired"
        match.request.status = "open"
        session.commit()
        return True
    finally:
        session.close()


def ignore_buddy_for_renter(renter_id: int, buddy_id: int, minutes: int = 15):
    session = SessionLocal()
    try:
        until = datetime.utcnow() + timedelta(minutes=minutes)
        session.add(BuddyIgnore(renter_id=renter_id, buddy_id=buddy_id, until=until))
        session.commit()
    finally:
        session.close()


def expire_stale_matches(minutes: int = 3):
    session = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(minutes=minutes)
        stale = session.query(Match).filter(
            Match.status == "pending", Match.created_at < cutoff
        ).all()
        for m in stale:
            m.status = "expired"
            if m.request and m.request.status == "pending":
                m.request.status = "open"
        session.commit()
        return len(stale)
    finally:
        session.close()


def get_open_requests_for_buddy(buddy_id: int = None):
    session = SessionLocal()
    try:
        open_reqs = (
            session.query(BuddyRequest)
            .filter(BuddyRequest.status == "open")
            .order_by(BuddyRequest.created_at.desc())
            .all()
        )
        result = []
        for r in open_reqs:
            result.append({
                "id": r.id, "type": "open",
                "location": r.location, "date": r.date, "time": r.time,
                "duration": r.duration, "reward": r.reward, "notes": r.notes,
                "renter_name": r.renter.name if r.renter else "Renter",
            })
        if buddy_id:
            pending = session.query(Match).filter(
                Match.buddy_id == buddy_id, Match.status == "pending"
            ).all()
            for m in pending:
                r = m.request
                result.append({
                    "id": r.id, "match_id": m.id, "type": "pending",
                    "location": r.location, "date": r.date, "time": r.time,
                    "duration": r.duration, "reward": r.reward, "notes": r.notes,
                    "renter_name": r.renter.name if r.renter else "Renter",
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                })
        return result
    finally:
        session.close()