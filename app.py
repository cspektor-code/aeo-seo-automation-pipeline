import hashlib
import os
import secrets
import sqlite3
import uuid
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile, BackgroundTasks, HTTPException, Form, Response, Request, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse
from pipeline_engine import run_pipeline

# Load private API keys from .env into memory
load_dotenv()

app = FastAPI(title="On The Brink B2B - SaaS Application")

DB_FILE = "saas_database.db"
JOBS = {}
SESSIONS = {}  # session_token -> user_email


def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            credits INTEGER DEFAULT 20
        )
    """)
    conn.commit()
    conn.close()

init_db()


def hash_password(password: str, salt: str = None) -> tuple:
    if not salt:
        salt = secrets.token_hex(16)
    pw_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), 100000).hex()
    return pw_hash, salt


def get_user_by_email(email: str):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT email, password_hash, salt, credits FROM users WHERE email = ?", (email.lower(),))
    user = cursor.fetchone()
    conn.close()
    if user:
        return {"email": user[0], "password_hash": user[1], "salt": user[2], "credits": user[3]}
    return None


def deduct_user_credits(email: str, amount: int):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET credits = credits - ? WHERE email = ?", (amount, email.lower()))
    conn.commit()
    conn.close()


def get_current_user(session_token: str = Cookie(None)):
    if session_token and session_token in SESSIONS:
        email = SESSIONS[session_token]
        return get_user_by_email(email)
    return None


@app.get("/login", response_class=HTMLResponse)
def login_page(msg: str = ""):
    alert_html = f'<div style="color:#b91c1c; background-color:#fee2e2; padding:10px; border-radius:6px; margin-bottom:15px; font-size:13px;">{msg}</div>' if msg else ""
    return f"""
    <!DOCTYPE html>
    <html>
        <head>
            <title>Log In - On The Brink B2B</title>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 400px; margin: 80px auto; padding: 20px; background: #f8f9fa; }}
                .card {{ background: white; border: 1px solid #e1e4e8; border-radius: 10px; padding: 28px; box-shadow: 0 4px 14px rgba(0,0,0,0.06); }}
                h2 {{ margin-top: 0; color: #111827; text-align: center; }}
                label {{ font-size: 13px; font-weight: 600; color: #374151; display: block; margin-top: 14px; margin-bottom: 4px; }}
                input {{ width: 100%; padding: 10px; font-size: 14px; border: 1px solid #d1d5db; border-radius: 6px; box-sizing: border-box; }}
                button {{ background-color: #0066cc; color: white; border: none; padding: 12px; font-size: 15px; font-weight: bold; border-radius: 6px; cursor: pointer; width: 100%; margin-top: 18px; }}
                button:hover {{ background-color: #0052a3; }}
                p {{ text-align: center; font-size: 13px; margin-top: 16px; color: #6b7280; }}
                a {{ color: #0066cc; text-decoration: none; font-weight: 600; }}
            </style>
        </head>
        <body>
            <div class="card">
                <h2>Sign In</h2>
                {alert_html}
                <form action="/login" method="post">
                    <label>Email Address</label>
                    <input type="email" name="email" required>
                    <label>Password</label>
                    <input type="password" name="password" required>
                    <button type="submit">Log In</button>
                </form>
                <p>Don't have an account? <a href="/register">Sign Up</a></p>
            </div>
        </body>
    </html>
    """


@app.post("/login")
def login_submit(email: str = Form(...), password: str = Form(...)):
    user = get_user_by_email(email)
    if not user:
        return RedirectResponse(url="/login?msg=Invalid+email+or+password", status_code=303)
    
    computed_hash, _ = hash_password(password, user["salt"])
    if computed_hash != user["password_hash"]:
        return RedirectResponse(url="/login?msg=Invalid+email+or+password", status_code=303)

    session_token = str(uuid.uuid4())
    SESSIONS[session_token] = user["email"]
    
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(key="session_token", value=session_token, httponly=True)
    return response


@app.get("/register", response_class=HTMLResponse)
def register_page(msg: str = ""):
    alert_html = f'<div style="color:#b91c1c; background-color:#fee2e2; padding:10px; border-radius:6px; margin-bottom:15px; font-size:13px;">{msg}</div>' if msg else ""
    return f"""
    <!DOCTYPE html>
    <html>
        <head>
            <title>Sign Up - On The Brink B2B</title>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 400px; margin: 80px auto; padding: 20px; background: #f8f9fa; }}
                .card {{ background: white; border: 1px solid #e1e4e8; border-radius: 10px; padding: 28px; box-shadow: 0 4px 14px rgba(0,0,0,0.06); }}
                h2 {{ margin-top: 0; color: #111827; text-align: center; }}
                label {{ font-size: 13px; font-weight: 600; color: #374151; display: block; margin-top: 14px; margin-bottom: 4px; }}
                input {{ width: 100%; padding: 10px; font-size: 14px; border: 1px solid #d1d5db; border-radius: 6px; box-sizing: border-box; }}
                button {{ background-color: #10b981; color: white; border: none; padding: 12px; font-size: 15px; font-weight: bold; border-radius: 6px; cursor: pointer; width: 100%; margin-top: 18px; }}
                button:hover {{ background-color: #059669; }}
                p {{ text-align: center; font-size: 13px; margin-top: 16px; color: #6b7280; }}
                a {{ color: #0066cc; text-decoration: none; font-weight: 600; }}
            </style>
        </head>
        <body>
            <div class="card">
                <h2>Create Account</h2>
                {alert_html}
                <form action="/register" method="post">
                    <label>Email Address</label>
                    <input type="email" name="email" required>
                    <label>Password</label>
                    <input type="password" name="password" required>
                    <button type="submit">Create Account (20 Free Credits)</button>
                </form>
                <p>Already have an account? <a href="/login">Log In</a></p>
            </div>
        </body>
    </html>
    """


@app.post("/register")
def register_submit(email: str = Form(...), password: str = Form(...)):
    email_clean = email.lower().strip()
    if get_user_by_email(email_clean):
        return RedirectResponse(url="/register?msg=Email+already+registered", status_code=303)

    pw_hash, salt = hash_password(password)
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO users (email, password_hash, salt, credits) VALUES (?, ?, ?, 20)", (email_clean, pw_hash, salt))
    conn.commit()
    conn.close()

    return RedirectResponse(url="/login?msg=Account+created!+Please+log+in.", status_code=303)


@app.get("/logout")
def logout(session_token: str = Cookie(None)):
    if session_token in SESSIONS:
        del SESSIONS[session_token]
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("session_token")
    return response


@app.get("/", response_class=HTMLResponse)
def home(session_token: str = Cookie(None)):
    user = get_current_user(session_token)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    return f"""
    <!DOCTYPE html>
    <html>
        <head>
            <title>Dashboard - On The Brink B2B</title>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 40px auto; padding: 20px; background: #f8f9fa; }}
                .card {{ background: white; border: 1px solid #e1e4e8; border-radius: 10px; padding: 28px; box-shadow: 0 4px 14px rgba(0,0,0,0.06); }}
                .header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #f3f4f6; padding-bottom: 14px; margin-bottom: 20px; }}
                .user-info {{ font-size: 13px; color: #4b5563; }}
                .badge {{ background-color: #dbeafe; color: #1e40af; padding: 4px 10px; border-radius: 12px; font-weight: bold; margin-left: 6px; }}
                label {{ font-size: 13px; font-weight: 600; color: #374151; display: block; margin-top: 14px; margin-bottom: 4px; }}
                input[type="file"] {{ margin-top: 6px; margin-bottom: 20px; width: 100%; }}
                button {{ background-color: #0066cc; color: white; border: none; padding: 12px 22px; font-size: 15px; font-weight: bold; border-radius: 6px; cursor: pointer; width: 100%; margin-top: 10px; }}
                button:hover {{ background-color: #0052a3; }}
                #progress-container {{ display: none; margin-top: 20px; }}
                .progress-bar-bg {{ background-color: #e5e7eb; border-radius: 999px; height: 14px; width: 100%; overflow: hidden; }}
                .progress-bar-fill {{ background-color: #0066cc; height: 100%; width: 0%; transition: width 0.4s ease; }}
                #status-text {{ font-size: 14px; color: #4b5563; margin-top: 12px; font-weight: 600; text-align: center; }}
                #download-btn {{ display: none; background-color: #10b981; margin-top: 20px; }}
                #download-btn:hover {{ background-color: #059669; }}
                #error-box {{ display: none; background-color: #fee2e2; color: #991b1b; padding: 12px; border-radius: 6px; margin-top: 15px; font-size: 14px; }}
            </style>
        </head>
        <body>
            <div class="card">
                <div class="header">
                    <div class="user-info">
                        <strong>{user['email']}</strong>
                        <span class="badge">{user['credits']} Credits</span>
                    </div>
                    <a href="/logout" style="color: #ef4444; font-size: 13px; text-decoration: none; font-weight:600;">Log Out</a>
                </div>

                <h2>AEO Content Engine</h2>
                <p style="color: #6b7280; font-size: 14px;">Upload your master Excel spreadsheet to run the automated AI pipeline.</p>

                <div id="form-container">
                    <form id="upload-form" onsubmit="startJob(event)">
                        <label>Master Excel File (.xlsx)</label>
                        <input type="file" name="file" accept=".xlsx" required><br>
                        <button type="submit">Process File (Costs 1 Credit)</button>
                    </form>
                </div>

                <div id="progress-container">
                    <h3 style="text-align:center; margin-bottom: 15px;">Processing Your File...</h3>
                    <div class="progress-bar-bg">
                        <div id="progress-fill" class="progress-bar-fill"></div>
                    </div>
                    <div id="status-text">Uploading...</div>
                    <a id="download-link" href="#" style="text-decoration:none;">
                        <button id="download-btn" type="button">Download Completed Excel</button>
                    </a>
                </div>
                
                <div id="error-box"></div>
            </div>

            <script>
                async function startJob(event) {{
                    event.preventDefault();
                    const form = document.getElementById('upload-form');
                    const formData = new FormData(form);
                    
                    document.getElementById('form-container').style.display = 'none';
                    document.getElementById('progress-container').style.display = 'block';
                    document.getElementById('error-box').style.display = 'none';

                    try {{
                        const response = await fetch('/api/process', {{ method: 'POST', body: formData }});
                        const data = await response.json();
                        if (response.ok) {{
                            pollStatus(data.job_id);
                        }} else {{
                            showError(data.detail || "Upload failed.");
                        }}
                    }} catch (err) {{
                        showError("Network error starting job.");
                    }}
                }}

                function pollStatus(jobId) {{
                    const interval = setInterval(async () => {{
                        try {{
                            const res = await fetch('/api/status/' + jobId);
                            const data = await res.json();
                            const percent = data.total > 0 ? Math.round((data.current / data.total) * 100) : 0;
                            document.getElementById('progress-fill').style.width = percent + '%';
                            document.getElementById('status-text').innerText = data.message;

                            if (data.status === "completed") {{
                                clearInterval(interval);
                                document.getElementById('status-text').innerText = "Processing Complete!";
                                document.getElementById('download-btn').style.display = 'block';
                                document.getElementById('download-link').href = '/api/download/' + jobId;
                            }} else if (data.status === "failed") {{
                                clearInterval(interval);
                                showError("Job failed: " + data.message);
                            }}
                        }} catch (err) {{
                            console.error("Polling error", err);
                        }}
                    }}, 1500);
                }}

                function showError(msg) {{
                    document.getElementById('progress-container').style.display = 'none';
                    document.getElementById('form-container').style.display = 'block';
                    const errBox = document.getElementById('error-box');
                    errBox.innerText = msg;
                    errBox.style.display = 'block';
                }}
            </script>
        </body>
    </html>
    """


def background_worker(job_id: str, file_bytes: bytes, filename: str, user_email: str):
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")

    def update_progress(current, total, message):
        JOBS[job_id]["current"] = current
        JOBS[job_id]["total"] = total
        JOBS[job_id]["message"] = message

    try:
        processed_bytes = run_pipeline(file_bytes, gemini_key, anthropic_key, progress_callback=update_progress)
        JOBS[job_id]["status"] = "completed"
        JOBS[job_id]["result_bytes"] = processed_bytes
        JOBS[job_id]["filename"] = "Processed_" + filename
        
        # Deduct credit upon successful execution
        deduct_user_credits(user_email, 1)
    except Exception as e:
        JOBS[job_id]["status"] = "failed"
        JOBS[job_id]["message"] = str(e)


@app.post("/api/process")
async def api_process(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    session_token: str = Cookie(None)
):
    user = get_current_user(session_token)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized. Please log in.")

    if user["credits"] <= 0:
        raise HTTPException(status_code=402, detail="Insufficient credits. Please purchase more credits.")

    if not file.filename.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Only .xlsx files supported.")

    file_bytes = await file.read()
    job_id = str(uuid.uuid4())
    
    JOBS[job_id] = {
        "status": "processing",
        "current": 0,
        "total": 0,
        "message": "Initializing...",
        "result_bytes": None,
        "filename": None,
        "user_email": user["email"]
    }
    
    background_tasks.add_task(background_worker, job_id, file_bytes, file.filename, user["email"])
    return {"job_id": job_id}


@app.get("/api/status/{job_id}")
def api_status(job_id: str):
    if job_id not in JOBS:
        raise HTTPException(status_code=404, detail="Job not found.")
    
    job = JOBS[job_id]
    return {
        "status": job["status"],
        "current": job["current"],
        "total": job["total"],
        "message": job["message"]
    }


@app.get("/api/download/{job_id}")
def api_download(job_id: str):
    if job_id not in JOBS or JOBS[job_id]["status"] != "completed":
        raise HTTPException(status_code=400, detail="File not ready.")
    
    job = JOBS[job_id]
    return Response(
        content=job["result_bytes"],
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=\"{job['filename']}\""}
    )

if __name__ == "__main__":
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)