import os
import json
import re
import time
import tempfile
from collections import defaultdict
from flask import Flask, render_template, request, jsonify
from anthropic import Anthropic

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024
client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

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

SYSTEM_PROMPT = """You review marketing briefs like a stand-up comedian roasting a heckler. You are FUNNY FIRST, useful second.

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
- The rewrite should be genuinely good and properly structured.
- No em dashes. No corporate language. No "lacks clarity" type phrases.

SCORING: Most briefs are 2-5. A 9+ is almost impossible. Be harsh.

OUTPUT: Return ONLY a single-line JSON object. No newlines inside strings. No markdown. No backticks.

Keys: "score" (number 0-10), "roast" (one killer joke sentence about this specific brief), "vibe" (one word: delusional/lazy/confused/generic/desperate/amateur/bloated/vague/corporate/hopeless), "callouts" (array of 3 objects with "issue" and "detail"), "missing" (array of 2-4 strings), "rewrite" (sections separated by " || ")

Example: {"score":2,"roast":"I've seen better strategic thinking on the back of a Denny's napkin at 3am.","vibe":"hopeless","callouts":[{"issue":"Audience Roulette","detail":"'Millennials and Gen Z interested in wellness' is not a target audience, it's the entire customer base of Whole Foods."},{"issue":"KPI Fairy Dust","detail":"Your success metric is 'increased engagement'. My success metric is not throwing my laptop out the window after reading this."},{"issue":"Budget Ghost","detail":"There is literally no budget mentioned. Are we manifesting media spend now?"}],"missing":["An actual budget","Competitive landscape","Single minded proposition","Success metrics that mean something"],"rewrite":"OBJECTIVE: Drive 10,000 app downloads in 90 days among health-conscious women 25-34 in metro cities || TARGET AUDIENCE: Women 25-34 in Tier 1 cities who currently use MyFitnessPal and spend on organic groceries || INSIGHT: They want to eat better but don't have time to research what 'better' actually means for their body || PROPOSITION: The only nutrition app that gives you a personalized plan in under 2 minutes || MANDATORIES: Must integrate with Apple Health, no subscription paywall for core features || SUCCESS METRICS: 10K downloads, 40% D7 retention, CAC under INR 150 || BUDGET: INR 25L || TIMELINE: 12 weeks from kickoff"}"""


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
    return run_roast(brief_text)

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
        return run_roast(text)
    except Exception as e:
        print(f"Upload error: {e}")
        return jsonify({"error": "Failed to read the file. Try pasting instead."}), 500

def run_roast(brief_text):
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

        # Format the rewrite nicely (convert || separators to newlines for display)
        if "rewrite" in result:
            result["rewrite"] = result["rewrite"].replace(" || ", "\n\n")

        return jsonify(result)

    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}")
        print(f"Raw text was: {result_text[:500]}")
        return jsonify({"error": "The brief was so bad it broke the AI. Try again."}), 500
    except Exception as e:
        print(f"Error type: {type(e).__name__}")
        print(f"Error detail: {e}")
        return jsonify({"error": f"Error: {type(e).__name__}: {str(e)[:200]}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True)
