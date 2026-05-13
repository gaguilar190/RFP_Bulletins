# RFP Grid Agent Deployment Guide

This app is a Streamlit app. It reads uploaded files in the web session, runs the matching/pricing logic, and returns an Excel workbook.

## Recommended deployment choice

Use local or private company deployment for real RFPs and pricing sheets. Your master pricing workbook and client briefs are sensitive.

## Option A: Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

Open the local URL shown in Terminal.

## Option B: Run with Docker locally or on an internal server

Build the image:

```bash
docker build -t rfp-grid-agent .
```

Run the app:

```bash
docker run --rm -p 8501:8501 rfp-grid-agent
```

Open:

```text
http://localhost:8501
```

On an internal server, replace localhost with the server hostname or IP address.

## Option C: Deploy to Streamlit Community Cloud

1. Create a private GitHub repo.
2. Push this code to the repo.
3. Do not commit master pricing workbooks or RFP files.
4. Go to Streamlit Community Cloud.
5. Connect GitHub.
6. Choose your repo, branch, and `app.py`.
7. Deploy.

Note: Local Ollama will not run inside Streamlit Community Cloud. Leave the Ollama checkbox off unless you customize the app to call a hosted LLM endpoint.

## Security checklist

- Keep the GitHub repo private.
- Do not upload your real master pricing workbook to GitHub.
- Do not place client RFPs in the repo.
- Use uploaded files through the app instead.
- For the safest workflow, run locally or on a company server behind VPN.
