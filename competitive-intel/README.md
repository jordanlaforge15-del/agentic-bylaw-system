# Competitive Intelligence

Version-controlled competitor database and analysis framework for ABS.

## Structure

```
competitive-intel/
├── config.yaml          # Our positioning + analysis dimensions
├── competitors/         # One YAML file per competitor
│   └── <slug>.yaml
├── reports/             # Generated analysis reports
│   └── YYYY-MM-DD.md
├── schema.py            # Pydantic schema + CLI validator
└── README.md
```

## Competitor file schema

Each file in `competitors/` follows this structure:

```yaml
name: "Company Name"
slug: "company-name"           # Matches filename (without .yaml)
url: "https://example.com"
category: direct | adjacent | emerging
status: active | acquired | defunct
discovered: "2026-05-27"
last_analyzed: "2026-05-27"

description: "One-line description of what they do."

product:
  type: saas | consulting | platform | marketplace | open-source
  target_market: [developers, planners, municipalities, citizens, lawyers]
  geography: [list of regions/countries]
  jurisdictions: [specific municipalities or states if known]
  pricing_model: subscription | per-query | freemium | enterprise | unknown
  pricing_range: "$X-$Y/mo or descriptive"
  key_features:
    - "Feature description"

positioning:
  tagline: "Their tagline"
  differentiators:
    - "What they claim sets them apart"
  weaknesses:
    - "Known gaps or limitations"

funding:
  stage: pre-seed | seed | series-a | series-b | growth | bootstrapped | public | unknown
  total_raised: "$Xm or unknown"
  last_round_date: "YYYY-MM-DD or null"
  notable_investors: []

signals:
  - date: "YYYY-MM-DD"
    type: product-launch | feature-update | funding | partnership | hiring | press | pricing-change | geographic-expansion | acquisition | regulatory
    summary: "What happened"
    source_url: "URL"

threat_assessment:
  level: high | medium | low
  rationale: "Why this threat level"
  overlap_areas: ["list of areas where we compete"]
  watch_triggers: ["events that would escalate threat level"]
```

## Running the monitor

The competitive-monitor skill is invoked via Claude Code:

- Say "competitive analysis", "run competitive monitor", "competitor scan", or invoke `/competitive-monitor`
- The skill reads the competitor database, searches for recent signals, updates the YAML files, and generates a dated report in `reports/`.

## Adding a competitor manually

1. Create `competitors/<slug>.yaml` following the schema above.
2. Run `python competitive-intel/schema.py` to validate.
3. Commit the file.

## Validation

```bash
# Validate all competitor files against the schema
python competitive-intel/schema.py

# Or via pytest
.venv/bin/pytest tests/competitive_intel/
```
