# Security Rules

## What is NEVER committed to this repo

| File / Path | Why |
|---|---|
| `knowledge/credentials/` | Contains Google service account private key |
| `knowledge/rules/` | Contains client-specific compliance rules |
| `knowledge/patterns/` | Contains client-specific review patterns |
| `knowledge/process/` | Contains client-specific SOPs |
| `knowledge/memory/` | Contains confidence scores, fix examples, URL registry, Drive folder IDs |
| `feedback_library/` | Contains client-specific compliance feedback and learned patterns |
| `.env` | Contains API keys |
| `output/` | Contains generated feedback documents with client content |
| `legacy/` | Contains old scripts with client references |

## Setup (local only — never share)

Copy the example files and fill in your values:

```bash
cp .env.example .env
cp knowledge/rules/compliance_rules.example.md knowledge/rules/compliance_rules.md
cp knowledge/memory/drive_config.example.json knowledge/memory/drive_config.json
```

Then add your credentials:

```
ANTHROPIC_API_KEY=sk-ant-...
```

Place your Google service account JSON at:
```
knowledge/credentials/google_service_account.json
```

## API Keys

- `ANTHROPIC_API_KEY` — set in `~/.zshrc` or `.env`, never hardcoded
- Google service account — stored locally in `knowledge/credentials/`, gitignored

## If a secret is accidentally committed

1. Immediately rotate the key (Anthropic console / Google Cloud console)
2. Remove from git history: `git filter-branch` or BFG Repo Cleaner
3. Force push to invalidate cached versions
