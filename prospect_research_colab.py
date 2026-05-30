# ================================
# 🏆 Hackathon: Prospect Research Agent
# Subtask 1 — Research Pipeline
# ================================

# ========= 1. INSTALL DEPENDENCIES =========
# !pip install requests beautifulsoup4 anthropic rapidfuzz -q

import os
import re
import json
import time
import random
import requests
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from rapidfuzz import fuzz
import google.generativeai as genai
from dotenv import load_dotenv

# ========= 2. CONFIG =========
load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")

genai.configure(api_key=API_KEY)

model = genai.GenerativeModel(
    "gemini-2.5-flash",
    generation_config={
        "temperature": 0.1,
        "response_mime_type": "application/json"
    }
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

RELEVANT_KEYWORDS = [
    "about", "about-us", "company", "who-we-are", "our-story",
    "contact", "contact-us", "get-in-touch", "reach-us",
    "services", "solutions", "what-we-do", "offerings", "products",
    "team", "leadership", "management",
]

IRRELEVANT_PATTERNS = [
    r"/blog/", r"/news/", r"/press/", r"/events/", r"/careers/",
    r"/jobs/", r"/login", r"/signup", r"/register", r"/cart",
    r"/checkout", r"#", r"javascript:", r"mailto:", r"tel:",
    r"\.(pdf|jpg|jpeg|png|gif|svg|zip|doc|docx)$",
]

# ========= 3. SCRAPING HELPERS =========

def safe_get(url: str, timeout: int = 10) -> requests.Response | None:
    """HTTP GET with retries and polite delays."""
    for attempt in range(3):
        try:
            time.sleep(random.uniform(0.5, 1.5))
            resp = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            if attempt == 2:
                print(f"  ⚠ Failed to fetch {url}: {e}")
    return None


def get_sitemap_urls(base_url: str) -> list[str]:
    """Try to pull links from sitemap.xml or sitemap_index.xml."""
    urls = []
    for path in ["/sitemap.xml", "/sitemap_index.xml", "/sitemap/"]:
        resp = safe_get(urljoin(base_url, path))
        if resp and resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "xml")
            urls = [loc.get_text() for loc in soup.find_all("loc")]
            if urls:
                print(f"  ✓ Sitemap found: {len(urls)} URLs")
                break
    return urls


def get_crawled_urls(base_url: str, max_links: int = 60) -> list[str]:
    """Crawl homepage and collect internal links."""
    resp = safe_get(base_url)
    if not resp:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    domain = urlparse(base_url).netloc
    seen = set()
    links = []
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        full = urljoin(base_url, href)
        if urlparse(full).netloc == domain and full not in seen:
            seen.add(full)
            links.append(full)
        if len(links) >= max_links:
            break
    return links


def is_irrelevant(url: str) -> bool:
    for pat in IRRELEVANT_PATTERNS:
        if re.search(pat, url, re.IGNORECASE):
            return True
    return False


def score_url(url: str) -> int:
    """Fuzzy-match URL path against RELEVANT_KEYWORDS."""
    path = urlparse(url).path.lower()
    best = max(fuzz.partial_ratio(kw, path) for kw in RELEVANT_KEYWORDS)
    return best


def select_relevant_urls(urls: list[str], base_url: str, top_n: int = 5) -> list[str]:
    """
    Score and rank URLs, always including the homepage,
    then pick the top_n most relevant.
    """
    chosen = [base_url]
    candidates = [(u, score_url(u)) for u in urls if not is_irrelevant(u) and u != base_url]
    candidates.sort(key=lambda x: x[1], reverse=True)
    chosen += [u for u, _ in candidates[:top_n]]
    return list(dict.fromkeys(chosen))  # deduplicate, preserve order


# ========= 4. HTML → CLEAN TEXT =========

BOILERPLATE_TAGS = ["script", "style", "noscript", "iframe", "svg", "nav", "footer", "header"]
BOILERPLATE_CLASSES = re.compile(
    r"(cookie|banner|popup|modal|menu|navbar|sidebar|footer|header|advertisement|promo)",
    re.IGNORECASE,
)


def clean_html(html: str) -> str:
    """Strip boilerplate HTML and return plain text (token-optimized)."""
    soup = BeautifulSoup(html, "html.parser")

    # Remove obvious noise tags
    for tag in soup(BOILERPLATE_TAGS):
        tag.decompose()

    # Remove elements with boilerplate class/id names
    for tag in soup.find_all(True):
        cls = " ".join(tag.get("class", []))
        tid = tag.get("id", "")
        if BOILERPLATE_CLASSES.search(cls) or BOILERPLATE_CLASSES.search(tid):
            tag.decompose()

    text = soup.get_text(separator="\n")
    # Collapse whitespace
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    # Drop very short lines (likely nav remnants)
    lines = [ln for ln in lines if len(ln) > 15]
    return "\n".join(lines)


def chunk_text(text: str, max_chars: int = 6000) -> str:
    """Trim text to max_chars to save tokens."""
    return text[:max_chars]


# ========= 5. EXTRACT RAW CONTACT DATA =========

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
PHONE_RE = re.compile(
    r"(\+?\d[\d\s\-().]{7,}\d)"
)


def extract_contact_hints(text: str) -> dict:
    """Pre-extract emails and phones to help the AI and prevent hallucination."""
    emails = list(dict.fromkeys(EMAIL_RE.findall(text)))
    # Filter common false positives
    emails = [e for e in emails if not re.search(r"\.(png|jpg|gif|svg|js|css|php)$", e, re.I)]

    raw_phones = PHONE_RE.findall(text)
    phones = []
    for ph in raw_phones:
        digits = re.sub(r"\D", "", ph)
        if 7 <= len(digits) <= 15:
            phones.append(ph.strip())
    phones = list(dict.fromkeys(phones))

    return {"emails": emails[:10], "phones": phones[:5]}


# ========= 6. SCRAPE A SINGLE COMPANY =========

def scrape_company(url: str) -> dict:
    """
    Multi-approach scraper:
      Approach A — Sitemap → select relevant pages → scrape
      Approach B — Homepage crawl → select relevant pages → scrape
      Approach C — Homepage only (fallback)
    Returns {"text": <combined clean text>, "contacts": {...}}
    """
    base_url = url.rstrip("/")
    print(f"\n🔍 Scraping: {base_url}")

    # Approach A: Sitemap
    all_urls = get_sitemap_urls(base_url)
    source = "sitemap"

    if not all_urls:
        # Approach B: Crawl homepage
        print("  → No sitemap, crawling homepage...")
        all_urls = get_crawled_urls(base_url)
        source = "crawl"

    if all_urls:
        target_urls = select_relevant_urls(all_urls, base_url, top_n=4)
    else:
        # Approach C: Homepage only
        target_urls = [base_url]
        source = "homepage-only"

    print(f"  → Source: {source} | Pages to scrape: {len(target_urls)}")

    combined_text = ""
    all_contacts = {"emails": [], "phones": []}

    for page_url in target_urls:
        resp = safe_get(page_url)
        if not resp:
            continue
        clean = clean_html(resp.text)
        contacts = extract_contact_hints(clean)
        all_contacts["emails"] += contacts["emails"]
        all_contacts["phones"] += contacts["phones"]
        combined_text += f"\n\n--- PAGE: {page_url} ---\n{clean}"

    # Deduplicate contacts
    all_contacts["emails"] = list(dict.fromkeys(all_contacts["emails"]))[:10]
    all_contacts["phones"] = list(dict.fromkeys(all_contacts["phones"]))[:5]

    # Trim for token budget
    optimized_text = chunk_text(combined_text, max_chars=7000)
    print(f"  → Text length after optimization: {len(optimized_text)} chars")

    return {"text": optimized_text, "contacts": all_contacts, "url": url}


# ========= 7. AI ENRICHMENT =========

SYSTEM_PROMPT = """You are a B2B research assistant. Your job is to extract ONLY information that is EXPLICITLY present in the provided website text.

STRICT RULES:
- NEVER invent, guess, or hallucinate any data.
- If a field is not present in the text, return "" or [] — never fabricate.
- For emails and phones: use ONLY the pre-extracted values provided. Do not create new ones.
- For outreach_opener: write one personalized, factual sentence referencing the company's actual service.
- Return ONLY a valid JSON object, no markdown, no explanation."""

USER_PROMPT_TEMPLATE = """Website URL: {url}

Pre-extracted contact data (use ONLY these, do not invent others):
Emails found: {emails}
Phones found: {phones}

Scraped website text (may be truncated):
{text}

Extract and return a JSON object with EXACTLY these keys:
{{
  "website_name": "short display name from the site",
  "company_name": "full legal or trading name",
  "address": "full address if found, else ''",
  "mobile_number": "best phone number if found, else ''",
  "mail": ["list", "of", "emails", "found"],
  "core_service": "1–2 sentence description of main service/product",
  "target_customer": "who they serve (industry/company size/persona)",
  "probable_pain_point": "likely business challenge their customers face",
  "outreach_opener": "one personalized cold-outreach sentence referencing their actual service"
}}"""


def enrich_with_ai(scraped: dict) -> dict:
    """Send optimized text to Gemini and parse JSON response."""

    contacts = scraped["contacts"]

    prompt = USER_PROMPT_TEMPLATE.format(
        url=scraped["url"],
        emails=contacts["emails"] if contacts["emails"] else "none found",
        phones=contacts["phones"] if contacts["phones"] else "none found",
        text=scraped["text"],
    )

    full_prompt = f"""
{SYSTEM_PROMPT}

{prompt}
"""

    response = model.generate_content(
        full_prompt,
        generation_config={
            "temperature": 0.1,
            "max_output_tokens": 1000,
        }
    )

    raw = response.text.strip()

    # Remove markdown fences if Gemini returns them
    raw = re.sub(r"^```json\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print("Gemini returned invalid JSON:")
        print(raw)
        raise


# ========= 8. REQUIRED FUNCTION =========

def enrich_company(url: str) -> dict:
    """
    Input: Company URL
    Output: Structured company profile (STRICT FORMAT)
    """
    scraped = scrape_company(url)
    result = enrich_with_ai(scraped)

    # Ensure schema stability — fill missing keys
    defaults = {
        "website_name": "",
        "company_name": "",
        "address": "",
        "mobile_number": "",
        "mail": [],
        "core_service": "",
        "target_customer": "",
        "probable_pain_point": "",
        "outreach_opener": "",
    }
    for key, default in defaults.items():
        if key not in result:
            result[key] = default

    # Enforce mail as list
    if isinstance(result["mail"], str):
        result["mail"] = [result["mail"]] if result["mail"] else []

    return result


# ========= 9. MAIN EXECUTION =========

if __name__ == "__main__":
    print("=" * 50)
    print("🚀 Prospect Research Agent")
    print("=" * 50)

    # 👉 Judges paste their URL array here
    raw_input = input("\nPaste the array of URLs (JSON format): ").strip()

    try:
        urls = json.loads(raw_input)
        if not isinstance(urls, list):
            raise ValueError("Input must be a JSON array")
    except (json.JSONDecodeError, ValueError) as e:
        print(f"❌ Invalid input: {e}")
        print('Expected format: ["https://example.com", "https://another.com"]')
        exit(1)

    print(f"\n✅ Processing {len(urls)} URL(s)...\n")

    results = []
    for url in urls:
        try:
            data = enrich_company(url)
            results.append(data)
            print(f"  ✓ Done: {url}")
        except Exception as e:
            print(f"  ✗ Error processing {url}: {e}")
            # Return safe fallback object instead of breaking
            results.append({
                "website_name": url,
                "company_name": "",
                "address": "",
                "mobile_number": "",
                "mail": [],
                "core_service": "",
                "target_customer": "",
                "probable_pain_point": "",
                "outreach_opener": "",
                "_error": str(e),
            })

    # Save results
    output_path = "results.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n💾 Saved to {output_path}")

    # Print for evaluation
    print("\n=== FINAL OUTPUT ===\n")
    print(json.dumps(results, indent=2))
