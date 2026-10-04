from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from database import init_db, SessionLocal, User, BuddyRequest, Match, ListingAnalysis, ViewingReport
from matching import find_best_buddy, get_open_requests_for_buddy
from ai import analyze_listing
from datetime import datetime
import os

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "rentflock-hackyeah-demo-secret-2026")

# Initialize database on startup
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
                # Create new demo user on the fly
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

    db = SessionLocal()
    try:
        # Upcoming matched requests
        upcoming = (
            db.query(BuddyRequest)
            .filter(
                BuddyRequest.renter_id == user.id,
                BuddyRequest.status.in_(["matched", "open"])
            )
            .order_by(BuddyRequest.created_at.desc())
            .limit(5)
            .all()
        )
        upcoming_data = []
        for r in upcoming:
            buddy_name = None
            if r.match:
                buddy = db.query(User).filter(User.id == r.match.buddy_id).first()
                buddy_name = buddy.name if buddy else None
            upcoming_data.append({
                "id": r.id,
                "location": r.location,
                "date": r.date,
                "time": r.time,
                "status": r.status,
                "buddy_name": buddy_name,
                "reward": r.reward,
            })
        return render_template("dashboard.html", user=user, upcoming=upcoming_data)
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
            # Optionally save
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

    match_result = None

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
                # Immediately try to match
                match_result = find_best_buddy(req.id)
                if match_result:
                    flash(f"Matched with {match_result['buddy']['name']}!", "success")
                else:
                    flash("No available buddies right now. Try again later.", "warning")
            finally:
                db.close()

    return render_template("buddy.html", user=user, match=match_result)


@app.route("/buddy/dashboard")
def buddy_dashboard():
    user = get_current_user()
    if not user or user.role != "buddy":
        return redirect(url_for("login"))

    open_requests = get_open_requests_for_buddy(user.id)

    db = SessionLocal()
    try:
        my_matches = (
            db.query(Match)
            .filter(Match.buddy_id == user.id)
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
        return render_template(
            "buddy_dashboard.html",
            user=user,
            open_requests=open_requests,
            matches=matches_data
        )
    finally:
        db.close()


@app.route("/buddy/accept/<int:request_id>", methods=["POST"])
def accept_request(request_id):
    user = get_current_user()
    if not user or user.role != "buddy":
        return redirect(url_for("login"))

    db = SessionLocal()
    try:
        req = db.query(BuddyRequest).filter(BuddyRequest.id == request_id, BuddyRequest.status == "open").first()
        if req:
            match = Match(request_id=req.id, buddy_id=user.id, status="confirmed")
            req.status = "matched"
            db.add(match)
            db.commit()
            flash("You accepted the request!", "success")
    finally:
        db.close()
    return redirect(url_for("buddy_dashboard"))


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

        # Access control: renter or matched buddy
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

            # Generate simple summary
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

            summary_parts = []
            if issues:
                summary_parts.append("Items to clarify: " + "; ".join(issues))
            else:
                summary_parts.append("✓ Most checklist items look good.")
            summary = " | ".join(summary_parts)

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
        if req.match:
            buddy = db.query(User).filter(User.id == req.match.buddy_id).first()
            buddy_name = buddy.name if buddy else None

        return render_template(
            "verify.html",
            user=user,
            request_obj=req,
            buddy_name=buddy_name,
            report=existing_report,
        )
    finally:
        db.close()


@app.route("/demo")
def demo_login():
    """Quick demo login as renter or buddy."""
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