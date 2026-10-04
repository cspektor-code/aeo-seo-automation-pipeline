# Dual-LLM AEO & SEO Automation Pipeline

An enterprise-grade Answer Engine Optimization (AEO) and Generative Engine Optimization (GEO) pipeline built in Python. This tool automates webpage content scraping, dual-LLM orchestration (Google Gemini + Anthropic Claude), strict rule-based quality assurance, and fact-retention verification to generate high-value technical Q&A pairs for modern AI search engines.

---

## 🛠️ Key Features

- **Dual-LLM Orchestration:**
  - **Pass 1 (Draft & Self-Critique — Google Gemini):** Scrapes article content, extracts section outlines, isolates atomic technical facts into a scratchpad, generates draft Q&A pairs, and executes an automated self-critique pass.
  - **Pass 2 (Peer Review & Fact-Check — Anthropic Claude):** Reviews draft Q&A pairs for accuracy, scope, tone, and brand alignment, enforcing strict evidence-matching fact checks against the raw article text.
- **Automated Guardrails & Fail-Safes:**
  - Strict constraint enforcement (Question: 10–18 words | Answer: 50–70 words).
  - Automated filtering of banned filler verbs (*leverage*, *utilize*) and prescriptive advice (*must*, *should*, *need to*).
  - Automatic fallback to Gemini's clean draft if Claude's proposed revision violates formatting rules or fails fact-checking.
- **AEO Fact-Retention Metrics:** Tracks extracted key facts (numbers, metrics, CVEs, tools, actors) and measures their retention ratio across AI generation steps.
- **Excel & CSV Integration:** Reads input batch spreadsheets, populates final outputs, logs Column J review rationales, and applies yellow highlights to rows flagged for manual inspection.
- **Streamlit Web Dashboard:** Features an interactive web app (`app.py`) for running batch processing jobs and monitoring execution metrics in real time.

---

## 🏗️ Pipeline Architecture
