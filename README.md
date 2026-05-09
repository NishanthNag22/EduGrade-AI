# 🎓 EduGrade AI: Intelligent Academic Assessment Engine

EduGrade AI is a professional-grade, batch-processing evaluation platform designed for modern educators. By leveraging LLaMA 3.3 (70B) via Groq, it automates the grading of student assignments (PDF, DOCX, TXT) against custom rubrics, identifies potential plagiarism, and generates professional feedback reports.

---

# 🌟 Key Features

## 🧠 LLaMA 3.3 Power
High-fidelity academic grading using the `llama-3.3-70b-versatile` model for nuanced reasoning.

## 📂 Bulk Evaluation
Process multiple student submissions simultaneously with a single click.

## 🔍 Plagiarism Guard
Built-in similarity detection using Cosine Similarity to identify overlaps between student work.

## 📄 Smart Rubric Parsing
Automatically extracts and structures grading criteria from raw text or rubric files.

## 📊 Comprehensive Reporting

- **PDF Reports:** Formal, structured evaluation summaries for official records.
- **TXT Feedback:** Lightweight, quick-view summaries for rapid student distribution.

## ⚡ High Performance
Asynchronous background tasks ensure the system remains responsive even during heavy AI processing.

---

# 🛠️ Technology Stack

| Layer | Technology |
|------|-------------|
| Backend | FastAPI, Uvicorn, Python 3.10+ |
| AI Engine | Groq SDK (Llama 3.3 70B) |
| Document Parsing | pdfplumber, python-docx |
| PDF Generation | ReportLab |
| Frontend | Modern Vanilla JS, CSS3 (Glassmorphism UI) |

---

# 📋 Installation & Setup

## 1. Prerequisites

- Python 3.10 or higher
- A Groq API Key

---

## 2. Backend Setup

```bash
# Clone your repository
git clone https://github.com/YOUR_USERNAME/EduGrade-AI.git

# Navigate to backend
cd EduGrade-AI/backend

# Install dependencies
pip install -r requirements.txt
```

---

## 3. Environment Configuration

Create a `.env` file inside the `backend/` folder:

```env
GROQ_API_KEY=your_actual_groq_key_here
```

---

## 4. Running the Server

```bash
# Start the FastAPI server
python -m uvicorn main:app --reload --port 8000
```

---

## 5. Accessing the UI

Open `frontend/index.html` directly in your browser.

The frontend is pre-linked to communicate with the local FastAPI server at:

```text
http://localhost:8000
```

---

# 📂 Project Structure

```text
EduGrade-AI/
├── backend/
│   ├── main.py           # API logic, AI integration & report generation
│   ├── requirements.txt  # Python dependencies
│   └── .env              # Secrets (Hidden from Git)
│
├── frontend/
│   ├── index.html        # Dashboard UI
│   ├── app.js            # API orchestration
│   └── style.css         # Modern UI styling
│
├── .gitignore            # Git exclusion rules
└── README.md             # Professional documentation
```

---

# 🛡️ Security & Privacy

## 🔐 API Protection
The `.gitignore` file ensures your API keys are never pushed to a public repository.

## 🧾 Secure Handling
Student submissions are processed in temporary directories and are not stored permanently on the server.

---

# 📄 License

This project is licensed under the MIT License.  
See the `LICENSE` file for details.