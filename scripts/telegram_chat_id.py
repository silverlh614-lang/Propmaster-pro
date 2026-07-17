"""@responsibility 텔레그램 셋업 도구 — 봇 토큰으로 chat_id 탐색·테스트 발송, 알림 배선 1회용 (stdlib 전용)

Telegram alert setup helper (one-time, standalone — no project imports).

Finding your chat_id is the only fiddly part of wiring the alerts. This
script talks to the public Bot API with the stdlib only (urllib) so it runs
anywhere Python does, no dependencies.

Usage:
  1) Create a bot in Telegram with @BotFather -> copy the token.
  2) Send ANY message to your new bot (or add it to a group and post there).
  3) Run:
        TELEGRAM_BOT_TOKEN=123:abc python scripts/telegram_chat_id.py
     It prints every chat_id that has messaged the bot.
  4) Put the token + chat_id into Railway variables:
        TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  5) Verify end-to-end (sends a real message):
        TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... \
            python scripts/telegram_chat_id.py --test

Nothing here touches the trading engine — it is pure setup convenience.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.telegram.org/bot{token}/{method}"
_TIMEOUT = 15


def _call(token: str, method: str, params: dict | None = None) -> dict:
    url = API.format(token=token, method=method)
    data = urllib.parse.urlencode(params).encode() if params else None
    try:
        with urllib.request.urlopen(url, data=data, timeout=_TIMEOUT) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        return {"ok": False, "error_code": e.code, "description": body}
    except Exception as e:  # noqa: BLE001 — surface any transport error plainly
        return {"ok": False, "description": f"{type(e).__name__}: {e}"}


def discover(token: str) -> int:
    """Print every chat that has messaged the bot. Returns a shell exit code."""
    res = _call(token, "getUpdates")
    if not res.get("ok"):
        print(f"[telegram] getUpdates 실패: {res.get('description')}",
              file=sys.stderr)
        print("  토큰이 맞는지, 봇에게 메시지를 한 번 보냈는지 확인하세요.",
              file=sys.stderr)
        return 1
    chats: dict[int, str] = {}
    for upd in res.get("result", []):
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        if cid is None:
            continue
        label = (chat.get("title") or chat.get("username")
                 or " ".join(filter(None, [chat.get("first_name"),
                                           chat.get("last_name")]))
                 or chat.get("type", "?"))
        chats[cid] = label
    if not chats:
        print("[telegram] 아직 메시지가 없습니다 — 봇에게 아무 메시지나 보낸 뒤 "
              "다시 실행하세요 (그룹이면 그룹에 글을 올리세요).")
        return 2
    print("발견한 chat_id (TELEGRAM_CHAT_ID 에 넣을 값):")
    for cid, label in chats.items():
        print(f"  {cid}   ← {label}")
    return 0


def send_test(token: str, chat_id: str) -> int:
    res = _call(token, "sendMessage",
                {"chat_id": chat_id,
                 "text": "🔔 Propmaster Pro — 텔레그램 알림 배선 테스트 정상"})
    if res.get("ok"):
        print(f"[telegram] 테스트 메시지 발송 성공 → chat {chat_id}")
        return 0
    print(f"[telegram] 발송 실패: {res.get('description')}", file=sys.stderr)
    return 1


def main(argv: list[str]) -> int:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("TELEGRAM_BOT_TOKEN 환경변수가 필요합니다 "
              "(@BotFather 에서 발급).", file=sys.stderr)
        return 1
    if "--test" in argv:
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        if not chat_id:
            print("--test 에는 TELEGRAM_CHAT_ID 도 필요합니다.", file=sys.stderr)
            return 1
        return send_test(token, chat_id)
    return discover(token)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
