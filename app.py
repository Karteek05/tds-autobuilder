import os
import re
import json
import time
import base64
import shutil
import uuid
import pathlib
import tempfile
import subprocess
from typing import Dict, Any, List, Tuple, Optional

import requests
from fastapi import FastAPI, Request, HTTPException

# ============== CONFIG ==============
EXPECTED_SECRET = os.getenv("EXPECTED_SECRET", "change-me")
GITHUB_USER = os.getenv("GITHUB_USER", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
AIPIPE_TOKEN = os.getenv("AIPIPE_TOKEN", "")

# ✅ Required checks
if not GITHUB_USER or not GITHUB_TOKEN:
    raise RuntimeError("Set GITHUB_USER and GITHUB_TOKEN environment variables.")
if not AIPIPE_TOKEN:
    raise RuntimeError("Set AIPIPE_TOKEN environment variable (provided by IITM AIPipe).")

AIPIPE_API_URL = "https://aipipe.org/openrouter/v1/chat/completions"
AIPIPE_MODEL = "openai/gpt-4o-mini"  # Recommended IITM-approved model

# ============== APP INIT ==============
app = FastAPI(title="TDS Auto-Builder with AIPipe (Round 1 & 2, Multi-file)")

@app.get("/")
def root():
    return {"message": "Auto Builder API (AIPipe, Round 1+2) is running."}

@app.get("/health")
def health():
    return {"status": "ok"}


# ============== UTILS ==============
def sh(cmd: str, cwd: Optional[pathlib.Path] = None, allow_fail: bool = False) -> str:
    """Run shell command safely."""
    res = subprocess.run(
        cmd, cwd=str(cwd) if cwd else None,
        shell=True, capture_output=True, text=True
    )
    if res.returncode != 0 and not allow_fail:
        raise RuntimeError(f"Cmd failed: {cmd}\n{res.stdout}\n{res.stderr}")
    return res.stdout.strip()


def github_api(method: str, url: str, json_body: Optional[dict] = None) -> requests.Response:
    """Call GitHub API with auth."""
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    r = requests.request(method, url, headers=headers, json=json_body, timeout=45)
    return r


def write_file(p: pathlib.Path, content: bytes):
    """Write file to repository path."""
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as f:
        f.write(content)


def parse_data_uri_to_bytes(data_uri: str) -> bytes:
    """Parse base64 data URI."""
    m = re.match(r"^data:.*?;base64,(.+)$", data_uri, re.DOTALL)
    if not m:
        raise ValueError("Invalid data URI")
    return base64.b64decode(m.group(1))


# ============== AIPIPE LLM CALL ==============
def call_aipipe(system_prompt: str, user_prompt: str, max_tokens: int = 2000) -> str:
    """
    Calls AIPipe LLM endpoint using GPT-4o-mini with enforced JSON output.
    """
    headers = {
        "Authorization": f"Bearer {AIPIPE_TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": AIPIPE_MODEL,
        "response_format": {"type": "json_object"},  # ✅ Force valid JSON
        "messages": [
            {
                "role": "system",
                "content": (
                    system_prompt
                    + "\nYou MUST reply with ONLY valid JSON matching the required schema."
                    + " Do not include explanations, markdown, or code fences."
                )
            },
            {"role": "user", "content": user_prompt}
        ],
        "max_tokens": max_tokens,
        "temperature": 0.1,
        "top_p": 0.9
    }

    try:
        r = requests.post(AIPIPE_API_URL, headers=headers, json=payload, timeout=120)
        if r.status_code >= 300:
            raise RuntimeError(f"AIPipe error {r.status_code}: {r.text}")

        data = r.json()
        raw_response = data["choices"][0]["message"]["content"]

        # ✅ Extra safety: strip any markdown or garbage
        if raw_response.strip().startswith("```"):
            raw_response = raw_response.strip().strip("`").replace("json", "", 1).strip()

        return raw_response
    except Exception as e:
        raise RuntimeError(f"AIPipe LLM call failed: {e}")

# ============== LICENSE TEXT ==============
MIT_LICENSE_TEXT = """MIT License

Copyright (c) {}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
""".format(time.strftime("%Y"))

# ============== ROUND PROMPTS ==============
SYSTEM_PLAN = """You are an expert code generator that outputs ONLY valid strict JSON. No explanations. No markdown fences."""

USER_PLAN_TEMPLATE = """
You are generating a multi-file static website for Round {round_no}.
BRIEF:
{brief}

Return a JSON object with this shape:
{{
  "files": [
    {{ "name": "index.html", "content": "<!doctype html> ... full HTML ..." }},
    {{ "name": "README.md", "content": "# ..." }},
    {{ "name": "LICENSE", "content": "MIT License ... " }}
  ]
}}

📌 STRICT RULES:
- Only valid JSON. No comments. No extra keys.
- Each file entry MUST have "name" and "content".
- Include complete working HTML pages.
- Use relative links.
- Always include README.md and LICENSE.
- Index must link to all other pages.
- Use static HTML/CSS only. No JS frameworks unless explicitly stated.

CHECKS to improve:
{checks_hint}
"""

ROUND2_IMPROVEMENTS = """
ROUND 2 TASK:
Regenerate a fully improved version of the entire static website.

⚠ STRICT REQUIREMENTS:
- OUTPUT MUST BE VALID JSON ONLY.
- DO NOT include any explanations, markdown fences, comments, or additional keys.
- Return an object with exactly one key: "files", which is a list.

Example of valid structure:
{
  "files": [
    { "name": "index.html", "content": "<!doctype html>...full html..." },
    { "name": "README.md", "content": "# Project Documentation" },
    { "name": "LICENSE", "content": "MIT License ... full text ..." }
  ]
}

Required improvements:
- Add accessibility tags (alt, aria, labels)
- Add consistent navigation across pages
- Add footer with contact details
- Use clean responsive layout
"""


# ============== MANIFEST GENERATION ==============
def make_index_from_manifest(manifest: Dict[str, str]) -> str:
    links = []
    for name in sorted(manifest.keys()):
        if name.lower() == "index.html":
            continue
        links.append(f'<li><a href="{name}" target="_blank">{name}</a></li>')
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Generated App</title>
</head>
<body>
<h1>Generated App</h1>
<p>This site was generated automatically.</p>
<ul>
{''.join(links)}
</ul>
</body>
</html>
"""

def build_manifest_via_llm(brief: str, round_no: int, checks: List[str]) -> Dict[str, str]:
    checks_hint = ", ".join(checks) if checks else "none"
    user_prompt = USER_PLAN_TEMPLATE.format(brief=brief, round_no=round_no, checks_hint=checks_hint)

    if round_no >= 2:
        user_prompt += "\n" + ROUND2_IMPROVEMENTS

    raw = call_aipipe(SYSTEM_PLAN, user_prompt)

    try:
        data = json.loads(raw)
        assert isinstance(data, dict) and "files" in data
    except Exception:
        # fallback
        return {
            "index.html": "<!doctype html><html><body><h1>Generated App</h1><p>LLM failed.</p></body></html>",
            "LICENSE": MIT_LICENSE_TEXT,
            "README.md": "# Generated App\n\nFallback generated site due to invalid LLM output."
        }

    manifest: Dict[str, str] = {}
    for file in data.get("files", []):
        name = file.get("name", "").strip()
        content = file.get("content", "")
        if name:
            manifest[name] = content

    # Ensure LICENSE
    if "LICENSE" not in (k.upper() for k in manifest.keys()):
        manifest["LICENSE"] = MIT_LICENSE_TEXT

    # Ensure README
    if not any(k.lower() == "readme.md" for k in manifest.keys()):
        manifest["README.md"] = "# Generated App\n\nThis was automatically generated."

    # Ensure index with links
    manifest["index.html"] = make_index_from_manifest(manifest)
    return manifest

# ============== GITHUB FUNCTIONS ==============
def repo_urls(repo_name: str) -> Tuple[str, str]:
    return (
        f"https://github.com/{GITHUB_USER}/{repo_name}",
        f"https://{GITHUB_USER}.github.io/{repo_name}/"
    )

def create_repo_if_needed(repo_name: str):
    r = github_api("POST", "https://api.github.com/user/repos",
                   {"name": repo_name, "private": False})
    if r.status_code not in (201, 422):
        raise HTTPException(status_code=500, detail=f"GitHub repo creation failed: {r.text}")

def enable_github_pages(repo_name: str):
    pages_api = f"https://api.github.com/repos/{GITHUB_USER}/{repo_name}/pages"
    github_api("PUT", pages_api, {"source": {"branch": "main", "path": "/"}})
    time.sleep(2)

def initial_push(repo_name: str, manifest: Dict[str, str], attachments: List[dict]) -> Tuple[str, str, str]:
    temp = pathlib.Path(tempfile.mkdtemp())
    try:
        for name, content in manifest.items():
            write_file(temp / name, content.encode())

        for att in attachments or []:
            try:
                name = att.get("name", "")
                data = parse_data_uri_to_bytes(att.get("url", ""))
                write_file(temp / name, data)
            except:
                pass

        create_repo_if_needed(repo_name)
        sh("git init -b main", temp)
        sh('git config user.name "Auto Builder"', temp)
        sh('git config user.email "bot@example.com"', temp)
        sh("git add .", temp)
        sh('git commit -m "Initial commit"', temp)
        sh(f'git remote add origin https://{GITHUB_USER}:{GITHUB_TOKEN}@github.com/{GITHUB_USER}/{repo_name}.git', temp)
        sh("git push -u origin main --force", temp)
        commit_sha = sh("git rev-parse HEAD", temp)

        enable_github_pages(repo_name)
        gh, pages = repo_urls(repo_name)
        return gh, pages, commit_sha
    finally:
        shutil.rmtree(temp, ignore_errors=True)

def update_push(repo_name: str, manifest: Dict[str, str], attachments: List[dict], full_regeneration: bool = True) -> Tuple[str, str, str]:
    temp = pathlib.Path(tempfile.mkdtemp())
    try:
        sh(f'git clone https://{GITHUB_USER}:{GITHUB_TOKEN}@github.com/{GITHUB_USER}/{repo_name}.git .', temp)

        if full_regeneration:
            for item in temp.iterdir():
                if item.name != ".git":
                    if item.is_file():
                        item.unlink()
                    else:
                        shutil.rmtree(item)

        for name, content in manifest.items():
            write_file(temp / name, content.encode())

        for att in attachments or []:
            try:
                name = att.get("name", "")
                data = parse_data_uri_to_bytes(att.get("url", ""))
                write_file(temp / name, data)
            except:
                pass

        sh("git add .", temp)
        status = sh("git status --porcelain", temp)
        if status:
            sh('git commit -m "Round update"', temp)
            sh("git push", temp)

        commit_sha = sh("git rev-parse HEAD", temp)
        gh, pages = repo_urls(repo_name)
        return gh, pages, commit_sha
    finally:
        shutil.rmtree(temp, ignore_errors=True)
# ============== EVALUATION CALLBACK ==============
def post_evaluation_with_retries(evaluation_url: str, payload: dict):
    """Notify evaluator with retries (round completion callback)."""
    for delay in [1, 2, 4, 8]:
        try:
            r = requests.post(evaluation_url, json=payload, timeout=15)
            if r.status_code < 300:
                return
        except Exception:
            pass
        time.sleep(delay)

# ============== MAIN API ENDPOINT ==============
@app.post("/api-endpoint")
async def api_endpoint(req: Request):
    """Main entry point for IITM evaluator system."""
    try:
        data = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON input")

    # ✅ Validate secret
    if data.get("secret") != EXPECTED_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    email = data.get("email", "unknown")
    task = data.get("task", f"task-{uuid.uuid4().hex[:6]}")
    round_no = int(data.get("round", 1))
    nonce = data.get("nonce", "")
    brief = data.get("brief", "Generate a static site.")
    checks = data.get("checks", [])
    evaluation_url = data.get("evaluation_url", "")
    attachments = data.get("attachments", [])

    print(f"📩 Received request | task={task}, round={round_no}, email={email}")

    # ✅ Generate file manifest using AIPipe LLM
    try:
        manifest = build_manifest_via_llm(brief, round_no, checks)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM error: {e}")

    # ✅ Determine GitHub repo name
    repo_name = f"{task}-auto"

    try:
        if round_no == 1:
            repo_url, pages_url, commit_sha = initial_push(repo_name, manifest, attachments)
        else:
            try:
                repo_url, pages_url, commit_sha = update_push(
                    repo_name, manifest, attachments, full_regeneration=True
                )
            except Exception:
                # If repo doesn't exist from Round 1, create a new unique one
                repo_name = f"{task}-auto-r{round_no}-{uuid.uuid4().hex[:4]}"
                repo_url, pages_url, commit_sha = initial_push(repo_name, manifest, attachments)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"GitHub deployment failed: {e}")

    # ✅ Optional evaluator callback
    if evaluation_url:
        callback_payload = {
            "email": email,
            "task": task,
            "round": round_no,
            "nonce": nonce,
            "repo_url": repo_url,
            "commit_sha": commit_sha,
            "pages_url": pages_url,
        }
        post_evaluation_with_retries(evaluation_url, callback_payload)

    return {
        "status": "ok",
        "task": task,
        "round": round_no,
        "repo_url": repo_url,
        "pages_url": pages_url,
    }

# ============== END OF FILE ==============