import os
import re
import io
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
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-oss-20b:free")

if not GITHUB_USER or not GITHUB_TOKEN:
    raise RuntimeError("Set GITHUB_USER and GITHUB_TOKEN env vars.")
if not OPENROUTER_API_KEY:
    raise RuntimeError("Set OPENROUTER_API_KEY env var.")

# ============== APP ==============
app = FastAPI(title="TDS Auto-Builder (OpenRouter, Multi-file, R1+R2+Attachments)")

@app.get("/")
def root():
    return {"message": "Auto Builder API (OpenRouter multi-file, Round 1+2) is running."}

@app.get("/health")
def health():
    return {"status": "ok"}

# ============== UTILS ==============
def sh(cmd: str, cwd: Optional[pathlib.Path] = None, allow_fail: bool = False) -> str:
    res = subprocess.run(cmd, cwd=str(cwd) if cwd else None,
                         shell=True, capture_output=True, text=True)
    if res.returncode != 0 and not allow_fail:
        raise RuntimeError(f"Cmd failed: {cmd}\n{res.stdout}\n{res.stderr}")
    return res.stdout.strip()

def github_api(method: str, url: str, json_body: Optional[dict] = None) -> requests.Response:
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    r = requests.request(method, url, headers=headers, json=json_body, timeout=45)
    return r

def create_repo_if_needed(repo_name: str):
    r = github_api("POST", "https://api.github.com/user/repos",
                   {"name": repo_name, "private": False, "auto_init": False})
    # 201 created, 422 already exists → fine
    if r.status_code not in (201, 422):
        raise HTTPException(status_code=500, detail=f"GitHub repo creation failed: {r.status_code} {r.text}")

def enable_github_pages(repo_name: str):
    pages_api = f"https://api.github.com/repos/{GITHUB_USER}/{repo_name}/pages"
    # Try PUT modern API (some accounts accept)
    r = github_api("PUT", pages_api, {"source": {"branch": "main", "path": "/"}})
    if r.status_code >= 400:
        time.sleep(2)
        # Fallback POST (older behavior)
        r2 = github_api("POST", pages_api, {"source": {"branch": "main", "path": "/"}})
        if r2.status_code >= 400:
            # Some orgs only allow enabling after first build. We won't hard fail.
            pass

def repo_urls(repo_name: str) -> Tuple[str, str]:
    return (
        f"https://github.com/{GITHUB_USER}/{repo_name}",
        f"https://{GITHUB_USER}.github.io/{repo_name}/",
    )

def write_file(p: pathlib.Path, content: bytes):
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as f:
        f.write(content)

def parse_data_uri_to_bytes(data_uri: str) -> bytes:
    # Expects data:<mime>;base64,<payload>
    m = re.match(r"^data:.*?;base64,(.+)$", data_uri, re.DOTALL)
    if not m:
        raise ValueError("Invalid data URI")
    return base64.b64decode(m.group(1))

# ======== OPENROUTER ========
def call_openrouter(system_prompt: str, user_prompt: str, max_tokens: int = 2200) -> str:
    """
    Calls OpenRouter Chat Completions (OpenAI-compatible). Returns assistant text.
    """
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    r = requests.post(url, headers=headers, json=payload, timeout=120)
    if r.status_code == 503:
        time.sleep(6)
        r = requests.post(url, headers=headers, json=payload, timeout=120)
    if r.status_code >= 300:
        raise RuntimeError(f"OpenRouter error {r.status_code}: {r.text}")
    data = r.json()
    try:
        return data["choices"][0]["message"]["content"]
    except Exception:
        return json.dumps(data)

def extract_json_block(text: str) -> str:
    """
    Try to extract a JSON object from model output (handles ```json blocks).
    """
    # ```json ... ```
    fence = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        return fence.group(1)
    # First {...} block (balanced braces)
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                return text[start:i+1]
    return text  # hope it's raw JSON

def make_index_from_manifest(manifest: Dict[str, str]) -> str:
    links = []
    for name in sorted(manifest.keys()):
        if name.lower() == "index.html":
            continue
        links.append(f'<li><a href="{name}" target="_blank" rel="noopener">{name}</a></li>')
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Generated App</title>
<meta name="description" content="Auto-generated static site."/>
<link rel="preconnect" href="https://cdn.jsdelivr.net" />
<style>
 body{{font-family:system-ui,Segoe UI,Roboto,Arial,sans-serif;max-width:960px;margin:2rem auto;padding:0 1rem;line-height:1.6}}
 code,pre{{background:#f5f5f7;padding:.5rem;border-radius:.4rem;overflow:auto}}
 a{{text-decoration:none}} a:hover{{text-decoration:underline}}
 ul{{padding-left:1.2rem}}
</style>
</head><body>
<header>
  <h1>Generated App</h1>
  <p>This site was generated automatically. Open the files below:</p>
</header>
<main role="main">
<ul>
{''.join(links)}
</ul>
</main>
<footer><small>Round-built static site • {time.strftime("%Y-%m-%d")}</small></footer>
</body></html>"""

MIT_LICENSE_TEXT = """MIT License

Copyright (c) 2025 Auto Builder

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""

# ======== LLM PROMPTS ========
SYSTEM_PLAN = """You are a precise code generator that outputs ONLY valid JSON matching the requested schema.
You are generating a multi-file static GitHub Pages site. Do not include explanations or code fences unless asked.
Always return valid JSON with the exact 'files' array schema.
"""

# Base template used for Round 1 and as a base for Round 2.
USER_PLAN_TEMPLATE = """
Create a multi-file static site based on this BRIEF (for round {round_no}):
---
{brief}
---

You MUST return ONLY a JSON object with this exact shape:
{{
  "files": [
    {{ "name": "index.html", "content": "<!doctype html> ... a complete HTML page ..." }},
    {{ "name": "README.md", "content": "# ..." }},
    {{ "name": "LICENSE", "content": "MIT License ... (full text)"}}
  ]
}}

STRICT RULES:
- Return valid JSON only (no trailing commas, no comments).
- Include complete, working content for each file.
- Prefer relative paths and simple structure (subfolders allowed).
- "index.html" must link to every other file in the list.
- If README.md is missing, include it.
- Always include a proper MIT LICENSE file if not specified.
- Keep everything static; no build tools; use CDN if needed.

CHECKS HINTS (if provided by the caller): {checks_hint}
"""

# Extra instructions applied ONLY for Round >= 2 (we regenerate improved version).
ROUND2_IMPROVEMENTS = """
ROUND 2: REGENERATE AN IMPROVED VERSION
- Treat the files from Round 1 as a baseline and produce a fresh, improved site that addresses the evaluator checks.
- Apply improvements across the entire project (not just individual files).
- Typical improvements that may be required: accessibility (alt text, landmarks, labels), metadata (title/description/open graph), internal nav linking, consistent styles, responsiveness, and simple validation for links.
- Keep it fully static and self-contained (no bundlers).
- Return **the complete site** (not a diff).
- Do not invent external assets that you cannot provide; if referencing images, include simple SVGs or inline assets.
"""

def build_manifest_via_llm(brief: str, round_no: int, checks: List[str]) -> Dict[str, str]:
    checks_hint = ", ".join(checks) if checks else "none"
    # Build the prompt
    prompt = USER_PLAN_TEMPLATE.format(brief=brief, round_no=round_no, checks_hint=checks_hint)
    if round_no >= 2:
        prompt += "\n" + ROUND2_IMPROVEMENTS

    raw = call_openrouter(SYSTEM_PLAN, prompt, max_tokens=2200)
    json_text = extract_json_block(raw)
    try:
        data = json.loads(json_text)
        assert isinstance(data, dict) and "files" in data and isinstance(data["files"], list)
    except Exception:
        # Fallback minimal site if LLM misbehaves
        fallback = {
            "files": [
                {"name": "index.html", "content": "<!doctype html><html><head><meta charset='utf-8'><title>Generated App</title></head><body><h1>Generated App</h1><p>LLM failed to produce a valid manifest.</p></body></html>"},
                {"name": "README.md", "content": "# Generated App\n\nLLM plan failed; minimal site generated.\n"},
                {"name": "LICENSE", "content": MIT_LICENSE_TEXT},
            ]
        }
        return {f["name"]: f["content"] for f in fallback["files"]}

    manifest: Dict[str, str] = {}
    for f in data["files"]:
        name = str(f.get("name", "")).strip().lstrip("./")
        content = str(f.get("content", ""))
        if not name:
            continue
        manifest[name] = content

    # Ensure LICENSE & README exist; ensure index.html exists and links files
    # Normalize key comparison
    keys_lower = {k.lower(): k for k in manifest.keys()}

    if "license" not in keys_lower:
        manifest["LICENSE"] = MIT_LICENSE_TEXT

    if "readme.md" not in keys_lower:
        manifest["README.md"] = "# Generated App\n\nThis repository was generated automatically."

    # Always (re)create index with guaranteed links for checks
    manifest["index.html"] = make_index_from_manifest(manifest)

    # Add a round marker (useful for evaluator transparency)
    manifest["README.md"] = (manifest.get("README.md", "# Generated App") +
                             f"\n\n---\nThis site was generated for **Round {round_no}**.\n"
                             f"\nChecks considered: {checks_hint}\n")

    return manifest

# ======== GIT OPS ========
def initial_push(repo_name: str, manifest: Dict[str, str], attachments: List[dict]) -> Tuple[str, str, str]:
    """
    Create temp repo, write files, add attachments, push, enable pages.
    Returns (repo_url, pages_url, commit_sha)
    """
    temp = pathlib.Path(tempfile.mkdtemp())
    try:
        # Write LLM files
        for name, content in manifest.items():
            p = temp / name
            write_file(p, content.encode("utf-8"))

        # Write attachments (data URIs)
        for att in attachments or []:
            try:
                name = att.get("name", "").strip().lstrip("./")
                url = att.get("url", "")
                if not name or not url.startswith("data:"):
                    continue
                data = parse_data_uri_to_bytes(url)
                write_file(temp / name, data)
            except Exception:
                pass

        create_repo_if_needed(repo_name)

        sh("git init -b main", temp)
        sh('git config user.name "Auto Builder Bot"', temp)
        sh('git config user.email "bot@example.com"', temp)
        sh("git add .", temp)
        sh('git commit -m "Initial commit (auto-builder)"', temp)
        sh(f'git remote add origin https://{GITHUB_USER}:{GITHUB_TOKEN}@github.com/{GITHUB_USER}/{repo_name}.git', temp)
        # Force push to be idempotent
        sh("git push -u origin main --force", temp)
        commit_sha = sh("git rev-parse HEAD", temp).strip()

        enable_github_pages(repo_name)
        gh, pages = repo_urls(repo_name)
        return gh, pages, commit_sha
    finally:
        shutil.rmtree(temp, ignore_errors=True)

def _wipe_repo_working_dir(path: pathlib.Path):
    """
    Remove all files/folders except the .git directory to ensure a clean full regeneration.
    """
    for item in path.iterdir():
        if item.name == ".git":
            continue
        if item.is_file() or item.is_symlink():
            item.unlink(missing_ok=True)
        else:
            shutil.rmtree(item, ignore_errors=True)

def update_push(repo_name: str, manifest: Dict[str, str], attachments: List[dict], full_regeneration: bool = False) -> Tuple[str, str, str]:
    """
    Clone existing repo, optionally wipe content, write new files, add any new attachments, commit, push.
    Returns (repo_url, pages_url, commit_sha)
    """
    temp = pathlib.Path(tempfile.mkdtemp())
    try:
        sh(f'git clone https://{GITHUB_USER}:{GITHUB_TOKEN}@github.com/{GITHUB_USER}/{repo_name}.git .', temp)

        if full_regeneration:
            _wipe_repo_working_dir(temp)

        # Overwrite/add
        for name, content in manifest.items():
            p = temp / name
            write_file(p, content.encode("utf-8"))
        # Attachments too
        for att in attachments or []:
            try:
                name = att.get("name", "").strip().lstrip("./")
                url = att.get("url", "")
                if not name or not url.startswith("data:"):
                    continue
                data = parse_data_uri_to_bytes(url)
                write_file(temp / name, data)
            except Exception:
                pass

        sh("git add .", temp)
        status = sh("git status --porcelain", temp)
        if status:
            sh('git commit -m "Round update (auto-builder)"', temp)
            sh("git push", temp)
        commit_sha = sh("git rev-parse HEAD", temp).strip()

        gh, pages = repo_urls(repo_name)
        return gh, pages, commit_sha
    finally:
        shutil.rmtree(temp, ignore_errors=True)

def post_evaluation_with_retries(evaluation_url: str, payload: dict):
    for delay in [1, 2, 4, 8]:
        try:
            r = requests.post(evaluation_url, json=payload, timeout=15)
            if r.status_code < 300:
                return
        except Exception:
            pass
        time.sleep(delay)

# ============== MAIN ENDPOINT ==============
@app.post("/api-endpoint")
async def api_endpoint(req: Request):
    try:
        data = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON.")

    # Secret
    if data.get("secret") != EXPECTED_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret.")

    email = data.get("email", "unknown")
    task = data.get("task", f"task-{uuid.uuid4().hex[:6]}")
    round_no = int(data.get("round", 1))
    nonce = data.get("nonce", "")
    brief = data.get("brief", "Create a minimal static multi-file site.")
    checks: List[str] = data.get("checks", [])
    evaluation_url = data.get("evaluation_url", "")
    attachments: List[dict] = data.get("attachments", [])

    print(f"✅ Received task={task}, round={round_no}, email={email}")

    # Build file manifest with LLM (Round 2 prompt includes improvements)
    try:
        manifest = build_manifest_via_llm(brief, round_no, checks)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM error: {e}")

    repo_name = f"{task}-auto"
    try:
        if round_no == 1:
            repo_url, pages_url, commit_sha = initial_push(repo_name, manifest, attachments)
        else:
            # Regenerate entire site with improvements and wipe old files
            try:
                repo_url, pages_url, commit_sha = update_push(
                    repo_name, manifest, attachments, full_regeneration=True
                )
            except Exception:
                # if update failed (repo missing?), create a new suffix repo
                repo_name = f"{task}-auto-r{round_no}-{uuid.uuid4().hex[:4]}"
                repo_url, pages_url, commit_sha = initial_push(repo_name, manifest, attachments)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Git/Push error: {e}")

    # Optional evaluation callback
    if evaluation_url:
        cb = {
            "email": email,
            "task": task,
            "round": round_no,
            "nonce": nonce,
            "repo_url": repo_url,
            "commit_sha": commit_sha,
            "pages_url": pages_url,
        }
        post_evaluation_with_retries(evaluation_url, cb)

    return {
        "status": "ok",
        "task": task,
        "round": round_no,
        "repo_url": repo_url,
        "pages_url": pages_url,
    }