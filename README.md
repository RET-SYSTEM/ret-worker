# Montenegro Expense Tracker - Python AI Worker (`ret-worker`)

This repository contains the Python-based microservice for the Montenegro Expense Tracker (RET) system. 

The AI Worker acts as the intermediary between the Spring Boot backend, the Montenegrin Tax API, and the Mistral AI ecosystem. It is specifically designed to bypass aggressive Web Application Firewalls (WAF) and to execute high-speed, dynamic AI categorization.

## 🚀 Key Responsibilities

1. **WAF Bypass (Scraping):** The Montenegrin Tax API (`https://mapr.tax.gov.me/ic/`) is protected by an F5 BIG-IP WAF that aggressively blocks standard HTTP clients (like Java's `RestClient` or Python's standard `requests`). This worker utilizes `curl_cffi` to impersonate a Chrome 120 browser, performing a two-step handshake to safely extract the raw JSON receipt data.
2. **AI Categorization:** Once the raw receipt is extracted, the worker feeds the item names (in the Montenegrin language) directly into the Mistral AI API (powered by `open-mistral-nemo`). It dynamically categorizes each item based on the specific custom categories provided by the Spring Boot backend.
3. **Data Normalization:** It flattens and normalizes the heavily nested government JSON into a clean schema before returning it to the Spring Boot backend for database persistence.

## 🛠️ Tech Stack

*   **Framework:** FastAPI
*   **Networking:** `curl_cffi` (for TLS/JA3 fingerprint impersonation)
*   **AI Integration:** `groq` (official Python SDK)
*   **Server:** `uvicorn`

## 🔐 Security & Environment Variables

This microservice is strictly internal. It should **never** be exposed directly to the public internet. It requires an `X-Internal-Api-Key` header for the main processing endpoint to prevent unauthorized scraping/AI token usage.

Create a `.env` file in the root directory:

```env
# Groq API Key for Llama-3 (REQUIRED)
GROQ_API_KEY=gsk_your_groq_api_key_here

# Security Key required by the Spring Boot Backend to call this worker
INTERNAL_API_KEY=your_super_secret_internal_key

# The port FastAPI runs on (Optional, defaults to 3501)
PYTHON_WORKER_PORT=3501
```

## 🏃‍♂️ Running Locally

To run this microservice locally for development:

1. Create a virtual environment and load it:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```
2. Install the necessary dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Start the Uvicorn server:
   ```bash
   python main.py
   ```

*(The server will boot up on `http://localhost:3501` and expose endpoint `/extract` and health-check `/health`)*.
