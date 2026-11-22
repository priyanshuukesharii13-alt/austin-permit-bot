import os
import re
import smtplib
import requests
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from google import genai

API_URL = "https://data.austintexas.gov/resource/3syk-w9eu.json"


def fetch_leads():
    now = datetime.now(timezone.utc)
    # Monday ko last 3 din, baaki weekdays ko last 2 din check karo; weekend test ke liye fallback 7 din
    initial_days = 3 if now.weekday() == 0 else 2
    target_work = ("new", "remodel", "demolition", "addition", "finish out")

    for days_back in (initial_days, 7):
        start_date = (now - timedelta(days=days_back)).strftime("%Y-%m-%dT00:00:00.000")
        params = {
            "$where": f"issue_date >= '{start_date}' AND permit_class_mapped = 'Commercial'",
            "$order": "issue_date DESC",
            "$limit": 150,
        }
        resp = requests.get(API_URL, params=params, timeout=30)
        resp.raise_for_status()
        raw = resp.json()

        priority = []
        others = []
        for item in raw:
            work_class = str(item.get("work_class") or "").strip()
            permit_type = str(item.get("permit_type_desc") or "").strip()
            desc = str(item.get("description") or "").strip()

            if not any(k in work_class.lower() or k in desc.lower() for k in target_work):
                continue

            entry = (
                f"- Date: {str(item.get('issue_date', ''))[:10]} | "
                f"Type: {work_class} ({permit_type}) | "
                f"Address: {item.get('original_address1', 'N/A')}, Austin, TX {item.get('original_zip', '')} | "
                f"Contractor: {item.get('contractor_company_name', 'N/A')} ({item.get('contractor_phone', 'N/A')}) | "
                f"Valuation: {item.get('total_job_valuation', 'N/A')} | "
                f"Desc: {desc[:220]}"
            )

            if "building" in permit_type.lower() or "demolition" in permit_type.lower():
                priority.append(entry)
            else:
                others.append(entry)

        combined = (priority + others)[:20]
        if len(combined) >= 5 or days_back == 7:
            print(f"Fetched {len(combined)} leads (lookback: {days_back} days)")
            return "\n".join(combined)

    return ""


def generate_html_email(leads_text):
    api_key = os.environ["GEMINI_API_KEY"].strip()
    client = genai.Client(api_key=api_key)

    today_str = datetime.now(timezone.utc).strftime("%B %d, %Y")
    prompt = f"""You are a B2B commercial construction lead researcher in Austin, Texas.
Select ONLY the TOP 5 highest-value commercial projects below that need roll-off dumpster rentals, heavy debris removal, or post-construction cleaning.
Ignore minor electrical/plumbing/sign permits unless tied to major new building construction or full demolition.
Use ONLY facts present in the raw data below (do not invent addresses, valuations, or phone numbers).

Output format rules (STRICT):
- Line 1 MUST start with: SUBJECT: <short catchy email subject with date {today_str}>
- Starting from Line 2, output ONLY clean, responsive HTML email body (inline CSS, clean typography, card/list styling, easy to read on Gmail/Outlook mobile & desktop).
- Do NOT wrap the output in markdown code blocks like ```html.
- Include for each of the 5 leads:
  1. Project Title
  2. Permit Date & Full Address
  3. Contractor Company & Phone
  4. Why It's Hot (1 crisp sentence)

RAW DATA:
{leads_text}
"""

    response = client.models.generate_content(
        model="models/gemini-3.0-flash",
        contents=prompt,
    )
    text = (response.text or "").strip()
    text = re.sub(r"^```html\s*|^```\s*|```$", "", text, flags=re.IGNORECASE | re.MULTILINE).strip()

    lines = text.splitlines()
    subject = f"Austin Commercial Permit Alerts - {today_str}"
    html_body = text

    if lines and lines[0].upper().startswith("SUBJECT:"):
        subject = lines[0].split(":", 1)[1].strip()
        html_body = "\n".join(lines[1:]).strip()

    return subject, html_body


def send_emails(subject, html_body):
    sender = os.environ["SENDER_EMAIL"].strip()
    app_password = os.environ["SENDER_APP_PASSWORD"].replace(" ", "").strip()
    raw_receivers = os.environ["RECEIVER_EMAILS"]
    recipients = [e.strip() for e in raw_receivers.split(",") if e.strip()]

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, app_password)
        for recipient in recipients:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"Austin Permit Alerts <{sender}>"
            msg["To"] = recipient
            msg.attach(MIMEText(html_body, "html"))
            server.sendmail(sender, recipient, msg.as_string())
            print(f"Email sent to: {recipient}")


if __name__ == "__main__":
    leads = fetch_leads()
    if not leads:
        print("No commercial leads found today. Skipping email.")
    else:
        subj, body = generate_html_email(leads)
        print(f"Generated Subject: {subj}")
        send_emails(subj, body)
        print("Autopilot run complete!")