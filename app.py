from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from database import (
    init_db, SessionLocal, User, BuddyRequest, Match, ListingAnalysis,
    ViewingReport, ChatMessage, Notification, BuddyIgnore
)
from matching import (
    find_best_buddy, get_open_requests_for_buddy, confirm_match,
    decline_match, ignore_buddy_for_renter, expire_stale_matches
)
from ai import analyze_listing
from datetime import datetime, timedelta
from urllib.parse import quote
import os

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "rentflock-hackyeah-demo-secret-2025")

init_db()


def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    db = SessionLocal()
    try:
        return db.query(User).filter(User.id == user_id).first()
    finally:
        db.close()


def add_notification(user_id, title, body=""):
    db = SessionLocal()
    try:
        n = Notification(user_id=user_id, title=title, body=body)
        db.add(n)
        db.commit()
    finally:
        db.close()


def google_calendar_link(title, location, date_str, time_str, duration_min=30):
    """Build a Google Calendar template URL (no API key needed)."""
    # Try to parse simple date/time for demo
    # date_str like "Today", "Tomorrow", "2025-10-05"
    # time_str like "18:00"
    now = datetime.utcnow()
    if date_str.lower() == "today":
        d = now.date()
    elif date_str.lower() == "tomorrow":
        d = (now + timedelta(days=1)).date()
    else:
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
        except Exception:
            d = now.date()
    try:
        t = datetime.strptime(time_str, "%H:%M").time()
    except Exception:
        t = datetime.strptime("18:00", "%H:%M").time()
    start = datetime.combine(d, t)
    end = start + timedelta(minutes=duration_min)
    # Google Calendar format: YYYYMMDDTHHMMSSZ (we use local-ish without Z for simplicity)
    fmt = "%Y%m%dT%H%M%S"
    dates = f"{start.strftime(fmt)}/{end.strftime(fmt)}"
    params = (
        f"action=TEMPLATE"
        f"&text={quote(title)}"
        f"&dates={dates}"
        f"&location={quote(location)}"
        f"&details={quote('RentFlock viewing')}"
    )
    return f"https://calendar.google.com/calendar/render?{params}"


@app.route("/")
def index():
    user = get_current_user()
    return render_template("index.html", user=user)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        role = request.form.get("role", "renter")
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.email == email).first()
            if not user:
                name = email.split("@")[0].capitalize()
                user = User(
                    name=name,
                    email=email,
                    role=role,
                    verification_status="verified" if role == "buddy" else "unverified",
                    neighborhood="Krowodrza"
                )
                db.add(user)
                db.commit()
                db.refresh(user)
            session["user_id"] = user.id
            session["role"] = user.role
            flash(f"Welcome, {user.name}!", "success")
            if user.role == "buddy":
                return redirect(url_for("buddy_dashboard"))
            return redirect(url_for("dashboard"))
        finally:
            db.close()
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for("index"))


@app.route("/dashboard")
def dashboard():
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))
    if user.role == "buddy":
        return redirect(url_for("buddy_dashboard"))

    expire_stale_matches(3)

    db = SessionLocal()
    try:
        upcoming = (
            db.query(BuddyRequest)
            .filter(
                BuddyRequest.renter_id == user.id,
                BuddyRequest.status.in_(["matched", "open", "pending"])
            )
            .order_by(BuddyRequest.created_at.desc())
            .limit(10)
            .all()
        )
        upcoming_data = []
        for r in upcoming:
            buddy_name = None
            match_status = None
            match_id = None
            if r.match:
                buddy = db.query(User).filter(User.id == r.match.buddy_id).first()
                buddy_name = buddy.name if buddy else None
                match_status = r.match.status
                match_id = r.match.id
            upcoming_data.append({
                "id": r.id,
                "location": r.location,
                "date": r.date,
                "time": r.time,
                "status": r.status,
                "buddy_name": buddy_name,
                "match_status": match_status,
                "match_id": match_id,
                "reward": r.reward,
            })

        # Unread notifications
        notifs = (
            db.query(Notification)
            .filter(Notification.user_id == user.id, Notification.is_read == False)
            .order_by(Notification.created_at.desc())
            .limit(5)
            .all()
        )
        return render_template(
            "dashboard.html",
            user=user,
            upcoming=upcoming_data,
            notifications=notifs,
        )
    finally:
        db.close()


@app.route("/check", methods=["GET", "POST"])
def check():
    user = get_current_user()
    result = None
    listing_text = ""

    if request.method == "POST":
        listing_text = request.form.get("listing_text", "").strip()
        if listing_text:
            result = analyze_listing(listing_text)
            db = SessionLocal()
            try:
                analysis = ListingAnalysis(
                    renter_id=user.id if user else None,
                    listing_text=listing_text,
                    analysis_result=result
                )
                db.add(analysis)
                db.commit()
            finally:
                db.close()

    return render_template("check.html", user=user, result=result, listing_text=listing_text)


@app.route("/buddy", methods=["GET", "POST"])
def buddy():
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))
    if user.role == "buddy":
        return redirect(url_for("buddy_dashboard"))

    expire_stale_matches(3)

    if request.method == "POST":
        action = request.form.get("action")
        if action == "request":
            location = request.form.get("location", "").strip()
            date = request.form.get("date", "").strip()
            time = request.form.get("time", "").strip()
            duration = int(request.form.get("duration", 30))
            reward = float(request.form.get("reward", 20))
            notes = request.form.get("notes", "").strip()

            db = SessionLocal()
            try:
                req = BuddyRequest(
                    renter_id=user.id,
                    location=location,
                    date=date,
                    time=time,
                    duration=duration,
                    reward=reward,
                    notes=notes,
                    status="open"
                )
                db.add(req)
                db.commit()
                db.refresh(req)
                match_result = find_best_buddy(req.id)
                if match_result:
                    # Notify the buddy
                    add_notification(
                        match_result["buddy"]["id"],
                        "New viewing request!",
                        f"{user.name} needs a buddy in {location} on {date} at {time}."
                    )
                    return redirect(url_for("waiting", match_id=match_result["match_id"]))
                else:
                    flash("No available buddies right now. Try again later.", "warning")
            finally:
                db.close()

    return render_template("buddy.html", user=user, match=None)


@app.route("/waiting/<int:match_id>")
def waiting(match_id):
    """Renter waiting page — pending confirmation + chat."""
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    expire_stale_matches(3)

    db = SessionLocal()
    try:
        match = db.query(Match).filter(Match.id == match_id).first()
        if not match or match.request.renter_id != user.id:
            flash("Match not found.", "error")
            return redirect(url_for("dashboard"))

        buddy = db.query(User).filter(User.id == match.buddy_id).first()
        req = match.request
        messages = (
            db.query(ChatMessage)
            .filter(ChatMessage.match_id == match.id)
            .order_by(ChatMessage.created_at.asc())
            .all()
        )
        msg_data = [
            {
                "id": m.id,
                "sender_id": m.sender_id,
                "sender_name": m.sender.name if m.sender else "?",
                "content": m.content,
                "created_at": m.created_at.strftime("%H:%M") if m.created_at else "",
                "is_me": m.sender_id == user.id,
            }
            for m in messages
        ]

        # Seconds remaining until 3-min timeout
        elapsed = (datetime.utcnow() - match.created_at).total_seconds() if match.created_at else 0
        remaining = max(0, 180 - int(elapsed))

        return render_template(
            "waiting.html",
            user=user,
            match=match,
            buddy=buddy,
            request_obj=req,
            messages=msg_data,
            remaining_seconds=remaining,
        )
    finally:
        db.close()


@app.route("/waiting/<int:match_id>/status")
def waiting_status(match_id):
    """AJAX: check match status for polling."""
    user = get_current_user()
    if not user:
        return jsonify({"error": "auth"}), 401

    expire_stale_matches(3)

    db = SessionLocal()
    try:
        match = db.query(Match).filter(Match.id == match_id).first()
        if not match or match.request.renter_id != user.id:
            return jsonify({"error": "not found"}), 404

        elapsed = (datetime.utcnow() - match.created_at).total_seconds() if match.created_at else 0
        remaining = max(0, 180 - int(elapsed))

        return jsonify({
            "status": match.status,
            "remaining_seconds": remaining,
        })
    finally:
        db.close()


@app.route("/waiting/<int:match_id>/rematch", methods=["POST"])
def rematch(match_id):
    """Look for another buddy (ignore current for 15 min)."""
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    db = SessionLocal()
    try:
        match = db.query(Match).filter(Match.id == match_id).first()
        if not match or match.request.renter_id != user.id:
            flash("Match not found.", "error")
            return redirect(url_for("dashboard"))

        old_buddy_id = match.buddy_id
        req = match.request

        # Expire current
        match.status = "expired"
        req.status = "open"
        db.commit()

        # Ignore old buddy for 15 min
        ignore_buddy_for_renter(user.id, old_buddy_id, 15)

        # Find new
        new_match = find_best_buddy(req.id, exclude_buddy_ids=[old_buddy_id])
        if new_match:
            add_notification(
                new_match["buddy"]["id"],
                "New viewing request!",
                f"{user.name} needs a buddy in {req.location}."
            )
            flash("Looking for another buddy...", "info")
            return redirect(url_for("waiting", match_id=new_match["match_id"]))
        else:
            flash("No other buddies available right now.", "warning")
            return redirect(url_for("dashboard"))
    finally:
        db.close()


@app.route("/waiting/<int:match_id>/waitmore", methods=["POST"])
def wait_more(match_id):
    """Reset the 3-minute timer by touching created_at."""
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    db = SessionLocal()
    try:
        match = db.query(Match).filter(Match.id == match_id, Match.status == "pending").first()
        if match and match.request.renter_id == user.id:
            match.created_at = datetime.utcnow()
            db.commit()
            flash("Waiting 3 more minutes...", "info")
        return redirect(url_for("waiting", match_id=match_id))
    finally:
        db.close()


@app.route("/waiting/<int:match_id>/cancel", methods=["POST"])
def cancel_waiting(match_id):
    """Cancel matching from waiting page."""
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    db = SessionLocal()
    try:
        match = db.query(Match).filter(Match.id == match_id).first()
        if match and match.request.renter_id == user.id:
            match.status = "cancelled"
            match.cancelled_by = user.id
            match.cancel_reason = "Cancelled while waiting for confirmation"
            match.request.status = "cancelled"
            db.commit()
            add_notification(
                match.buddy_id,
                "Request cancelled",
                f"The renter cancelled the viewing request for {match.request.location}."
            )
            flash("Matching cancelled.", "info")
        return redirect(url_for("dashboard"))
    finally:
        db.close()


@app.route("/buddy/dashboard")
def buddy_dashboard():
    user = get_current_user()
    if not user or user.role != "buddy":
        return redirect(url_for("login"))

    expire_stale_matches(3)
    open_requests = get_open_requests_for_buddy(user.id)

    db = SessionLocal()
    try:
        my_matches = (
            db.query(Match)
            .filter(
                Match.buddy_id == user.id,
                Match.status.in_(["confirmed", "pending"])
            )
            .order_by(Match.created_at.desc())
            .limit(10)
            .all()
        )
        matches_data = []
        for m in my_matches:
            req = m.request
            matches_data.append({
                "match_id": m.id,
                "request_id": req.id,
                "location": req.location,
                "date": req.date,
                "time": req.time,
                "reward": req.reward,
                "status": m.status,
                "renter_name": req.renter.name if req.renter else "Renter",
            })

        notifs = (
            db.query(Notification)
            .filter(Notification.user_id == user.id, Notification.is_read == False)
            .order_by(Notification.created_at.desc())
            .limit(5)
            .all()
        )
        return render_template(
            "buddy_dashboard.html",
            user=user,
            open_requests=open_requests,
            matches=matches_data,
            notifications=notifs,
        )
    finally:
        db.close()


@app.route("/buddy/confirm/<int:match_id>", methods=["POST"])
def buddy_confirm(match_id):
    user = get_current_user()
    if not user or user.role != "buddy":
        return redirect(url_for("login"))

    if confirm_match(match_id, user.id):
        db = SessionLocal()
        try:
            match = db.query(Match).filter(Match.id == match_id).first()
            if match:
                add_notification(
                    match.request.renter_id,
                    "Buddy confirmed! ✓",
                    f"{user.name} accepted your request for {match.request.location}."
                )
        finally:
            db.close()
        flash("You confirmed the viewing!", "success")
    else:
        flash("Could not confirm (maybe expired).", "warning")
    return redirect(url_for("buddy_dashboard"))


@app.route("/buddy/decline/<int:match_id>", methods=["POST"])
def buddy_decline(match_id):
    user = get_current_user()
    if not user or user.role != "buddy":
        return redirect(url_for("login"))

    if decline_match(match_id, user.id):
        flash("You declined the request.", "info")
    return redirect(url_for("buddy_dashboard"))


@app.route("/buddy/accept/<int:request_id>", methods=["POST"])
def accept_request(request_id):
    """Legacy: accept an open request (creates pending then auto-confirm for demo speed)."""
    user = get_current_user()
    if not user or user.role != "buddy":
        return redirect(url_for("login"))

    db = SessionLocal()
    try:
        req = db.query(BuddyRequest).filter(
            BuddyRequest.id == request_id,
            BuddyRequest.status == "open"
        ).first()
        if req:
            match = Match(request_id=req.id, buddy_id=user.id, status="confirmed",
                          confirmed_at=datetime.utcnow())
            req.status = "matched"
            db.add(match)
            db.commit()
            add_notification(
                req.renter_id,
                "Buddy confirmed! ✓",
                f"{user.name} accepted your request for {req.location}."
            )
            flash("You accepted the request!", "success")
    finally:
        db.close()
    return redirect(url_for("buddy_dashboard"))


# ─── Chat ───────────────────────────────────────────────

@app.route("/chat/<int:match_id>", methods=["GET", "POST"])
def chat(match_id):
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    db = SessionLocal()
    try:
        match = db.query(Match).filter(Match.id == match_id).first()
        if not match:
            flash("Match not found.", "error")
            return redirect(url_for("dashboard"))

        is_renter = match.request.renter_id == user.id
        is_buddy = match.buddy_id == user.id
        if not (is_renter or is_buddy):
            flash("Access denied.", "error")
            return redirect(url_for("dashboard"))

        if request.method == "POST":
            content = request.form.get("content", "").strip()
            if content:
                msg = ChatMessage(match_id=match.id, sender_id=user.id, content=content)
                db.add(msg)
                db.commit()
            # Stay on same page or return to waiting
            next_url = request.form.get("next") or request.referrer
            if next_url:
                return redirect(next_url)
            return redirect(url_for("chat", match_id=match_id))

        buddy = db.query(User).filter(User.id == match.buddy_id).first()
        renter = match.request.renter
        messages = (
            db.query(ChatMessage)
            .filter(ChatMessage.match_id == match.id)
            .order_by(ChatMessage.created_at.asc())
            .all()
        )
        msg_data = [
            {
                "id": m.id,
                "sender_id": m.sender_id,
                "sender_name": m.sender.name if m.sender else "?",
                "content": m.content,
                "created_at": m.created_at.strftime("%H:%M") if m.created_at else "",
                "is_me": m.sender_id == user.id,
            }
            for m in messages
        ]
        other_name = buddy.name if is_renter else (renter.name if renter else "Renter")

        return render_template(
            "chat.html",
            user=user,
            match=match,
            messages=msg_data,
            other_name=other_name,
            request_obj=match.request,
        )
    finally:
        db.close()


@app.route("/chat/<int:match_id>/messages")
def chat_messages_json(match_id):
    """AJAX poll for new messages."""
    user = get_current_user()
    if not user:
        return jsonify([])
    db = SessionLocal()
    try:
        match = db.query(Match).filter(Match.id == match_id).first()
        if not match:
            return jsonify([])
        if match.request.renter_id != user.id and match.buddy_id != user.id:
            return jsonify([])
        messages = (
            db.query(ChatMessage)
            .filter(ChatMessage.match_id == match.id)
            .order_by(ChatMessage.created_at.asc())
            .all()
        )
        return jsonify([
            {
                "id": m.id,
                "sender_id": m.sender_id,
                "sender_name": m.sender.name if m.sender else "?",
                "content": m.content,
                "created_at": m.created_at.strftime("%H:%M") if m.created_at else "",
                "is_me": m.sender_id == user.id,
            }
            for m in messages
        ])
    finally:
        db.close()


# ─── Cancel meeting ─────────────────────────────────────

@app.route("/cancel/<int:match_id>", methods=["GET", "POST"])
def cancel_meeting(match_id):
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    db = SessionLocal()
    try:
        match = db.query(Match).filter(Match.id == match_id).first()
        if not match:
            flash("Match not found.", "error")
            return redirect(url_for("dashboard"))

        is_renter = match.request.renter_id == user.id
        is_buddy = match.buddy_id == user.id
        if not (is_renter or is_buddy):
            flash("Access denied.", "error")
            return redirect(url_for("dashboard"))

        if request.method == "POST":
            reason = request.form.get("reason", "").strip()
            if len(reason) < 10:
                flash("Please provide a reason (at least 10 characters).", "warning")
                return render_template(
                    "cancel.html", user=user, match=match, request_obj=match.request
                )
            match.status = "cancelled"
            match.cancelled_by = user.id
            match.cancel_reason = reason
            match.request.status = "cancelled"
            db.commit()

            # Notify the other person
            other_id = match.buddy_id if is_renter else match.request.renter_id
            when = f"{match.request.date} at {match.request.time}"
            add_notification(
                other_id,
                f"Meeting cancelled — {when}",
                f"They reported that they needed to deal with something.\n\n"
                f"\"{reason}\"\n\nSorry for the inconvenience."
            )
            flash("Meeting cancelled. The other person has been notified.", "info")
            if user.role == "buddy":
                return redirect(url_for("buddy_dashboard"))
            return redirect(url_for("dashboard"))

        return render_template(
            "cancel.html", user=user, match=match, request_obj=match.request
        )
    finally:
        db.close()


# ─── Schedule / Calendar ────────────────────────────────

@app.route("/schedule")
def schedule():
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    db = SessionLocal()
    try:
        items = []
        if user.role == "renter":
            reqs = (
                db.query(BuddyRequest)
                .filter(
                    BuddyRequest.renter_id == user.id,
                    BuddyRequest.status.in_(["matched", "pending"])
                )
                .order_by(BuddyRequest.created_at.desc())
                .all()
            )
            for r in reqs:
                buddy_name = None
                match_id = None
                mstatus = None
                if r.match:
                    b = db.query(User).filter(User.id == r.match.buddy_id).first()
                    buddy_name = b.name if b else None
                    match_id = r.match.id
                    mstatus = r.match.status
                gcal = google_calendar_link(
                    f"RentFlock viewing — {r.location}",
                    r.location, r.date, r.time, r.duration
                )
                items.append({
                    "location": r.location,
                    "date": r.date,
                    "time": r.time,
                    "duration": r.duration,
                    "reward": r.reward,
                    "status": r.status,
                    "match_status": mstatus,
                    "other_name": buddy_name,
                    "match_id": match_id,
                    "gcal": gcal,
                    "role": "renter",
                })
        else:
            matches = (
                db.query(Match)
                .filter(
                    Match.buddy_id == user.id,
                    Match.status.in_(["confirmed", "pending"])
                )
                .order_by(Match.created_at.desc())
                .all()
            )
            for m in matches:
                r = m.request
                gcal = google_calendar_link(
                    f"RentFlock buddy job — {r.location}",
                    r.location, r.date, r.time, r.duration
                )
                items.append({
                    "location": r.location,
                    "date": r.date,
                    "time": r.time,
                    "duration": r.duration,
                    "reward": r.reward,
                    "status": m.status,
                    "match_status": m.status,
                    "other_name": r.renter.name if r.renter else "Renter",
                    "match_id": m.id,
                    "gcal": gcal,
                    "role": "buddy",
                })

        return render_template("schedule.html", user=user, items=items)
    finally:
        db.close()


@app.route("/verify/<int:request_id>", methods=["GET", "POST"])
def verify(request_id):
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    db = SessionLocal()
    try:
        req = db.query(BuddyRequest).filter(BuddyRequest.id == request_id).first()
        if not req:
            flash("Viewing not found.", "error")
            return redirect(url_for("dashboard"))

        is_renter = req.renter_id == user.id
        is_buddy = req.match and req.match.buddy_id == user.id
        if not (is_renter or is_buddy):
            flash("You don't have access to this viewing.", "error")
            return redirect(url_for("dashboard"))

        existing_report = req.report

        if request.method == "POST":
            checklist = {
                "apartment": {
                    "shower": request.form.get("shower") == "on",
                    "kitchen": request.form.get("kitchen") == "on",
                    "appliances": request.form.get("appliances") == "on",
                    "heating": request.form.get("heating") == "on",
                    "windows": request.form.get("windows") == "on",
                    "ventilation": request.form.get("ventilation") == "on",
                    "mold": request.form.get("mold") == "on",
                    "electricity": request.form.get("electricity") == "on",
                },
                "costs": {
                    "rent_clear": request.form.get("rent_clear") == "on",
                    "utilities_clear": request.form.get("utilities_clear") == "on",
                    "meters": request.form.get("meters") == "on",
                    "included": request.form.get("included") == "on",
                },
                "documents": {
                    "owner_id": request.form.get("owner_id") == "on",
                    "proof_ownership": request.form.get("proof_ownership") == "on",
                    "ksiega": request.form.get("ksiega") == "on",
                },
                "payment": {
                    "bank_transfer": request.form.get("bank_transfer") == "on",
                    "deposit_clear": request.form.get("deposit_clear") == "on",
                },
                "contract": {
                    "notice_period": request.form.get("notice_period") == "on",
                    "early_termination": request.form.get("early_termination") == "on",
                    "deposit_return": request.form.get("deposit_return") == "on",
                },
            }
            notes = request.form.get("notes", "")

            issues = []
            if not checklist["apartment"]["heating"]:
                issues.append("Heating needs checking")
            if not checklist["costs"]["utilities_clear"]:
                issues.append("Utility costs unclear")
            if not checklist["documents"]["proof_ownership"]:
                issues.append("Ownership documentation still to verify")
            if not checklist["payment"]["bank_transfer"]:
                issues.append("Bank transfer not confirmed")
            if not checklist["contract"]["early_termination"]:
                issues.append("Early termination clause needs clarification")

            summary = ("⚠ Items to clarify: " + "; ".join(issues)) if issues else "✓ Most checklist items look good."

            if existing_report:
                existing_report.checklist_data = checklist
                existing_report.notes = notes
                existing_report.summary = summary
            else:
                report = ViewingReport(
                    request_id=req.id,
                    checklist_data=checklist,
                    notes=notes,
                    summary=summary
                )
                db.add(report)
            db.commit()
            flash("Checklist saved!", "success")
            return redirect(url_for("verify", request_id=request_id))

        buddy_name = None
        match_id = None
        if req.match:
            buddy = db.query(User).filter(User.id == req.match.buddy_id).first()
            buddy_name = buddy.name if buddy else None
            match_id = req.match.id

        return render_template(
            "verify.html",
            user=user,
            request_obj=req,
            buddy_name=buddy_name,
            match_id=match_id,
            report=existing_report,
        )
    finally:
        db.close()


@app.route("/notifications/read", methods=["POST"])
def mark_notifications_read():
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))
    db = SessionLocal()
    try:
        db.query(Notification).filter(
            Notification.user_id == user.id, Notification.is_read == False
        ).update({"is_read": True})
        db.commit()
    finally:
        db.close()
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/demo")
def demo_login():
    role = request.args.get("role", "renter")
    email = "anna@demo.com" if role == "renter" else "marta@demo.com"
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if user:
            session["user_id"] = user.id
            session["role"] = user.role
            flash(f"Logged in as {user.name} ({user.role})", "success")
            if user.role == "buddy":
                return redirect(url_for("buddy_dashboard"))
            return redirect(url_for("dashboard"))
    finally:
        db.close()
    return redirect(url_for("login"))


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)