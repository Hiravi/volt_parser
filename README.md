# Volt Parser ⚡️

Extract company names from un-structured text, enrich them with **WikiData / Wikipedia
metadata**, and (optionally) fill missing details via Anthropic Claude’s real-time **web-search tool**.  
Outputs a single, schema-validated JSON file you can feed straight into downstream
pipelines or BI dashboards.

## 🖇️ Quick-start
```bash
### 1. Clone the repo


git clone https://github.com/<your-org>/volt_parser.git
cd volt_parser

### 2. Create and activate a virtual-env (Python ≥ 3.9)
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows (PowerShell)
.venv\Scripts\Activate.ps1


### 3. Install the package
pip install -e ".[llm]"


### 4. Download the spaCy model
python -m spacy download en_core_web_trf


### 5. Set environment variables
export ANTHROPIC_API_KEY="sk-ant-api-key..."
(Windows CMD → set, PowerShell → $Env:ANTHROPIC_API_KEY="...")


## 🚀 Usage
python -m volt_parser.cli note.md -o result.json --llm-fallback
