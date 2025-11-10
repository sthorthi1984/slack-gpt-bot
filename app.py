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

# OpenAI (v0.28.x)
openai.api_key = OPENAI_API_KEY

# Slack client & verifier
client = WebClient(token=SLACK_BOT_TOKEN)
signature_verifier = SignatureVerifier(SLACK_SIGNING_SECRET)

# Simple custom Q&A
custom_qa = {
    "what is the leave policy": "Avertra provides 12 sick and 12 casual leaves annually.",
    "who do i contact for it issues": "Please email it.support@avertra.com or message #it-helpdesk.",
    "what is avertra's vision": "Simplify Utility Innovation is Avertra's long-term vision.",
    "how do i request an id card re-issue": "Use the ID Card Form from the HR Portal or type `id card` here.",
    "where can i find the holiday calendar": "Visit SharePoint > HR Documents > Holiday Calendar 2025.",
    "who is the head of sap department": "The SAP department head is Mr. Khurram Siddique.",
    "how do i claim medical reimbursement": "Use the form on Intranet > Finance > Claims → Upload bills.",
    "what is the company dress code": "Smart casuals on weekdays, formals on Mondays.",
    "how do i get access to sap dev system": "Raise a request at sapaccess@avertra.com with your manager in CC.",
    "what is the organization structure": "You can view the org chart in HR Portal > Org Chart."
}

app = Flask(__name__)

def clean_text(text: str) -> str:
    """Remove Slack mentions like <@U12345> and return lowercased text."""
    if not text:
        return ""
    cleaned = re.sub(r"<@[^>]+>", "", text)
    return cleaned.strip()

def looks_like_search_query(text: str) -> bool:
    """Very simple heuristic: if text contains who/what/where/when/how/define/look up or ends with '?' treat as search-ish."""
    t = text.lower()
    keywords = ["who", "what", "where", "when", "how", "define", "wiki", "latest", "news", "?"]
    return any(k in t for k in keywords)

def wiki_summary(query: str, max_chars=800) -> str | None:
    """
    Query Wikipedia Opensearch & return a one-paragraph summary if found.
    Returns None if no good result.
    """
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
            # fetch summary
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
                    # return a truncated summary
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

    event = payload.get("event", {})
    if not event:
        return make_response("", 200)

    logger.info("Event received: %s", event)

    # Ignore bot messages including our own
    if event.get("bot_id") or event.get("subtype") == "bot_message":
        logger.debug("Ignoring bot message/event.")
        return make_response("", 200)

    event_type = event.get("type")
    # Only handle app mentions (prevents duplicate responses). Add DM handling later if desired.
    if event_type != "app_mention":
        logger.debug("Ignoring non-app_mention event type: %s", event_type)
        return make_response("", 200)

    raw_text = event.get("text", "")
    cleaned_text = clean_text(raw_text)
    logger.info("Cleaned user text: '%s'", cleaned_text)

    if not cleaned_text:
        return make_response("", 200)

    # Local handling for date/time questions
    lc = cleaned_text.lower()
    if ("date" in lc and ("today" in lc or "current" in lc)) or (lc.strip() == "what is today's date" or "what's today's date" in lc):
        today = datetime.now().strftime("%B %d, %Y")
        response_text = f"Today's date is {today}."
    elif ("time" in lc and ("now" in lc or "current" in lc)):
        now = datetime.now().strftime("%I:%M %p")
        response_text = f"The current time is {now}."
    else:
        # 1) Check custom Q&A (exact/fuzzy)
        match = get_close_matches(cleaned_text.lower(), custom_qa.keys(), n=1, cutoff=0.65)
        if match:
            response_text = custom_qa[match[0]]
        else:
            # 2) Try quick wiki lookup for fact-like queries if enabled
            wiki_ctx = None
            if WIKI_LOOKUP_ENABLED and looks_like_search_query(cleaned_text):
                wiki_ctx = wiki_summary(cleaned_text)
                logger.info("Wiki context: %s", wiki_ctx)

            # 3) Build system + user messages for OpenAI to reduce hallucination and include current datetime and wiki context
            system_prompt_lines = [
                "You are a helpful assistant. Always be accurate and prefer saying 'I don't know' if you are not sure.",
                f"Current date and time (server): {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                "If you use external facts supplied in the context, cite the source when possible."
            ]
            if wiki_ctx:
                system_prompt_lines.append("Context from Wikipedia (do not hallucinate beyond this):")
                system_prompt_lines.append(wiki_ctx)

            system_prompt = "\n".join(system_prompt_lines)

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": cleaned_text}
            ]

            # Call OpenAI (v0.28 ChatCompletion)
            try:
                completion = openai.ChatCompletion.create(
                    model="gpt-3.5-turbo",
                    messages=messages,
                    max_tokens=400,
                )
                response_text = completion.choices[0].message["content"].strip()
            except Exception as e:
                logger.exception("OpenAI error")
                response_text = "Sorry, I had an internal error while trying to answer."

    # Send reply back to Slack
    try:
        client.chat_postMessage(channel=event["channel"], text=response_text)
        logger.info("Replied to channel %s", event.get("channel"))
    except SlackApiError as e:
        logger.exception("Slack API error sending message: %s", e)

    return make_response("", 200)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
