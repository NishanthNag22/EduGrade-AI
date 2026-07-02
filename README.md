# 🎓 EduGrade AI — Intelligent Academic Assessment Engine

> AI-powered batch grading platform for educators. Upload 100+ student submissions, get detailed feedback, plagiarism detection, and PDF reports — all in one click.

## 🔗 Live Demo
**[edugrade-ai.netlify.app](https://edugrade-ai.netlify.app)**

> ⚠️ Backend hosted on Render free tier — first request may take ~30 seconds (cold start). The app is fully functional after wakeup.

---

## 🌟 Key Features

| Feature | Description |
|---|---|
| 🧠 **LLaMA 3.3 70B Grading** | High-fidelity academic evaluation using Groq's ultra-fast inference |
| 📂 **Batch Processing** | Grade 100+ submissions simultaneously via async background tasks |
| 🔍 **Plagiarism Detection** | Cosine similarity analysis across all submission pairs |
| 📄 **Smart Rubric Parsing** | Upload a rubric file — AI extracts and structures criteria automatically |
| 📊 **PDF & TXT Reports** | Formal PDF reports + lightweight TXT feedback per student |
| ⚡ **Real-time Progress** | Live polling shows evaluation progress as submissions are graded |

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| **Backend** | FastAPI, Uvicorn, Python 3.10+ |
| **AI Engine** | Groq SDK — LLaMA 3.3 70B Versatile |
| **Document Parsing** | pdfplumber, python-docx |
| **PDF Generation** | ReportLab |
| **Plagiarism** | Cosine Similarity (custom implementation) |
| **Frontend** | Vanilla JS, CSS3 |
| **Deployment** | Render (backend) + Netlify (frontend) |

---

## 🏗️ Architecture

```
User Browser (Netlify)
        │
        │ HTTP/REST
        ▼
FastAPI Backend (Render)
        │
        ├── pdfplumber / python-docx  →  Text extraction
        ├── Cosine Similarity          →  Plagiarism detection
        ├── Groq API (LLaMA 3.3 70B)  →  AI evaluation
        └── ReportLab                  →  PDF report generation
```

---

## 📂 Project Structure

```
EduGrade-AI/
├── .env                  # Secrets (excluded from Git) — GROQ_API_KEY lives here
├── backend/
│   ├── main.py            # FastAPI routes, AI integration, PDF generation
│   └── requirements.txt   # Python dependencies
│
├── frontend/
│   ├── index.html         # Main UI
│   ├── app.js              # API calls, state management
│   └── style.css           # Styling
│
├── .gitignore
└── README.md
```

> **Note:** `.env` lives in the **project root**, one level above `backend/` — not inside `backend/` itself. `main.py` loads it explicitly from there:
> ```python
> load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")
> ```
> This keeps the key discoverable regardless of the working directory the server is launched from (important for Docker/Render deployments).

---

## 🚀 Local Setup

### Prerequisites
- Python 3.10+
- A [Groq API Key](https://console.groq.com) (free)

### 1. Clone the Repository

```bash
git clone https://github.com/NishanthNag22/EduGrade-AI.git
cd EduGrade-AI
```

### 2. Environment Variables

Create a `.env` file **here, in the project root** (`EduGrade-AI/.env`):

```env
GROQ_API_KEY=your_groq_api_key_here
```

### 3. Install Backend Dependencies

```bash
cd backend
pip install -r requirements.txt
```

### 4. Run Backend

```bash
uvicorn main:app --reload --port 8000
```

The backend will read `.env` from the project root automatically.

### 5. Open Frontend

Open `frontend/index.html` in your browser, or serve it locally with any static server. The frontend auto-detects whether it's running on `localhost` and points to the local backend (`http://localhost:8000`) accordingly — no manual config needed for local dev.

---

## 🔒 Security

- API keys stored in `.env` at the project root — never committed to Git
- Student submissions processed in temporary directories — not stored permanently
- `.gitignore` excludes all secrets and upload folders
- CORS restricted to an explicit allow-list (`ALLOWED_ORIGINS` env var) rather than `"*"`
- Per-IP rate limiting on upload/evaluation endpoints
- Uploaded filenames sanitized to prevent path traversal

---

## 📄 License

MIT License — see `LICENSE` for details.