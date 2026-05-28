import os
import json
import datetime as dt
from typing import Any

import requests


BASE_URL = os.getenv("BCS_BASE_URL", "https://be.broker.ru").rstrip("/")
REFRESH_TOKEN = os.getenv("BCS_REFRESH_TOKEN", "").strip()
TICKER = os.getenv("BCS_TEST_TICKER", "SBER").strip().upper()
CLASS_CODE = os.getenv("BCS_TEST_CLASS_CODE", "TQBR").strip().upper()

AUTH_URL = BASE_URL + "/trade-api-keycloak/realms/tradeapi/protocol/openid-connect/token"
INSTRUMENTS_URL = BASE_URL + "/trade-api-information-service/api/v1/instruments/by-tickers"
CANDLES_URL = BASE_URL + "/trade-api-market-data-connector/api/v1/candles-chart"


def mask(value: str) -> str:
    if not value:
        return "<empty>"
    if len(value) <= 12:
        return "***"
    return value[:4] + "..." + value[-4:]


def safe_json(resp: requests.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return resp.text[:3000]


def sanitize(obj: Any) -> Any:
    if isinstance(obj, dict):
        clean = {}
        for key, value in obj.items():
            if "token" in key.lower():
                clean[key] = "<hidden>"
            else:
                clean[key] = sanitize(value)
        return clean
    if isinstance(obj, list):
        return [sanitize(x) for x in obj[:5]]
    return obj


def print_response(title: str, resp: requests.Response) -> None:
    print(f"\n--- {title} ---")
    print("URL:", resp.url)
    print("HTTP:", resp.status_code)
    print("Content-Type:", resp.headers.get("content-type"))

    body = sanitize(safe_json(resp))
    if isinstance(body, (dict, list)):
        print(json.dumps(body, ensure_ascii=False, indent=2)[:5000])
    else:
        print(str(body)[:5000])


def get_access_token() -> str:
    if not REFRESH_TOKEN:
        raise RuntimeError(
            "BCS_REFRESH_TOKEN is empty. Set it first:\n"
            '$env:BCS_REFRESH_TOKEN = "your_refresh_token"'
        )

    print("BASE_URL:", BASE_URL)
    print("TICKER:", TICKER)
    print("CLASS_CODE:", CLASS_CODE)
    print("REFRESH_TOKEN:", mask(REFRESH_TOKEN))

    resp = requests.post(
        AUTH_URL,
        data={
            "client_id": "trade-api-write",
            "grant_type": "refresh_token",
            "refresh_token": REFRESH_TOKEN,
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        timeout=30,
    )

    print_response("AUTH RESPONSE", resp)

    if not resp.ok:
        raise RuntimeError("Auth failed. See AUTH RESPONSE above.")

    data = resp.json()
    access_token = data.get("access_token")

    if not isinstance(access_token, str) or not access_token:
        raise RuntimeError("Auth response does not contain access_token.")

    print("\nAUTH OK")
    print("ACCESS_TOKEN:", mask(access_token))
    print("EXPIRES_IN:", data.get("expires_in"))
    print("REFRESH_EXPIRES_IN:", data.get("refresh_expires_in"))

    return access_token


def auth_headers(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }


def test_instruments(access_token: str) -> None:
    body = {"tickers": [TICKER]}

    print("\nTrying instruments request:")
    print(json.dumps(body, ensure_ascii=False, indent=2))

    resp = requests.post(
        INSTRUMENTS_URL,
        json=body,
        headers={
            **auth_headers(access_token),
            "Content-Type": "application/json",
        },
        timeout=30,
    )

    print_response("INSTRUMENTS RESPONSE", resp)

    if resp.ok:
        data = safe_json(resp)
        if isinstance(data, list) and data:
            print("\nINSTRUMENTS OK")
            print("First item keys:", list(data[0].keys()))


def test_candles(access_token: str) -> None:
    end = dt.datetime.now(dt.timezone.utc)
    start = end - dt.timedelta(days=3)

    params = {
        "ticker": TICKER,
        "classCode": CLASS_CODE,
        "startDate": start.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "endDate": end.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "timeFrame": "M5",
    }

    print("\nTrying candles request:")
    print(json.dumps(params, ensure_ascii=False, indent=2))

    resp = requests.get(
        CANDLES_URL,
        params=params,
        headers=auth_headers(access_token),
        timeout=30,
    )

    print_response("CANDLES RESPONSE", resp)

    if resp.ok:
        data = safe_json(resp)
        print("\nCANDLES OK")
        print("Response type:", type(data).__name__)

        if isinstance(data, list):
            print("Candles count:", len(data))
            if data:
                print("First candle:")
                print(json.dumps(sanitize(data[0]), ensure_ascii=False, indent=2)[:2000])
        elif isinstance(data, dict):
            print("Response keys:", list(data.keys()))


def main() -> None:
    access_token = get_access_token()
    test_instruments(access_token)
    test_candles(access_token)


if __name__ == "__main__":
    main()
