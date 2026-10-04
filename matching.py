"""
Simple rule-based matching engine for RentFlock.
Find verified available buddies nearby, rank by rating + experience.
"""

from database import SessionLocal, User, BuddyRequest, Match


def find_best_buddy(request_id: int):
    """
    Find the best available verified buddy for a given request.
    Ranking: higher rating + more completed viewings + same neighborhood preferred.
    """
    session = SessionLocal()
    try:
        request = session.query(BuddyRequest).filter(BuddyRequest.id == request_id).first()
        if not request:
            return None

        # Get all verified, available buddies
        buddies = (
            session.query(User)
            .filter(
                User.role == "buddy",
                User.verification_status == "verified",
                User.available == True,
            )
            .all()
        )

        if not buddies:
            return None

        def score(buddy: User) -> float:
            # Base score from rating (0-5 -> 0-50)
            s = buddy.rating * 10
            # Experience bonus
            s += min(buddy.completed_viewings * 0.5, 20)
            # Neighborhood match bonus
            if buddy.neighborhood and request.location:
                if buddy.neighborhood.lower() in request.location.lower() or \
                   request.location.lower() in buddy.neighborhood.lower():
                    s += 15
            # Attendance bonus
            s += (buddy.attendance_rate / 100) * 5
            return s

        # Sort by score descending
        ranked = sorted(buddies, key=score, reverse=True)
        best = ranked[0]

        # Create match
        match = Match(
            request_id=request.id,
            buddy_id=best.id,
            status="confirmed"
        )
        request.status = "matched"
        session.add(match)
        session.commit()

        # Refresh to get relationships
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
            }
        }
    finally:
        session.close()


def get_open_requests_for_buddy(buddy_id: int = None):
    """Return open requests (for buddy dashboard demo)."""
    session = SessionLocal()
    try:
        requests = (
            session.query(BuddyRequest)
            .filter(BuddyRequest.status == "open")
            .order_by(BuddyRequest.created_at.desc())
            .all()
        )
        return [
            {
                "id": r.id,
                "location": r.location,
                "date": r.date,
                "time": r.time,
                "duration": r.duration,
                "reward": r.reward,
                "notes": r.notes,
                "renter_name": r.renter.name if r.renter else "Renter",
            }
            for r in requests
        ]
    finally:
        session.close()