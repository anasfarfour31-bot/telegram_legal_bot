#!/usr/bin/env python3
"""Send mixed Telegram posts and update state.json.

Required env:
  TELEGRAM_BOT_TOKEN  Bot token from @BotFather
  TELEGRAM_CHAT_ID    Channel username like @your_channel or numeric chat id
Optional env:
  POST_FOOTER         Text appended to every post
  POSTS_PER_RUN       Number of posts to send each run, default: 3
  DELAY_SECONDS       Delay between Telegram messages, default: 5
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POSTS_PATH = ROOT / "data" / "posts.json"
STATE_PATH = ROOT / "data" / "state.json"
MAX_TELEGRAM_TEXT = 4096


def load_json(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def normalize_state(state: dict) -> dict:
    state.setdefault("sent_ids", [])
    state.setdefault("recent_sources", [])
    state.setdefault("last_sent_at", None)
    state.setdefault("sent_batches", [])
    return state


def env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def choose_post(posts: list[dict], state: dict, picked_this_run: set[int]) -> tuple[int, dict]:
    sent = set(int(i) for i in state.get("sent_ids", [])) | picked_this_run
    all_ids = set(range(len(posts)))
    remaining = list(all_ids - sent)

    # When all posts were sent once, start a new cycle automatically.
    if not remaining:
        state["sent_ids"] = []
        state["recent_sources"] = []
        picked_this_run.clear()
        remaining = list(all_ids)

    recent_sources = set(state.get("recent_sources", [])[-3:])
    preferred = [i for i in remaining if posts[i].get("source_file") not in recent_sources]
    pool = preferred or remaining

    idx = random.choice(pool)
    return idx, posts[idx]


def build_message(post: dict) -> str:
    text = str(post.get("text", "")).strip()
    footer = os.environ.get("POST_FOOTER", "").strip()
    if footer:
        text = f"{text}\n\n{footer}"
    if len(text) > MAX_TELEGRAM_TEXT:
        text = text[: MAX_TELEGRAM_TEXT - 20].rstrip() + "\n\n..."
    return text


def telegram_send(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    request = urllib.request.Request(url, data=payload, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8", errors="replace")
            result = json.loads(body)
    except Exception as exc:
        raise RuntimeError(f"Telegram request failed: {exc}") from exc

    if not result.get("ok"):
        raise RuntimeError(f"Telegram API error: {result}")


def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID", file=sys.stderr)
        return 2

    posts = load_json(POSTS_PATH, [])
    if not isinstance(posts, list) or not posts:
        print("No posts found in data/posts.json", file=sys.stderr)
        return 2

    posts_per_run = env_int("POSTS_PER_RUN", default=3, minimum=1, maximum=20)
    delay_seconds = env_int("DELAY_SECONDS", default=5, minimum=0, maximum=120)

    state = normalize_state(load_json(STATE_PATH, {}))
    random.seed(f"{datetime.now(timezone.utc).date()}-{time.time_ns()}")

    picked_this_run: set[int] = set()
    sent_now: list[dict] = []

    for number in range(posts_per_run):
        idx, post = choose_post(posts, state, picked_this_run)
        message = build_message(post)
        telegram_send(token, chat_id, message)

        picked_this_run.add(idx)
        state["sent_ids"].append(idx)
        state["recent_sources"].append(post.get("source_file", "unknown"))
        state["recent_sources"] = state["recent_sources"][-20:]

        sent_now.append({
            "post_id": idx,
            "source_file": post.get("source_file", "unknown"),
            "sent_at": datetime.now(timezone.utc).isoformat(),
        })
        print(f"Sent post {number + 1}/{posts_per_run}: #{idx} from {post.get('source_file', 'unknown')}")

        if number < posts_per_run - 1 and delay_seconds > 0:
            time.sleep(delay_seconds)

    state["last_sent_at"] = datetime.now(timezone.utc).isoformat()
    state["last_post_id"] = sent_now[-1]["post_id"] if sent_now else None
    state["last_source_file"] = sent_now[-1]["source_file"] if sent_now else None
    state["last_batch"] = sent_now
    state["sent_batches"] = (state.get("sent_batches", []) + [{
        "run_at": datetime.now(timezone.utc).isoformat(),
        "count": len(sent_now),
        "posts": sent_now,
    }])[-30:]
    save_json(STATE_PATH, state)

    print(f"Sent {len(sent_now)} posts successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
