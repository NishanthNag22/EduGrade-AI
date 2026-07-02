"""
EduGrade AI — Backend
FastAPI + Groq (LLaMA 3.3 70B) + Plagiarism Detection + PDF Reports

FIXES APPLIED (see inline "# FIX N:" comments):
 1. Added missing /feedback-txt/{job_id}/{file_id} route (frontend linked to it, 404'd before)
 2. Sanitized uploaded filenames to prevent path traversal
 3. Restricted CORS to an allow-list (env-configurable) instead of "*"
 4. Lightweight in-memory rate limiter on /upload and /evaluate
 6. Rubric point total (=100) now validated server-side, not just in the frontend
 7. run_job now actually evaluates submissions concurrently (asyncio.gather + semaphore)
 8. Plagiarism check precomputes token counters once per file instead of per pair (O(n) not O(n^2) tokenization)
 9. Temp upload/report files now cleaned up automatically after a TTL
10. Exceptions are logged with the raw LLM output for debuggability instead of being swallowed silently
11. Groq calls retry with exponential backoff on rate-limit / transient errors
12. Upload size is capped and the Groq/httpx client is reused instead of rebuilt per call
"""
from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional
from groq import Groq
from dotenv import load_dotenv
import json, os, re, uuid, math, shutil, tempfile, asyncio, httpx, time, logging
from datetime import datetime
from collections import Counter, defaultdict, deque
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

# .env lives at the project root (one level above backend/), not in this
# folder — point load_dotenv() at it explicitly so it's found regardless of
# the working directory the server is launched from (matters on Render/Docker).
load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("edugrade")

# Optional heavy imports
try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
        Table, TableStyle, HRFlowable, KeepTogether)
    from reportlab.lib.enums import TA_CENTER
    REPORTLAB_OK = True
except ImportError:
    REPORTLAB_OK = False

try:
    import pdfplumber
    PDFPLUMBER_OK = True
except ImportError:
    PDFPLUMBER_OK = False

try:
    from docx import Document as DocxDoc
    DOCX_OK = True
except ImportError:
    DOCX_OK = False

app = FastAPI(title="EduGrade AI", version="2.1.0")

# ── FIX 3: CORS allow-list instead of "*" ──────────────────────────────────
# Set ALLOWED_ORIGINS="https://your-frontend.com,https://another.com" in env.
# Falls back to localhost dev origins if not set, so local dev still works.
_default_origins = "http://localhost:5500,http://127.0.0.1:5500,http://localhost:8000,http://127.0.0.1:8000"
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", _default_origins).split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS,
                   allow_methods=["*"], allow_headers=["*"])

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", 15 * 1024 * 1024))  # FIX 12: 15MB/file default
FILE_TTL_SECONDS = int(os.environ.get("FILE_TTL_SECONDS", 2 * 60 * 60))  # FIX 9: 2h default

UPLOAD_DIR = Path(tempfile.mkdtemp())
REPORT_DIR = Path(tempfile.mkdtemp())
jobs: dict = {}
job_created_at: dict = {}
ai_executor = ThreadPoolExecutor(max_workers=8)

# ── Models ───────────────────────────────────────────────────────────────
class RubricItem(BaseModel):
    criterion: str
    points: int
    desc: str

class EvaluationRequest(BaseModel):
    rubrics: List[RubricItem]
    file_ids: List[str]

# ── FIX 12: shared client instead of a new one per call ────────────────────
@lru_cache(maxsize=1)
def get_groq_client() -> Groq:
    return Groq(api_key=GROQ_API_KEY, http_client=httpx.Client(timeout=60.0))

# ── FIX 4: simple in-memory sliding-window rate limiter ─────────────────────
_rate_buckets: dict = defaultdict(deque)
RATE_LIMIT = int(os.environ.get("RATE_LIMIT_PER_HOUR", 20))
RATE_WINDOW = 3600

def client_ip(request: Request) -> str:
    # Render (and most PaaS/reverse-proxy setups) put the real client IP in
    # X-Forwarded-For, while request.client.host often reports the proxy's
    # own address for every visitor — which would silently collapse this
    # per-IP limiter into one shared bucket for the whole app. Prefer the
    # header when present; fall back to the direct connection otherwise
    # (e.g. running locally with no proxy in front).
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

def rate_limit(request: Request):
    ip = client_ip(request)
    now = time.time()
    bucket = _rate_buckets[ip]
    while bucket and now - bucket[0] > RATE_WINDOW:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT:
        raise HTTPException(429, "Rate limit exceeded. Try again later.")
    bucket.append(now)

# ── FIX 2: sanitize filenames to prevent path traversal ─────────────────────
def safe_filename(name: str) -> str:
    name = Path(name).name  # strips any directory components (../, /, \)
    name = re.sub(r"[^A-Za-z0-9_.\- ]", "_", name)
    return name[:255] or "file"

# ── Text extraction ──────────────────────────────────────────────────────
def extract_text(path: str, filename: str) -> str:
    ext = filename.lower().rsplit(".", 1)[-1]
    if ext == "pdf" and PDFPLUMBER_OK:
        try:
            text = ""
            with pdfplumber.open(path) as pdf:
                for page in pdf.pages:
                    t = page.extract_text()
                    if t:
                        text += t + "\n"
            return text
        except Exception:
            log.exception("PDF extraction failed for %s", filename)
            return ""
    elif ext == "docx" and DOCX_OK:
        try:
            doc = DocxDoc(path)
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        except Exception:
            log.exception("DOCX extraction failed for %s", filename)
            return ""
    else:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception:
            log.exception("Text extraction failed for %s", filename)
            return ""

# ── FIX 11: retry wrapper for Groq calls (rate limits / transient errors) ──
def call_groq_with_retry(fn, *args, max_retries=3, **kwargs):
    delay = 1.5
    last_err = None
    for attempt in range(max_retries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_err = e
            msg = str(e)
            transient = "429" in msg or "rate" in msg.lower() or "timeout" in msg.lower() or "503" in msg
            if attempt < max_retries - 1 and transient:
                log.warning("Groq call failed (attempt %d/%d): %s — retrying in %.1fs",
                            attempt + 1, max_retries, msg, delay)
                time.sleep(delay)
                delay *= 2
                continue
            raise
    raise last_err

# ── Rubric parsing ────────────────────────────────────────────────────────
def parse_rubric_from_text(text: str) -> List[dict]:
    if not GROQ_API_KEY:
        return simple_rubric_parse(text)
    try:
        client = get_groq_client()
        prompt = f"""Parse this rubric into a JSON array. Each item needs:
- "criterion": criterion name
- "points": integer points
- "desc": what is evaluated

Text:
{text[:3000]}

Respond ONLY with valid JSON array, no markdown."""
        resp = call_groq_with_retry(
            client.chat.completions.create,
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=600, temperature=0.1)
        raw = resp.choices[0].message.content.strip()
        cleaned = re.sub(r"^```json|^```|```$", "", raw, flags=re.MULTILINE).strip()
        return json.loads(cleaned)
    except Exception:
        log.exception("Rubric parse via Groq failed, falling back to regex parse")
        return simple_rubric_parse(text)

def simple_rubric_parse(text: str) -> List[dict]:
    rubrics = []
    pattern = re.compile(
        r"([A-Za-z ]+)\s*[\(\[]\s*(\d+)\s*(?:pts?|points?)?\s*[\)\]]\s*[:\-]?\s*(.*)",
        re.IGNORECASE)
    for line in text.splitlines():
        m = pattern.search(line)
        if m:
            rubrics.append({"criterion": m.group(1).strip(),
                            "points": int(m.group(2)),
                            "desc": m.group(3).strip()})
    return rubrics or [{"criterion": "Overall Quality", "points": 100,
                        "desc": "General assessment"}]

# ── Plagiarism (FIX 8: precompute counters once, not per pair) ─────────────
def tokenize(text: str):
    return re.findall(r"[a-z0-9]+", text.lower())

def cosine_sim_from_counters(ca: Counter, cb: Counter, mag_a: float, mag_b: float) -> float:
    if not ca or not cb or not mag_a or not mag_b:
        return 0.0
    vocab = ca.keys() & cb.keys()
    dot = sum(ca[t] * cb[t] for t in vocab)
    return dot / (mag_a * mag_b)

def check_plagiarism(subs: list) -> list:
    counters, mags = [], []
    for s in subs:
        c = Counter(tokenize(s["text"]))
        counters.append(c)
        mags.append(math.sqrt(sum(v * v for v in c.values())))

    pairs = []
    for i in range(len(subs)):
        for j in range(i + 1, len(subs)):
            sim = cosine_sim_from_counters(counters[i], counters[j], mags[i], mags[j])
            if sim > 0.70:
                pairs.append({
                    "file_a": subs[i]["name"],
                    "file_b": subs[j]["name"],
                    "similarity": round(sim * 100)
                })
    return pairs

# ── Groq evaluation ─────────────────────────────────────────────────────
def evaluate_with_groq(rubrics: List[RubricItem], filename: str, text: str) -> dict:
    client = get_groq_client()
    total = sum(r.points for r in rubrics)
    rubric_text = "\n".join(f"- {r.criterion} ({r.points} pts): {r.desc}" for r in rubrics)
    content = f"\n\nContent:\n```\n{text[:5000]}\n```" if text.strip() else ""

    prompt = f"""You are an expert academic evaluator assessing "{filename}".

Rubric (total {total} pts):
{rubric_text}{content}

Respond ONLY with valid JSON (no markdown):
{{
  "scores": [{{"criterion":"...","maxPoints":30,"earned":25,"reasoning":"2-3 specific sentences."}}],
  "summary": "3-4 sentence overall assessment.",
  "vivaQuestions": ["Q1?","Q2?","Q3?","Q4?","Q5?","Q6?","Q7?","Q8?"]
}}

Rules: earned <= maxPoints. Be specific to submission. Exactly 8 viva questions."""

    resp = call_groq_with_retry(
        client.chat.completions.create,
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000, temperature=0.3)
    raw = resp.choices[0].message.content.strip()
    cleaned = re.sub(r"^```json|^```|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # FIX 10: log the raw output so failures are debuggable instead of a bare traceback
        log.error("Failed to parse Groq JSON for %s. Raw output:\n%s", filename, raw)
        raise

# ── PDF generation ────────────────────────────────────────────────────────
def generate_pdf(result: dict, filename: str, rubrics: List[RubricItem],
                 plag_note: Optional[str]) -> str:
    total_pts = sum(r.points for r in rubrics)
    earned = sum(s.get("earned", 0) for s in result.get("scores", []))
    pct = round((earned / total_pts) * 100) if total_pts else 0
    grade = "A" if pct>=90 else "B" if pct>=80 else "C" if pct>=70 else "D" if pct>=60 else "F"
    out = str(REPORT_DIR / f"{uuid.uuid4().hex}_report.pdf")

    doc = SimpleDocTemplate(out, pagesize=letter,
        topMargin=0.8*inch, bottomMargin=0.8*inch,
        leftMargin=1*inch, rightMargin=1*inch)

    def ps(name, **kw):
        return ParagraphStyle(name, **kw)

    sc = "#16a34a" if pct>=70 else "#d97706" if pct>=50 else "#dc2626"
    story = []
    story.append(Paragraph("EduGrade AI — Evaluation Report",
        ps("T", fontName="Helvetica-Bold", fontSize=20,
           textColor=colors.HexColor("#0f172a"), spaceAfter=4)))
    story.append(Paragraph(f"Generated {datetime.now().strftime('%B %d, %Y')}",
        ps("M", fontName="Helvetica", fontSize=10,
           textColor=colors.HexColor("#64748b"), spaceAfter=16)))
    story.append(HRFlowable(width="100%", thickness=1,
        color=colors.HexColor("#e2e8f0"), spaceAfter=12))

    info = [["File", filename], ["Score", f"{earned} / {total_pts} ({pct}%)"],
            ["Grade", grade], ["Model", "LLaMA 3.3 70B via Groq"]]
    t = Table(info, colWidths=[1.2*inch, 5.3*inch])
    t.setStyle(TableStyle([
        ("FONTNAME",(0,0),(0,-1),"Helvetica-Bold"),
        ("TEXTCOLOR",(0,0),(0,-1),colors.HexColor("#64748b")),
        ("TEXTCOLOR",(1,1),(1,2),colors.HexColor(sc)),
        ("FONTNAME",(1,1),(1,2),"Helvetica-Bold"),
        ("FONTSIZE",(1,2),(1,2),14),
        ("ROWBACKGROUNDS",(0,0),(-1,-1),
            [colors.HexColor("#f8fafc"),colors.white]),
        ("TOPPADDING",(0,0),(-1,-1),6),
        ("BOTTOMPADDING",(0,0),(-1,-1),6),
        ("LEFTPADDING",(0,0),(-1,-1),8),
    ]))
    story.append(t); story.append(Spacer(1,12))

    if plag_note:
        story.append(Paragraph("⚠ Plagiarism Alert",
            ps("PW", fontName="Helvetica-Bold", fontSize=11,
               textColor=colors.HexColor("#92400e"), spaceAfter=4)))
        story.append(Paragraph(plag_note,
            ps("W", fontName="Helvetica", fontSize=10,
               textColor=colors.HexColor("#92400e"), leading=14, spaceAfter=8)))

    h2 = ps("H2", fontName="Helvetica-Bold", fontSize=12,
            textColor=colors.HexColor("#0f172a"), spaceBefore=12, spaceAfter=6)
    body = ps("B", fontName="Helvetica", fontSize=10,
              textColor=colors.HexColor("#334155"), leading=15, spaceAfter=6)
    crit = ps("C", fontName="Helvetica-Bold", fontSize=10,
              textColor=colors.HexColor("#1e40af"), spaceAfter=3)

    story.append(HRFlowable(width="100%", thickness=0.5,
        color=colors.HexColor("#e2e8f0"), spaceAfter=8))
    story.append(Paragraph("Detailed Feedback", h2))
    for s in result.get("scores", []):
        p = round((s["earned"]/s["maxPoints"])*100) if s.get("maxPoints") else 0
        story.append(KeepTogether([
            Paragraph(f"{s['criterion']}  —  {s['earned']}/{s['maxPoints']} ({p}%)", crit),
            Paragraph(s.get("reasoning",""), body), Spacer(1,4)]))

    story.append(HRFlowable(width="100%", thickness=0.5,
        color=colors.HexColor("#e2e8f0"), spaceAfter=8))
    story.append(Paragraph("Overall Summary", h2))
    story.append(Paragraph(result.get("summary",""), body))
    story.append(Spacer(1,10))

    story.append(HRFlowable(width="100%", thickness=0.5,
        color=colors.HexColor("#e2e8f0"), spaceAfter=8))
    story.append(Paragraph("Viva Questions", h2))
    for i, q in enumerate(result.get("vivaQuestions",[]), 1):
        story.append(Paragraph(f"{i}.  {q}", body))

    story.append(Spacer(1,20))
    story.append(HRFlowable(width="100%", thickness=0.5,
        color=colors.HexColor("#e2e8f0"), spaceAfter=6))
    story.append(Paragraph("EduGrade AI  •  Powered by LLaMA 3.3 70B via Groq",
        ps("F", fontName="Helvetica", fontSize=8,
           textColor=colors.HexColor("#94a3b8"), alignment=TA_CENTER)))
    doc.build(story)
    return out

# ── FIX 1: text feedback generation (frontend already links to this route) ─
def generate_txt_feedback(result: dict, filename: str, rubrics: List[RubricItem],
                           plag_note: Optional[str]) -> str:
    total_pts = sum(r.points for r in rubrics)
    earned = sum(s.get("earned", 0) for s in result.get("scores", []))
    pct = round((earned / total_pts) * 100) if total_pts else 0
    grade = "A" if pct>=90 else "B" if pct>=80 else "C" if pct>=70 else "D" if pct>=60 else "F"

    lines = [
        "EduGrade AI — Evaluation Report",
        f"Generated {datetime.now().strftime('%B %d, %Y')}",
        "=" * 50,
        f"File:  {filename}",
        f"Score: {earned} / {total_pts} ({pct}%)",
        f"Grade: {grade}",
        "",
    ]
    if plag_note:
        lines += [f"⚠ Plagiarism Alert: {plag_note}", ""]

    lines.append("Detailed Feedback")
    lines.append("-" * 50)
    for s in result.get("scores", []):
        p = round((s["earned"] / s["maxPoints"]) * 100) if s.get("maxPoints") else 0
        lines.append(f"{s['criterion']} — {s['earned']}/{s['maxPoints']} ({p}%)")
        lines.append(s.get("reasoning", ""))
        lines.append("")

    lines.append("Overall Summary")
    lines.append("-" * 50)
    lines.append(result.get("summary", ""))
    lines.append("")

    lines.append("Viva Questions")
    lines.append("-" * 50)
    for i, q in enumerate(result.get("vivaQuestions", []), 1):
        lines.append(f"{i}. {q}")

    out = str(REPORT_DIR / f"{uuid.uuid4().hex}_report.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return out

# ── FIX 9: periodic cleanup of old temp files ───────────────────────────────
async def cleanup_loop():
    while True:
        await asyncio.sleep(600)  # every 10 minutes
        now = time.time()
        for d in (UPLOAD_DIR, REPORT_DIR):
            try:
                for p in d.iterdir():
                    if now - p.stat().st_mtime > FILE_TTL_SECONDS:
                        p.unlink(missing_ok=True)
            except Exception:
                log.exception("Cleanup pass failed for %s", d)
        stale_jobs = [jid for jid, ts in job_created_at.items() if now - ts > FILE_TTL_SECONDS]
        for jid in stale_jobs:
            jobs.pop(jid, None)
            job_created_at.pop(jid, None)

        # Evict rate-limit buckets that have gone quiet (no requests within
        # the window) so _rate_buckets doesn't grow forever with dead IPs.
        for ip in [ip for ip, bucket in _rate_buckets.items()
                   if not bucket or now - bucket[-1] > RATE_WINDOW]:
            del _rate_buckets[ip]

@app.on_event("startup")
async def on_startup():
    asyncio.create_task(cleanup_loop())

# ── API Routes ──────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "groq_key_set": bool(GROQ_API_KEY),
            "pdf": REPORTLAB_OK, "pdf_extract": PDFPLUMBER_OK, "docx": DOCX_OK}

@app.post("/parse-rubric", dependencies=[Depends(rate_limit)])
async def parse_rubric_file(file: UploadFile = File(...)):
    fid = uuid.uuid4().hex
    name = safe_filename(file.filename or "rubric")  # FIX 2
    dest = UPLOAD_DIR / f"{fid}_{name}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    text = extract_text(str(dest), name)
    if not text.strip():
        raise HTTPException(400, "Could not extract text from file.")
    rubrics = parse_rubric_from_text(text)
    return {"rubrics": rubrics}

@app.post("/upload", dependencies=[Depends(rate_limit)])
async def upload_files(files: List[UploadFile] = File(...)):
    uploaded = []
    for file in files:
        fid = uuid.uuid4().hex
        name = safe_filename(file.filename or "file")  # FIX 2: no path traversal

        # FIX 12: enforce per-file size cap while streaming to disk
        dest = UPLOAD_DIR / f"{fid}_{name}"
        size = 0
        with open(dest, "wb") as f:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    f.close()
                    dest.unlink(missing_ok=True)
                    raise HTTPException(413, f"{name} exceeds max upload size of "
                                              f"{MAX_UPLOAD_BYTES // (1024*1024)}MB")
                f.write(chunk)

        uploaded.append({"file_id": fid, "name": name, "size": size})
    return {"files": uploaded}

@app.post("/evaluate", dependencies=[Depends(rate_limit)])
async def start_evaluation(req: EvaluationRequest, bg: BackgroundTasks):
    # FIX 6: server-side rubric total validation (frontend check alone is bypassable)
    total = sum(r.points for r in req.rubrics)
    if total != 100:
        raise HTTPException(400, f"Rubric must total 100 points (got {total})")
    if not req.file_ids:
        raise HTTPException(400, "No files provided")

    job_id = uuid.uuid4().hex
    jobs[job_id] = {"status": "running", "total": len(req.file_ids),
                    "completed": 0, "results": [],
                    "plagiarism_pairs": [], "errors": []}
    job_created_at[job_id] = time.time()
    bg.add_task(run_job, job_id, req.rubrics, req.file_ids)
    return {"job_id": job_id}

@app.get("/job/{job_id}")
def get_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")
    return jobs[job_id]

@app.get("/report/{job_id}/{file_id}")
def get_report(job_id: str, file_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")

    for r in jobs[job_id]["results"]:
        if r.get("file_id") == file_id and r.get("report_path"):
            path = r["report_path"]
            if os.path.exists(path):
                clean_name = Path(r.get("name", "document")).stem
                download_name = f"report_{clean_name}.pdf"
                return FileResponse(path, media_type="application/pdf", filename=download_name)
    raise HTTPException(404, "Report not ready")

# FIX 1: the route the frontend was already trying to call
@app.get("/feedback-txt/{job_id}/{file_id}")
def get_feedback_txt(job_id: str, file_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")

    for r in jobs[job_id]["results"]:
        if r.get("file_id") == file_id and r.get("txt_path"):
            path = r["txt_path"]
            if os.path.exists(path):
                clean_name = Path(r.get("name", "document")).stem
                download_name = f"feedback_{clean_name}.txt"
                return FileResponse(path, media_type="text/plain", filename=download_name)
    raise HTTPException(404, "Feedback not ready")

# ── Background job (FIX 7: real concurrency via asyncio.gather + semaphore) ─
async def run_job(job_id: str, rubrics: List[RubricItem], file_ids: List[str]):
    job = jobs[job_id]
    file_map = {}
    for fid in file_ids:
        matches = list(UPLOAD_DIR.glob(f"{fid}_*"))
        if matches:
            file_map[fid] = matches[0]

    subs = []
    for fid, path in file_map.items():
        name = path.name.split("_", 1)[-1]
        text = extract_text(str(path), name)
        subs.append({"file_id": fid, "name": name, "text": text})

    pairs = check_plagiarism(subs)
    job["plagiarism_pairs"] = pairs
    plag_map: dict = {}
    for p in pairs:
        plag_map.setdefault(p["file_a"], []).append(
            f"{p['similarity']}% similar to \"{p['file_b']}\"")
        plag_map.setdefault(p["file_b"], []).append(
            f"{p['similarity']}% similar to \"{p['file_a']}\"")

    total_pts = sum(r.points for r in rubrics)
    loop = asyncio.get_event_loop()
    sem = asyncio.Semaphore(4)  # matches ai_executor's effective concurrency for Groq calls

    async def process_one(sub):
        fid, name, text = sub["file_id"], sub["name"], sub["text"]
        plag_note = "; ".join(plag_map.get(name, []))
        async with sem:
            try:
                result = await loop.run_in_executor(
                    ai_executor, evaluate_with_groq, rubrics, name, text)
                report_path = None
                txt_path = None
                if REPORTLAB_OK:
                    report_path = await loop.run_in_executor(
                        ai_executor, generate_pdf, result, name, rubrics, plag_note or None)
                txt_path = await loop.run_in_executor(
                    ai_executor, generate_txt_feedback, result, name, rubrics, plag_note or None)
                earned = sum(s.get("earned", 0) for s in result.get("scores", []))
                pct = round((earned / total_pts) * 100) if total_pts else 0
                grade = ("A" if pct>=90 else "B" if pct>=80 else
                         "C" if pct>=70 else "D" if pct>=60 else "F")
                job["results"].append({
                    "file_id": fid, "name": name,
                    "status": "plagiarism" if plag_note else "done",
                    "pct": pct, "grade": grade,
                    "total_earned": earned, "total_pts": total_pts,
                    "plag_note": plag_note, "result": result,
                    "report_path": report_path, "txt_path": txt_path,
                })
            except Exception as e:
                log.exception("Evaluation failed for %s", name)  # FIX 10
                job["results"].append({
                    "file_id": fid, "name": name,
                    "status": "error", "error": str(e)})
                job["errors"].append({"file": name, "error": str(e)})
            job["completed"] += 1

    await asyncio.gather(*(process_one(sub) for sub in subs))
    job["status"] = "done"
