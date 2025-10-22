# TDS Project 1 — Automated App Builder API

This repository contains my submission for **TDS Project 1 (September 2025)**.  
It implements a simple **FastAPI** backend that receives JSON task requests,
verifies a secret, and responds with confirmation.

---

## 🚀 API Endpoint
**URL:** `https://tds-autobuilder-1.onrender.com/api-endpoint`  
**Method:** `POST`  
**Content-Type:** `application/json`

### Example Request
```json
{ "secret": "karteek123", "hello": "karteek" }

Example Response
{
  "status": "ok",
  "message": "API is working!",
  "data": { "hello": "karteek" }
}


Secret

The secret for instructor requests is:

karteek123
⚙️ Run Locally
pip install fastapi uvicorn
uvicorn app:app --host 0.0.0.0 --port 8000


Expose publicly with ngrok:

ngrok http 8000

👨‍💻 Developer

Cherukupalli Sai Sriram Karteek
Email – 23f2000484@ds.study.iitm.ac.in

GitHub – Karteek05

### Demo
Repo: [demo-task-auto](https://github.com/Karteek05/demo-task-auto)  
Live: [https://Karteek05.github.io/demo-task-auto/](https://Karteek05.github.io/demo-task-auto/)
