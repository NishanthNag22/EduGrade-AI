"""
EduGrade AI — Backend
FastAPI + Groq (LLaMA 3.3 70B) + Plagiarism Detection + PDF Reports
"""
from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional
from groq import Groq
from dotenv import load_dotenv
import json, os, re, uuid, math, shutil, tempfile, asyncio, httpx
from datetime import datetime
from collections import Counter
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
load_dotenv()

# Optional heavy imports
try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
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

app = FastAPI(title="EduGrade AI", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
UPLOAD_DIR = Path(tempfile.mkdtemp())
REPORT_DIR = Path(tempfile.mkdtemp())
jobs: dict = {}
ai_executor = ThreadPoolExecutor(max_workers=4)

# ── Models ────────────────────────────────────────────────────────────────────
class RubricItem(BaseModel):
    criterion: str
    points: int
    desc: str

class EvaluationRequest(BaseModel):
    rubrics: List[RubricItem]
    file_ids: List[str]

def get_groq_client():
    """Explicitly uses a clean httpx client to bypass proxy errors."""
    return Groq(
        api_key=GROQ_API_KEY,
        http_client=httpx.Client()
    )

# ── Text extraction ───────────────────────────────────────────────────────────
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
            return ""
    elif ext == "docx" and DOCX_OK:
        try:
            doc = DocxDoc(path)
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        except Exception:
            return ""
    else:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception:
            return ""

# ── Rubric parsing ────────────────────────────────────────────────────────────
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
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=600, temperature=0.1)
        raw = resp.choices[0].message.content.strip()
        cleaned = re.sub(r"^```json|^```|```$", "", raw, flags=re.MULTILINE).strip()
        return json.loads(cleaned)
    except Exception:
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

# ── Plagiarism ────────────────────────────────────────────────────────────────
def tokenize(text: str):
    return re.findall(r"[a-z0-9]+", text.lower())

def cosine_sim(a: str, b: str) -> float:
    ca, cb = Counter(tokenize(a)), Counter(tokenize(b))
    if not ca or not cb:
        return 0.0
    vocab = set(ca) | set(cb)
    dot = sum(ca[t] * cb[t] for t in vocab)
    mag = lambda c: math.sqrt(sum(v*v for v in c.values()))
    ma, mb = mag(ca), mag(cb)
    return dot / (ma * mb) if ma and mb else 0.0

def check_plagiarism(subs: list) -> list:
    pairs = []
    for i in range(len(subs)):
        for j in range(i+1, len(subs)):
            sim = cosine_sim(subs[i]["text"], subs[j]["text"])
            if sim > 0.70:
                pairs.append({
                    "file_a": subs[i]["name"],
                    "file_b": subs[j]["name"],
                    "similarity": round(sim * 100)
                })
    return pairs

# ── Groq evaluation ───────────────────────────────────────────────────────────
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

    resp = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000, temperature=0.3)
    raw = resp.choices[0].message.content.strip()
    cleaned = re.sub(r"^```json|^```|```$", "", raw, flags=re.MULTILINE).strip()
    return json.loads(cleaned)

# ── PDF generation ────────────────────────────────────────────────────────────
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

# ── API Routes ────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "groq_key_set": bool(GROQ_API_KEY),
            "pdf": REPORTLAB_OK, "pdf_extract": PDFPLUMBER_OK, "docx": DOCX_OK}

@app.post("/parse-rubric")
async def parse_rubric_file(file: UploadFile = File(...)):
    fid = uuid.uuid4().hex
    dest = UPLOAD_DIR / f"{fid}_{file.filename}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    text = extract_text(str(dest), file.filename)
    if not text.strip():
        raise HTTPException(400, "Could not extract text from file.")
    rubrics = parse_rubric_from_text(text)
    return {"rubrics": rubrics}

@app.post("/upload")
async def upload_files(files: List[UploadFile] = File(...)):
    uploaded = []
    for file in files:
        fid = uuid.uuid4().hex
        dest = UPLOAD_DIR / f"{fid}_{file.filename}"
        with open(dest, "wb") as f:
            shutil.copyfileobj(file.file, f)
        uploaded.append({"file_id": fid, "name": file.filename,
                         "size": dest.stat().st_size})
    return {"files": uploaded}

@app.post("/evaluate")
async def start_evaluation(req: EvaluationRequest, bg: BackgroundTasks):
    job_id = uuid.uuid4().hex
    jobs[job_id] = {"status": "running", "total": len(req.file_ids),
                    "completed": 0, "results": [],
                    "plagiarism_pairs": [], "errors": []}
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
                # .stem removes the extension (e.g., 'code.py' becomes 'code')
                clean_name = Path(r.get("name", "document")).stem
                download_name = f"report_{clean_name}.pdf"
                
                return FileResponse(
                    path, 
                    media_type="application/pdf",
                    filename=download_name
                )
    raise HTTPException(404, "Report not ready")
# Serve frontend from /frontend folder
app.mount("/", StaticFiles(directory="../frontend", html=True), name="static")

# ── Background job ────────────────────────────────────────────────────────────
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

    for sub in subs:
        fid, name, text = sub["file_id"], sub["name"], sub["text"]
        plag_note = "; ".join(plag_map.get(name, []))
        try:
            result = await loop.run_in_executor(
                ai_executor, evaluate_with_groq, rubrics, name, text)
            report_path = None
            if REPORTLAB_OK:
                report_path = await loop.run_in_executor(
                    ai_executor, generate_pdf, result, name, rubrics, plag_note or None)
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
                "report_path": report_path,
            })
        except Exception as e:
            job["results"].append({
                "file_id": fid, "name": name,
                "status": "error", "error": str(e)})
            job["errors"].append({"file": name, "error": str(e)})
        job["completed"] += 1

    job["status"] = "done"