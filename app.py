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
    "how to apply for leave": "Please access the following PTO link to apply for your leave: https://docs.google.com/spreadsheets/d/10ilz4TLd1KzsqzRTp6kvydV96-kZuN6inslLmxnx7p8/edit?gid=61543925#gid=61543925",
    "byd link": "https://my335994.sapbydesign.com/sap/public/ap/ui/repository/SAP_UI/HTMLOBERON5/client.html?",
    "who is the payroll vendor for avertra": "Payline India",
    "what is the payroll portal link for indian employees in avertra": "URL: https://avertra.paylineindia.com. Log in with your ESS credentials.",
    "what are the current ongoing projects in the sap department": "NTUA AMS, NTUA SuccessFactors Implementation Project, and Aramex.",
    "who is the founder of avertra": "Mr. Giancarlo Reyes",
    "who is the ceo/cto of avertra": "Mr. Bashir Bseirani",
    "what is avertra": "Since 2007, Avertra has been driven by one mission: to simplify life. Over the years, we've expanded our reach across many cultures and geographies, ultimately recognizing that people share core needs—from access to trusted digital services to clean water and stable power. Guided by its diverse perspectives and foundational pillars—empathy, science, strategy, and technology—we create experiences that empower communities and connect people to what matters most.",
    "what is the avertra website link": "https://avertra.com",
    "can you brief us on a few success stories of avertra": "Yes, please use the URL below to access Avertra's success stories: https://avertra.com/category/success-stories/",
    "what is the ai initiative program in the sap department": "The AI Initiative program in the SAP department is a strategic effort aimed at exploring and defining artificial intelligence (AI) use cases that can significantly enhance the way we work. This includes identifying opportunities where AI can improve processes, enhance customer experiences, and support smarter decision-making within SAP operations. The program encourages collaboration among team members to share ideas, identify impactful use cases or projects, and explore tools and technologies that can be leveraged to implement or enhance AI-driven solutions."
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
