import os
import json
import boto3
from pymongo import MongoClient

# Boto3 will automatically use the Lambda's IAM role credentials
_secret_name = os.environ["MONGODB_SECRET_NAME"]
_region     = os.environ.get("AWS_REGION", "us-east-1")

def _get_secret_dict():
    client = boto3.client("secretsmanager", region_name=_region)
    resp   = client.get_secret_value(SecretId=_secret_name)
    return json.loads(resp["SecretString"])

secrets   = _get_secret_dict()
passcode = secrets["mongodb_passcode"]
mongo_username = secrets["mongodb_username"]

# Create one client per container (reused across invocations)
client = MongoClient(f"mongodb+srv://{mongo_username}:{passcode}@cluster0.gbnbssg.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")

db = client.scramble