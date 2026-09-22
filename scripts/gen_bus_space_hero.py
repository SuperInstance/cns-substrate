#!/usr/bin/env python3
"""Generate the BusSpace hero image via Cloudflare FLUX-1-schnell.

The oauth token is read from the Wrangler config (or CF_API_TOKEN), never
hardcoded. FLUX sometimes returns a JPEG; this re-encodes it to PNG so the
``.png`` filename stays honest.
"""
import base64
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ACCOUNT = "049ff5e84ecf636b53b162cbb580aae6"
MODEL = "@cf/black-forest-labs/flux-1-schnell"
URL = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/ai/run/{MODEL}"

PROMPT = (
    "A brass-and-wood telegraph room, painterly oil painting, warm lamplight. "
    "Every incoming message glows softly on a large wooden message board; the "
    "board's overall glow shifts to warm amber where a friendly exchange is "
    "happening. One single message among them glows red where the room's mood "
    "crossed a threshold. Brass instruments, curled copper wires, paper slips, "
    "atmospheric, cinematic, rich texture."
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "images" / "bus-space.png"


def _oauth_token() -> str:
    env = os.environ.get("CF_API_TOKEN")
    if env:
        return env
    cfg = os.path.expanduser("~/.config/.wrangler/config/default.toml")
    try:
        with open(cfg, encoding="utf-8") as f:
            for line in f:
                m = re.search(r'oauth_token\s*=\s*"([^"]+)"', line)
                if m:
                    return m.group(1)
    except OSError:
        pass
    raise SystemExit("no CF_API_TOKEN and no oauth_token in wrangler config")


def generate(prompt: str, token: str) -> bytes:
    body = json.dumps({"prompt": prompt}).encode("utf-8")
    req = urllib.request.Request(
        URL, data=body, method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not payload.get("success", False):
        raise RuntimeError(f"FLUX error: {payload.get('errors')}")
    return base64.b64decode(payload["result"]["image"])


def _ensure_png(data: bytes) -> bytes:
    """FLUX sometimes returns a JPEG; re-encode to PNG so the .png name is honest."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return data
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data)).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception as e:  # noqa: BLE001
        print(f"  (no PIL for jpeg->png: {e}; keeping raw bytes)")
        return data


def main() -> int:
    token = _oauth_token()
    for attempt in range(5):
        try:
            data = generate(PROMPT, token)
            if len(data) > 1000:
                data = _ensure_png(data)
                OUT.parent.mkdir(parents=True, exist_ok=True)
                with open(OUT, "wb") as f:
                    f.write(data)
                print(f"OK {len(data)} bytes -> {OUT}")
                return 0
            print(f"attempt {attempt}: image too small ({len(data)} bytes)")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code == 429:
                print(f"attempt {attempt}: 429, sleeping 8s")
            else:
                print(f"attempt {attempt}: HTTP {e.code} {body[:300]}")
        except Exception as e:  # noqa: BLE001
            print(f"attempt {attempt}: {type(e).__name__} {e}")
        time.sleep(8)
    print("FAILED after 5 attempts")
    return 1


if __name__ == "__main__":
    sys.exit(main())
