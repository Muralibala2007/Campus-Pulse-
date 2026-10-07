"""CampusPulse: Smart Notice & Query Bot with Verification (Groq edition, redesigned UI, dark mode)
Run:  streamlit run app.py
Requires theme.py (light/dark styling) in the same folder.
"""
import base64
import calendar
import hashlib
import html
import io
import json
import os
import re
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone

import extra_streamlit_components as stx
import streamlit as st
from dotenv import load_dotenv
from groq import Groq, NotFoundError, RateLimitError
from pypdf import PdfReader

from theme import apply_theme

load_dotenv()
MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
DB_PATH = "campuspulse.db"
MAX_CHARS = 24000  # keeps requests inside Groq free-tier token-per-minute limits
MIN_QUOTE_LEN = 15
# Image / scanned-PDF reading (vision model on Groq). Override in .env if Groq renames the model.
# Tried in order; the first one your Groq account can use is remembered for the session.
VISION_MODELS = [m for m in [
    os.getenv("GROQ_VISION_MODEL"),
    "qwen/qwen3.8-27b",
    "qwen/qwen3.6-27b",
    "meta-llama/llama-4-scout-17b-16e-instruct",
] if m]
_vision_ok = {"model": None}
IMG_TYPES = ["png", "jpg", "jpeg", "webp"]
MAX_OCR_PAGES = 6       # per PDF, to stay inside free-tier limits
MIN_PAGE_CHARS = 40     # a PDF page with less text than this is treated as a scan

PICK = "Select"   # placeholder shown until the student chooses
YEAR_OPTS = [PICK, "FE", "SE", "TE", "BE"]
YEAR_LABELS = {PICK: "Select year", "FE": "FE (1st year)", "SE": "SE (2nd year)", "TE": "TE (3rd year)", "BE": "BE (4th year)"}
BRANCH_OPTS = [PICK, "CE", "IT", "EXTC", "AIML", "ECS", "AIDS", "CSE IOT"]
YEAR_ALIASES = {
    "FE": ["fe", "first", "1st", "f.e"],
    "SE": ["se", "second", "2nd", "s.e"],
    "TE": ["te", "third", "3rd", "t.e"],
    "BE": ["be", "fourth", "4th", "final", "b.e"],
}
# Aliases are in normalized form: lowercase, "&" -> "and", punctuation -> spaces.
BRANCH_ALIASES = {
    "CE": ["ce", "comp", "computer engineering", "computer engg"],
    "IT": ["it", "information technology"],
    "EXTC": ["extc", "entc", "e and tc", "electronics and telecommunication",
             "electronics and telecommunications", "electronics telecommunication"],
    "AIML": ["aiml", "ai and ml", "ai ml", "artificial intelligence and machine learning"],
    "ECS": ["ecs", "electronics and computer science"],
    "AIDS": ["aids", "ai and ds", "ai ds", "artificial intelligence and data science"],
    "CSE IOT": ["cse iot", "csiot", "iot", "cse internet of things",
                "computer science and engineering iot"],
}
COOKIE_DAYS = 365
SUGGESTIONS = ["Does this apply to me?", "What happens if I'm late?", "What do I need to bring?"]

# Dashboard filter options
WHEN_OPTS = ["Next 7 days", "Next 30 days", "All upcoming", "Overdue", "Everything"]
TYPE_OPTS = ["All deadlines", "Main notice only", "Fine print only"]
STATUS_OPTS = ["To do", "Done", "All"]
SORT_OPTS = ["Soonest first", "Latest first", "Group by notice"]
ALL_NOTICES = "All notices"

st.set_page_config(page_title="CampusPulse", page_icon="📌", layout="wide", initial_sidebar_state="expanded")


# ---------------------------------------------------------------- small helpers
def esc(x) -> str:
    return html.escape(str(x if x is not None else ""), quote=True)


def esc_text(s: str) -> str:
    """Escape for the source view and turn newlines into <br> (keeps the HTML on one line)."""
    return html.escape(s, quote=False).replace("\r", "").replace("\n", "<br>")


def md_safe(s: str) -> str:
    return str(s).replace("$", "\\$")


def truncate(s: str, n: int) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def note(kind: str, inner: str):
    st.markdown(f"<div class='note {kind}'>{inner}</div>", unsafe_allow_html=True)


# ---------------------------------------------------------------- storage
def db():
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "CREATE TABLE IF NOT EXISTS docs (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " name TEXT, text TEXT, analysis TEXT, created TEXT)"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS done (doc_id INTEGER, key TEXT, PRIMARY KEY (doc_id, key))"
    )
    return con


def save_doc(name, text, analysis):
    con = db()
    cur = con.execute(
        "INSERT INTO docs (name, text, analysis, created) VALUES (?,?,?,?)",
        (name, text, json.dumps(analysis), datetime.now().isoformat(timespec="seconds")),
    )
    con.commit()
    doc_id = cur.lastrowid
    con.close()
    return doc_id


def list_docs():
    con = db()
    rows = con.execute("SELECT id, name, text, analysis FROM docs ORDER BY id DESC").fetchall()
    con.close()
    return [{"id": r[0], "name": r[1], "text": r[2], "analysis": json.loads(r[3])} for r in rows]


def delete_doc(doc_id):
    con = db()
    con.execute("DELETE FROM docs WHERE id = ?", (doc_id,))
    con.execute("DELETE FROM done WHERE doc_id = ?", (doc_id,))
    con.commit()
    con.close()


def dl_key(d: dict) -> str:
    """Stable id for one deadline inside a notice (used to remember 'done')."""
    return hashlib.md5(f"{d.get('date')}|{d.get('event')}".encode()).hexdigest()[:12]


def load_done() -> set:
    con = db()
    rows = con.execute("SELECT doc_id, key FROM done").fetchall()
    con.close()
    return {(r[0], r[1]) for r in rows}


def set_done(doc_id, key, flag: bool):
    con = db()
    if flag:
        con.execute("INSERT OR IGNORE INTO done (doc_id, key) VALUES (?,?)", (doc_id, key))
    else:
        con.execute("DELETE FROM done WHERE doc_id = ? AND key = ?", (doc_id, key))
    con.commit()
    con.close()


def _toggle_done(doc_id, key, widget_key):
    set_done(doc_id, key, bool(st.session_state.get(widget_key)))


# ---------------------------------------------------------------- ingestion
OCR_PROMPT = (
    "Transcribe ALL text visible in this image exactly as written, from top to bottom, including small print, "
    "footnotes, stamps, tables and handwritten notes. Keep line breaks. Do not summarize, translate, explain or "
    "add anything. If there is no readable text, reply with exactly: NO_TEXT"
)


def _prep_image(data: bytes) -> str:
    """Downscale, fix rotation and return a base64 JPEG (keeps requests small)."""
    from PIL import Image, ImageOps

    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    img.thumbnail((1800, 1800))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def _clean_ocr(out: str) -> str:
    out = re.sub(r"<think>.*?</think>", "", out or "", flags=re.S).strip()   # reasoning models may add this
    return "" if out.upper().startswith("NO_TEXT") else out


def ocr_image_bytes(data: bytes) -> str:
    """Read the text in one image with a Groq vision model (falls back across model names)."""
    b64 = _prep_image(data)
    client = get_client()
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": OCR_PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ],
    }]
    candidates = [_vision_ok["model"]] if _vision_ok["model"] else VISION_MODELS
    last_err = None
    for model in candidates:
        for attempt in range(3):
            try:
                resp = client.chat.completions.create(model=model, messages=messages, temperature=0)
                _vision_ok["model"] = model
                return _clean_ocr(resp.choices[0].message.content)
            except NotFoundError as e:       # model retired / not available to this key: try the next one
                last_err = e
                break
            except RateLimitError as e:
                last_err = e
                time.sleep(2 * (attempt + 1))
        else:
            raise RuntimeError(f"Groq rate limit hit while reading an image, try again in a moment. ({last_err})")
    raise RuntimeError(
        "No Groq vision model was available for this API key. Open console.groq.com/docs/vision, "
        f"pick a current vision model and set GROQ_VISION_MODEL in your .env. (Last error: {last_err})"
    )


def extract_pdf(data: bytes):
    """Text PDFs use the embedded text; scanned pages are rendered and read with OCR."""
    pages = [(p.extract_text() or "").strip() for p in PdfReader(io.BytesIO(data)).pages]
    need = [i for i, t in enumerate(pages) if len(t) < MIN_PAGE_CHARS]
    used, notes = False, []
    if need:
        try:
            import pymupdf as fitz
        except ImportError:
            try:
                import fitz
            except ImportError:
                fitz = None
        if fitz is None:
            if not any(pages):
                raise RuntimeError("This looks like a scanned PDF. Install PyMuPDF to read it: pip install pymupdf")
            notes.append("Some pages look scanned but PyMuPDF isn't installed, so they were skipped.")
        else:
            doc = fitz.open(stream=data, filetype="pdf")
            for i in need[:MAX_OCR_PAGES]:
                pages[i] = ocr_image_bytes(doc[i].get_pixmap(dpi=150).tobytes("jpeg"))
                used = True
            if len(need) > MAX_OCR_PAGES:
                notes.append(f"Only the first {MAX_OCR_PAGES} scanned pages were read ({len(need)} found).")
    return "\n\n".join(t for t in pages if t).strip(), used, notes


def extract_text(uploaded):
    """Returns (text, used_ocr, notes) for a PDF, image or TXT upload."""
    name = uploaded.name.lower()
    data = uploaded.getvalue()
    if name.endswith(".pdf"):
        return extract_pdf(data)
    if name.rsplit(".", 1)[-1] in IMG_TYPES:
        return ocr_image_bytes(data), True, []
    return data.decode("utf-8", errors="ignore").strip(), False, []


def extract_all(files):
    """Combine several uploads (e.g. photos of one notice) into one text."""
    parts, used, notes = [], False, []
    for f in files:
        t, u, n = extract_text(f)
        used = used or u
        notes += n
        if t:
            parts.append(t if len(files) == 1 else f"--- {f.name} ---\n{t}")
        else:
            notes.append(f"No text could be read from {f.name}.")
    return "\n\n".join(parts).strip(), {"used": used, "notes": notes}


def clip(text: str, n: int = MAX_CHARS) -> str:
    """Keep the head AND the tail so footnote deadlines survive truncation."""
    if len(text) <= n:
        return text
    head = n * 2 // 3
    tail = n - head
    return text[:head] + "\n...\n" + text[-tail:]


# ---------------------------------------------------------------- LLM (Groq)
@st.cache_resource
def _make_client(key: str) -> Groq:
    return Groq(api_key=key)


def get_client() -> Groq:
    key = os.getenv("GROQ_API_KEY")
    if not key:
        st.error("Missing GROQ_API_KEY. Add it to a .env file (see .env.example).")
        st.stop()
    return _make_client(key)


def ask_llm_json(prompt: str) -> dict:
    """Call Groq in JSON mode and return a parsed dict."""
    client = get_client()
    last_err = None
    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a precise assistant. Respond with a single valid JSON object only.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                response_format={"type": "json_object"},
            )
            text = (resp.choices[0].message.content or "").strip()
            text = re.sub(r"^```(?:json)?|```$", "", text).strip()
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("Model did not return a JSON object.")
            return data
        except RateLimitError as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Groq rate limit hit, try again in a moment. ({last_err})")


ANALYSIS_PROMPT = """You are an assistant that reads messy college circulars for students.
Read the notice below and return ONLY JSON with this exact shape:
{{
  "title": "short title",
  "one_line_summary": "one sentence TL;DR",
  "tldr": ["max 4 short bullets"],
  "deadlines": [
    {{
      "date": "YYYY-MM-DD or null if unclear",
      "date_text": "date exactly as written in notice",
      "event": "what is due / happening",
      "is_footnote": true if the date appears in a footnote, fine print, P.S., or note at the end, else false,
      "source_quote": "an EXACT verbatim snippet (under 25 words) copied from the notice proving this deadline"
    }}
  ],
  "affected": {{"branches": [], "years": [], "divisions": []}},
  "action_points": ["things the student must do"],
  "late_policy": "what the notice says about late submission/penalty, or 'Not mentioned'"
}}
Rules: never invent facts. Copy source_quote character-for-character from the notice.
Write affected.branches using only these codes: CE, IT, EXTC, AIML, ECS, AIDS, CSE IOT (map full names to them, e.g. "Computer Engineering" -> CE). Use "All" if every branch is included.
Assume the current year is {year} if a year is missing. Include fine-print deadlines.

NOTICE:
\"\"\"
{text}
\"\"\"
"""

QA_PROMPT = """Answer the student's question using ONLY the notice below.
Student profile: {profile}
Return ONLY JSON: {{"answer": "...", "quote": "EXACT verbatim snippet (under 25 words) from the notice supporting the answer, or empty string if the notice does not answer it"}}
If the notice does not contain the answer, say so in "answer" and leave quote empty. Never guess.

NOTICE:
\"\"\"
{text}
\"\"\"

QUESTION: {question}
"""


# ---------------------------------------------------------------- verification
def norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", (s or "").lower())).strip()


def verify_quote(quote: str, source: str) -> bool:
    q = norm(quote)
    return len(q) >= MIN_QUOTE_LEN and q in norm(source)


def in_tail(quote: str, source: str, frac: float = 0.2) -> bool:
    """True if the quote sits in the last `frac` of the document (footnote territory)."""
    src, q = norm(source), norm(quote)
    if len(q) < MIN_QUOTE_LEN:
        return False
    i = src.find(q)
    return i >= 0 and i > len(src) * (1 - frac)


def analyze(text: str) -> dict:
    a = ask_llm_json(ANALYSIS_PROMPT.format(year=date.today().year, text=clip(text)))
    deadlines = [d for d in (a.get("deadlines") or []) if isinstance(d, dict)]
    for d in deadlines:
        # don't trust the model's footnote flag alone: also check the position ourselves
        d["is_footnote"] = bool(d.get("is_footnote")) or in_tail(d.get("source_quote", ""), text)
    a["deadlines"] = deadlines
    a["affected"] = a.get("affected") or {}
    a["action_points"] = a.get("action_points") or []
    return a


def answer_question(q: str, text: str, profile: str) -> dict:
    try:
        r = ask_llm_json(QA_PROMPT.format(profile=profile, text=clip(text), question=q))
        quote = str(r.get("quote") or "")
        if quote and verify_quote(quote, text):
            status = "ok"
        elif quote:
            status = "bad"
        else:
            status = "none"
        return {
            "role": "assistant",
            "content": str(r.get("answer") or "No answer."),
            "proof": {"status": status, "quote": quote},
        }
    except Exception as e:
        return {"role": "assistant", "content": f"Something went wrong while checking the notice: {e}. Try asking again.", "proof": None}


# ---------------------------------------------------------------- dates
def parse_date(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def days_left(s):
    dt = parse_date(s)
    return (dt - date.today()).days if dt else None


def urgency(delta):
    """Returns (css class, plain-language label)."""
    if delta is None:
        return "unknown", "Date unclear"
    if delta < 0:
        return "past", f"Passed {-delta} day{'' if delta == -1 else 's'} ago"
    if delta == 0:
        return "hot", "Due today"
    if delta == 1:
        return "hot", "Due tomorrow"
    if delta <= 3:
        return "hot", f"Due in {delta} days"
    if delta <= 7:
        return "soon", f"Due in {delta} days"
    return "later", f"Due in {delta} days"


def short_when(delta) -> str:
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Tomorrow"
    return f"{delta} days"


# ---------------------------------------------------------------- sample notice
def sample_notice() -> str:
    t = date.today()
    main, fine = t + timedelta(days=6), t + timedelta(days=3)
    return (
        "SAMPLE COLLEGE OF ENGINEERING\n"
        f"CIRCULAR | Ref: SCE/EXAM/2026/114 | Date: {t:%d %B %Y}\n\n"
        "Subject: Submission of term-work journals, Semester III\n\n"
        "All SE (Computer Engineering and IT) students are informed that the term-work journals "
        f"for Semester III must be submitted to the respective subject teachers on or before {main:%d %B %Y}, 4:00 PM. "
        "Journals must be certified by the subject teacher and signed by the Head of Department.\n\n"
        "Students must carry their college ID card and a printed copy of the practical list. "
        "Journals submitted without the practical list will not be accepted.\n\n"
        "Late submissions will be accepted for up to 2 days with a deduction of 10% of term-work marks per day.\n\n"
        "Principal\n\n"
        "Note: Students who have not yet paid the examination form fee must submit the fee receipt to the exam cell "
        f"on or before {fine:%d %B %Y}. Term-work of such students will not be forwarded to the university."
    )


def load_sample():
    st.session_state.paste_box = sample_notice()


# ---------------------------------------------------------------- HTML builders
def deadline_card(d: dict, text: str, source: str = None, done: bool = False) -> str:
    dt = parse_date(d.get("date"))
    delta = days_left(d.get("date"))
    cls, label = urgency(delta)
    fine = bool(d.get("is_footnote"))
    quote = d.get("source_quote", "")
    ok = verify_quote(quote, text)
    date_text = d.get("date_text") or ""

    if dt:
        tile = f"<div class='tile'><div class='m'>{dt:%b}</div><div class='d'>{dt.day}</div></div>"
        when = f"{dt:%a, %d %b %Y}"
        if date_text:
            when += f" (notice says “{esc(date_text)}”)"
    else:
        tile = "<div class='tile'><div class='m'>Date</div><div class='d'>?</div></div>"
        when = esc(date_text) or "No clear date in the notice"
    if source:
        when += f" · from {esc(truncate(source, 40))}"

    pills = f"<span class='pill {cls}'>{esc(label)}</span>"
    if done:
        pills += "<span class='pill ok'>Done</span>"
    if fine:
        pills += "<span class='pill fine'>Fine print</span>"
    if ok:
        pills += "<span class='pill ok'>Matches the notice</span>"
    else:
        pills += "<span class='pill warn'>Couldn't match, check the original</span>"

    quote_html = f"<div class='quote'>“{esc(quote)}”</div>" if quote else ""
    return (
        f"<div class='dl {cls}{' fine' if fine else ''}'>{tile}"
        f"<div class='dl-body'><div class='dl-title'>{esc(d.get('event', ''))}</div>"
        f"<div class='dl-when'>{when}</div>{quote_html}<div class='pills'>{pills}</div></div></div>"
    )


def fineprint_alert(hidden: list) -> str:
    n = len(hidden)
    title = "A deadline is hiding in the fine print" if n == 1 else f"{n} deadlines are hiding in the fine print"
    items = "".join(
        f"<li><span class='hl'>{esc(d.get('date_text') or d.get('date') or 'Date unclear')}</span> {esc(d.get('event', ''))}</li>"
        for d in hidden
    )
    return (
        f"<div class='fineprint-alert'><h3>{title}</h3>"
        f"<div>Easy to miss if you only read the top of the notice.</div><ul>{items}</ul></div>"
    )


def find_span(quote: str, source: str):
    toks = re.findall(r"\w+", quote or "")
    if len(" ".join(toks)) < MIN_QUOTE_LEN:
        return None
    pat = r"\W+".join(re.escape(t) for t in toks)
    m = re.search(pat, source, re.IGNORECASE)
    return (m.start(), m.end()) if m else None


def highlight_html(text: str, deadlines: list):
    """Returns (html, number of deadlines that could not be located)."""
    spans, missing = [], 0
    for d in deadlines:
        sp = find_span(d.get("source_quote", ""), text)
        if sp:
            spans.append((sp[0], sp[1], "fine" if d.get("is_footnote") else "body"))
        else:
            missing += 1
    spans.sort()
    out, pos = [], 0
    for s, e, k in spans:
        if s < pos:
            continue
        out.append(esc_text(text[pos:s]))
        out.append(f"<mark class='{k}'>{esc_text(text[s:e])}</mark>")
        pos = e
    out.append(esc_text(text[pos:]))
    return "".join(out), missing


def calendar_month_html(y: int, m: int, events: dict) -> str:
    today = date.today()
    out = f"<div class='cal'><div class='mo'>{calendar.month_name[m]} {y}</div><table>"
    out += "<tr>" + "".join(f"<th>{n}</th>" for n in ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]) + "</tr>"
    for week in calendar.monthcalendar(y, m):
        out += "<tr>"
        for day in week:
            if day == 0:
                out += "<td></td>"
                continue
            dt = date(y, m, day)
            evs = events.get(dt)
            if evs:
                fine = any(e.get("is_footnote") for e in evs)
                tip = esc(" | ".join(str(e.get("event", "")) for e in evs))
                cls = "ev" + (" fine" if fine else "") + (" now" if dt == today else "")
                out += f"<td><span class='{cls}' title='{tip}'>{day}</span></td>"
            elif dt == today:
                out += f"<td><span class='today'>{day}</span></td>"
            else:
                out += f"<td>{day}</td>"
        out += "</tr>"
    return out + "</table></div>"


def render_calendar(deadlines: list):
    events = {}
    for d in deadlines:
        dt = parse_date(d.get("date"))
        if dt:
            events.setdefault(dt, []).append(d)
    all_months = sorted({(x.year, x.month) for x in events})
    today = date.today()
    upcoming = [ym for ym in all_months if ym >= (today.year, today.month)]
    months = (upcoming or all_months)[:3]
    if not months:
        st.info("No exact dates to show yet. Add a notice and its deadlines will appear here.")
        return
    cols = st.columns(len(months), gap="medium")
    for col, (y, m) in zip(cols, months):
        with col:
            st.markdown(calendar_month_html(y, m, events), unsafe_allow_html=True)
    st.markdown(
        "<div class='cal-legend'>Blue days are deadlines in the main notice. Pink days were hidden in the fine print. "
        "Hover a day to see what's due.</div>",
        unsafe_allow_html=True,
    )


def _year_matches(year: str, listed) -> bool:
    text = str(listed).lower()
    if re.search(r"\b(all|any|every)\b", text):
        return True
    return any(re.search(rf"\b{re.escape(a)}\b", text) for a in YEAR_ALIASES.get(year, [year.lower()]))


def _norm_branch(s) -> str:
    t = str(s).lower().replace("&", " and ")
    return " " + re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", t)).strip() + " "


def _branch_matches(branch: str, listed) -> bool:
    text = _norm_branch(listed)
    if re.search(r"\b(all|any|every)\b", text):
        return True
    return any(f" {a} " in text for a in BRANCH_ALIASES.get(branch, [branch.lower()]))


def relevance(analysis, year, branch):
    """Reasons a notice does NOT apply to this class (empty list = applies, or nothing is named)."""
    aff = analysis.get("affected") or {}
    notes = []
    if year != "Any" and aff.get("years"):
        if not any(_year_matches(year, y) for y in aff["years"]):
            notes.append(f"lists {', '.join(map(str, aff['years']))}, not {year}")
    if branch != "Any" and aff.get("branches"):
        if not any(_branch_matches(branch, b) for b in aff["branches"]):
            notes.append(f"lists branches {', '.join(map(str, aff['branches']))}, not {branch}")
    return notes


# ---------------------------------------------------------------- share / export
def share_text(a: dict, deadlines: list) -> str:
    lines = [f"*{a.get('title') or 'Notice'}*"]
    if a.get("one_line_summary"):
        lines.append(str(a["one_line_summary"]))
    ups = [d for d in deadlines if (days_left(d.get("date")) is None or days_left(d.get("date")) >= 0)]
    if ups:
        lines += ["", "*Deadlines*"]
        for d in sorted(ups, key=lambda x: x.get("date") or "9999"):
            dt = parse_date(d.get("date"))
            when = f"{dt:%a, %d %b %Y}" if dt else (d.get("date_text") or "date unclear")
            flag = " (fine print!)" if d.get("is_footnote") else ""
            lines.append(f"• {when}: {d.get('event', '')}{flag}")
    acts = a.get("action_points") or []
    if acts:
        lines += ["", "*To do*"] + [f"• {x}" for x in acts]
    lp = a.get("late_policy")
    if lp and "not mentioned" not in str(lp).lower():
        lines += ["", f"*If you're late:* {lp}"]
    lines += ["", "Checked with CampusPulse"]
    return "\n".join(lines)


def _ics_escape(s) -> str:
    return str(s).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")


def build_ics(deadlines: list, title: str):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//CampusPulse//EN", "CALSCALE:GREGORIAN"]
    count = 0
    for d in deadlines:
        dt = parse_date(d.get("date"))
        if not dt:
            continue
        uid = hashlib.md5(f"{title}{d.get('event')}{dt}".encode()).hexdigest()[:16]
        desc = f"From: {title}"
        if d.get("source_quote"):
            desc += f"\nNotice says: {d['source_quote']}"
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}@campuspulse",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{dt:%Y%m%d}",
            f"DTEND;VALUE=DATE:{dt + timedelta(days=1):%Y%m%d}",
            f"SUMMARY:{_ics_escape(d.get('event') or 'Deadline')}",
            f"DESCRIPTION:{_ics_escape(desc)}",
            "BEGIN:VALARM",
            "TRIGGER:-P1D",
            "ACTION:DISPLAY",
            "DESCRIPTION:Deadline tomorrow",
            "END:VALARM",
            "END:VEVENT",
        ]
        count += 1
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines), count


# ---------------------------------------------------------------- chat rendering
def msg_body(m: dict):
    st.markdown(md_safe(m["content"]))
    p = m.get("proof")
    if not p:
        return
    q = esc(p.get("quote", ""))
    if p["status"] == "ok":
        h = f"<div class='proof ok'><b>Found in the notice</b><i>“{q}”</i></div>"
    elif p["status"] == "bad":
        h = (
            "<div class='proof bad'><b>Couldn't find this line in the notice.</b> "
            f"Check the original before relying on this answer.<i>“{q}”</i></div>"
        )
    else:
        h = "<div class='proof none'>No line in the notice backs this answer up.</div>"
    st.markdown(h, unsafe_allow_html=True)


def render_msg(m: dict):
    with st.chat_message(m["role"], avatar="🧑‍🎓" if m["role"] == "user" else "📌"):
        msg_body(m)


# ---------------------------------------------------------------- navigation helpers
def go_dashboard():
    st.session_state.view = "dashboard"
    st.session_state.current = None
    st.session_state.chat = []


def open_notice(doc: dict):
    st.session_state.current = doc
    st.session_state.chat = []
    st.session_state.view = "notice"


# ---------------------------------------------------------------- sidebar
def render_sidebar(docs: list):
    cur = st.session_state.current
    view = st.session_state.view
    done = load_done()
    with st.sidebar:
        st.markdown(
            "<div class='brand'>📌 CampusPulse</div>"
            "<div class='tag'>Never miss a deadline buried in a circular.</div>",
            unsafe_allow_html=True,
        )
        if docs:
            if st.button("🏠 Dashboard", use_container_width=True,
                         type="primary" if view == "dashboard" else "secondary"):
                go_dashboard()
                st.rerun()
        if st.button("＋ New notice", use_container_width=True, type="primary" if view == "new" else "secondary"):
            st.session_state.current = None
            st.session_state.chat = []
            st.session_state.view = "new"
            st.rerun()

        st.markdown("<div class='side-h'>Your class</div>", unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        sel_y = c1.selectbox("Year", YEAR_OPTS, key="sel_year", format_func=lambda x: YEAR_LABELS.get(x, x))
        sel_b = c2.selectbox("Branch", BRANCH_OPTS, key="sel_branch", format_func=lambda x: "Select" if x == PICK else x)
        year = "Any" if sel_y == PICK else sel_y
        branch = "Any" if sel_b == PICK else sel_b
        st.caption("Your dashboard only shows notices for your class. We remember your choice on this browser.")

        st.markdown("<div class='side-h'>Coming up</div>", unsafe_allow_html=True)
        items = []
        for doc in docs:
            for d in doc["analysis"].get("deadlines") or []:
                if not isinstance(d, dict):
                    continue
                if (doc["id"], dl_key(d)) in done:
                    continue
                if relevance(doc["analysis"], year, branch):
                    continue
                delta = days_left(d.get("date"))
                if delta is not None and delta >= 0:
                    items.append((delta, d))
        items.sort(key=lambda x: x[0])
        if items:
            rows = "".join(
                f"<div class='up'><span class='pill {urgency(delta)[0]}'>{short_when(delta)}</span>"
                f"<span>{esc(truncate(d.get('event', ''), 44))}</span></div>"
                for delta, d in items[:4]
            )
            st.markdown(rows, unsafe_allow_html=True)
        else:
            st.caption("Nothing due yet. Add a notice and it shows up here.")

        st.markdown("<div class='side-h'>Saved notices</div>", unsafe_allow_html=True)
        if not docs:
            st.caption("Your analyzed notices are saved here.")
        for d in docs[:8]:
            label = truncate(d["analysis"].get("title") or d["name"], 34)
            is_cur = view == "notice" and bool(cur) and cur.get("id") == d["id"]
            if st.button(label, key=f"doc{d['id']}", use_container_width=True,
                         type="primary" if is_cur else "secondary"):
                open_notice(d)
                st.rerun()
    return year, branch


# ---------------------------------------------------------------- landing
def render_landing():
    st.markdown(
        "<div class='hero'><h1>What's due, and by when?</h1>"
        "<p>Drop in a college circular. CampusPulse lists every deadline, tells you whether it applies to your class, "
        "and shows the exact line it found each one in, even the ones hiding in the fine print.</p></div>",
        unsafe_allow_html=True,
    )

    c1, c2 = st.columns(2, gap="large")
    with c1:
        ups = st.file_uploader(
            "Upload a circular (PDF, photo, screenshot or TXT)",
            type=["pdf", "txt"] + IMG_TYPES,
            accept_multiple_files=True,
        )
        st.caption("Photos and scanned PDFs are read automatically. Several photos of one notice are fine.")
    with c2:
        st.text_area("Or paste the notice text", height=170, key="paste_box", placeholder="Paste the circular here…")
    pasted = st.session_state.get("paste_box", "")

    if ups and pasted.strip():
        st.caption("You gave both files and pasted text. The uploaded files will be used.")

    b1, b2 = st.columns([2, 1])
    go = b1.button("Find my deadlines", type="primary", use_container_width=True)
    b2.button("Try a sample notice", on_click=load_sample, use_container_width=True)

    if go:
        text, name, ocr = "", "", {"used": False, "notes": []}
        if ups:
            err = None
            try:
                with st.spinner("Reading your files…"):
                    text, ocr = extract_all(ups)
            except Exception as e:
                err = e
            name = ups[0].name if len(ups) == 1 else f"{len(ups)} files"
            if err:
                st.error(f"Couldn't read the file: {err}")
            elif not text:
                if pasted.strip():
                    st.info("No text could be read from the files, so the pasted text is being used.")
                else:
                    st.warning("No text could be read from these files. Try a clearer photo, or paste the text instead.")
        if not text and pasted.strip():
            text, name, ocr = pasted.strip(), "Pasted notice", {"used": False, "notes": []}
        if not text:
            if not ups:
                st.warning("Upload a file or paste some text first.")
        else:
            done = False
            with st.spinner("Reading the fine print…"):
                try:
                    analysis = analyze(text)
                    analysis["ocr"] = ocr
                    doc_id = save_doc(name, text, analysis)
                    st.session_state.current = {"id": doc_id, "name": name, "text": text, "analysis": analysis}
                    st.session_state.chat = []
                    st.session_state.view = "notice"
                    done = True
                except Exception as e:
                    st.error(f"Couldn't analyze this notice: {e}. Check your GROQ_API_KEY and try again.")
            if done:
                st.rerun()

    st.markdown(
        "<div class='feats'>"
        "<div><b>Every deadline in one place</b>Dates are pulled out, sorted and counted down for you.</div>"
        "<div><b>Fine print gets flagged</b>Deadlines buried at the bottom are highlighted so you can't miss them.</div>"
        "<div><b>Answers show their proof</b>Each answer comes with the line in the notice it's based on.</div>"
        "</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------- dashboard
def collect_items(docs: list, year: str, branch: str) -> list:
    """Flatten every deadline from every saved notice into one list."""
    done = load_done()
    items = []
    for doc in docs:
        a = doc["analysis"]
        not_for_me = bool(relevance(a, year, branch))
        title = a.get("title") or doc["name"]
        for d in a.get("deadlines") or []:
            if not isinstance(d, dict):
                continue
            key = dl_key(d)
            items.append({
                "doc": doc,
                "d": d,
                "key": key,
                "title": title,
                "delta": days_left(d.get("date")),
                "done": (doc["id"], key) in done,
                "not_for_me": not_for_me,
                "fine": bool(d.get("is_footnote")),
            })
    return items


def in_window(delta, when: str) -> bool:
    if when == "Everything":
        return True
    if delta is None:                      # unclear dates stay visible in "All upcoming"
        return when == "All upcoming"
    if when == "Overdue":
        return delta < 0
    if when == "Next 7 days":
        return 0 <= delta <= 7
    if when == "Next 30 days":
        return 0 <= delta <= 30
    return delta >= 0                      # All upcoming


def bucket(delta) -> str:
    if delta is None:
        return "Date unclear"
    if delta < 0:
        return "Overdue"
    if delta == 0:
        return "Today"
    if delta <= 7:
        return "This week"
    return "Later"


BUCKET_ORDER = ["Overdue", "Today", "This week", "Later", "Date unclear"]


def sort_key(it):
    return (it["delta"] if it["delta"] is not None else 10 ** 6, it["title"])


def render_item(it: dict, scope: str):
    d, doc = it["d"], it["doc"]
    st.markdown(
        deadline_card(d, doc["text"], source=it["title"], done=it["done"]),
        unsafe_allow_html=True,
    )
    wkey = f"done_{scope}_{doc['id']}_{it['key']}"
    c1, c2, _ = st.columns([1.2, 1.2, 3])
    c1.checkbox(
        "Mark as done",
        value=it["done"],
        key=wkey,
        on_change=_toggle_done,
        args=(doc["id"], it["key"], wkey),
    )
    if c2.button("Open notice", key=f"open_{scope}_{doc['id']}_{it['key']}"):
        open_notice(doc)
        st.rerun()


def render_week_strip(items: list):
    today = date.today()
    cols = st.columns(7, gap="small")
    for i, col in enumerate(cols):
        day = today + timedelta(days=i)
        n = sum(1 for it in items if parse_date(it["d"].get("date")) == day)
        label = "Today" if i == 0 else f"{day:%a}"
        weight = "700" if n else "400"
        opacity = "1" if n else "0.5"
        count = f"{n} due" if n else "free"
        col.markdown(
            f"<div style='text-align:center;opacity:{opacity};line-height:1.4'>"
            f"<div style='font-size:.8rem'>{label}</div>"
            f"<div style='font-size:1.4rem;font-weight:{weight}'>{day.day}</div>"
            f"<div style='font-size:.75rem'>{count}</div></div>",
            unsafe_allow_html=True,
        )


def render_dashboard(docs: list, year: str, branch: str):
    st.markdown(
        "<div class='nh'><h2>Your dashboard</h2>"
        "<p>Every deadline from all your saved notices. Pick what you want to see below.</p></div>",
        unsafe_allow_html=True,
    )

    class_set = year != "Any" and branch != "Any"
    if not class_set:
        note("info", "<b>Choose your year and branch first.</b> Pick them in the sidebar and this page will only "
                     "show notices for your class. Your choice is remembered on this browser.")
        return
    st.caption(f"Showing notices for {YEAR_LABELS.get(year, year)} · Branch {branch}")

    # ---- selections
    labels = {d["id"]: truncate(d["analysis"].get("title") or d["name"], 50) for d in docs}
    labels = {i: t for i, t in labels.items() if not relevance(next(d for d in docs if d["id"] == i)["analysis"], year, branch)}
    notice_opts = [ALL_NOTICES] + list(labels)
    st.markdown("#### Show me")
    r1 = st.columns(3, gap="medium")
    sel_notice = r1[0].selectbox(
        "Notice", notice_opts, key="dash_notice",
        format_func=lambda x: x if x == ALL_NOTICES else labels.get(x, str(x)),
    )
    sel_when = r1[1].selectbox("When", WHEN_OPTS, key="dash_when")
    sel_status = r1[2].selectbox("Status", STATUS_OPTS, key="dash_status")
    r2 = st.columns(3, gap="medium")
    sel_type = r2[0].selectbox("Type", TYPE_OPTS, key="dash_type")
    sel_sort = r2[1].selectbox("Sort by", SORT_OPTS, key="dash_sort")

    # ---- base set: notice + type + class filters (the stats use this)
    items = [it for it in collect_items(docs, year, branch) if not it["not_for_me"]]
    other_class = sum(1 for d in docs if relevance(d["analysis"], year, branch))
    base = [it for it in items if sel_notice == ALL_NOTICES or it["doc"]["id"] == sel_notice]
    if sel_type == "Main notice only":
        base = [it for it in base if not it["fine"]]
    elif sel_type == "Fine print only":
        base = [it for it in base if it["fine"]]

    pending = [it for it in base if not it["done"]]
    upcoming = [it for it in pending if it["delta"] is not None and it["delta"] >= 0]
    overdue = [it for it in pending if it["delta"] is not None and it["delta"] < 0]

    # ---- stat tiles
    m = st.columns(4, gap="medium")
    m[0].metric("Overdue", len(overdue))
    m[1].metric("Next 3 days", sum(1 for it in upcoming if it["delta"] <= 3))
    m[2].metric("Next 7 days", sum(1 for it in upcoming if it["delta"] <= 7))
    m[3].metric("Done", f"{sum(1 for it in base if it['done'])} / {len(base)}")

    # ---- fine-print warning
    sneaky = [it for it in upcoming if it["fine"]]
    if sneaky:
        note("warn", f"<b>{len(sneaky)} upcoming deadline{'s are' if len(sneaky) != 1 else ' is'} hiding in the fine print.</b> "
                     "Use Type → Fine print only to see them.")
    if other_class:
        st.caption(f"{other_class} saved notice{'s are' if other_class != 1 else ' is'} for other classes and not shown here.")

    # ---- next up + week strip
    st.markdown("#### This week at a glance")
    render_week_strip(upcoming)
    if upcoming:
        nxt = sorted(upcoming, key=sort_key)[0]
        st.markdown("#### Next up")
        st.markdown(deadline_card(nxt["d"], nxt["doc"]["text"], source=nxt["title"]), unsafe_allow_html=True)

    # ---- filtered list
    shown = [it for it in base if in_window(it["delta"], sel_when)]
    if sel_status == "To do":
        shown = [it for it in shown if not it["done"]]
    elif sel_status == "Done":
        shown = [it for it in shown if it["done"]]

    st.markdown(f"#### Deadlines ({len(shown)})")
    if not items:
        st.info("No deadlines for your class yet. Add a notice and it will show up here.")
    elif not shown:
        st.info("Nothing matches these selections. Try changing “When” or “Status”.")
    elif sel_sort == "Group by notice":
        for title in dict.fromkeys(it["title"] for it in sorted(shown, key=lambda x: x["title"])):
            st.markdown(f"##### {md_safe(title)}")
            for it in sorted([x for x in shown if x["title"] == title], key=sort_key):
                render_item(it, "list")
    elif sel_sort == "Latest first":
        for it in sorted(shown, key=sort_key, reverse=True):
            render_item(it, "list")
    else:
        ordered = sorted(shown, key=sort_key)
        for b in BUCKET_ORDER:
            group = [it for it in ordered if bucket(it["delta"]) == b]
            if not group:
                continue
            st.markdown(f"##### {b} ({len(group)})")
            for it in group:
                render_item(it, "list")

    # ---- calendar of what is being shown
    with st.expander("Calendar view of these deadlines"):
        render_calendar([it["d"] for it in shown])


# ---------------------------------------------------------------- results
def render_results(cur: dict, docs: list, year: str, branch: str):
    a, text = cur["analysis"], cur["text"]
    deadlines = [d for d in (a.get("deadlines") or []) if isinstance(d, dict)]
    aff = a.get("affected") or {}

    if docs:
        if st.button("← Back to dashboard"):
            go_dashboard()
            st.rerun()

    # header
    st.markdown(
        f"<div class='nh'><h2>{esc(a.get('title') or cur['name'])}</h2><p>{esc(a.get('one_line_summary', ''))}</p></div>",
        unsafe_allow_html=True,
    )

    # who it's for
    chips = "".join(f"<span class='chip'>{esc(y)}</span>" for y in aff.get("years") or [])
    chips += "".join(f"<span class='chip'>{esc(b)}</span>" for b in aff.get("branches") or [])
    if not chips:
        chips = "<span class='chip plain'>Everyone (nothing specific is named)</span>"
    st.markdown(f"<div class='who'><b>Who it's for</b>{chips}</div>", unsafe_allow_html=True)

    # text came from an image / scan
    ocr = a.get("ocr") or {}
    if ocr.get("used"):
        note("info", "<b>Read from an image or scan.</b> Text recognition can misread digits, so double-check the dates against the original.")
    for n in ocr.get("notes") or []:
        note("warn", esc(n))

    # relevance to the student's class
    notes = relevance(a, year, branch)
    if notes:
        for n in notes:
            note("warn", f"<b>This may not be for you.</b> This notice {esc(n)}.")
    elif year != "Any" or branch != "Any":
        if aff.get("years") or aff.get("branches"):
            note("ok", "<b>This looks relevant to you.</b> Nothing in it rules out your year or branch.")
        else:
            note("info", "<b>No year or branch is named,</b> so it probably applies to everyone.")

    # late policy
    late = a.get("late_policy")
    if late and "not mentioned" not in str(late).lower():
        note("warn", f"<b>If you're late:</b> {esc(late)}")

    # the loud one: deadlines hidden in the fine print
    hidden = [d for d in deadlines if d.get("is_footnote")]
    if hidden:
        st.markdown(fineprint_alert(hidden), unsafe_allow_html=True)

    tab_do, tab_ask, tab_src, tab_cal = st.tabs(["What to do", "Ask", "Source", "Calendar"])

    # ---- what to do
    with tab_do:
        tldr = [x for x in (a.get("tldr") or []) if x][:4]
        if tldr:
            st.markdown("#### In short")
            st.markdown("\n".join(f"- {md_safe(x)}" for x in tldr))

        st.markdown("#### Deadlines")
        done_set = load_done()
        upcoming = sorted([d for d in deadlines if (days_left(d.get("date")) or 0) >= 0 and days_left(d.get("date")) is not None],
                          key=lambda d: d.get("date") or "9999")
        unknown = [d for d in deadlines if days_left(d.get("date")) is None]
        past = sorted([d for d in deadlines if days_left(d.get("date")) is not None and days_left(d.get("date")) < 0],
                      key=lambda d: d.get("date") or "", reverse=True)
        if not deadlines:
            st.info("No deadlines found in this notice. Check the Source tab to read the full text.")
        for d in upcoming + unknown:
            st.markdown(deadline_card(d, text, done=(cur["id"], dl_key(d)) in done_set), unsafe_allow_html=True)
        if past:
            with st.expander(f"Already passed ({len(past)})"):
                for d in past:
                    st.markdown(deadline_card(d, text, done=(cur["id"], dl_key(d)) in done_set), unsafe_allow_html=True)

        actions = [x for x in (a.get("action_points") or []) if x]
        if actions:
            st.markdown("#### Your checklist")
            for i, x in enumerate(actions):
                st.checkbox(str(x), key=f"todo_{cur['id']}_{i}")

        st.markdown("#### Take it with you")
        s1, s2 = st.columns(2, gap="large")
        with s1:
            st.caption("Copy this into your class group")
            st.code(share_text(a, deadlines), language=None)
        with s2:
            ics, n_ev = build_ics(deadlines, a.get("title") or cur["name"])
            if n_ev:
                st.caption("Works with Google Calendar, Apple Calendar and Outlook. You get a reminder the day before.")
                st.download_button(
                    f"Add {n_ev} deadline{'s' if n_ev != 1 else ''} to my calendar",
                    data=ics,
                    file_name="campuspulse-deadlines.ics",
                    mime="text/calendar",
                    use_container_width=True,
                )
            else:
                st.caption("No exact dates were found, so there's nothing to add to a calendar.")

    # ---- ask
    with tab_ask:
        st.caption("Answers come only from this notice, and each one shows the line it's based on.")
        history = st.container()
        chips_slot = st.empty()
        clicked = None
        if not st.session_state.chat:
            with chips_slot.container():
                cols = st.columns(len(SUGGESTIONS))
                for i, (c, s) in enumerate(zip(cols, SUGGESTIONS)):
                    if c.button(s, key=f"sg{i}", use_container_width=True):
                        clicked = s
        typed = st.chat_input("Ask about this notice, e.g. Is late submission allowed?")
        q = clicked or typed

        with history:
            for m in st.session_state.chat:
                render_msg(m)
            if q:
                chips_slot.empty()
                um = {"role": "user", "content": q}
                st.session_state.chat.append(um)
                render_msg(um)
                with st.chat_message("assistant", avatar="📌"):
                    with st.spinner("Checking the notice…"):
                        am = answer_question(q, text, f"Year={year}, Branch={branch}")
                    msg_body(am)
                st.session_state.chat.append(am)

    # ---- source
    with tab_src:
        with st.expander("Skim test: what you'd see if you only read the top", expanded=bool(hidden)):
            st.markdown(f"<div class='skim'>{esc_text(text[:450])}…</div>", unsafe_allow_html=True)
            if hidden:
                st.markdown(
                    "<div class='note warn'><b>What a quick skim misses:</b> "
                    + "; ".join(f"{esc(d.get('date_text', ''))} ({esc(d.get('event', ''))})" for d in hidden)
                    + "</div>",
                    unsafe_allow_html=True,
                )
            else:
                note("info", "No fine-print deadlines in this notice.")

        st.markdown(
            "<div class='legend'><span><mark class='body'>&nbsp;</mark>Deadline in the main notice</span>"
            "<span><mark class='fine'>&nbsp;</mark>Deadline in the fine print</span></div>",
            unsafe_allow_html=True,
        )
        marked, missing = highlight_html(text, deadlines)
        st.markdown(f"<div class='paper' tabindex='0'>{marked}</div>", unsafe_allow_html=True)
        if missing:
            st.caption(
                f"{missing} deadline{'s' if missing != 1 else ''} couldn't be located in the text, so "
                f"{'it is' if missing == 1 else 'they are'} not highlighted. Check the original."
            )

    # ---- calendar
    with tab_cal:
        st.caption("Deadlines from all your saved notices.")
        render_calendar([dl for d in docs for dl in (d["analysis"].get("deadlines") or []) if isinstance(dl, dict)])

    # ---- manage
    with st.expander("Manage this notice"):
        sure = st.checkbox("Yes, delete this notice and its saved progress", key=f"del_ok_{cur['id']}")
        if st.button("Delete notice", disabled=not sure, key=f"del_{cur['id']}"):
            delete_doc(cur["id"])
            go_dashboard()
            st.rerun()


# ---------------------------------------------------------------- app
apply_theme()

if "current" not in st.session_state:
    st.session_state.current = None  # dict(id,name,text,analysis)
if "chat" not in st.session_state:
    st.session_state.chat = []

all_docs = list_docs()

# ---- remember the student's class in a browser cookie
cookies = stx.CookieManager(key="cp_cookies")
st.session_state.setdefault("sel_year", PICK)
st.session_state.setdefault("sel_branch", PICK)
if not st.session_state.get("prefs_synced"):
    _y, _b = cookies.get("cp_year"), cookies.get("cp_branch")
    if _y in YEAR_OPTS[1:] or _b in BRANCH_OPTS[1:]:
        if _y in YEAR_OPTS[1:]:
            st.session_state.sel_year = _y
        if _b in BRANCH_OPTS[1:]:
            st.session_state.sel_branch = _b
        st.session_state.saved_prefs = (st.session_state.sel_year, st.session_state.sel_branch)
        st.session_state.prefs_synced = True

if "view" not in st.session_state:
    st.session_state.view = "dashboard" if all_docs else "new"
if st.session_state.view == "dashboard" and not all_docs:
    st.session_state.view = "new"

sel_year, sel_branch = render_sidebar(all_docs)

_pref = (st.session_state.sel_year, st.session_state.sel_branch)
if _pref != st.session_state.get("saved_prefs", (PICK, PICK)):
    _exp = datetime.now() + timedelta(days=COOKIE_DAYS)
    cookies.set("cp_year", _pref[0], expires_at=_exp, key="set_cp_year")
    cookies.set("cp_branch", _pref[1], expires_at=_exp, key="set_cp_branch")
    st.session_state.saved_prefs = _pref
    st.session_state.prefs_synced = True

_view = st.session_state.view
if _view == "notice" and st.session_state.current:
    render_results(st.session_state.current, all_docs, sel_year, sel_branch)
elif _view == "dashboard" and all_docs:
    render_dashboard(all_docs, sel_year, sel_branch)
else:
    render_landing()