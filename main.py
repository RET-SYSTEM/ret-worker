"""
RET Worker — Python FastAPI microservice.
Bridges the Montenegrin Tax Administration API and Groq AI categorization.
Called by the Spring Boot backend on POST /extract.
"""

import json
import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from curl_cffi import requests as cffi_requests
from groq import Groq

# ── Bootstrap ─────────────────────────────────────────────────────────
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("ret-worker")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY is not set in environment variables.")

groq_client = Groq(api_key=GROQ_API_KEY)

TAX_LANDING_URL = "https://mapr.tax.gov.me/ic/"
TAX_API_URL = "https://mapr.tax.gov.me/ic/api/verifyInvoice"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

DEFAULT_CATEGORIES = [
    "Groceries", "Eating Out", "Coffe", "Transport", 
    "Clothing", "Hardware", "Health", "Hygiene & Cosmetics", 
    "Subscriptions", "Entertainment", "Education", "Gifts", 
    "Uncategorized"
]

app = FastAPI(title="RET Worker", version="1.0.0")


# ── Request / Response schemas ────────────────────────────────────────
class ExtractRequest(BaseModel):
    iic: str
    tin: str
    dateTimeCreated: str
    categories: list[str] | None = None


class ItemOut(BaseModel):
    name: str
    unitPriceAfterVat: float
    quantity: float
    category: str | None = None


class ExtractResponse(BaseModel):
    iic: str
    dateTimeCreated: str
    sellerName: str
    totalPrice: float
    paymentMethod: str
    items: list[ItemOut]


# ── Tax API integration (WAF bypass via curl_cffi) ────────────────────
def fetch_invoice_from_tax_api(iic: str, tin: str, date_time_created: str) -> dict:
    """
    Two-step WAF bypass following the proven mpi.py pattern:
    1. GET the landing page to acquire F5 BIG-IP cookies.
    2. POST form-data to the verify endpoint.
    """
    session = cffi_requests.Session(impersonate="chrome120")

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://mapr.tax.gov.me",
        "Referer": TAX_LANDING_URL,
    }

    # Step 1 — collect WAF cookies
    logger.info("Visiting landing page to collect cookies…")
    session.get(TAX_LANDING_URL, headers={"User-Agent": USER_AGENT})

    # Step 2 — POST to the API (form-urlencoded, NOT json — per mpi.py)
    payload = {
        "iic": iic,
        "tin": tin,
        "dateTimeCreated": date_time_created,
    }
    logger.info("Sending POST to tax API for IIC=%s", iic)
    response = session.post(TAX_API_URL, data=payload, headers=headers)

    # Detect WAF block
    if "Request Rejected" in response.text:
        logger.error("WAF blocked the request: %s", response.text[:300])
        raise HTTPException(
            status_code=502,
            detail="Tax API request was rejected by the WAF (F5 BIG-IP). Try again later.",
        )

    # Validate JSON
    try:
        data = response.json()
    except Exception:
        logger.error("Non-JSON response: %s", response.text[:500])
        raise HTTPException(
            status_code=502,
            detail="Tax API returned a non-JSON response.",
        )

    if response.status_code != 200:
        logger.error("Tax API error %d: %s", response.status_code, response.text[:500])
        raise HTTPException(
            status_code=502,
            detail=f"Tax API returned HTTP {response.status_code}.",
        )

    return data


# ── Normalize raw API response ────────────────────────────────────────
def normalize_invoice(raw: dict, iic: str) -> dict:
    """
    Extract the fields Spring Boot cares about from the raw tax API JSON.
    """
    seller_name = raw.get("seller", {}).get("name", "Unknown")
    date_time = raw.get("dateTimeCreated", "")
    total_price = raw.get("totalPrice", 0.0)

    # Payment method — the API returns a list like [{"type": "CASH"}]
    payment_methods = raw.get("paymentMethod", [])
    payment_type = "CASH"
    if isinstance(payment_methods, list) and payment_methods:
        payment_type = payment_methods[0].get("typeCode", "CASH")
    elif isinstance(payment_methods, str):
        payment_type = payment_methods

    # Items
    raw_items = raw.get("items", [])
    items = []
    for ri in raw_items:
        items.append({
            "name": ri.get("name", "Unknown"),
            "unitPriceAfterVat": ri.get("unitPriceAfterVat", 0.0),
            "quantity": ri.get("quantity", 1.0),
        })

    return {
        "iic": iic,
        "dateTimeCreated": date_time,
        "sellerName": seller_name,
        "totalPrice": total_price,
        "paymentMethod": payment_type,
        "items": items,
    }


# ── Groq AI categorization ───────────────────────────────────────────
def categorize_items(item_names: list[str], categories: list[str] | None = None) -> dict[str, str]:
    """
    Ask Groq (llama-3.3-70b-versatile) to categorize each item name into
    one of the provided categories.  Uses JSON mode for structured output.
    Falls back to DEFAULT_CATEGORIES if none provided by Spring Boot.
    """
    if not item_names:
        return {}

    active_categories = categories if categories else DEFAULT_CATEGORIES
    categories_str = ", ".join(active_categories)
    products_list = "\n".join(f"- {n}" for n in item_names)

    try:
        completion = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a product categorizer for a personal finance app in Montenegro. "
                        f"Allowed categories: [{categories_str}]. "
                        "Return ONLY a JSON object mapping each product name to its category. "
                        "No extra text, no markdown fences."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Categorize these products:\n{products_list}",
                },
            ],
            temperature=0.0,
            max_completion_tokens=1024,
            response_format={"type": "json_object"},
        )

        raw_text = completion.choices[0].message.content
        mapping = json.loads(raw_text)

        logger.info("Groq categorization result: %s", mapping)
        return mapping

    except Exception as e:
        logger.warning("Groq categorization failed (%s). Defaulting to 'Uncategorized'.", e)
        return {name: "Uncategorized" for name in item_names}


# ── Main endpoint ─────────────────────────────────────────────────────
@app.post("/extract", response_model=ExtractResponse)
async def extract_receipt(request: ExtractRequest):
    """
    Full pipeline:
    1. Fetch raw receipt from Montenegrin Tax API (WAF bypass).
    2. Normalize into flat structure.
    3. Categorize items via Groq.
    4. Return enriched JSON to Spring Boot.
    """
    logger.info("Received extract request: iic=%s tin=%s", request.iic, request.tin)

    # 1. Fetch
    raw_data = fetch_invoice_from_tax_api(request.iic, request.tin, request.dateTimeCreated)

    # 2. Normalize
    normalized = normalize_invoice(raw_data, request.iic)

    # 3. Categorize
    item_names = [item["name"] for item in normalized["items"]]
    categories = categorize_items(item_names, request.categories)

    # Enrich items with categories
    for item in normalized["items"]:
        item["category"] = categories.get(item["name"], "Uncategorized")

    logger.info(
        "Returning %d items for invoice IIC=%s (seller=%s)",
        len(normalized["items"]),
        request.iic,
        normalized["sellerName"],
    )

    return normalized


# ── Health check ──────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok"}


# ── Entrypoint ────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PYTHON_WORKER_PORT", "3501"))
    logger.info("Starting RET Worker on port %d", port)
    uvicorn.run(app, host="0.0.0.0", port=port)
