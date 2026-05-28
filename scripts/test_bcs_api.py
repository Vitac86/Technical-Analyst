cd "C:\Projects\Technical-Analyst"

New-Item -ItemType Directory -Force -Path ".\scripts" | Out-Null

@'
import os
import json
import datetime as dt
from typing import Any

import requests


BASE_URL = os.getenv("BCS_BASE_URL", "https://trade-api.bcs.ru").rstrip("/")
REFRESH_TOKEN = os.getenv("BCS_REFRESH_TOKEN", "").strip()
TICKER = os.getenv("BCS_TEST_TICKER", "SBER").strip().upper()

AUTH_PATH = "/trade-api-keycloak/realms/tradeapi/protocol/openid-connect/token"
INSTRUMENTS_BY_TICKER_PATH = "/api/v1/instruments/by-tickers"
CANDLES_PATH = "/api/v1/candles-chart"


def mask_token(token: str) -> str:
    if len(token) <= 12:
        return "***"
    return token[:4] + "..." + token[-4:]


def safe_json(resp: requests.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return resp.text[:2000]


def print_response(title: str, resp: requests.Response) -> None:
    print(f"\n--- {title} ---")
    print("URL:", resp.url)
    print("HTTP:", resp.status_code)
    print("Content-Type:", resp.headers.get("content-type"))
    body = safe_json(resp)

    # Не печатаем токены, даже если они пришли.
    if isinstance(body, dict):
        sanitized = {}
        for k, v in body.items():
            lk = k.lower()
            if "token" in lk:
                sanitized[k] = "<hidden>"
            else:
                sanitized[k] = v
        print(json.dumps(sanitized, ensure_ascii=False, indent=2)[:4000])
    else:
        print(str(body)[:4000])


def get_access_token() -> str:
    if not REFRESH_TOKEN:
        raise RuntimeError(
            "BCS_REFRESH_TOKEN is empty. Set it first:\n"
            '$env:BCS_REFRESH_TOKEN = "paste_your_refresh_token_here"'
        )

    print("Base URL:", BASE_URL)
    print("Refresh token:", mask_token(REFRESH_TOKEN))

    url = BASE_URL + AUTH_PATH

    # Keycloak/OAuth token endpoints обычно принимают form-urlencoded.
    # Точный client_id в документации может отличаться, поэтому пробуем несколько безопасных вариантов.
    attempts = [
        ("client_id=tradeapi", {
            "grant_type": "refresh_token",
            "refresh_token": REFRESH_TOKEN,
            "client_id": "tradeapi",
        }),
        ("client_id=mobile_app", {
            "grant_type": "refresh_token",
            "refresh_token": REFRESH_TOKEN,
            "client_id": "mobile_app",
        }),
        ("without client_id", {
            "grant_type": "refresh_token",
            "refresh_token": REFRESH_TOKEN,
        }),
    ]

    last_error = None

    for label, form in attempts:
        print(f"\nTrying auth: {label}")
        try:
            resp = requests.post(
                url,
                data=form,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=20,
            )
        except Exception as e:
            last_error = e
            print("Network error:", repr(e))
            continue

        print_response("AUTH RESPONSE", resp)

        if resp.ok:
            data = resp.json()
            access_token = data.get("access_token")
            if isinstance(access_token, str) and access_token:
                print("\nAUTH OK")
                print("Access token:", mask_token(access_token))
                print("Expires in:", data.get("expires_in"))
                return access_token

        last_error = f"Auth failed with HTTP {resp.status_code}"

    raise RuntimeError(f"Could not get access token. Last error: {last_error}")


def auth_headers(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }


def find_instrument(access_token: str) -> dict | None:
    url = BASE_URL + INSTRUMENTS_BY_TICKER_PATH

    # Точная форма body может отличаться. Пробуем наиболее вероятные варианты.
    bodies = [
        {"tickers": [TICKER], "page": 0, "size": 10},
        {"ticker": TICKER, "page": 0, "size": 10},
        {"securities": [TICKER], "page": 0, "size": 10},
    ]

    for body in bodies:
        print("\nTrying instruments body:")
        print(json.dumps(body, ensure_ascii=False, indent=2))

        resp = requests.post(url, json=body, headers=auth_headers(access_token), timeout=20)
        print_response("INSTRUMENTS RESPONSE", resp)

        if not resp.ok:
            continue

        data = safe_json(resp)

        # Пытаемся найти первый объект инструмента в разных возможных envelope-структурах.
        candidates = []
        if isinstance(data, list):
            candidates = data
        elif isinstance(data, dict):
            for key in ("data", "items", "content", "instruments", "result"):
                val = data.get(key)
                if isinstance(val, list):
                    candidates = val
                    break

        if candidates:
            inst = candidates[0]
            if isinstance(inst, dict):
                print("\nINSTRUMENT FOUND:")
                print(json.dumps(inst, ensure_ascii=False, indent=2)[:4000])
                return inst

    print("\nInstrument not found or request shape is different.")
    return None


def pick_instrument_id(inst: dict) -> str | None:
    # Печатаем ключи, чтобы понять реальную структуру.
    print("\nInstrument keys:", list(inst.keys()))

    possible_keys = [
        "instrumentId",
        "instrument_id",
        "id",
        "securityId",
        "securityCode",
        "symbol",
        "ticker",
    ]

    for key in possible_keys:
        val = inst.get(key)
        if isinstance(val, (str, int)) and str(val):
            print(f"Using instrument id from field `{key}`:", val)
            return str(val)

    return None


def test_candles(access_token: str, instrument_id: str) -> None:
    url = BASE_URL + CANDLES_PATH

    end = dt.datetime.now(dt.timezone.utc)
    start = end - dt.timedelta(days=3)

    start_s = start.isoformat(timespec="seconds").replace("+00:00", "Z")
    end_s = end.isoformat(timespec="seconds").replace("+00:00", "Z")

    # Точная форма params в документации может быть startDate/endDate/timeFrame.
    # Проверяем эту форму первой.
    params_variants = [
        {
            "instrumentId": instrument_id,
            "timeFrame": "M5",
            "startDate": start_s,
            "endDate": end_s,
        },
        {
            "instrumentId": instrument_id,
            "interval": "M5",
            "startDate": start_s,
            "endDate": end_s,
        },
        {
            "instrumentId": instrument_id,
            "timeFrame": "M5",
            "from": start_s,
            "to": end_s,
        },
    ]

    for params in params_variants:
        print("\nTrying candles params:")
        print(json.dumps(params, ensure_ascii=False, indent=2))

        resp = requests.get(url, params=params, headers=auth_headers(access_token), timeout=30)
        print_response("CANDLES RESPONSE", resp)

        if resp.ok:
            data = safe_json(resp)
            print("\nCANDLES OK. Response type:", type(data).__name__)
            return

    print("\nCandles request did not succeed with tested parameter shapes.")


def main() -> None:
    access_token = get_access_token()

    inst = find_instrument(access_token)
    if not inst:
        print("\nAUTH WORKS, but instrument search failed.")
        print("Next step: verify body shape for /api/v1/instruments/by-tickers in BCS Postman/docs.")
        return

    instrument_id = pick_instrument_id(inst)
    if not instrument_id:
        print("\nAUTH + INSTRUMENT SEARCH WORK, but could not detect instrumentId field.")
        print("Send me the printed instrument JSON without tokens; we will map the correct field.")
        return

    test_candles(access_token, instrument_id)


if __name__ == "__main__":
    main()
'@ | Set-Content -Encoding UTF8 ".\scripts\test_bcs_api.py"