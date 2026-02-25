import os
import io
import csv
import json
import re
import sqlite3
import time
import tempfile
from datetime import datetime, timezone
from collections import defaultdict
from flask import Flask, render_template, request, jsonify, Response
from anthropic import Anthropic

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024
client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

ADMIN_KEY = os.environ.get("ADMIN_KEY", "changeme")

# --- SQLite Brief Repository ---
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "briefs.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS briefs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            brief_text TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'paste',
            filename TEXT,
            score INTEGER,
            vibe TEXT,
            roast TEXT,
            full_result TEXT,
            ip TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

init_db()

def save_brief(brief_text, source, filename, result_dict, ip):
    try:
        conn = get_db()
        conn.execute(
            "INSERT INTO briefs (brief_text, source, filename, score, vibe, roast, full_result, ip, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                brief_text,
                source,
                filename,
                result_dict.get("score"),
                result_dict.get("vibe"),
                result_dict.get("roast"),
                json.dumps(result_dict),
                ip,
                datetime.now(timezone.utc).isoformat()
            )
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"DB save error: {e}")

# Simple in-memory rate limiter: 5 requests per IP per minute
_rate_limits = defaultdict(list)
RATE_LIMIT = 5
RATE_WINDOW = 60

def check_rate_limit():
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    now = time.time()
    _rate_limits[ip] = [t for t in _rate_limits[ip] if now - t < RATE_WINDOW]
    if len(_rate_limits[ip]) >= RATE_LIMIT:
        return False
    _rate_limits[ip].append(now)
    return True

ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx', 'txt', 'rtf'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def extract_text(filepath, filename):
    ext = filename.rsplit('.', 1)[1].lower()
    if ext == 'txt':
        with open(filepath, 'r', errors='ignore') as f:
            return f.read()
    if ext == 'pdf':
        try:
            import fitz
            doc = fitz.open(filepath)
            text = ""
            for page in doc:
                text += page.get_text()
            doc.close()
            return text.strip()
        except Exception:
            pass
        return None
    if ext in ('doc', 'docx', 'rtf'):
        if ext == 'docx':
            try:
                from docx import Document
                doc = Document(filepath)
                return "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
            except Exception:
                pass
        return None
    return None

SYSTEM_PROMPT = """You review marketing briefs like a stand-up comedian roasting a heckler. You are FUNNY. That is the entire job. Not helpful. Not constructive. FUNNY.

VOICE EXAMPLES (match this energy):
- "Your target audience is 'women 18-65'. So... women. You're targeting women. Groundbreaking."
- "The budget section is blank. I assume you're paying the agency in exposure and good vibes?"
- "You've used the word 'synergy' three times. That's three more times than any human should."
- "This reads like someone fed a strategy deck into a blender and poured it onto a page."
- "Your KPI is 'brand awareness'. That's not a KPI, that's a wish upon a star."
- "The timeline says Q3. Q3 of what year? What century? The heat death of the universe?"

RULES:
- The "roast" field must be a joke. Not a critique. A JOKE. Something that makes someone snort-laugh.
- Callout "detail" fields must be sarcastic and quote specific words from the brief.
- Creative issue labels like "Audience Roulette", "The Buzzword Buffet", "Timeline Fantasy", "Budget Ghost", "KPI Fairy Dust"
- No em dashes. No corporate language. No "lacks clarity" type phrases.
- NEVER be helpful. NEVER give actual advice. Everything is a roast.

THE "MISSING" SECTION: This is NOT a checklist. These are funny roasts about the HUMAN side of marketing that the brief clearly forgot exists. Real customers. Real conversations. Gut instinct. Talking to actual people. Going outside. The stuff AI and dashboards can't replace. Each one must be a joke about a specific human thing they missed.

THE "NEXT STEPS" SECTION: This replaces any rewrite. These are 3-4 funny, sarcastic suggested responses or next moves. Think: what should you email back? What should you say in the review meeting? What should you do with this brief? These should be hilarious, specific to this brief, and absolutely NOT constructive.

SCORING: Most briefs are 2-5. A 9+ is almost impossible. Be harsh.

OUTPUT: Return ONLY a single-line JSON object. No newlines inside strings. No markdown. No backticks.

Keys: "score" (number 0-10), "roast" (one killer joke sentence about this specific brief), "vibe" (one word: delusional/lazy/confused/generic/desperate/amateur/bloated/vague/corporate/hopeless), "callouts" (array of 3 objects with "issue" and "detail"), "missing" (array of 3-4 objects with "thing" and "joke" about the human element of marketing they forgot), "next_steps" (array of 3-4 funny string responses/actions)

Example: {"score":2,"roast":"I've seen better strategic thinking on the back of a Denny's napkin at 3am.","vibe":"hopeless","callouts":[{"issue":"Audience Roulette","detail":"'Millennials and Gen Z interested in wellness' is not a target audience, it's the entire customer base of Whole Foods."},{"issue":"KPI Fairy Dust","detail":"Your success metric is 'increased engagement'. My success metric is not throwing my laptop out the window after reading this."},{"issue":"Budget Ghost","detail":"There is literally no budget mentioned. Are we manifesting media spend now?"}],"missing":[{"thing":"Talking to a real customer","joke":"You know, those people who actually buy things? With money? Instead of reading a 2019 trend report and calling it research?"},{"thing":"A single original thought","joke":"Every line in this brief could be AI-generated. And I would know."},{"thing":"Going outside","joke":"The person who wrote this has clearly not spoken to a human woman in a grocery store since 2017."},{"thing":"Gut instinct","joke":"Somewhere between the third dashboard screenshot and the fifth alignment meeting, someone's gut feeling died and nobody held a funeral."}],"next_steps":["Reply-all with: 'Per my last brief, which was also shit, I have some thoughts'","Print it out, fold it into a paper airplane, and sail it back across the open-plan office","Forward it to your competitor. This would set their strategy back months.","Schedule a 'brief alignment sync' just to watch everyone's soul leave their body in real time"]}"""


@app.route("/")
def index():
    return render_template("index.html")

@app.route("/roast", methods=["POST"])
def roast():
    if not check_rate_limit():
        return jsonify({"error": "Slow down. Even bad briefs deserve a breather. Try again in a minute."}), 429
    data = request.json
    brief_text = data.get("brief", "").strip()
    if not brief_text:
        return jsonify({"error": "No brief provided"}), 400
    if len(brief_text) < 20:
        return jsonify({"error": "That's not a brief. That's barely a sentence."}), 400
    return run_roast(brief_text, source="paste")

@app.route("/upload", methods=["POST"])
def upload():
    if not check_rate_limit():
        return jsonify({"error": "Slow down. Even bad briefs deserve a breather. Try again in a minute."}), 429
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400
    if not allowed_file(file.filename):
        return jsonify({"error": "Supported formats: PDF, DOC, DOCX, TXT, RTF"}), 400
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix='.' + file.filename.rsplit('.', 1)[1].lower()) as tmp:
            file.save(tmp.name)
            text = extract_text(tmp.name, file.filename)
            os.unlink(tmp.name)
        if not text or len(text.strip()) < 20:
            return jsonify({"error": "Could not extract enough text. Try pasting instead."}), 400
        if len(text) > 15000:
            text = text[:15000]
        return run_roast(text, source="upload", filename=file.filename)
    except Exception as e:
        print(f"Upload error: {e}")
        return jsonify({"error": "Failed to read the file. Try pasting instead."}), 500

def run_roast(brief_text, source="paste", filename=None):
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Roast this marketing brief. Remember: be SPECIFIC, quote the brief, be funny, return ONLY a single-line JSON object.\n\nBRIEF:\n{brief_text}"}]
        )
        result_text = response.content[0].text.strip()
        print(f"Raw response: {result_text[:300]}...")

        # Strip markdown wrapping
        if result_text.startswith("```"):
            result_text = result_text.split("\n", 1)[1]
            if result_text.endswith("```"):
                result_text = result_text[:-3]
            result_text = result_text.strip()

        # Kill control characters
        result_text = re.sub(r'[\x00-\x1f\x7f]', ' ', result_text)

        # Try to extract JSON if there's text around it
        json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
        if json_match:
            result_text = json_match.group()

        result = json.loads(result_text)

        # Save brief + result to repository
        ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        save_brief(brief_text, source, filename, result, ip)

        return jsonify(result)

    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}")
        print(f"Raw text was: {result_text[:500]}")
        return jsonify({"error": "The brief was so bad it broke the AI. Try again."}), 500
    except Exception as e:
        print(f"Error type: {type(e).__name__}")
        print(f"Error detail: {e}")
        return jsonify({"error": f"Error: {type(e).__name__}: {str(e)[:200]}"}), 500

# --- Admin: Brief Repository ---

@app.route("/admin/briefs")
def admin_briefs():
    if request.args.get("key") != ADMIN_KEY:
        return jsonify({"error": "Unauthorized"}), 401
    conn = get_db()
    rows = conn.execute("SELECT id, source, filename, score, vibe, roast, created_at FROM briefs ORDER BY id DESC").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route("/admin/briefs/<int:brief_id>")
def admin_brief_detail(brief_id):
    if request.args.get("key") != ADMIN_KEY:
        return jsonify({"error": "Unauthorized"}), 401
    conn = get_db()
    row = conn.execute("SELECT * FROM briefs WHERE id = ?", (brief_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Not found"}), 404
    d = dict(row)
    if d.get("full_result"):
        d["full_result"] = json.loads(d["full_result"])
    return jsonify(d)

@app.route("/admin/briefs/export")
def admin_export():
    if request.args.get("key") != ADMIN_KEY:
        return jsonify({"error": "Unauthorized"}), 401
    conn = get_db()
    rows = conn.execute("SELECT id, brief_text, source, filename, score, vibe, roast, ip, created_at FROM briefs ORDER BY id DESC").fetchall()
    conn.close()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "brief_text", "source", "filename", "score", "vibe", "roast", "ip", "created_at"])
    for r in rows:
        writer.writerow([r["id"], r["brief_text"], r["source"], r["filename"], r["score"], r["vibe"], r["roast"], r["ip"], r["created_at"]])
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=briefs_export.csv"}
    )

@app.route("/admin/briefs/stats")
def admin_stats():
    if request.args.get("key") != ADMIN_KEY:
        return jsonify({"error": "Unauthorized"}), 401
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) as c FROM briefs").fetchone()["c"]
    by_source = conn.execute("SELECT source, COUNT(*) as c FROM briefs GROUP BY source").fetchall()
    avg_score = conn.execute("SELECT AVG(score) as avg FROM briefs WHERE score IS NOT NULL").fetchone()["avg"]
    by_vibe = conn.execute("SELECT vibe, COUNT(*) as c FROM briefs GROUP BY vibe ORDER BY c DESC").fetchall()
    conn.close()
    return jsonify({
        "total_briefs": total,
        "by_source": {r["source"]: r["c"] for r in by_source},
        "average_score": round(avg_score, 1) if avg_score else 0,
        "by_vibe": {r["vibe"]: r["c"] for r in by_vibe}
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True)
