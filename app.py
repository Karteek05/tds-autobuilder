import os
import uuid
import json
import time
import shutil
import tempfile
import pathlib
import subprocess
from typing import List, Tuple, Dict

import requests
from fastapi import FastAPI, Request, HTTPException

# ================================
# CONFIG (env; NEVER hardcode)
# ================================
EXPECTED_SECRET = os.getenv("EXPECTED_SECRET", "karteek123")
GITHUB_USER = os.getenv("GITHUB_USER", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

# OpenRouter model
OR_MODEL = "openai/gpt-oss-20b:free"
OR_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

if not GITHUB_USER or not GITHUB_TOKEN:
    raise RuntimeError("Please set GITHUB_USER and GITHUB_TOKEN environment variables.")
if not OPENROUTER_API_KEY:
    raise RuntimeError("Please set OPENROUTER_API_KEY environment variable.")

# ================================
# FASTAPI APP
# ================================
app = FastAPI(title="TDS Project 1 Auto-Builder (OpenRouter | Multi-file | Round 1+2)")

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/")
def root():
    return {"message": "Auto Builder API (OpenRouter multi-file, Round 1+2) is running."}

# ================================
# Helpers
# ================================
def run_git(cmd: str, cwd: pathlib.Path):
    subprocess.run(cmd, cwd=str(cwd), check=True, shell=True)

def or_chat(system: str, user: str, max_tokens: int = 2200, temperature: float = 0.2) -> str:
    """
    Call OpenRouter's Chat Completions API and return message content (string).
    """
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        # Optional but recommended:
        "HTTP-Referer": "https://github.com/{user}".format(user=GITHUB_USER),
        "X-Title": "TDS Project Auto-Builder",
    }
    payload = {
        "model": OR_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user}
        ],
        "max_tokens": max_tokens,
        "temperature": temperature
    }
    r = requests.post(OR_ENDPOINT, headers=headers, json=payload, timeout=120)
    if r.status_code in (429, 503):  # brief retry for rate/cold start
        time.sleep(5)
        r = requests.post(OR_ENDPOINT, headers=headers, json=payload, timeout=120)
    if r.status_code >= 300:
        raise RuntimeError(f"OpenRouter error {r.status_code}: {r.text}")
    data = r.json()
    try:
        return data["choices"][0]["message"]["content"]
    except Exception:
        return json.dumps(data)

def _strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        # remove ```json / ```html fences
        first_newline = s.find("\n")
        if first_newline != -1:
            s = s[first_newline+1:]
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()

def generate_assets(brief: str, checks: List[str]) -> Dict[str, str]:
    """
    Ask the LLM to produce multi-file app: index.html + script.js + styles.css + README.md.
    We require STRICT JSON and we validate/patch if needed.
    """
    checks_text = "\n".join(f"- {c}" for c in checks) if checks else "- Must run on GitHub Pages\n- Include required IDs/selectors\n- No runtime errors"

    system = (
        "You are a senior frontend engineer. "
        "Generate a minimal multi-file static web app (HTML + JS + CSS) for GitHub Pages. "
        "No servers, no bundlers, CDNs allowed. Return STRICT JSON only."
    )
    user = f"""
BRIEF:
{brief}

CHECKS (must pass):
{checks_text}

CONSTRAINTS:
- Files: index.html, script.js, styles.css, README.md
- index.html MUST:
  - be a valid HTML5 document with <!doctype html>
  - link styles.css
  - load script.js at the end of <body>
- Use CDN (jsdelivr/unpkg/cdnjs) if you include external libs (e.g., marked.js, highlight.js, bootstrap)
- Ensure IDs/selectors referenced in checks exist
- Avoid console errors; use try/catch where needed

OUTPUT FORMAT:
Return ONLY valid JSON (no prose, no Markdown). Shape:
{{
  "files": {{
    "index.html": "<full html>",
    "script.js": "<js code>",
    "styles.css": "<css>"
  }},
  "readme": "<markdown content for README.md>"
}}
"""
    content = or_chat(system, user, max_tokens=3000, temperature=0.15)
    content = _strip_fences(content)

    # Parse JSON robustly
    try:
        obj = json.loads(content)
        files = obj.get("files", {})
        readme = obj.get("readme", "")
    except Exception:
        # Model failed to follow instructions; create a basic scaffold
        files = {}
        readme = f"# Generated App\n\nThis repository contains a generated app for:\n\n{brief}\n\n## License\nMIT\n"

    # Ensure mandatory files exist
    index_html = files.get("index.html", "").strip()
    script_js = files.get("script.js", "").strip()
    styles_css = files.get("styles.css", "").strip()

    if not index_html.lower().startswith("<!doctype html"):
        # Patch a minimal HTML that includes the other files
        index_html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Generated App</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <link rel="stylesheet" href="styles.css" />
</head>
<body>
  <main class="container">
    <h1>Generated App</h1>
    <p>Brief:</p>
    <pre>{brief}</pre>
  </main>
  <script src="script.js"></script>
</body>
</html>"""

    if not styles_css:
        styles_css = "body{font-family:system-ui,Arial,sans-serif;margin:2rem} .container{max-width:900px;margin:0 auto}"

    if not script_js:
        script_js = "console.log('Generated app loaded.');"

    if not readme.strip().startswith("#"):
        readme = f"# Generated App\n\n{readme}\n\n## License\nMIT\n"

    return {
        "index.html": index_html,
        "script.js": script_js,
        "styles.css": styles_css,
        "README.md": readme
    }

def github_create_repo(repo_name: str) -> None:
    r = requests.post(
        "https://api.github.com/user/repos",
        headers={"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"},
        json={"name": repo_name, "private": False, "auto_init": False},
        timeout=30,
    )
    if r.status_code not in (201, 422):  # 422 = already exists
        raise HTTPException(status_code=500, detail=f"GitHub repo creation failed: {r.status_code} {r.text}")

def github_enable_pages(repo_name: str) -> None:
    pages_api = f"https://api.github.com/repos/{GITHUB_USER}/{repo_name}/pages"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    r = requests.put(pages_api, headers=headers, json={"source": {"branch": "main", "path": "/"}}, timeout=30)
    if r.status_code >= 400:
        time.sleep(2)
        r2 = requests.post(pages_api, headers=headers, json={"source": {"branch": "main", "path": "/"}}, timeout=30)
        if r2.status_code >= 400:
            raise HTTPException(status_code=500, detail=f"Enable Pages failed: {r2.status_code} {r2.text}")

def repo_urls(repo_name: str) -> Tuple[str, str]:
    return (
        f"https://github.com/{GITHUB_USER}/{repo_name}",
        f"https://{GITHUB_USER}.github.io/{repo_name}/",
    )

def write_files_to_dir(target: pathlib.Path, files: Dict[str, str]):
    for name, content in files.items():
        (target / name).write_text(content, encoding="utf-8")
    # license minimal
    (target / "LICENSE").write_text("MIT License\n", encoding="utf-8")

def create_new_repo_and_push(repo_name: str, files: Dict[str, str]) -> Tuple[str, str]:
    temp_dir = pathlib.Path(tempfile.mkdtemp())
    try:
        write_files_to_dir(temp_dir, files)

        github_create_repo(repo_name)

        run_git("git init -b main", temp_dir)
        run_git('git config user.name "Auto Builder Bot"', temp_dir)
        run_git('git config user.email "bot@example.com"', temp_dir)
        run_git("git add .", temp_dir)
        run_git('git commit -m "Initial commit from auto-builder (OpenRouter multi-file)"', temp_dir)
        run_git(f'git remote add origin https://{GITHUB_USER}:{GITHUB_TOKEN}@github.com/{GITHUB_USER}/{repo_name}.git', temp_dir)
        try:
            run_git("git push -u origin main", temp_dir)
        except subprocess.CalledProcessError:
            run_git("git push -u origin main --force", temp_dir)

        github_enable_pages(repo_name)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    return repo_urls(repo_name)

def update_existing_repo(repo_name: str, files: Dict[str, str]) -> Tuple[str, str]:
    temp_dir = pathlib.Path(tempfile.mkdtemp())
    try:
    # clone
        run_git(f'git clone https://{GITHUB_USER}:{GITHUB_TOKEN}@github.com/{GITHUB_USER}/{repo_name}.git .', temp_dir)

        write_files_to_dir(temp_dir, files)

        run_git('git config user.name "Auto Builder Bot"', temp_dir)
        run_git('git config user.email "bot@example.com"', temp_dir)
        run_git("git add .", temp_dir)
        # allow empty commit to trigger Pages
        run_git('git commit -m "Round 2 update from auto-builder (OpenRouter multi-file)" || echo done', temp_dir)
        run_git("git push", temp_dir)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    return repo_urls(repo_name)

def post_evaluation(evaluation_url: str, payload: dict):
    for delay in [1, 2, 4]:
        try:
            r = requests.post(evaluation_url, json=payload, timeout=15)
            if r.status_code < 300:
                return
        except Exception:
            pass
        time.sleep(delay)

# ================================
# MAIN ENDPOINT
# ================================
@app.post("/api-endpoint")
async def receive_task(request: Request):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON.")

    if data.get("secret") != EXPECTED_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    email = data.get("email", "unknown")
    task = data.get("task", f"task-{uuid.uuid4().hex[:5]}")
    round_no = int(data.get("round", 1))
    nonce = data.get("nonce", "")
    brief = data.get("brief", "Create a minimal static app.")
    checks = data.get("checks", [])
    evaluation_url = data.get("evaluation_url")

    print(f"✅ Received task={task}, round={round_no}, brief='{brief}'")

    # LLM: generate multi-file assets
    try:
        files = generate_assets(brief, checks)
        repo_name = f"{task}-auto"

        if round_no == 1:
            gh_url, pages_url = create_new_repo_and_push(repo_name, files)
        else:
            try:
                gh_url, pages_url = update_existing_repo(repo_name, files)
            except Exception:
                repo_name = f"{task}-auto-r{round_no}-{uuid.uuid4().hex[:4]}"
                gh_url, pages_url = create_new_repo_and_push(repo_name, files)

    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"Git error: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unhandled error occurred: {e}")

    if evaluation_url:
        payload = {
            "email": email,
            "task": task,
            "round": round_no,
            "nonce": nonce,
            "repo_url": gh_url,
            "commit_sha": "auto",
            "pages_url": pages_url,
        }
        try:
            post_evaluation(evaluation_url, payload)
        except Exception as e:
            print("⚠️ evaluation_url callback failed:", e)

    print(f"✅ Success task={task}. Pages: {pages_url}")
    return {
        "status": "ok",
        "task": task,
        "round": round_no,
        "repo_url": gh_url,
        "pages_url": pages_url,
    }
