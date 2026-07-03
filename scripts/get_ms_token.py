"""
One-time script to get a Microsoft Graph refresh token.

Run:  python scripts/get_ms_token.py

You'll be given a URL and a code — open the URL in your browser,
sign in with your Microsoft account, and enter the code.

The refresh token will be printed — copy it and add as a GitHub secret.
"""

import requests
import sys
import time

# You'll need to fill in your app's client ID
CLIENT_ID = input("Paste your Application (client) ID: ").strip()
TENANT_ID = input("Paste your Directory (tenant) ID: ").strip()

SCOPE = "https://graph.microsoft.com/Mail.Send offline_access"

# Step 1: Request device code
print("\nRequesting device code...")
resp = requests.post(
    f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/devicecode",
    data={
        "client_id": CLIENT_ID,
        "scope": SCOPE,
    },
)

if resp.status_code != 200:
    print(f"Error: {resp.status_code} {resp.text}")
    sys.exit(1)

data = resp.json()
print(f"\n{'='*60}")
print(f"  Go to:  {data['verification_uri']}")
print(f"  Enter:  {data['user_code']}")
print(f"{'='*60}")
print("\nWaiting for you to sign in...")

# Step 2: Poll for token
interval = data.get("interval", 5)
device_code = data["device_code"]

while True:
    time.sleep(interval)

    token_resp = requests.post(
        f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": CLIENT_ID,
            "device_code": device_code,
        },
    )

    token_data = token_resp.json()

    if "access_token" in token_data:
        print("\nAuthentication successful!")
        print(f"\n{'='*60}")
        print(f"REFRESH TOKEN (copy this entire string):")
        print(f"{'='*60}")
        print(token_data["refresh_token"])
        print(f"{'='*60}")
        print(f"\nAdd this as a GitHub secret named: MS_REFRESH_TOKEN")
        print(f"Also add these secrets if not already set:")
        print(f"  MS_CLIENT_ID = {CLIENT_ID}")
        print(f"  MS_TENANT_ID = {TENANT_ID}")
        break
    elif token_data.get("error") == "authorization_pending":
        continue
    elif token_data.get("error") == "expired_token":
        print("Device code expired. Run the script again.")
        sys.exit(1)
    else:
        print(f"Error: {token_data.get('error')}: {token_data.get('error_description')}")
        sys.exit(1)
