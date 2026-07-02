from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.request


def send_lark_text(text: str, webhook_env: str, secret_env: str, dry_run: bool = False) -> None:
    webhook = os.environ.get(webhook_env, "")
    secret = os.environ.get(secret_env, "")
    payload = {"msg_type": "text", "content": {"text": text}}
    if secret:
        timestamp = str(int(time.time()))
        payload["timestamp"] = timestamp
        payload["sign"] = _sign(timestamp, secret)

    if dry_run or not webhook:
        print("Lark payload:")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if not webhook and not dry_run:
            print(f"Skip Lark send: env {webhook_env} is not set")
        return

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode("utf-8")
    result = json.loads(body)
    if result.get("StatusCode", result.get("code", 0)) not in (0, None):
        raise RuntimeError(f"Lark webhook failed: {body}")


def _sign(timestamp: str, secret: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(string_to_sign, b"", digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")
