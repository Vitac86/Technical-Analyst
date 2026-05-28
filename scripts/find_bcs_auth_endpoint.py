import os
import json
import requests

REFRESH_TOKEN = os.getenv("BCS_REFRESH_TOKEN", "").strip()

BASE_URLS = [
    "https://trade-api.bcs.ru",
    "http://trade-api.bcs.ru",
    "https://api-gateway.bcs.ru",
    "https://api.bcs.ru",
    "https://mybroker.bcs.ru",
    "https://lk.bcs.ru",
]

AUTH_PATHS = [
    "/trade-api-keycloak/realms/tradeapi/protocol/openid-connect/token",
    "/realms/tradeapi/protocol/openid-connect/token",
    "/auth/realms/tradeapi/protocol/openid-connect/token",
    "/v1/oauth/token",
    "/api/v1/oauth/token",
]

CLIENT_IDS = [
    "tradeapi",
    "mobile_app",
    None,
]

def mask(token: str) -> str:
    if not token:
        return "<empty>"
    if len(token) <= 12:
        return "***"
    return token[:4] + "..." + token[-4:]

def sanitize(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if "token" in k.lower():
                out[k] = "<hidden>"
            else:
                out[k] = sanitize(v)
        return out
    if isinstance(obj, list):
        return [sanitize(x) for x in obj[:3]]
    return obj

def body_preview(resp):
    try:
        data = resp.json()
        return json.dumps(sanitize(data), ensure_ascii=False, indent=2)[:1200]
    except Exception:
        return resp.text[:500].replace("\n", " ")

def main():
    if not REFRESH_TOKEN:
        raise SystemExit(
            'BCS_REFRESH_TOKEN is empty. Set it first:\n'
            '$env:BCS_REFRESH_TOKEN = "your_refresh_token"'
        )

    print("Refresh token:", mask(REFRESH_TOKEN))
    print("Testing BCS auth endpoints...\n")

    found = False

    for base in BASE_URLS:
        for path in AUTH_PATHS:
            url = base.rstrip("/") + path

            for client_id in CLIENT_IDS:
                data = {
                    "grant_type": "refresh_token",
                    "refresh_token": REFRESH_TOKEN,
                }
                label = "without client_id"

                if client_id:
                    data["client_id"] = client_id
                    label = f"client_id={client_id}"

                try:
                    resp = requests.post(
                        url,
                        data=data,
                        headers={
                            "Content-Type": "application/x-www-form-urlencoded",
                            "Accept": "application/json",
                        },
                        timeout=15,
                    )
                except Exception as e:
                    print(f"[ERR] {url} [{label}] -> {type(e).__name__}: {e}")
                    continue

                status = resp.status_code

                # Печатаем все не-404, потому что это уже полезный сигнал:
                # 400 может означать правильный endpoint, но неправильный body/client_id/token.
                if status != 404:
                    print("=" * 90)
                    print(f"URL:    {url}")
                    print(f"TRY:    {label}")
                    print(f"HTTP:   {status}")
                    print(f"TYPE:   {resp.headers.get('content-type')}")
                    print("BODY:")
                    print(body_preview(resp))

                if resp.ok:
                    try:
                        js = resp.json()
                    except Exception:
                        js = {}

                    access = js.get("access_token")
                    if isinstance(access, str) and access:
                        print("\nSUCCESS!")
                        print("Working URL:", url)
                        print("Working client:", label)
                        print("Access token:", mask(access))
                        found = True
                        return

    if not found:
        print("\nNo successful auth endpoint found.")
        print("If you saw HTTP 400 invalid_grant on some URL, that URL is probably correct, but token/client_id/body needs adjustment.")
        print("If everything is 404, we need the baseUrl from the BCS Postman environment.")

if __name__ == "__main__":
    main()
