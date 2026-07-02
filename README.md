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
├── backend/
│   ├── main.py           # FastAPI routes, AI integration, PDF generation
│   ├── requirements.txt  # Python dependencies
│   └── .env              # Secrets (excluded from Git)
│
├── frontend/
│   ├── index.html        # Main UI
│   ├── app.js            # API calls, state management
│   └── style.css         # Styling
│
├── .gitignore
└── README.md
```

---

## 🚀 Local Setup

### Prerequisites
- Python 3.10+
- A [Groq API Key](https://console.groq.com) (free)

### 1. Clone & Install

```bash
git clone https://github.com/NishanthNag22/EduGrade-AI.git
cd EduGrade-AI/backend
pip install -r requirements.txt
```

### 2. Environment Variables

Create a `.env` file inside `backend/`:

```env
GROQ_API_KEY=your_groq_api_key_here
```

### 3. Run Backend

```bash
uvicorn main:app --reload --port 8000
```

### 4. Open Frontend

Open `frontend/index.html` in your browser.

---

## 🔒 Security

- API keys stored in `.env` — never committed to Git
- Student submissions processed in temporary directories — not stored permanently
- `.gitignore` excludes all secrets and upload folders

---

## 📄 License

MIT License — see `LICENSE` for details.
