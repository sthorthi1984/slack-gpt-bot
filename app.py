import os
import re
import openai
from flask import Flask, request, jsonify, make_response
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from slack_sdk.signature import SignatureVerifier
from difflib import get_close_matches
from dotenv import load_dotenv
import logging

load_dotenv()

# Logging
logging.basicConfig(level=logging.INFO)

openai.api_key = os.getenv("OPENAI_API_KEY")
slack_token = os.getenv("SLACK_BOT_TOKEN")
signing_secret = os.getenv("SLACK_SIGNING_SECRET")

client = WebClient(token=slack_token)
signature_verifier = SignatureVerifier(signing_secret)

# custom Q&A
custom_qa = {
    "what is the leave policy": "Avertra provides 12 sick and 12 casual leaves annually.",
    "who do i contact for it issues": "Please email it.support@avertra.com or message #it-helpdesk.",
    "what is avertra’s vision": "“Simplify Utility Innovation” is Avertra’s long-term vision.",
    # ... rest omitted for brevity
}

app = Flask(__name__)

def clean_text(text):
    """Remove <@U...> mentions and trim whitespace."""
    if not text:
        return ""
    # Remove slack user mentions like <@U12345>
    cleaned = re.sub(r"<@[\w]+>", "", text)
    return cleaned.strip().lower()

@app.route("/slack/events", methods=["POST"])
def slack_events():
    # Verify Slack signature
    raw_body = request.get_data()
    headers = request.headers
    if not signature_verifier.is_valid_request(raw_body, headers):
        logging.warning("Slack signature verification failed.")
        return make_response("Invalid request", 400)

    payload = request.json
    logging.info("Full payload: %s", payload)

    # URL verification for Slack
    if payload.get("type") == "url_verification":
        return jsonify({"challenge": payload["challenge"]})

    event = payload.get("event", {})
    logging.info("Event received: %s", event)

    # Ignore bot messages
    if event.get("bot_id"):
        return make_response("", 200)

    # Normalize incoming text depending on event type
    event_type = event.get("type")
    user_text = ""
    if event_type == "message":
        user_text = event.get("text", "")
    elif event_type == "app_mention":
        user_text = event.get("text", "")
    else:
        # not a type we care about
        return make_response("", 200)

    cleaned = clean_text(user_text)
    logging.info("Cleaned user text: '%s'", cleaned)
    if not cleaned:
        return make_response("", 200)

    # Try matching custom Q&A
    match = get_close_matches(cleaned, custom_qa.keys(), n=1, cutoff=0.6)
    if match:
        response_text = custom_qa[match[0]]
    else:
        # GPT fallback
        try:
            completion = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": cleaned}],
                max_tokens=400
            )
            response_text = completion.choices[0].message["content"].strip()
        except Exception as e:
            logging.exception("OpenAI error")
            response_text = "Sorry, I had an internal error while trying to answer."

    # Post message back to Slack
    try:
        client.chat_postMessage(channel=event["channel"], text=response_text)
        logging.info("Replied to channel %s", event["channel"])
    except SlackApiError as e:
        logging.exception("Slack API error sending message: %s", e)

    return make_response("", 200)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
