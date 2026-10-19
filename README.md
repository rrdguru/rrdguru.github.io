# Peer Evaluation & Automated Grading System

A complete Flask web app for university project peer evaluation with:

- Admin dashboard (`/`) for creating groups and student rosters.
- Unique student portal links for each group (`/group/<token>`).
- Built-in SQLite database (`peer_eval.db`) with required tables (`Groups`, `Students`, `Evaluations`).
- Foolproof survey sections (self-declaration, dynamics roles, behavior ratings, and strict 100-point constant-sum split).
- CSV export for all evaluations.
- AI-style grading engine with discrepancy penalties and automatic hindrance-majority penalty cap.

## Tech Stack

- Python + Flask
- SQLite (embedded database, no external DB setup required)
- Gunicorn (production server)
- HTML/CSS/Vanilla JS (mobile-friendly)

## Local Run (optional)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open: `http://localhost:5000`

## Deployment on Abacus.AI (quick path)

1. Push this repository to GitHub.
2. In Abacus.AI, create a new **App** from GitHub repo.
3. Set runtime to Python.
4. Build command:
   ```bash
   pip install -r requirements.txt
   ```
5. Start command:
   ```bash
   gunicorn app:app
   ```
6. Deploy.

Abacus will provide a hosted URL immediately after successful deploy.

## AI Evaluation Logic (implemented)

For each student, the system calculates:

- **Average Peer Score**: mean of constant-sum points assigned by others.
- **Honesty Discrepancy**: `(self allocated points) - (average peer score)`.
- **Group Dynamics Flag**:
  - self-inflation detection,
  - high disagreement (std dev threshold),
  - majority bottleneck/hindrance detection.
- **Final Grade Multiplier**:
  - starts from expected-share ratio,
  - includes bonus for strong contributors,
  - automatic penalty for self-inflation,
  - automatic **0.5x cap** when majority identifies member as hindrance.

## Notes

- `peer_eval.db` is auto-initialized with required schema at startup.
- In production, move `SECRET_KEY` to an environment variable.
