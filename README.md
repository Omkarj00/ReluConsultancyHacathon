# Relu Consultancy Hackathon 
> AI-powered B2B company enrichment: scrape → clean → enrich → display

---

## Project Structure
```
├── prospect_research_colab.py   # Subtask 1 — Colab notebook code
├── server.py                    # Subtask 2 — Flask backend
├── templates/
│   └── index.html               # Frontend UI
├── requirements.txt
├── Procfile                     # For Render / Railway deployment
└── results.json                 # Auto-created DB file
```

---

## Subtask 1 — Google Colab

1. Open the provided Colab template link
2. Copy-paste the content of `prospect_research_colab.py` into the cells
3. Install dependencies: `!pip install requests beautifulsoup4 anthropic rapidfuzz lxml -q`
4. Set your `API_KEY` at the top
5. Run all cells — it will prompt for a JSON array of URLs
6. Paste input like: `["https://example.com", "https://another.com"]`
7. Output is printed and saved to `results.json`

---

## Subtask 2 — Local Development

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Set API key
```bash
export ANTHROPIC_API_KEY="your-key-here"
```

### 3. Run
```bash
python server.py
```

Visit: http://localhost:5000

---

## Deployment (Render — Free Tier)

1. Push code to GitHub
2. Create new **Web Service** on [render.com](https://render.com)
3. Set:
   - **Build command**: `pip install -r requirements.txt`
   - **Start command**: `gunicorn server:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120`
4. Add environment variable: `ANTHROPIC_API_KEY = your-key`
5. Deploy — get your public URL

---

## API Reference

### `POST /enrich`
```json
// Request
{ "url": "https://company.com", "website_name": "Company Display Name" }

// Response
{
  "website_name": "...",
  "company_name": "...",
  "address": "...",
  "mobile_number": "...",
  "mail": ["email@company.com"],
  "core_service": "...",
  "target_customer": "...",
  "probable_pain_point": "...",
  "outreach_opener": "..."
}
```

### `GET /results`
Returns array of all enriched company profiles.

---

## How it Works

### Smart Scraping (3 approaches)
1. **Sitemap** — tries `/sitemap.xml`, `/sitemap_index.xml`
2. **Homepage crawl** — extracts all internal links
3. **Homepage only** — fallback if all else fails

### Fuzzy URL Selection
Uses `rapidfuzz` to score each URL against keywords like `about`, `contact`, `services` — picks the top 4-5 most relevant pages.

### Token Optimization
- Strips script/style/nav/footer tags
- Removes boilerplate elements by class/ID pattern matching
- Drops lines under 15 chars (nav remnants)
- Truncates to 7,000 chars before sending to AI

### Anti-Hallucination
- Emails and phone numbers are pre-extracted with regex
- The AI is told to use **only** those pre-extracted values
- Missing fields return `""` or `[]` — never fabricated data

---

## Scoring Notes
- ✅ 3 scraping fallback approaches
- ✅ Fuzzy URL matching (rapidfuzz)
- ✅ HTML cleaning + token optimization
- ✅ Anti-hallucination prompt engineering
- ✅ Schema stability (never breaks on missing fields)
- ✅ Loading state with step-by-step status indicator (+10 bonus)
- ✅ Results table + enrich section
- ✅ Website name input field
- ✅ Handles mail[] as array in UI
- ✅ Rate limiting via random delays + retry logic
