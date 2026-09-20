"""OAuth device-поток для входа в НОВУЮ учётку Яндекса (реальный аккаунт).

Двухшаговый запуск (чтобы ссылка и код были видны до ожидания подтверждения):

  1) python scripts/oauth_new_account.py --start
     -> печатает ссылку и user_code, сохраняет device_code во временный файл.
  2) Пользователь открывает ссылку, вводит код, подтверждает доступ
     (при необходимости выходит из текущего аккаунта / использует приватное окно).
  3) python scripts/oauth_new_account.py --finish
     -> меняет device_code на токены, сохраняет в зашифрованное хранилище.

Секреты (access/refresh токен, client_secret) в вывод НЕ попадают.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from yandex_metrika_agent.config import load_settings
from yandex_metrika_agent.oauth import (
    METRIKA_ALL_SCOPES,
    AuthorizationPendingError,
    OAuthClient,
    new_device_id,
)
from yandex_metrika_agent.tokens import EncryptedFileStore

PENDING = Path(__file__).resolve().parent / ".oauth_device_pending.json"
DEFAULT_CONNECTION = "new-account"


def _build_client():
    cfg = load_settings()
    if not cfg.client_id or not cfg.client_secret:
        print(
            json.dumps(
                {"error": "no_client", "message": "Задайте YANDEX_CLIENT_ID/SECRET в .env"}
            )
        )
        sys.exit(1)
    if cfg.token_key is None:
        print(json.dumps({"error": "no_token_key", "message": "Задайте METRIKA_TOKEN_KEY в .env"}))
        sys.exit(1)
    client = OAuthClient(client_id=cfg.client_id, client_secret=cfg.client_secret)
    store = EncryptedFileStore(cfg.token_dir, key=cfg.token_key)
    return cfg, client, store


async def start(connection_id: str) -> int:
    cfg, client, _store = _build_client()
    device_id = new_device_id()
    device = await client.request_device_code(scopes=METRIKA_ALL_SCOPES, device_id=device_id)
    PENDING.write_text(
        json.dumps(
            {
                "device_code": device.device_code,
                "user_code": device.user_code,
                "verification_url": device.verification_url,
                "interval": device.interval,
                "expires_at": device.expires_at,
                "connection_id": connection_id,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "step": "start",
                "connection_id": connection_id,
                "verification_url": device.verification_url,
                "user_code": device.user_code,
                "expires_in_seconds": int(device.expires_at - time.time()),
                "hint": "Откройте ссылку в приватном окне и войдите в НОВУЮ учётку, "
                "затем введите код и подтвердите доступ.",
            },
            ensure_ascii=False,
        )
    )
    return 0


async def finish() -> int:
    if not PENDING.exists():
        print(json.dumps({"error": "no_pending", "message": "Сначала выполните --start"}))
        return 1
    pending = json.loads(PENDING.read_text(encoding="utf-8"))
    cfg, client, store = _build_client()
    connection_id = pending["connection_id"]
    deadline = time.time() + max(pending["expires_at"] - time.time(), 1.0)
    interval = float(pending.get("interval") or 5.0)
    while time.time() < deadline:
        await asyncio.sleep(interval)
        try:
            record = await client.exchange_device_code(
                pending["device_code"], connection_id=connection_id
            )
        except AuthorizationPendingError:
            continue
        user_login = await client.fetch_user_login(record.access_token)
        from dataclasses import replace

        saved = replace(record, user_login=user_login or record.user_login)
        store.save(saved)
        PENDING.unlink(missing_ok=True)
        scopes = list(record.scopes)
        print(
            json.dumps(
                {
                    "step": "finish",
                    "ok": True,
                    "connection_id": saved.connection_id,
                    "user_login": saved.user_login,
                    "scopes": scopes,
                    "metrika_read": "metrika:read" in scopes if scopes else None,
                    "token_saved": bool(record.access_token),
                },
                ensure_ascii=False,
            )
        )
        return 0
    print(
        json.dumps(
            {"step": "finish", "ok": False, "reason": "not_confirmed_or_expired",
             "user_code": pending.get("user_code")}
        )
    )
    return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", action="store_true")
    parser.add_argument("--finish", action="store_true")
    parser.add_argument("--connection", default=DEFAULT_CONNECTION)
    args = parser.parse_args()
    if args.start:
        sys.exit(asyncio.run(start(args.connection)))
    if args.finish:
        sys.exit(asyncio.run(finish()))
    parser.print_help()
