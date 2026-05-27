import json
import os
import re
import smtplib
from email.mime.text import MIMEText
from pathlib import Path

import requests

# -------- CONFIG --------
SERIAL_NUMBER = "99757535"
MATTER_REFERENCE_NUMBER = "CF-0002252-US"
STATUS_FILE = Path("status.json")

USPTO_API_KEY = os.getenv("USPTO_API_KEY")
EMAIL_FROM = os.getenv("EMAIL_FROM")
EMAIL_TO = os.getenv("EMAIL_TO")            # comma-separated allowed
SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
# ------------------------


def tsdr_case_url(serial: str) -> str:
    return (
        "https://tsdr.uspto.gov/"
        f"#caseNumber={serial}"
        "&caseSearchType=US_APPLICATION"
        "&caseType=DEFAULT"
        "&searchType=statusSearch"
    )


def load_last_status():
    if not STATUS_FILE.exists():
        return None
    try:
        with STATUS_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("last_status")
    except Exception:
        return None


def save_last_status(status_text: str):
    with STATUS_FILE.open("w", encoding="utf-8") as f:
        json.dump({"last_status": status_text}, f, indent=2)


def fetch_current_status():
    base_url = "https://tsdrapi.uspto.gov/ts/cd/caseMultiStatus/sn"
    headers = {
        "USPTO-API-KEY": USPTO_API_KEY,
        "Accept": "application/json",
    }
    params = {"ids": SERIAL_NUMBER}

    resp = requests.get(base_url, params=params, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    try:
        tm = data["transactionList"][0]["trademarks"][0]
        status = tm.get("status", {})

        mark = status.get("markElement") or ""
        tm5_desc = status.get("tm5StatusDesc") or ""
        ext_desc = status.get("extStatusDesc") or ""
        code = status.get("status")
        status_date = status.get("statusDate") or ""
        owner_name = extract_owner_name(data, SERIAL_NUMBER)

        parts = []
        if mark:
            parts.append(f"Mark: {mark}")
        if owner_name:
            parts.append(f"Owner: {owner_name}")
        if tm5_desc:
            parts.append(f"USPTO status: {tm5_desc}")
        if code is not None:
            parts.append(f"Status code: {code}")
        if status_date:
            parts.append(f"Status date: {status_date}")
        if ext_desc:
            parts.append(f"Detail: {ext_desc}")

        summary = " | ".join(parts) if parts else "No simple status fields found."
        return summary, data

    except Exception:
        summary = json.dumps(data, sort_keys=True)
        return summary, data


def fetch_case_status_html(serial: str) -> str:
    url = f"https://tsdrapi.uspto.gov/ts/cd/casestatus/sn{serial}/content.html"
    headers = {
        "USPTO-API-KEY": USPTO_API_KEY,
        "Accept": "text/html",
    }
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.text


def html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", html)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n", text)
    text = re.sub(r"(?i)</div>", "\n", text)
    text = re.sub(r"(?i)</tr>", "\n", text)
    text = re.sub(r"(?i)</td>", " ", text)
    text = re.sub(r"(?i)</th>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ")
    text = text.replace("&amp;", "&")
    text = text.replace("&#39;", "'")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n+", "\n", text)
    return text.strip()


def extract_owner_name_from_api(raw_case: dict) -> str:
    candidates = []

    try:
        tm = raw_case["transactionList"][0]["trademarks"][0]
    except Exception:
        return ""

    for key in ["owners", "applicants"]:
        items = tm.get(key, [])
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                for name_key in ["partyName", "ownerName", "name", "entityName", "applicantName"]:
                    value = item.get(name_key)
                    if isinstance(value, str) and value.strip():
                        candidates.append(value.strip())

    applicant = tm.get("applicant")
    if isinstance(applicant, dict):
        for name_key in ["partyName", "ownerName", "name", "entityName", "applicantName"]:
            value = applicant.get(name_key)
            if isinstance(value, str) and value.strip():
                candidates.append(value.strip())

    seen = set()
    cleaned = []
    for item in candidates:
        if item not in seen:
            seen.add(item)
            cleaned.append(item)

    return ", ".join(cleaned)


def extract_owner_name_from_html(serial: str) -> str:
    try:
        html = fetch_case_status_html(serial)
        text = html_to_text(html)

        patterns = [
            r"Current Owner\(s\) Information.*?Owner Name:\s*(.+?)(?:\n|Owner Address:|Legal Entity Type:|State or Country Where Organized:|$)",
            r"Owner Name:\s*(.+?)(?:\n|Owner Address:|Legal Entity Type:|State or Country Where Organized:|$)",
        ]

        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
            if match:
                value = re.sub(r"\s+", " ", match.group(1)).strip(" :-")
                if value:
                    return value
    except Exception:
        pass

    return ""


def extract_owner_name(raw_case: dict, serial: str) -> str:
    owner = extract_owner_name_from_api(raw_case)
    if owner:
        return owner
    return extract_owner_name_from_html(serial)


def extract_fields(raw_case: dict) -> dict:
    out = {
        "mark": "",
        "owner": "",
        "serial": str(SERIAL_NUMBER),
        "filing_date": "",
        "status_text": "",
        "status_date": "",
    }

    try:
        tm = raw_case["transactionList"][0]["trademarks"][0]
        status = tm.get("status", {})

        out["mark"] = status.get("markElement") or ""
        out["filing_date"] = status.get("filingDate") or ""
        out["status_date"] = status.get("statusDate") or ""
        out["status_text"] = (status.get("extStatusDesc") or status.get("tm5StatusDesc") or "").strip()
        out["owner"] = extract_owner_name(raw_case, SERIAL_NUMBER)

    except Exception:
        pass

    return out


def build_email(serial: str, raw_case: dict) -> tuple[str, str, str]:
    f = extract_fields(raw_case)
    url = tsdr_case_url(serial)

    mark_display = f["mark"] or f"Serial {serial}"
    subject = f"[TSDR] {mark_display} ({serial})"

    body_text = (
        f"Mark: {f['mark']}\n"
        f"Owner: {f['owner']}\n"
        f"US Serial Number: {serial}\n"
        f"Application Filing Date: {f['filing_date']}\n"
        f"Matter Reference Number: {MATTER_REFERENCE_NUMBER}\n\n"
        f"Status:\n{f['status_text']}\n"
        f"Status Date: {f['status_date']}\n"
        f"Link: {url}\n"
    )

    body_html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; line-height: 1.4; margin:0; padding:24px;">
        <div style="max-width:760px; margin:0 auto;">

          <table cellpadding="6" cellspacing="0" style="border-collapse: collapse; width:100%;">
            <tr>
              <td style="vertical-align: top; width:190px;"><strong>Mark:</strong></td>
              <td>{f['mark']}</td>
            </tr>
            <tr>
              <td style="vertical-align: top;"><strong>Owner:</strong></td>
              <td>{f['owner']}</td>
            </tr>
            <tr>
              <td style="vertical-align: top;"><strong>US Serial Number:</strong></td>
              <td>{serial}</td>
            </tr>
            <tr>
              <td style="vertical-align: top;"><strong>Application Filing Date:</strong></td>
              <td>{f['filing_date']}</td>
            </tr>
            <tr>
              <td style="vertical-align: top;"><strong>Matter Reference Number:</strong></td>
              <td>{MATTER_REFERENCE_NUMBER}</td>
            </tr>
            <tr>
              <td style="vertical-align: top; padding-top:16px;"><strong>Status:</strong></td>
              <td style="padding-top:16px;">
                <div style="padding:10px; background:#f6f8fa; border:1px solid #e5e7eb; border-radius:6px;">
                  {f['status_text']}
                </div>
              </td>
            </tr>
            <tr>
              <td style="vertical-align: top;"><strong>Status Date:</strong></td>
              <td>{f['status_date']}</td>
            </tr>
            <tr>
              <td style="vertical-align: top;"><strong>Link:</strong></td>
              <td><a href="{url}">Open TSDR record</a></td>
            </tr>
          </table>

          <hr style="border:none; border-top:1px solid #e5e7eb; margin:16px 0;">
          <p style="margin:0; font-size:12px; color:#6b7280;">
            Automated notification. For full details, open the TSDR record.
          </p>

        </div>
      </body>
    </html>
    """

    return subject, body_text, body_html


def send_email(subject: str, body_text: str, body_html: str | None = None):
    if not (EMAIL_FROM and EMAIL_TO and SMTP_HOST and SMTP_USER and SMTP_PASS):
        print("Email settings incomplete; skipping email notification.")
        return

    recipients = [addr.strip() for addr in EMAIL_TO.split(",") if addr.strip()]

    if body_html is None:
        msg = MIMEText(body_text, "plain", "utf-8")
    else:
        from email.mime.multipart import MIMEMultipart
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(body_text, "plain", "utf-8"))
        msg.attach(MIMEText(body_html, "html", "utf-8"))

    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = ", ".join(recipients)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(EMAIL_FROM, recipients, msg.as_string())


def main():
    if not USPTO_API_KEY:
        raise RuntimeError("USPTO_API_KEY is not set!")

    last_status = load_last_status()
    current_status, raw_case = fetch_current_status()

    print(f"Previous status: {last_status}")
    print(f"Current status:  {current_status}")

    if last_status is None:
        save_last_status(current_status)
        subject, body_text, body_html = build_email(
            serial=SERIAL_NUMBER,
            raw_case=raw_case,
        )
        send_email(subject, body_text, body_html)
        print("Initial status saved; initial email sent.")
        return

    if current_status != last_status:
        save_last_status(current_status)
        subject, body_text, body_html = build_email(
            serial=SERIAL_NUMBER,
            raw_case=raw_case,
        )
        send_email(subject, body_text, body_html)
        print("Status changed; notification sent.")
    else:
        print("No status change.")


if __name__ == "__main__":
    main()
