import csv
import io
import secrets
import sqlite3
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from flask import Flask, abort, flash, g, redirect, render_template, request, send_file, url_for

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "peer_eval.db"

app = Flask(__name__)
app.config["SECRET_KEY"] = "replace-this-in-production"


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    cur = db.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS Groups (
            group_id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_name TEXT NOT NULL,
            unique_link TEXT NOT NULL UNIQUE
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS Students (
            student_id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            FOREIGN KEY(group_id) REFERENCES Groups(group_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS Evaluations (
            eval_id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            evaluator_name TEXT NOT NULL,
            evaluatee_name TEXT NOT NULL,
            availability_score INTEGER NOT NULL,
            reliability_score INTEGER NOT NULL,
            quality_score INTEGER NOT NULL,
            constant_sum INTEGER NOT NULL,
            primary_components TEXT,
            self_contribution TEXT,
            project_driver TEXT,
            bottleneck TEXT,
            bottleneck_explanation TEXT,
            submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(group_id) REFERENCES Groups(group_id)
        )
        """
    )
    db.commit()
    db.close()


init_db()

def score_to_multiplier(avg_peer_points, expected_share, discrepancy, majority_hindrance):
    if expected_share <= 0:
        return 1.0

    base = avg_peer_points / expected_share
    multiplier = max(0.70, min(1.25, base))

    if discrepancy > 10:
        multiplier -= min(0.30, (discrepancy - 10) * 0.02)

    if avg_peer_points > expected_share * 1.2 and discrepancy <= 5:
        multiplier += 0.05

    if majority_hindrance:
        multiplier = min(multiplier, 0.50)

    return round(max(0.50, multiplier), 2)


def ai_evaluate_group(group_id):
    db = get_db()
    students = [r["name"] for r in db.execute("SELECT name FROM Students WHERE group_id = ? ORDER BY name", (group_id,)).fetchall()]
    if not students:
        return []

    rows = db.execute(
        "SELECT * FROM Evaluations WHERE group_id = ? ORDER BY submitted_at ASC",
        (group_id,),
    ).fetchall()

    if not rows:
        return []

    points_by_target = defaultdict(list)
    self_points = {}

    responses = {}
    hindrance_votes = Counter()

    for row in rows:
        evaluator = row["evaluator_name"]
        evaluatee = row["evaluatee_name"]
        points = row["constant_sum"]

        points_by_target[evaluatee].append({"evaluator": evaluator, "points": points})
        if evaluator == evaluatee:
            self_points[evaluator] = points
            responses[evaluator] = {
                "driver": row["project_driver"],
                "bottleneck": row["bottleneck"],
                "why": row["bottleneck_explanation"] or "",
            }
            bottleneck = (row["bottleneck"] or "").strip()
            if bottleneck and bottleneck.lower() != "no one hindered the project":
                hindrance_votes[bottleneck] += 1

    n = len(students)
    expected_share = 100 / n if n else 0
    total_respondents = len(responses)

    report = []

    for student in students:
        entries = points_by_target.get(student, [])
        peer_points = [e["points"] for e in entries if e["evaluator"] != student]
        avg_peer = round(statistics.mean(peer_points), 2) if peer_points else 0.0
        student_self = self_points.get(student, 0)
        discrepancy = round(student_self - avg_peer, 2)

        stddev = statistics.pstdev([e["points"] for e in entries]) if len(entries) > 1 else 0.0
        majority_hindrance = total_respondents > 0 and hindrance_votes[student] > total_respondents / 2

        flags = []
        if discrepancy >= 15:
            flags.append("Self-inflation detected")
        elif discrepancy <= -15:
            flags.append("Under-claiming contribution")

        if stddev >= 15:
            flags.append("High disagreement among raters")

        if majority_hindrance:
            flags.append("Majority named this student as a hindrance")

        if not flags:
            flags.append("No major anomaly")

        report.append(
            {
                "student": student,
                "avg_peer_score": avg_peer,
                "honesty_discrepancy": discrepancy,
                "group_dynamics_flag": "; ".join(flags),
                "final_multiplier": score_to_multiplier(avg_peer, expected_share, discrepancy, majority_hindrance),
            }
        )

    return sorted(report, key=lambda r: r["final_multiplier"], reverse=True)


@app.route("/", methods=["GET", "POST"])
def dashboard():
    db = get_db()

    if request.method == "POST":
        group_name = request.form.get("group_name", "").strip()
        raw_students = request.form.get("student_names", "")

        students = [s.strip() for s in raw_students.splitlines() if s.strip()]

        if not group_name:
            flash("Group name is required.", "error")
            return redirect(url_for("dashboard"))
        if len(students) < 2:
            flash("Please add at least 2 student names.", "error")
            return redirect(url_for("dashboard"))

        token = secrets.token_urlsafe(10)
        db.execute("INSERT INTO Groups (group_name, unique_link) VALUES (?, ?)", (group_name, token))
        group_id = db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

        db.executemany(
            "INSERT INTO Students (group_id, name) VALUES (?, ?)",
            [(group_id, name) for name in students],
        )
        db.commit()

        flash("Group created successfully.", "success")
        return redirect(url_for("dashboard"))

    groups = db.execute(
        """
        SELECT g.group_id, g.group_name, g.unique_link, COUNT(s.student_id) AS members
        FROM Groups g
        LEFT JOIN Students s ON s.group_id = g.group_id
        GROUP BY g.group_id
        ORDER BY g.group_id DESC
        """
    ).fetchall()

    return render_template("dashboard.html", groups=groups)


@app.route("/group/<token>", methods=["GET", "POST"])
def group_portal(token):
    db = get_db()
    group = db.execute("SELECT * FROM Groups WHERE unique_link = ?", (token,)).fetchone()
    if not group:
        abort(404)

    students = [r["name"] for r in db.execute("SELECT name FROM Students WHERE group_id = ? ORDER BY name", (group["group_id"],)).fetchall()]

    if request.method == "POST":
        evaluator_name = request.form.get("evaluator_name", "").strip()
        primary_components = ", ".join(request.form.getlist("primary_components"))
        self_contribution = request.form.get("self_contribution", "").strip()
        project_driver = request.form.get("project_driver", "").strip()
        bottleneck = request.form.get("bottleneck", "").strip()
        bottleneck_explanation = request.form.get("bottleneck_explanation", "").strip()

        if evaluator_name not in students:
            flash("Please select your name.", "error")
            return redirect(request.url)

        if not primary_components:
            flash("Please select at least one responsibility area.", "error")
            return redirect(request.url)

        if not self_contribution:
            flash("Please add your contribution description.", "error")
            return redirect(request.url)

        if bottleneck and bottleneck.lower() != "no one hindered the project" and not bottleneck_explanation:
            flash("Please explain the hindrance when a person is selected.", "error")
            return redirect(request.url)

        total_points = 0
        evaluation_rows = []

        for student in students:
            try:
                availability = int(request.form.get(f"availability_{student}", "0"))
                reliability = int(request.form.get(f"reliability_{student}", "0"))
                quality = int(request.form.get(f"quality_{student}", "0"))
                points = int(request.form.get(f"points_{student}", "0"))
            except ValueError:
                flash("All ratings and points must be integers.", "error")
                return redirect(request.url)

            if not (1 <= availability <= 5 and 1 <= reliability <= 5 and 1 <= quality <= 5):
                flash("Ratings must be between 1 and 5.", "error")
                return redirect(request.url)
            if points < 0:
                flash("Points cannot be negative.", "error")
                return redirect(request.url)

            total_points += points
            evaluation_rows.append(
                (
                    group["group_id"],
                    evaluator_name,
                    student,
                    availability,
                    reliability,
                    quality,
                    points,
                    primary_components,
                    self_contribution,
                    project_driver,
                    bottleneck,
                    bottleneck_explanation,
                )
            )

        if total_points != 100:
            flash(f"Constant-sum validation failed: total is {total_points}, must be exactly 100.", "error")
            return redirect(request.url)

        db.executemany(
            """
            INSERT INTO Evaluations (
                group_id, evaluator_name, evaluatee_name,
                availability_score, reliability_score, quality_score, constant_sum,
                primary_components, self_contribution, project_driver,
                bottleneck, bottleneck_explanation
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            evaluation_rows,
        )
        db.commit()

        return render_template("confirmation.html", group_name=group["group_name"], evaluator_name=evaluator_name)

    return render_template("portal.html", group=group, students=students)


@app.route("/download/<int:group_id>")
def download_csv(group_id):
    db = get_db()
    group = db.execute("SELECT * FROM Groups WHERE group_id = ?", (group_id,)).fetchone()
    if not group:
        abort(404)

    rows = db.execute(
        """
        SELECT eval_id, evaluator_name, evaluatee_name,
               availability_score, reliability_score, quality_score, constant_sum,
               primary_components, self_contribution, project_driver,
               bottleneck, bottleneck_explanation, submitted_at
        FROM Evaluations
        WHERE group_id = ?
        ORDER BY eval_id ASC
        """,
        (group_id,),
    ).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "eval_id",
            "evaluator_name",
            "evaluatee_name",
            "availability_score",
            "reliability_score",
            "quality_score",
            "constant_sum",
            "primary_components",
            "self_contribution",
            "project_driver",
            "bottleneck",
            "bottleneck_explanation",
            "submitted_at",
        ]
    )

    for row in rows:
        writer.writerow(row)

    mem = io.BytesIO(output.getvalue().encode("utf-8"))
    mem.seek(0)

    filename = f"{group['group_name'].replace(' ', '_').lower()}_evaluations.csv"
    return send_file(mem, mimetype="text/csv", as_attachment=True, download_name=filename)


@app.route("/analyze/<int:group_id>")
def analyze(group_id):
    db = get_db()
    group = db.execute("SELECT * FROM Groups WHERE group_id = ?", (group_id,)).fetchone()
    if not group:
        abort(404)

    report = ai_evaluate_group(group_id)
    return render_template("analysis.html", group=group, report=report)


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
