import os
from flask import Flask, request, jsonify
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from slack_sdk.signature import SignatureVerifier
from difflib import get_close_matches
from dotenv import load_dotenv
import openai

# Load environment variables from .env file
load_dotenv()

# Set up API keys and tokens
openai_client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
slack_token = os.getenv("SLACK_BOT_TOKEN")
signing_secret = os.getenv("SLACK_SIGNING_SECRET")

slack_client = WebClient(token=slack_token)
signature_verifier = SignatureVerifier(signing_secret)

# Define your custom Q&A
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
    "what is the organization structure": "You can view the org chart in HR Portal > Org Chart."
}

# Flask app setup
app = Flask(__name__)

@app.route("/slack/events", methods=["POST"])
def slack_events():
    if not signature_verifier.is_valid_request(request.get_data(), request.headers):
        return "Request verification failed", 400

    payload = request.json
    print("Full payload received from Slack:", payload)

    # Handle Slack's URL verification challenge
    if payload.get("type") == "url_verification":
        return jsonify({"challenge": payload["challenge"]})

    event = payload.get("event", {})
    print("Incoming event from Slack:", event)

    if event.get("type") == "message" and "bot_id" not in event:
        user_text = event.get("text", "").lower()
        print("User message received:", user_text)

        # Try matching custom Q&A
        match = get_close_matches(user_text, custom_qa.keys(), n=1, cutoff=0.6)
        if match:
            response_text = custom_qa[match[0]]
        else:
            # Fallback to GPT
            try:
                chat_response = openai_client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[{"role": "user", "content": user_text}]
                )
                response_text = chat_response.choices[0].message.content
            except Exception as e:
                response_text = f"Sorry, I had an issue: {str(e)}"

        # Send reply back to Slack
        try:
            slack_client.chat_postMessage(
                channel=event["channel"],
                text=response_text
            )
        except SlackApiError as e:
            print(f"Error sending message: {e}")

    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000)
