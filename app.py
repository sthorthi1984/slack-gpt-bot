import os
import re
import logging
import openai
from flask import Flask, request, jsonify, make_response
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from slack_sdk.signature import SignatureVerifier
from difflib import get_close_matches
from dotenv import load_dotenv

# Load env (if using .env locally)
load_dotenv()

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Env vars
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")

if not (OPENAI_API_KEY and SLACK_BOT_TOKEN and SLACK_SIGNING_SECRET):
    logger.warning("One or more required env vars are missing (OPENAI_API_KEY, SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET)")

# OpenAI (pinned to 0.28.x as in requirements)
openai.api_key = OPENAI_API_KEY

# Slack client & verifier
client = WebClient(token=SLACK_BOT_TOKEN)
signature_verifier = SignatureVerifier(SLACK_SIGNING_SECRET)

# Simple custom Q&A (all strings closed and commas present)
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
    """Remove Slack mentions like <@U12345> and extra whitespace, return lowercased text."""
    if not text:
        return ""
    # Remove slack user mentions like <@U12345>
    cleaned = re.sub(r"<@[^>]+>", "", text)
    return cleaned.strip().lower()

@app.route("/", methods=["GET"])
def health():
    return "OK - Slack GPT Bot is running", 200

@app.route("/slack/events", methods=["POST"])
def slack_events():
    # Raw body needed for signature verification
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

    # URL verification challenge
    if payload.get("type") == "url_verification":
        return jsonify({"challenge": payload.get("challenge")})

    # Event callback
    event = payload.get("event", {})
    if not event:
        return make_response("", 200)

    logger.info("Event received: %s", event)

    # Ignore messages from bots (including our own)
    if event.get("bot_id") or event.get("subtype") == "bot_message":
        logger.debug("Ignoring bot message/event.")
        return make_response("", 200)

    event_type = event.get("type")
    # Only handle app mentions (avoid duplicate replies). Allow DM in future by checking channel_type == "im".
    if event_type != "app_mention":
        logger.debug("Ignoring non-app_mention event type: %s", event_type)
        return make_response("", 200)

    user_text = event.get("text", "")
    cleaned = clean_text(user_text)
    logger.info("Cleaned user text: '%s'", cleaned)

    if not cleaned:
        return make_response("", 200)

    # Try custom Q&A (fuzzy match)
    match = get_close_matches(cleaned, custom_qa.keys(), n=1, cutoff=0.6)
    if match:
        response_text = custom_qa[match[0]]
    else:
        # Fallback to OpenAI ChatCompletion (v0.28.x usage)
        try:
            completion = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": cleaned}],
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

    # Respond 200 to Slack immediately
    return make_response("", 200)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
