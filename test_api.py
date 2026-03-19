import requests
import json

try:
    response = requests.get("http://localhost:5001/prophecy?token=0x4ed4E862860beD51a9570b96d89aF5E1B0Efefed")
    print(f"Status Code: {response.status_code}")
    print(json.dumps(response.json(), indent=2))
except Exception as e:
    print(f"Error: {e}")
