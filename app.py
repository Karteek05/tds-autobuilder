import os
import json
import base64
import requests
import tempfile
import pathlib
import subprocess
from fastapi import FastAPI, Request, HTTPException

# ===========================
# CONFIGURATION
# ===========================
EXPECTED_SECRET = "karteek123"  # must match the one you gave in the form
GITHUB_USER = "Karteek05"       # your GitHub username
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")  # your token

# ===========================
app = FastAPI(title="TDS Project 1 Auto-Builder")

@app.get("/")
async def root():
    return {"message": "Auto Builder API is running."}


@app.post("/api-endpoint")
async def receive_task(request: Request):
    """
    Handles instructor requests:
    1. Verifies secret
    2. Creates a GitHub repo
    3. Generates a simple HTML app
    4. Pushes it to GitHub and enables Pages
    5. Posts back to evaluation_url
    """
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON format.")

    # 1️⃣ Verify secret
    if data.get("secret") != EXPECTED_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret.")

    # Extract required fields
    email = data.get("email", "unknown")
    task = data.get("task", "demo-task")
    nonce = data.get("nonce", "none")
    evaluation_url = data.get("evaluation_url")
    round_no = data.get("round", 1)

    # 2️⃣ Create temp folder for repo
    temp_dir = tempfile.mkdtemp()
    repo_name = f"{task}-auto"
    repo_path = pathlib.Path(temp_dir)

    # 3️⃣ Generate a simple HTML app (based on 'brief')
    brief = data.get("brief", "Simple auto-generated app")
    index_html = f"""<!DOCTYPE html>
<html>
<head>
<title>{task}</title>
<meta charset="utf-8" />
<style>body {{ font-family: sans-serif; margin: 2rem; }}</style>
</head>
<body>
<h1>Auto-generated App: {task}</h1>
<p><b>Brief:</b> {brief}</p>
<p>This is an automatically deployed page for <b>{email}</b>.</p>
<p>Round: {round_no}</p>
</body>
</html>"""
    (repo_path / "index.html").write_text(index_html, encoding="utf-8")

    # Add README.md and LICENSE
    (repo_path / "README.md").write_text(f"# {task}\n\n{brief}\n", encoding="utf-8")
    (repo_path / "LICENSE").write_text("MIT License\n\nAuto-generated project.", encoding="utf-8")

    # 4️⃣ Create repo via GitHub API
    repo_api = "https://api.github.com/user/repos"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    payload = {"name": repo_name, "private": False, "auto_init": False}

    r = requests.post(repo_api, headers=headers, json=payload)
    if r.status_code not in [201, 422]:
        raise HTTPException(status_code=500, detail=f"GitHub repo creation failed: {r.text}")

    repo_url = f"https://github.com/{GITHUB_USER}/{repo_name}.git"

    # 5️⃣ Initialize and push with git
    def run_git(cmd):
        subprocess.run(cmd, cwd=repo_path, check=True, shell=True)

    run_git("git init -b main")
    run_git("git add .")
    run_git('git commit -m "Initial auto-generated commit"')
    run_git(f'git remote add origin https://{GITHUB_USER}:{GITHUB_TOKEN}@github.com/{GITHUB_USER}/{repo_name}.git')
    run_git("git push -u origin main")

    # 6️⃣ Enable GitHub Pages
    pages_api = f"https://api.github.com/repos/{GITHUB_USER}/{repo_name}/pages"
    pages_payload = {"source": {"branch": "main", "path": "/"}}
    requests.put(pages_api, headers=headers, json=pages_payload)

    pages_url = f"https://{GITHUB_USER}.github.io/{repo_name}/"

    # 7️⃣ POST back to evaluation_url (if provided)
    if evaluation_url:
        payload = {
            "email": email,
            "task": task,
            "round": round_no,
            "nonce": nonce,
            "repo_url": f"https://github.com/{GITHUB_USER}/{repo_name}",
            "commit_sha": "auto",
            "pages_url": pages_url,
        }
        try:
            res = requests.post(evaluation_url, json=payload)
            print("Evaluation server responded:", res.status_code)
        except Exception as e:
            print("Failed to contact evaluation_url:", e)

    # 8️⃣ Final response to instructor
    return {
        "status": "ok",
        "task": task,
        "repo_url": f"https://github.com/{GITHUB_USER}/{repo_name}",
        "pages_url": pages_url,
    }
