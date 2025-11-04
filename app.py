import os
import re
import json
import tempfile
from datetime import datetime
from flask import Flask, request, jsonify
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from slack_sdk.signature import SignatureVerifier
from difflib import get_close_matches
from dotenv import load_dotenv
from doc import Document
import openai

# Load environment variables
load_dotenv()

# Initialize clients
openai_client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
slack_token = os.getenv("SLACK_BOT_TOKEN")
signing_secret = os.getenv("SLACK_SIGNING_SECRET")

slack_client = WebClient(token=slack_token)
signature_verifier = SignatureVerifier(signing_secret)

# =========================
# Custom Employee Q&A
# =========================
custom_qa = {
    "what is the leave policy": "Avertra provides 12 sick and 12 casual leaves annually.",
    "who do i contact for it issues": "Please email it.support@avertra.com or message #it-helpdesk.",
    "what is avertra’s vision": "“Simplify Utility Innovation” is Avertra’s long-term vision.",
    "how do i request an id card re-issue": "Use the ID Card Form from the HR Portal or type `id card` here.",
    "where can i find the holiday calendar": "Visit SharePoint > HR Documents > Holiday Calendar 2025.",
    "who is the head of sap department": "The SAP department head is Mr. Khurram Siddique.",
    "how do i claim medical reimbursement": "Use the form on Intranet > Finance > Claims → Upload bills.",
    "what is the company dress code": "Smart casuals on weekdays, formals on Mondays.",
    "how do i get access to sap dev system": "Raise a request at sapaccess@avertra.com with your manager in CC.",
    "what is the organization structure": "You can view the org chart in HR Portal > Org Chart.",
    "how to apply for leave": " Please use the following leave request form: https://docs.google.com/forms/d/e/1FAIpQLSd1GZ1Rg_7QxOD19Sgm43-OJtfG6TVBfyLo1REIosTLoH4piQ/viewform",
    "pto planner link": "Please access the following PTO link to apply for your leave: https://docs.google.com/spreadsheets/d/10ilz4TLd1KzsqzRTp6kvydV96-kZuN6inslLmxnx7p8/edit?gid=61543925#gid=61543925",
    "byd link": "https://my335994.sapbydesign.com/sap/public/ap/ui/repository/SAP_UI/HTMLOBERON5/client.html?",
    "who is the payroll vendor for avertra": "Payline India",
    "what is the payroll portal link for indian employees in avertra": "URL: https://avertra.paylineindia.com\nLog in with your ESS credentials.",
    "what are the current ongoing projects in the sap department": "NTUA AMS, NTUA SuccessFactors Implementation Project, and Aramex.",
    "who is the founder of avertra": "Mr. Giancarlo Reyes",
    "who is the ceo/cto of avertra": "Mr. Bashir Bseirani",
    "what is avertra": "Since 2007, Avertra has been driven by one mission: to simplify life. Over the years, we've expanded our reach across many cultures and geographies, ultimately recognizing that people share core needs—from access to trusted digital services to clean water and stable power. Guided by its diverse perspectives and foundational pillars—empathy, science, strategy, and technology—we create experiences that empower communities and connect people to what matters most.",
    "what is the avertra website link": "https://avertra.com",
    "can you brief us on a few success stories of avertra": "Yes, please use the URL below to access Avertra's success stories: https://avertra.com/category/success-stories/",
    "what is the ai initiative program in the sap department": "The AI Initiative program in the SAP department is a strategic effort aimed at exploring and defining artificial intelligence (AI) use cases that can significantly enhance the way we work. This includes identifying opportunities where AI can improve processes, enhance customer experiences, and support smarter decision-making within SAP operations."
}

# =========================
# Helper Functions for FS Generator
# =========================
def ask_openai_for_fs(requirement_text):
    """Call OpenAI to generate FS as JSON."""
    system_prompt = (
        "You are an expert SAP functional consultant. "
        "Return ONLY valid JSON with keys: title, module, purpose, as_is, to_be, "
        "functional_requirements (array of {id,description,field_name,validation,data_source,remarks}), "
        "integration_details, reports_forms, authorization_requirements, assumptions_dependencies, appendix."
    )

    user_prompt = f"Requirement:\n{requirement_text}\n\nGenerate the Functional Specification (as JSON)."

    response = openai_client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.0,
        max_tokens=1500,
    )

    text = response.choices[0].message.content

    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            return json.loads(match.group(0))
        raise ValueError("Could not parse valid JSON from OpenAI response.")

def render_fs_docx(fs_json):
    """Generate Word file from FS JSON and return file path."""
    doc = Document()
    doc.add_heading(fs_json.get("title", "Functional Specification (Draft)"), level=1)

    # Document Info
    doc.add_heading("Document Information", level=2)
    info = {
        "Document Title": fs_json.get("title", ""),
        "Module": fs_json.get("module", ""),
        "Created By": "AI Generator",
        "Created Date": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "Version": "0.1 Draft",
    }
    table = doc.add_table(rows=1, cols=2)
    hdr = table.rows[0].cells
    hdr[0].text = "Field"
    hdr[1].text = "Value"
    for k, v in info.items():
        row = table.add_row().cells
        row[0].text = k
        row[1].text = v

    # Add sections
    sections = [
        ("Purpose", "purpose"),
        ("AS-IS Process", "as_is"),
        ("TO-BE Process", "to_be"),
        ("Integration Details", "integration_details"),
        ("Reports / Forms", "reports_forms"),
        ("Authorization Requirements", "authorization_requirements"),
        ("Assumptions & Dependencies", "assumptions_dependencies"),
        ("Appendix", "appendix")
    ]
    for title, key in sections:
        doc.add_heading(title, level=2)
        doc.add_paragraph(fs_json.get(key, ""))

    # Functional Requirements Table
    doc.add_heading("Functional Requirements", level=2)
    frs = fs_json.get("functional_requirements", [])
    if isinstance(frs, list) and frs:
        table = doc.add_table(rows=1, cols=6)
        hdr = table.rows[0].cells
        headers = ["ID", "Description", "Field Name", "Validation", "Data Source", "Remarks"]
        for i, h in enumerate(headers):
            hdr[i].text = h
        for fr in frs:
            row = table.add_row().cells
            row[0].text = fr.get("id", "")
            row[1].text = fr.get("description", "")
            row[2].text = fr.get("field_name", "")
            row[3].text = fr.get("validation", "")
            row[4].text = fr.get("data_source", "")
            row[5].text = fr.get("remarks", "")

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".docx")
    doc.save(tmp.name)
    return tmp.name

def upload_file_to_slack(channel, filepath, title="FS_Draft.docx"):
    """Upload Word file to Slack."""
    try:
        slack_client.files_upload(
            channels=channel,
            file=filepath,
            title=title,
            initial_comment="Here’s your AI-generated Functional Specification draft."
        )
    except Exception as e:
        print("Slack file upload failed:", e)

# =========================
# Flask App Logic
# =========================
app = Flask(__name__)

@app.route("/slack/events", methods=["POST"])
def slack_events():
    if not signature_verifier.is_valid_request(request.get_data(), request.headers):
        return "Request verification failed", 400

    payload = request.json
    event = payload.get("event", {})

    # Slack verification
    if payload.get("type") == "url_verification":
        return jsonify({"challenge": payload["challenge"]})

    # Handle incoming messages
    if event.get("type") == "message" and "bot_id" not in event:
        user_text = event.get("text", "").lower()
        channel = event.get("channel")

        print("User message received:", user_text)

        # 🔹 FS Generator Command
        if user_text.startswith("generate fs:") or user_text.startswith("gen fs:"):
            requirement = re.sub(r"^(generate fs:|gen fs:)\s*", "", user_text, flags=re.IGNORECASE).strip()
            if not requirement:
                slack_client.chat_postMessage(channel=channel, text="Please provide a requirement after 'generate fs:'.")
                return jsonify({"status": "ok"})

            try:
                slack_client.chat_postMessage(channel=channel, text="Generating Functional Specification draft, please wait...")
                fs_json = ask_openai_for_fs(requirement)
                filepath = render_fs_docx(fs_json)
                upload_file_to_slack(channel, filepath, title=f"{fs_json.get('title', 'FS_Draft')}.docx")
            except Exception as e:
                slack_client.chat_postMessage(channel=channel, text=f"Error generating FS: {str(e)}")

        else:
            # Regular Employee Q&A
            match = get_close_matches(user_text, custom_qa.keys(), n=1, cutoff=0.6)
            if match:
                response_text = custom_qa[match[0]]
            else:
                try:
                    chat_response = openai_client.chat.completions.create(
                        model="gpt-3.5-turbo",
                        messages=[{"role": "user", "content": user_text}]
                    )
                    response_text = chat_response.choices[0].message.content
                except Exception as e:
                    response_text = f"Sorry, I had an issue: {str(e)}"

            try:
                slack_client.chat_postMessage(channel=channel, text=response_text)
            except SlackApiError as e:
                print(f"Error sending message: {e}")

    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000)
