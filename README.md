# RFP Grid Agent

A local, free-first RFP assistant for out-of-home proposal grids.

This app uses your master pricing workbook as the source of truth, then helps you:

- normalize board/unit data
- extract RFP requirements from text
- match inventory to the brief
- calculate contracted-period pricing
- apply rate increases or discounts
- calculate straight-line distance to a POI
- fill an agency grid template
- create review, pricing audit, distance audit, excluded units, and unmapped column tabs

## Important design rule

The LLM does not invent prices, impressions, availability, or distances.

The LLM only reads messy RFP language and turns it into structured requirements. The actual matching, pricing, distance, and grid fill logic is rule-based Python.

## Repo structure

```text
rfp_grid_agent/
  app.py
  requirements.txt
  config/
    column_aliases.json
    pricing_rules.json
    audience_rules.json
  examples/
    sample_requirements.json
  scripts/
    run_agent.py
    inspect_master.py
  src/
    distance.py
    grid_writer.py
    inventory.py
    matcher.py
    pricing.py
    requirements_extractor.py
    utils.py
  outputs/
```

## What your current master workbook supports

Your master workbook's `Master List ` sheet uses row 2 as the header row and includes columns such as:

- Unit #
- Geopath Frame ID
- Target Audience Index
- Freeway/Street
- Description
- Rate Card
- Negotiated Rate
- Location
- City
- Zip Code
- Line
- Facing
- Reads
- Size
- Reach Net
- Target In-Market Rating Points
- In-Market Reach
- In-Market Frequency
- Geopath 18+ weekly impressions
- Geopath 18+ 4 week impressions
- Latitude (decimal)
- Longitude (decimal)
- Comments

The code in this repo is already mapped to those fields.

## Step 1: Install tools

Install Python 3.11 or newer.

Create a virtual environment:

```bash
python -m venv .venv
```

Activate it.

Mac:

```bash
source .venv/bin/activate
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Install dependencies:

```bash
pip install -r requirements.txt
```

## Step 2: Optional local AI setup with Ollama

Install Ollama, then pull a model:

```bash
ollama pull llama3.1:8b
```

The app works without Ollama if you manually paste/edit requirements JSON.

## Step 3: Run the app

```bash
streamlit run app.py
```

Upload:

1. your master pricing workbook
2. an RFP brief as text, PDF, or pasted text
3. optional blank agency grid

Then review requirements, run matching, calculate pricing, and download the output workbook.

## Step 4: Run from command line instead

```bash
python scripts/run_agent.py \
  --master "path/to/Copy of All units pricing-NEW PRICING 3.25.26(7).xlsx" \
  --requirements examples/sample_requirements.json \
  --output outputs/test_output.xlsx
```

With a blank agency grid template:

```bash
python scripts/run_agent.py \
  --master "path/to/master.xlsx" \
  --requirements examples/sample_requirements.json \
  --template "path/to/blank_rfp_grid.xlsx" \
  --output outputs/filled_rfp_grid.xlsx
```

## Step 5: Push to GitHub

From inside this folder:

```bash
git init
git add .
git commit -m "Initial RFP grid agent"
```

Create a new empty repo on GitHub, then connect and push:

```bash
git remote add origin https://github.com/YOUR-USERNAME/rfp-grid-agent.git
git branch -M main
git push -u origin main
```

## How to improve the agent over time

When a new RFP grid has a weird column name, add it to `config/column_aliases.json`.

When a pricing rule changes, update `config/pricing_rules.json`.

When the system needs to understand a new audience target, update `config/audience_rules.json`.

When a board is directional to a location like LAX, SoFi, airport traffic, downtown traffic, etc., add those tags into the `directional_tags` field in the master sheet.

## Data fields to add next to your master sheet

Add these columns when you are ready:

```text
market
state
availability_start
availability_end
status
rate_basis
production_cost
install_cost
directional_tags
qr_code_allowed
spot_length_seconds
spots_per_loop
creative_specs
```

The agent can run without them, but it will flag more rows for review.

## Fixed business rules in this version

This version includes Cori's required output rules:

- `Vendor` / `Media Owner` always outputs `Bulletin Displays`.
- `Availability` always outputs the campaign date range from the requirements JSON.
- `Illuminated` always outputs `Yes`.
- `4 Week Media Cost` always outputs the `Negotiated Rate` from the master sheet.
- `Installation Cost` defaults to `850`, except unit `01134` / `01134-SF`, which outputs `1600`.
- `Production Cost` defaults to `750`, except unit `01134` / `01134-SF`, which outputs `950`.
- `Is Production Forced` always outputs `No`.
- `Taxes` always outputs `0`.
- `A18+ 4-wk Reach (%)` is derived from `In-Market Reach x 4`.
- `A18+ 4-wk Freq (x)` is derived from `In-Market Frequency x 4`.
- Market matching now uses structured market/city fields only, so a San Francisco-only brief will not backfill Los Angeles boards just to reach the requested unit count.
- Non-Ollama extraction now has a simple heuristic mode that can detect major markets like San Francisco, Los Angeles, Sacramento, and Fresno from brief text.

You can edit the fixed cost overrides in:

```text
config/business_rules.json
```

