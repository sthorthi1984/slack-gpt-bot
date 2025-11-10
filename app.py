import os
import re
import logging
import openai
import requests
from datetime import datetime
from flask import Flask, request, jsonify, make_response
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from slack_sdk.signature import SignatureVerifier
from difflib import get_close_matches
from dotenv import load_dotenv

load_dotenv()

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Env vars
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")
WIKI_LOOKUP_ENABLED = os.getenv("WIKI_LOOKUP_ENABLED", "true").lower() in ("1", "true", "yes")

if not (OPENAI_API_KEY and SLACK_BOT_TOKEN and SLACK_SIGNING_SECRET):
    logger.warning("One or more required env vars are missing (OPENAI_API_KEY, SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET)")

# OpenAI (v0.28.x pinned)
openai.api_key = OPENAI_API_KEY

# Slack client & verifier
client = WebClient(token=SLACK_BOT_TOKEN)
signature_verifier = SignatureVerifier(SLACK_SIGNING_SECRET)

# Simple custom Q&A
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
    "how to apply for leave": "Please use the following leave request form: https://docs.google.com/forms/d/e/1FAIpQLSd1GZ1Rg_7QxOD19Sgm43-OJtfG6TVBfyLo1REIosTLoH4piQ/viewform",
    "pto planner link": "Please access the following PTO link to apply for your leave: https://docs.google.com/spreadsheets/d/10ilz4TLd1KzsqzRTp6kvydV96-kZuN6inslLmxnx7p8/edit?gid=61543925#gid=61543925",
    "byd link": "https://my335994.sapbydesign.com/sap/public/ap/ui/repository/SAP_UI/HTMLOBERON5/client.html?",
    "who is the payroll vendor for avertra": "Payline India.",
    "what is the payroll portal link for indian employees in avertra": "URL: https://avertra.paylineindia.com\nLog in with your ESS credentials.",
    "what is avertra": "Since 2007, Avertra has been driven by one mission: to simplify life. Over the years, we've expanded our reach across many cultures and geographies, ultimately recognizing that people share core needs—from access to trusted digital services to clean water and stable power. Guided by its diverse perspectives and foundational pillars—empathy, science, strategy, and technology—we create experiences that empower communities and connect people to what matters most.",
    "what is the avertra website link": "https://avertra.com",
    "can you brief us on a few success stories of avertra": "Yes, please use the URL below to access Avertra's success stories: https://avertra.com/category/success-stories/",
    "what is the ai initiative program in the sap department": "The AI Initiative program in the SAP department is a strategic effort aimed at exploring and defining artificial intelligence (AI) use cases that can significantly enhance the way we work. This includes identifying opportunities where AI can improve processes, enhance customer experiences, and support smarter decision-making within SAP operations."
}

# In-memory stores (dev-friendly). Replace with Redis/DB for production.
processed_event_ids = set()
MAX_SEEN = 2000

# Conversation history: channel_id -> list of {"role": "user"|"assistant", "content": "..."}
conversations: dict[str, list[dict]] = {}
# Session timestamps: channel_id -> last activity epoch
session_timestamps: dict[str, float] = {}
MAX_HISTORY = 10                # keep last N messages
SESSION_TTL_SECONDS = 60 * 30   # expire sessions after 30 minutes of inactivity

app = Flask(__name__)

def clean_text(text: str) -> str:
    """Remove Slack mentions like <@U12345> and return trimmed text."""
    if not text:
        return ""
    cleaned = re.sub(r"<@[^>]+>", "", text)
    return cleaned.strip()

def looks_like_search_query(text: str) -> bool:
    t = text.lower()
    keywords = ["who", "what", "where", "when", "how", "define", "wiki", "latest", "news", "?"]
    return any(k in t for k in keywords)

def wiki_summary(query: str, max_chars=800) -> str | None:
    try:
        url = "https://en.wikipedia.org/w/api.php"
        params = {
            "action": "opensearch",
            "search": query,
            "limit": 1,
            "namespace": 0,
            "format": "json"
        }
        r = requests.get(url, params=params, timeout=5)
        r.raise_for_status()
        data = r.json()
        if len(data) >= 4 and data[1]:
            title = data[1][0]
            page_url = data[3][0] if len(data[3]) > 0 else None
            summary_params = {
                "action": "query",
                "prop": "extracts",
                "exintro": True,
                "explaintext": True,
                "titles": title,
                "format": "json",
                "redirects": True
            }
            r2 = requests.get(url, params=summary_params, timeout=5)
            r2.raise_for_status()
            j = r2.json()
            pages = j.get("query", {}).get("pages", {})
            for pid, page in pages.items():
                extract = page.get("extract", "")
                if extract:
                    txt = extract.strip()
                    if len(txt) > max_chars:
                        txt = txt[:max_chars].rsplit(" ", 1)[0] + "..."
                    context = f"Wikipedia summary for '{title}':\n{txt}"
                    if page_url:
                        context += f"\nSource: {page_url}"
                    return context
    except Exception as e:
        logger.debug("Wikipedia lookup failed: %s", e)
    return None

def prune_inactive_sessions():
    """Remove conversations older than SESSION_TTL_SECONDS to keep memory bounded."""
    now_ts = datetime.now().timestamp()
    to_delete = [ch for ch, ts in session_timestamps.items() if now_ts - ts > SESSION_TTL_SECONDS]
    for ch in to_delete:
        session_timestamps.pop(ch, None)
        conversations.pop(ch, None)

@app.route("/", methods=["GET"])
def health():
    return "OK - Slack GPT Bot is running", 200

@app.route("/slack/events", methods=["POST"])
def slack_events():
    raw_body = request.get_data()
    headers = request.headers

    # Verify Slack signature
    try:
        if not signature_verifier.is_valid_request(raw_body, headers):
            logger.warning("Slack signature verification failed.")
            return make_response("Invalid signature", 400)
    except Exception as e:
        logger.exception("Exception during signature verification.")
        return make_response("Verification error", 400)

    payload = request.json or {}
    logger.info("Full payload: %s", payload)

    # URL verification
    if payload.get("type") == "url_verification":
        return jsonify({"challenge": payload.get("challenge")})

    # Dedupe by event_id (protect against retries)
    event_id = payload.get("event_id")
    if event_id:
        if event_id in processed_event_ids:
            logger.info("Duplicate event_id %s - skipping", event_id)
            return make_response("", 200)
        processed_event_ids.add(event_id)
        if len(processed_event_ids) > MAX_SEEN:
            while len(processed_event_ids) > MAX_SEEN // 2:
                processed_event_ids.pop()

    # prune old sessions at start
    prune_inactive_sessions()

    event = payload.get("event", {})
    if not event:
        return make_response("", 200)

    logger.info("Event received: %s", event)

    # Ignore bot messages including our own and message edits
    if event.get("bot_id") or event.get("subtype") in ["bot_message", "message_changed"]:
        logger.debug("Ignoring bot or edited message/event.")
        return make_response("", 200)

    event_type = event.get("type")
    channel_type = event.get("channel_type", "")  # 'im' for direct messages
    channel_id = event.get("channel") or event.get("channel_id") or event.get("user")

    # Only process:
    #  - app_mention (channel mentions)
    #  - message events that are direct messages (channel_type == "im")
    if not (event_type == "app_mention" or (event_type == "message" and channel_type == "im")):
        logger.debug("Ignoring event type=%s channel_type=%s", event_type, channel_type)
        return make_response("", 200)

    # Extract and clean user text
    user_text = event.get("text", "")
    cleaned_text = clean_text(user_text)
    logger.info("Cleaned user text: '%s'", cleaned_text)

    if not cleaned_text:
        return make_response("", 200)

    # Update session timestamp
    session_timestamps[channel_id] = datetime.now().timestamp()

    # Local handling for date/time questions
    lc = cleaned_text.lower()
    if ("date" in lc and ("today" in lc or "current" in lc)) or (lc.strip() in ["what is today's date", "what's today's date"]):
        response_text = f"Today's date is {datetime.now().strftime('%B %d, %Y')}."
    elif ("time" in lc and ("now" in lc or "current" in lc)):
        response_text = f"The current time is {datetime.now().strftime('%I:%M %p')}."
    else:
        # 1) custom Q&A
        match = get_close_matches(cleaned_text.lower(), custom_qa.keys(), n=1, cutoff=0.65)
        if match:
            response_text = custom_qa[match[0]]
        else:
            # 2) Optional wiki lookup
            wiki_ctx = None
            if WIKI_LOOKUP_ENABLED and looks_like_search_query(cleaned_text):
                wiki_ctx = wiki_summary(cleaned_text)
                logger.info("Wiki context: %s", wiki_ctx)

            # 3) Use conversation history: append user message to history
            hist = conversations.get(channel_id, [])
            # append user role
            hist.append({"role": "user", "content": cleaned_text})
            # trim to last MAX_HISTORY messages
            hist = hist[-MAX_HISTORY:]
            conversations[channel_id] = hist

            # Build system prompt including wiki context and server time
            system_prompt_lines = [
                "You are a helpful assistant. Always be accurate and prefer saying 'I don't know' if you are not sure.",
                f"Current date and time (server): {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                "If you use external facts supplied in the context, cite the source when possible."
            ]
            if wiki_ctx:
                system_prompt_lines.append("Context from Wikipedia (do not hallucinate beyond this):")
                system_prompt_lines.append(wiki_ctx)

            system_prompt = "\n".join(system_prompt_lines)

            # Build messages list: system + history (convert roles to OpenAI format)
            messages = [{"role": "system", "content": system_prompt}]
            # include history (user/assistant)
            for item in hist:
                messages.append({"role": item["role"], "content": item["content"]})

            # Call OpenAI with history so model has context
            try:
                completion = openai.ChatCompletion.create(
                    model="gpt-3.5-turbo",
                    messages=messages,
                    max_tokens=400,
                )
                assistant_text = completion.choices[0].message["content"].strip()
                response_text = assistant_text
                # append assistant reply to conversation history and trim
                conversations[channel_id].append({"role": "assistant", "content": assistant_text})
                conversations[channel_id] = conversations[channel_id][-MAX_HISTORY:]
                # refresh timestamp
                session_timestamps[channel_id] = datetime.now().timestamp()
            except Exception:
                logger.exception("OpenAI error")
                response_text = "Sorry, I had an internal error while trying to answer."

    # Send reply back to Slack
    try:
        client.chat_postMessage(channel=channel_id, text=response_text)
        logger.info("Replied to channel %s", channel_id)
    except SlackApiError as e:
        logger.exception("Slack API error sending message: %s", e)

    return make_response("", 200)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
