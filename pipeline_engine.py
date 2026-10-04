import copy
import io
import json
import os
import random
import re
import sys
import time
import warnings

import anthropic
from anthropic import Anthropic
from bs4 import BeautifulSoup
from htmldate import find_date  # NEW: Commercial date extractor
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
import pandas as pd
import requests

from google import genai
from google.genai import types

warnings.filterwarnings("ignore", category=DeprecationWarning, module="google")

SHEET_NAME = "aeo blog digestible cyberpro qa"
MAX_CELL_CHARACTERS = 4000

MIN_SCRAPE_DELAY = 2.0
MAX_SCRAPE_DELAY = 4.0
CLAUDE_TIMEOUT_SECONDS = 45.0

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

GEMINI_MODELS = ["gemini-3.8-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
CLAUDE_MODELS = ["claude-haiku-4-5-20251001", "claude-3-5-sonnet-20240620"]

COL_STATUS = 1
COL_DATA_SOURCE = 2
COL_PUB_DATE = 3
COL_URL = 4
COL_H1 = 5
COL_GEMINI_QUESTION = 6
COL_GEMINI_ANSWER = 7
COL_CLAUDE_QUESTION = 8
COL_CLAUDE_ANSWER = 9
COL_CLAUDE_EXPLANATION = 10
COL_CHUNK_START = 11

TRIGGER_LEVELBLUE = "levelblue blog ready for python"
TRIGGER_SPIDERLABS = "spiderlabs blog ready for python"

COLOR_RED_FILL = "FFC7CE"
COLOR_RED_TEXT = "9C0006"
COLOR_YELLOW_FILL = "FFEB9C"

ALIGN_WRAP_TOP = Alignment(wrap_text=True, vertical="top")

Q_MIN_WORDS, Q_MAX_WORDS = 10, 18
A_MIN_WORDS, A_MAX_WORDS = 50, 70
MAX_QA_ATTEMPTS = 3

NOISE_TAGS = ["nav", "footer", "header", "aside", "script", "style", "noscript", "form"]

BANNED_ANSWER_PATTERNS = [
    (r"\baccording to levelblue\b", "uses stock phrase 'according to LevelBlue'"),
    (r"\blevelblue research indicates\b", "uses stock phrase 'LevelBlue research indicates'"),
    (r"\blevelblue spiderlabs\b", "uses stock attribution 'LevelBlue SpiderLabs'"),
    (r"\b(this|the) (article|blog|blog post|post|page)\b", "refers to 'the article/page/post'"),
    (r"\bthe author\b", "refers to 'the author'"),
    (r"\bleverag(e|es|ed|ing)\b", "uses filler verb 'leverage'"),
    (r"\butiliz(e|es|ed|ing)\b", "uses filler verb 'utilize'"),
    (r"\b(defenders?|security teams?|organizations?|companies)\b.{0,40}\b(must|should|need to)\b", "contains prescriptive defensive advice"),
]

BANNED_QUESTION_PATTERNS = [
    (r"\bleverag(e|es|ed|ing)\b", "uses filler verb 'leverage'"),
    (r"\butiliz(e|es|ed|ing)\b",  "uses filler verb 'utilize'"),
]


def log(msg):
    print(msg, flush=True)


def sanitize_for_excel(text):
    if text is None: return ""
    return re.sub(r'[\x00-\x08\x0B-\x0C\x0E-\x1F]', '', str(text))


def normalize_whitespace(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def clean_and_count_words(text):
    cleaned = sanitize_for_excel(text)
    cleaned = re.sub(r"\s*[\u2014\u2013]\s*", ", ", cleaned)
    cleaned = re.sub(r"\s+--+\s+", ", ", cleaned)
    cleaned = re.sub(r"\s+-\s+", ", ", cleaned)
    cleaned = normalize_whitespace(cleaned)
    cleaned = re.sub(r",\s*,", ",", cleaned)
    cleaned = re.sub(r"\s+([,.;:?!])", r"\1", cleaned)
    return cleaned, len(cleaned.split())


def chunk_text(text, limit=MAX_CELL_CHARACTERS):
    chunks = []
    current = ""
    for para in text.split("\n\n"):
        para = para.strip()
        if not para: continue
        while len(para) > limit:
            cut = para.rfind(" ", 0, limit)
            if cut <= 0: cut = limit
            piece, para = para[:cut].strip(), para[cut:].strip()
            if current: chunks.append(current); current = ""
            chunks.append(piece)
        if not para: continue
        if not current: current = para
        elif len(current) + 2 + len(para) <= limit: current += "\n\n" + para
        else: chunks.append(current); current = para
    if current: chunks.append(current)
    return chunks


def get_url_from_row(ws, r_idx):
    for col_idx in [COL_URL, 3, 2, 1, 5, 6, 7]:
        val = ws.cell(row=r_idx, column=col_idx).value
        if val and ("http" in str(val).lower() or "levelblue.com" in str(val).lower()):
            return str(val).strip()
    return ""


def scrape_webpage(url):
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        if response.status_code != 200:
            return f"Error HTTP {response.status_code}", "N/A", ""

        # 1. Use htmldate to find the publication date
        extracted_date = find_date(response.text)
        pub_date = extracted_date if extracted_date else "N/A"

        # 2. Extract H1 Title and Article Text
        soup = BeautifulSoup(response.text, "html.parser")
        h1_tag = soup.find("h1")
        h1_text = normalize_whitespace(h1_tag.get_text()) if h1_tag else "No H1 Found"

        for tag in soup.find_all(NOISE_TAGS):
            tag.decompose()

        paragraphs = [normalize_whitespace(p.get_text()) for p in soup.find_all(["p", "h2", "h3"]) if len(p.get_text()) > 20]
        full_text = "\n\n".join(paragraphs).strip()
        
        return sanitize_for_excel(h1_text), sanitize_for_excel(pub_date), sanitize_for_excel(full_text)
    except Exception as e:
        return "Scrape Error", "N/A", f"Failed to fetch content: {str(e)}"


def determine_category(status_val, url_val):
    s_norm = str(status_val or "").strip().lower()
    u_norm = str(url_val or "").strip().lower()
    if s_norm == TRIGGER_SPIDERLABS or "spiderlabs-blog" in u_norm:
        return "Category B"
    return "Category A"


SYSTEM_INSTRUCTION = (
    "You are an elite enterprise cybersecurity research analyst writing technical Q&A snippets "
    "for senior practitioners. You never define basic terms or talk down to the audience.\n\n"
    "HARD CONSTRAINTS:\n"
    "1. WORD COUNTS: Question must be 10-18 words. Answer must be 50-70 words.\n"
    "2. NO DEFENSIVE ADVICE: Describe attack mechanics as facts only.\n"
    "3. NO FILLER VERBS: Never use leverage or utilize.\n"
    "4. NO STOCK ATTRIBUTION: Never open an answer with 'according to LevelBlue'.\n"
    "5. JSON OUTPUT: Return exactly two keys: 'question' and 'answer'."
)


def build_qa_prompt(h1_title, full_article_text, category, avoid_openers):
    scope_instruction = (
        "CATEGORY B STRATEGY (SPIDERLABS TOPIC SNIPPET):\n- Focus on ONE major technical finding."
        if category == "Category B" else
        "CATEGORY A STRATEGY (LEVELBLUE FULL SUMMARY):\n- Summarize ENTIRE article thesis across all major sections."
    )
    avoid_line = f"Do NOT start this question with: {', '.join(avoid_openers)}." if avoid_openers else ""

    return f"""Draft a technical Question and Answer pair based STRICTLY on this page.

### SCOPE:
{scope_instruction}

### MANDATORY RULES:
1. QUESTION: 10 to 18 words. Do NOT start with "What is" or "What are". {avoid_line}
2. ANSWER: 50 to 70 words. No filler verbs (leverage/utilize). No defensive advice.

Article H1: {h1_title}
FULL Article Text:
{full_article_text}

Return JSON with keys "question" and "answer"."""


def validate_qa(question, answer, avoid_openers):
    issues = []
    variety_issues = []

    q_words = len(question.split())
    a_words = len(answer.split())
    if not (Q_MIN_WORDS <= q_words <= Q_MAX_WORDS):
        issues.append(f"Question is {q_words} words (must be {Q_MIN_WORDS} to {Q_MAX_WORDS})")
    if not (A_MIN_WORDS <= a_words <= A_MAX_WORDS):
        issues.append(f"Answer is {a_words} words (must be {A_MIN_WORDS} to {A_MAX_WORDS})")

    q_low = question.lower()
    if "levelblue" in q_low:
        issues.append("Question contains LevelBlue")
    if re.match(r"\s*what (is|are)\b", question, re.IGNORECASE):
        issues.append("Question starts with 'What is/are'")
    for pattern, message in BANNED_QUESTION_PATTERNS:
        if re.search(pattern, q_low):
            issues.append(f"Question {message}")

    a_low = answer.lower().strip()
    if a_low.startswith("levelblue"):
        issues.append("Answer opens with LevelBlue")
    for pattern, message in BANNED_ANSWER_PATTERNS:
        if re.search(pattern, a_low, re.IGNORECASE):
            issues.append(f"Answer {message}")

    return issues, variety_issues


def call_gemini(gemini_client, prompt):
    if not gemini_client:
        return None, None, None

    for model_name in GEMINI_MODELS:
        try:
            response = gemini_client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    temperature=0.2,
                    response_mime_type="application/json",
                ),
            )
            raw_text = response.text.strip()
            if raw_text.startswith("```"):
                raw_text = re.sub(r"^```[a-zA-Z]*", "", raw_text).strip()
                raw_text = re.sub(r"```$", "", raw_text).strip()
            content = json.loads(raw_text)
            return model_name, content.get("question", "N/A"), content.get("answer", "N/A")
        except Exception as err:
            log(f"   ❌ [Gemini Error on {model_name}]: {err}")
            time.sleep(1.0)

    return None, None, None


def generate_expert_qa(gemini_client, h1_title, full_article_text, category, avoid_openers=()):
    base_prompt = build_qa_prompt(h1_title, full_article_text, category, list(avoid_openers))
    feedback = ""
    result = None

    for attempt in range(1, MAX_QA_ATTEMPTS + 1):
        if attempt > 1:
            time.sleep(1.0)

        model_name, raw_q, raw_a = call_gemini(gemini_client, base_prompt + feedback)
        if model_name is None:
            continue

        question, q_words = clean_and_count_words(raw_q)
        answer, a_words = clean_and_count_words(raw_a)
        issues, variety_issues = validate_qa(question, answer, avoid_openers)
        result = (question, answer, issues)

        log(f"   -> Gemini Attempt {attempt}/{MAX_QA_ATTEMPTS} ({model_name}) | Q: {q_words} w | A: {a_words} w")

        if not issues and (not variety_issues or attempt == MAX_QA_ATTEMPTS):
            return question, answer, True, []

        problems = issues + variety_issues
        feedback = "\n\nFIX CHECKS:\n" + "\n".join(f"- {p}" for p in problems)

    if result is None:
        return "API Exception", "Error generating Q&A: All Gemini model attempts failed.", False, ["Gemini API failed"]

    question, answer, issues = result
    return question, answer, True, issues


def review_qa_with_claude(claude_client, h1_title, full_article_text, category, gemini_q, gemini_a):
    if not claude_client:
        log("   [Claude API skipped]")
        return gemini_q, gemini_a, "unchanged", "Claude API key missing; kept Gemini draft.", []

    base_prompt = f"""Review this draft Q&A generated by Gemini. Decide whether to KEEP IT AS-IS or IMPROVE IT.

ARTICLE TITLE: {h1_title}
ARTICLE CONTENT: {full_article_text[:10000]}

GEMINI DRAFT:
Question: {gemini_q}
Answer: {gemini_a}

STRICT WORD COUNT CONSTRAINTS (CRITICAL):
1. Question MUST be between 10 and 18 words.
2. Answer MUST be between 50 and 70 words. Do NOT make it overly concise.
3. Question MUST NOT contain 'LevelBlue'.
4. Answer MUST NOT start with 'LevelBlue'.
5. No filler verbs ('leverage' or 'utilize').

Return JSON with keys: "action" ("unchanged" or "revised"), "question", "answer", "explanation"."""

    feedback = ""

    for attempt in range(1, 3 + 1):
        if attempt > 1:
            time.sleep(1.0)

        current_prompt = base_prompt + feedback

        for model_name in CLAUDE_MODELS:
            try:
                response = claude_client.messages.create(
                    model=model_name,
                    max_tokens=1500,
                    messages=[{"role": "user", "content": current_prompt}]
                )
                raw_text = response.content[0].text.strip()
                if raw_text.startswith("```"):
                    raw_text = re.sub(r"^```[a-zA-Z]*", "", raw_text).strip()
                    raw_text = re.sub(r"```$", "", raw_text).strip()

                data = json.loads(raw_text)
                action = str(data.get("action", "unchanged")).lower().strip()
                explanation = str(data.get("explanation", "Reviewed by Claude.")).strip()

                if action != "revised":
                    log(f"   -> Claude [{model_name}]: [UNCHANGED]")
                    return gemini_q, gemini_a, "unchanged", explanation, []

                clean_cq, cq_words = clean_and_count_words(data.get("question", gemini_q))
                clean_ca, ca_words = clean_and_count_words(data.get("answer", gemini_a))

                rule_issues, _ = validate_qa(clean_cq, clean_ca, ())
                if not rule_issues:
                    log(f"   -> Claude Attempt {attempt} [{model_name}]: [REVISED - VALID] | Q: {cq_words} w | A: {ca_words} w")
                    return clean_cq, clean_ca, "revised", explanation, []

                log(f"   -> Claude Attempt {attempt} revision failed checks ({'; '.join(rule_issues)}). Retrying...")
                feedback = f"\n\nCRITICAL FIX NEEDED FOR YOUR REVISION:\n" + "\n".join(f"- {p}" for p in rule_issues) + "\nPlease adjust your wording so Question is 10-18 words and Answer is strictly 50-70 words."
                break  # Try next attempt loop

            except Exception as err:
                log(f"   ❌ [Claude Error on {model_name}]: {err}")
                time.sleep(1.0)

    log("   -> Claude failed constraints after retries; kept Gemini draft.")
    return gemini_q, gemini_a, "unchanged", "Claude revisions exceeded constraint retry limits; kept valid Gemini draft.", []

def run_pipeline(file_bytes: bytes, gemini_key: str, anthropic_key: str = "", progress_callback=None) -> bytes:
    g_key = gemini_key.strip()
    a_key = anthropic_key.strip()

    if not g_key:
        raise ValueError("GEMINI_API_KEY is required.")

    gemini_client = genai.Client(api_key=g_key)
    claude_client = Anthropic(api_key=a_key, timeout=CLAUDE_TIMEOUT_SECONDS) if a_key else None

    wb = openpyxl.load_workbook(io.BytesIO(file_bytes))
    sheet_name = SHEET_NAME if SHEET_NAME in wb.sheetnames else wb.sheetnames[0]
    ws = wb[sheet_name]

    candidate_rows = []
    for r_idx in range(2, ws.max_row + 1):
        status_val = str(ws.cell(row=r_idx, column=COL_STATUS).value or "").strip().lower()
        if status_val in [TRIGGER_LEVELBLUE, TRIGGER_SPIDERLABS]:
            candidate_rows.append(r_idx)

    total_rows = len(candidate_rows)
    if progress_callback: progress_callback(0, total_rows, "Starting engine...")

    fill_yellow = PatternFill(start_color=COLOR_YELLOW_FILL, end_color=COLOR_YELLOW_FILL, fill_type="solid")
    recent_openers = []

    for count, r_idx in enumerate(candidate_rows, 1):
        target_url = get_url_from_row(ws, r_idx)
        if progress_callback: progress_callback(count, total_rows, f"Scraping row {count} of {total_rows}...")

        category = determine_category(ws.cell(row=r_idx, column=COL_STATUS).value, target_url)
        time.sleep(random.uniform(MIN_SCRAPE_DELAY, MAX_SCRAPE_DELAY))

        h1, pub_date, full_text = scrape_webpage(target_url)
        scrape_ok = bool(full_text) and h1 != "N/A" and not h1.startswith(("Error HTTP", "Scrape Error"))
        qa_issues = []
        claude_flags = []

        if scrape_ok:
            if progress_callback: progress_callback(count, total_rows, f"Gemini drafting row {count}...")
            gemini_q, gemini_a, api_ok, qa_issues = generate_expert_qa(gemini_client, h1, full_text, category, recent_openers[-2:])

            if api_ok:
                if progress_callback: progress_callback(count, total_rows, f"Claude reviewing row {count}...")
                claude_q, claude_a, claude_action, claude_explanation, claude_flags = review_qa_with_claude(
                    claude_client, h1, full_text, category, gemini_q, gemini_a
                )
                action_label = "Unchanged" if claude_action == "unchanged" else "Revised"
                brand_prefix = "LevelBlue" if category == "Category A" else "SpiderLabs"
                completion_status = f"{brand_prefix} blog processed - Claude [{action_label}]"
            else:
                gemini_q, gemini_a, claude_q, claude_a, claude_explanation, completion_status = "N/A", "N/A", "N/A", "N/A", "Gemini failed", "Error"
        else:
            gemini_q, gemini_a, claude_q, claude_a, claude_explanation, completion_status = "N/A", "N/A", "N/A", "N/A", "Scrape failed", "Error"

        ws.cell(row=r_idx, column=COL_STATUS, value=sanitize_for_excel(completion_status)).alignment = ALIGN_WRAP_TOP
        ws.cell(row=r_idx, column=COL_PUB_DATE, value=sanitize_for_excel(pub_date)).alignment = ALIGN_WRAP_TOP
        ws.cell(row=r_idx, column=COL_H1, value=sanitize_for_excel(h1)).alignment = ALIGN_WRAP_TOP
        ws.cell(row=r_idx, column=COL_GEMINI_QUESTION, value=sanitize_for_excel(gemini_q)).alignment = ALIGN_WRAP_TOP
        ws.cell(row=r_idx, column=COL_GEMINI_ANSWER, value=sanitize_for_excel(gemini_a)).alignment = ALIGN_WRAP_TOP
        cq = ws.cell(row=r_idx, column=COL_CLAUDE_QUESTION, value=sanitize_for_excel(claude_q))
        ca = ws.cell(row=r_idx, column=COL_CLAUDE_ANSWER, value=sanitize_for_excel(claude_a))
        cq.alignment = ca.alignment = ALIGN_WRAP_TOP
        ws.cell(row=r_idx, column=COL_CLAUDE_EXPLANATION, value=sanitize_for_excel(claude_explanation)).alignment = ALIGN_WRAP_TOP

        if qa_issues:
            cq.fill = fill_yellow
            ca.fill = fill_yellow

        if full_text:
            for part_num, chunk in enumerate(chunk_text(full_text, MAX_CELL_CHARACTERS), 1):
                ws.cell(row=r_idx, column=COL_CHUNK_START - 1 + part_num, value=sanitize_for_excel(chunk)).alignment = ALIGN_WRAP_TOP

    ws.column_dimensions['F'].width = ws.column_dimensions['H'].width = 45
    ws.column_dimensions['G'].width = ws.column_dimensions['I'].width = ws.column_dimensions['J'].width = 50

    if progress_callback: progress_callback(total_rows, total_rows, "Finalizing Excel file...")
    out_buffer = io.BytesIO()
    wb.save(out_buffer)
    out_buffer.seek(0)
    return out_buffer.getvalue()