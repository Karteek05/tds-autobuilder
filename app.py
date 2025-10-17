import os
import json
import requests
import tempfile
import pathlib
import subprocess
from fastapi import FastAPI, Request, HTTPException

# ================================
# CONFIGURATION (Environment Variables)
# ================================
EXPECTED_SECRET = "karteek123"  # The same one you gave in the form
GITHUB_USER = os.getenv("GITHUB_USER", "Karteek05")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")  # Must be set locally
if not GITHUB_TOKEN:
    raise RuntimeError("Missing GITHUB_TOKEN environment variable")

# ================================
app = FastAPI(title="TDS Project 1 Auto-Builder")

@app.get("/")
async def root():
    """Basic check route to confirm API is live."""
    return {"message": "Auto Builder API is running."}


@app.post("/api-endpoint")
async def receive_task(request: Request):
    """
    Handles instructor POST requests.
    1. Verifies secret
    2. Creates a new GitHub repo
    3. Generates and pushes a simple web app
    4. Enables GitHub Pages
    5. Optionally posts back to evaluation_url
    """
    data = await request.json()

    # 1️⃣ Verify secret
    if data.get("secret") != EXPECTED_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    email = data.get("email", "unknown")
    task = data.get("task", "demo-task")
    brief = data.get("brief", "No brief provided.")
    evaluation_url = data.get("evaluation_url")
    nonce = data.get("nonce", "none")
    round_no = data.get("round", 1)

    print(f"✅ Received task: {task} | Round: {round_no}")

    # 2️⃣ Create local temp directory for new repo
    temp_dir = tempfile.mkdtemp()
    repo_path = pathlib.Path(temp_dir)
    repo_name = f"{task}-auto"

    # 3️⃣ Generate index.html file
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
<p>Email: {email}</p>
<p>Round: {round_no}</p>
</body>
</html>"""
    (repo_path / "index.html").write_text(index_html, encoding="utf-8")
    (repo_path / "README.md").write_text(f"# {task}\n\n{brief}\n", encoding="utf-8")
    (repo_path / "LICENSE").write_text("MIT License\n\nAuto-generated project.", encoding="utf-8")

    # 4️⃣ Create GitHub repo
    repo_api = "https://api.github.com/user/repos"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    payload = {"name": repo_name, "private": False, "auto_init": False}
    response = requests.post(repo_api, headers=headers, json=payload)

    if response.status_code not in [201, 422]:
        raise HTTPException(status_code=500, detail=f"GitHub repo creation failed: {response.text}")

    print(f"📁 Repo created: {repo_name}")

    # 5️⃣ Initialize git and push files
    def run_git(cmd):
        subprocess.run(cmd, cwd=repo_path, check=True, shell=True)

    run_git("git init -b main")
    run_git("git add .")
    run_git('git commit -m "Initial commit from auto-builder"')
    run_git(f'git remote add origin https://{GITHUB_USER}:{GITHUB_TOKEN}@github.com/{GITHUB_USER}/{repo_name}.git')
    run_git("git push -u origin main")

    # 6️⃣ Enable GitHub Pages
    pages_api = f"https://api.github.com/repos/{GITHUB_USER}/{repo_name}/pages"
    pages_payload = {"source": {"branch": "main", "path": "/"}}
    requests.put(pages_api, headers=headers, json=pages_payload)
    pages_url = f"https://{GITHUB_USER}.github.io/{repo_name}/"

    print(f"🌍 GitHub Pages activated: {pages_url}")

    # 7️⃣ Optionally notify evaluation server
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
            requests.post(evaluation_url, json=payload)
            print("📬 Notified evaluation server successfully.")
        except Exception as e:
            print("⚠️ Failed to contact evaluation server:", e)

    return {
        "status": "ok",
        "task": task,
        "repo_url": f"https://github.com/{GITHUB_USER}/{repo_name}",
        "pages_url": pages_url,
    }