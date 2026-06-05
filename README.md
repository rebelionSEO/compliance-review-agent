# Compliance Review Agent

AI-assisted pre-screening system for marketing content compliance. Reviews blogs, landing pages, email copy, paid media assets, and video scripts against regulatory standards before they enter the official compliance review workflow.

Built with Python + Anthropic Claude API.

---

## What it does

- Pulls content directly from Google Drive (no manual uploads)
- Reviews against compliance rules, real-world patterns, and official review SOPs
- Applies fixes automatically using surgical text replacement
- Uploads revised content + feedback documents back to Google Drive as proper Google Docs
- Learns from every QA decision — confidence scoring improves over time

## Content types supported

| Input | Type | Output |
|---|---|---|
| Google Doc URL / `.txt` | Blog, article, landing page | Revised Google Doc + Feedback Doc |
| Google Sheets / `.csv` | Paid media, ad copy, video scripts | Column M compliance review |
| PDF with feedback | Ops compliance feedback | Updated `knowledge/patterns/` |

## Architecture

```
run.py                    # Single entry point
agents/
  reviewer.py             # ReviewAgent — flags issues
  fixer.py                # FixAgent + VerifyAgent (combined, one API call)
  delivery.py             # Uploads to Google Drive as Google Docs
  context.py              # Route-based context loading (token optimization)
tools/
  orchestrator.py         # Coordinates full pipeline
  qa.py                   # Human QA validation + confidence scoring
knowledge/
  rules/                  # Compliance rules KB (gitignored — client-specific)
  patterns/               # Review patterns from real compliance feedback (gitignored)
  process/                # Official review SOPs (gitignored)
  memory/                 # Confidence scores, fix examples, URL registry (gitignored)
  credentials/            # Google service account (gitignored)
```

## Setup

### 1. Install dependencies

```bash
pip install anthropic google-auth google-api-python-client
brew install poppler  # for learn.py PDF extraction
```

### 2. Set your Anthropic API key

```bash
echo 'export ANTHROPIC_API_KEY="sk-ant-..."' >> ~/.zshrc
source ~/.zshrc
```

### 3. Add your compliance knowledge base

```bash
cp knowledge/rules/compliance_rules.example.md knowledge/rules/compliance_rules.md
# Edit compliance_rules.md with your client's rules
```

### 4. Configure Google Drive output folders

```bash
cp knowledge/memory/drive_config.example.json knowledge/memory/drive_config.json
# Edit drive_config.json with your folder IDs
```

Place your Google service account JSON at:
```
knowledge/credentials/google_service_account.json
```

Share your output folders with the service account email.

---

## Usage

```bash
# Review a blog article (Google Doc URL)
# Pull via Drive MCP in Claude Code, then:
python3 run.py article.txt

# Review paid media (CSV export from Google Sheets)
python3 paid_media.py ads.csv

# QA a draft review
python3 run.py --qa "output/Article – Compliance Feedback – CCA.json"

# Revision check (v2 against v1 flags)
python3 run.py article_v2.txt --revision-of "output/article_v1 – CCA.json"

# Process Ops feedback PDFs → update patterns
python3 tools/learn.py                    # scans Google Drive folder
python3 tools/learn.py feedback.pdf       # single PDF
```

---

## How it learns

Every QA decision updates `knowledge/memory/confidence.json`. Patterns confirmed 5+ times at 80%+ approval rate are auto-approved in future sessions. Rejected flags are logged to `knowledge/memory/false_positives.md`. Confirmed fix pairs accumulate in `knowledge/memory/fix_examples.json` — after enough examples, the fixer uses real patterns rather than rules alone.

---

## Security

See `SECURITY.md`. Client-specific knowledge base, credentials, and output are all gitignored. Only generic framework code is committed.
