import requests
import json

try:
    response = requests.get("http://localhost:5001/social-prophecy?handle=clanker")
    print(f"Status Code: {response.status_code}")
    print(json.dumps(response.json(), indent=2))
except Exception as e:
    print(f"Error: {e}")
