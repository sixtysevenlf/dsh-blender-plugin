""" Tencent Cloud signing and Hunyuan API profiles. """

import json
import time
from datetime import datetime
import hashlib, hmac, base64

HUNYUAN_API_PROFILES = {
    "mainland": {
        "service": "ai3d",
        "version": "2025-05-13",
        "region": "ap-guangzhou",
        "submit_action": "SubmitHunyuanTo3DProJob",
        "query_action": "QueryHunyuanTo3DProJob",
        "submit_body": {},
    },
    "international_pro": {
        "service": "hunyuan",
        "version": "2023-09-01",
        "region": "ap-singapore",
        "submit_action": "SubmitHunyuanTo3DProJob",
        "query_action": "QueryHunyuanTo3DProJob",
        "submit_body": {"EnablePBR": True},
    },
}


def hunyuan_api_profile(international_pro: bool) -> dict:
    """Return a copy of the Tencent Cloud API profile for the selected account type."""
    profile = HUNYUAN_API_PROFILES["international_pro" if international_pro else "mainland"]
    return {**profile, "submit_body": dict(profile["submit_body"])}


def get_tencent_cloud_sign_headers(
    method: str,
    path: str,
    headParams: dict,
    data: dict,
    service: str,
    region: str,
    secret_id: str,
    secret_key: str,
    host: str = None
):
    """Generate the signature header required for Tencent Cloud API requests headers"""
    # Generate timestamp
    timestamp = int(time.time())
    date = datetime.utcfromtimestamp(timestamp).strftime("%Y-%m-%d")

    # If host is not provided, it is generated based on service and region.
    if not host:
        host = f"{service}.tencentcloudapi.com"

    endpoint = f"https://{host}"

    # Constructing the request body
    payload_str = json.dumps(data)

    # ************* Step 1: Concatenate the canonical request string *************
    canonical_uri = path
    canonical_querystring = ""
    ct = "application/json; charset=utf-8"
    canonical_headers = f"content-type:{ct}\nhost:{host}\nx-tc-action:{headParams.get('Action', '').lower()}\n"
    signed_headers = "content-type;host;x-tc-action"
    hashed_request_payload = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()

    canonical_request = (method + "\n" +
                        canonical_uri + "\n" +
                        canonical_querystring + "\n" +
                        canonical_headers + "\n" +
                        signed_headers + "\n" +
                        hashed_request_payload)

    # ************* Step 2: Construct the reception signature string *************
    credential_scope = f"{date}/{service}/tc3_request"
    hashed_canonical_request = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    string_to_sign = ("TC3-HMAC-SHA256" + "\n" +
                    str(timestamp) + "\n" +
                    credential_scope + "\n" +
                    hashed_canonical_request)

    # ************* Step 3: Calculate the signature *************
    def sign(key, msg):
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    secret_date = sign(("TC3" + secret_key).encode("utf-8"), date)
    secret_service = sign(secret_date, service)
    secret_signing = sign(secret_service, "tc3_request")
    signature = hmac.new(
        secret_signing,
        string_to_sign.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    # ************* Step 4: Connect Authorization *************
    authorization = ("TC3-HMAC-SHA256" + " " +
                    "Credential=" + secret_id + "/" + credential_scope + ", " +
                    "SignedHeaders=" + signed_headers + ", " +
                    "Signature=" + signature)

    # Constructing request headers
    headers = {
        "Authorization": authorization,
        "Content-Type": "application/json; charset=utf-8",
        "Host": host,
        "X-TC-Action": headParams.get("Action", ""),
        "X-TC-Timestamp": str(timestamp),
        "X-TC-Version": headParams.get("Version", ""),
        "X-TC-Region": region
    }

    return headers, endpoint
