import os
import json
import boto3
from pymongo import MongoClient
from dotenv import load_dotenv

# Load from .env if present
load_dotenv()

# Try to load from environment first
mongo_username = os.getenv("MONGODB_USERNAME")
passcode = os.getenv("MONGODB_PASSCODE")

# If credentials not found locally, fetch from AWS Secrets Manager
if not mongo_username or not passcode:
    secret_name = os.getenv("MONGODB_SECRET_NAME", "mongo_db/secret")
    region_name = os.getenv("AWS_REGION", "us-east-2")

    def _get_secret_dict():
        client = boto3.client("secretsmanager", region_name=region_name)
        response = client.get_secret_value(SecretId=secret_name)
        return json.loads(response["SecretString"])

    secrets = _get_secret_dict()
    mongo_username = secrets["mongodb_username"]
    passcode = secrets["mongodb_passcode"]

# Connect to MongoDB
client = MongoClient(
    f"mongodb+srv://{mongo_username}:{passcode}@cluster0.gbnbssg.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
)
db = client.scramble