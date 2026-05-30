"""
Prospect Research Agent — Flask Backend (Gemini Edition)
POST /enrich  →  enrich a company URL
GET  /results →  return all enriched companies
GET  /        →  serve frontend
"""

import os, re, json, time, random, traceback
import requests
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from rapidfuzz import fuzz
from google import genai
from google.genai import types
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv

# ── Load .env file ────────────────────────────────────────────────────────────
load_dotenv()

app = Flask(__name__, template_folder="templates", static_folder="templates")
CORS(app)

# ── Config ───────────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    raise ValueError("❌ GEMINI_API_KEY not found in .env file.")

print(f"✅ Gemini API key loaded: {GEMINI_API_KEY[:8]}...")

client = genai.Client(api_key=GEMINI_API_KEY)
GEMINI_MODEL = "gemini-2.5-flash"

DB_FILE = "results.json"

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}

RELEVANT_KEYWORDS = [
    "about","about-us","company","who-we-are","our-story",
    "contact","contact-us","get-in-touch",
    "services","solutions","what-we-do","offerings","products",
    "team","leadership",
]

IRRELEVANT_PATTERNS = [
    r"/blog/",r"/news/",r"/press/",r"/events/",r"/careers/",r"/jobs/",
    r"/login",r"/signup",r"/register",r"/cart",r"/checkout",
    r"#",r"javascript:",r"mailto:",r"tel:",
    r"\.(pdf|jpg|jpeg|png|gif|svg|zip|doc|docx)$",
]

BOILERPLATE_TAGS    = ["script","style","noscript","iframe","svg","nav","footer","header"]
BOILERPLATE_CLASSES = re.compile(
    r"(cookie|banner|popup|modal|menu|navbar|sidebar|footer|header|advertisement)",
    re.IGNORECASE)

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
PHONE_RE = re.compile(r"(\+?\d[\d\s\-().]{7,}\d)")

# ── DB helpers ────────────────────────────────────────────────────────────────
def load_db() -> list:
    if os.path.exists(DB_FILE):
        with open(DB_FILE) as f:
            return json.load(f)
    return []

def save_db(data: list):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ── Scraping ──────────────────────────────────────────────────────────────────
def safe_get(url, timeout=10):
    for attempt in range(3):
        try:
            time.sleep(random.uniform(0.4, 1.2))
            r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
            r.raise_for_status()
            return r
        except Exception:
            if attempt == 2: return None

def get_sitemap_urls(base):
    for path in ["/sitemap.xml", "/sitemap_index.xml"]:
        r = safe_get(urljoin(base, path))
        if r and r.status_code == 200:
            soup = BeautifulSoup(r.text, "xml")
            locs = [l.get_text() for l in soup.find_all("loc")]
            if locs: return locs
    return []

def get_crawled_urls(base, max_links=60):
    r = safe_get(base)
    if not r: return []
    soup   = BeautifulSoup(r.text, "html.parser")
    domain = urlparse(base).netloc
    seen, links = set(), []
    for a in soup.find_all("a", href=True):
        full = urljoin(base, a["href"].strip())
        if urlparse(full).netloc == domain and full not in seen:
            seen.add(full); links.append(full)
        if len(links) >= max_links: break
    return links

def is_irrelevant(url):
    return any(re.search(p, url, re.IGNORECASE) for p in IRRELEVANT_PATTERNS)

def score_url(url):
    path = urlparse(url).path.lower()
    return max(fuzz.partial_ratio(kw, path) for kw in RELEVANT_KEYWORDS)

def select_relevant(urls, base, top_n=4):
    chosen = [base]
    cands  = sorted(
        [(u, score_url(u)) for u in urls if not is_irrelevant(u) and u != base],
        key=lambda x: x[1], reverse=True)
    chosen += [u for u, _ in cands[:top_n]]
    return list(dict.fromkeys(chosen))

def clean_html(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(BOILERPLATE_TAGS): tag.decompose()
    for tag in soup.find_all(True):
        if tag is None or not hasattr(tag, "attrs") or tag.attrs is None: continue
        cls = " ".join(tag.get("class", []))
        tid = tag.get("id", "")
        if BOILERPLATE_CLASSES.search(cls) or BOILERPLATE_CLASSES.search(tid):
            tag.decompose()
    lines = [ln.strip() for ln in soup.get_text(separator="\n").splitlines()
             if ln.strip() and len(ln.strip()) > 15]
    return "\n".join(lines)

def extract_contacts(text):
    emails = list(dict.fromkeys(EMAIL_RE.findall(text)))
    emails = [e for e in emails if not re.search(r"\.(png|jpg|gif|svg|js|css)$", e, re.I)][:10]
    phones = []
    for ph in PHONE_RE.findall(text):
        d = re.sub(r"\D", "", ph)
        if 7 <= len(d) <= 15: phones.append(ph.strip())
    return {"emails": emails, "phones": list(dict.fromkeys(phones))[:5]}

def scrape_company(url):
    print(f"  [scrape] Starting: {url}")
    base     = url.rstrip("/")
    all_urls = get_sitemap_urls(base) or get_crawled_urls(base)
    targets  = select_relevant(all_urls, base) if all_urls else [base]
    print(f"  [scrape] Pages selected: {targets}")

    combined, contacts = "", {"emails": [], "phones": []}
    for pg in targets:
        r = safe_get(pg)
        if not r: continue
        clean = clean_html(r.text)
        c     = extract_contacts(clean)
        contacts["emails"] += c["emails"]
        contacts["phones"] += c["phones"]
        combined += f"\n\n--- PAGE: {pg} ---\n{clean}"

    contacts["emails"] = list(dict.fromkeys(contacts["emails"]))[:10]
    contacts["phones"] = list(dict.fromkeys(contacts["phones"]))[:5]
    print(f"  [scrape] Done. Text len={len(combined[:8000])} emails={contacts['emails']} phones={contacts['phones']}")
    return {"text": combined[:8000], "contacts": contacts, "url": url}

# ── AI Enrichment (Gemini new SDK) ───────────────────────────────────────────
PROMPT_TEMPLATE = """You are a B2B research assistant. Extract ONLY information EXPLICITLY present in the website text.

STRICT RULES:
- NEVER invent or hallucinate any data.
- If a field is missing, return "" or [].
- For "mail": use ONLY the pre-extracted emails listed below. Do NOT invent new ones.
- For "mobile_number": use ONLY the pre-extracted phones listed below. Do NOT invent new ones.
- Return ONLY valid JSON. No markdown. No explanation.

Website URL: {url}
Pre-extracted emails (use ONLY these): {emails}
Pre-extracted phones (use ONLY these): {phones}

Website text:
{text}

Return a JSON object with EXACTLY these keys:
{{"website_name":"","company_name":"","address":"","mobile_number":"","mail":[],"core_service":"","target_customer":"","probable_pain_point":"","outreach_opener":""}}"""

def enrich_with_ai(scraped):
    c      = scraped["contacts"]
    prompt = PROMPT_TEMPLATE.format(
        url    = scraped["url"],
        emails = c["emails"] if c["emails"] else "none found",
        phones = c["phones"] if c["phones"] else "none found",
        text   = scraped["text"],
    )

    print(f"  [ai] Calling Gemini ({GEMINI_MODEL})...")
    response = client.models.generate_content(
        model   = GEMINI_MODEL,
        contents= prompt,
        config  = types.GenerateContentConfig(
            temperature        = 0.1,
            response_mime_type = "application/json",
        )
    )
    print(f"  [ai] Raw response: {repr(response.text[:200]) if response and response.text else 'EMPTY'}")

    if not response or not response.text:
        raise ValueError("Gemini returned empty response — check API key or quota")

    raw = response.text.strip()
    raw = re.sub(r"^```json\s*", "", raw)
    raw = re.sub(r"\s*```$",     "", raw)

    return json.loads(raw)

DEFAULTS = {
    "website_name": "", "company_name": "", "address": "",
    "mobile_number": "", "mail": [], "core_service": "",
    "target_customer": "", "probable_pain_point": "", "outreach_opener": ""
}

def enrich_company(url):
    scraped = scrape_company(url)
    result  = enrich_with_ai(scraped)
    for k, v in DEFAULTS.items():
        if k not in result: result[k] = v
    if isinstance(result["mail"], str):
        result["mail"] = [result["mail"]] if result["mail"] else []
    return result

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory("templates", "index.html")

@app.route("/enrich", methods=["POST"])
def enrich_route():
    body         = request.get_json(silent=True) or {}
    url          = (body.get("url") or "").strip()
    website_name = (body.get("website_name") or "").strip()

    if not url:
        return jsonify({"error": "Missing 'url' field"}), 400
    if not url.startswith("http"):
        url = "https://" + url

    print(f"\n{'='*50}\n[enrich] Request for: {url}\n{'='*50}")

    try:
        data = enrich_company(url)
        if website_name:
            data["website_name"] = website_name
        data["_source_url"] = url

        db       = load_db()
        existing = next((i for i, r in enumerate(db) if r.get("_source_url") == url), None)
        if existing is not None:
            db[existing] = data
        else:
            db.append(data)
        save_db(db)

        print(f"  [enrich] ✅ Success: {data.get('company_name')}")
        return jsonify(data), 200

    except Exception as e:
        # Print FULL traceback to terminal so we can see exactly what failed
        print(f"\n  [enrich] ❌ ERROR: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/results", methods=["GET"])
def results_route():
    return jsonify(load_db()), 200

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
