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

# Simple custom Q&A
custom_qa = {
    "what is the leave policy": "Avertra provides 12 sick and 12 casual leaves annually.",
    "who do i contact for it issues": "Please email it.support@avertra.com or message #it-helpdesk.",
    "what is avertra’s vision": "“Simplify Utility Innovation” is Avertra’s long-term vision.",
    "how do i request an id card re-issue": "Use the ID Card Form from the HR Portal or type `id card` here.",
    "where can i find the holiday calendar": "Visit SharePoint > HR Documents > Holiday Calendar
