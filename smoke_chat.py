import json
import time
import urllib.error
import urllib.request

payload = json.dumps({"message": "预算300元，玩一天，想了解徐州两汉历史文化"}, ensure_ascii=False).encode()
req = urllib.request.Request(
    "http://nginx/api/v1/chat",
    data=payload,
    headers={"Content-Type": "application/json"},
)
start = time.time()
try:
    resp = urllib.request.urlopen(req, timeout=60)
    raw = resp.read()
    elapsed = time.time() - start
    data = json.loads(raw)
    print(f"HTTP {resp.status}  {elapsed:.1f}s  bytes={len(raw)}")
    print("top-level keys:", list(data.keys()))
    plans = data.get("plans") or data.get("data", {}).get("plans") or []
    print("plan count:", len(plans))
    for i, p in enumerate(plans[:3], 1):
        title = p.get("title") or p.get("name")
        cost = p.get("total_cost") or p.get("cost") or p.get("budget")
        items = p.get("items") or []
        print(f"  #{i} {title} | cost={cost} | items={len(items)}")
    reply = data.get("reply") or data.get("message") or ""
    print("reply:", (reply[:120] if isinstance(reply, str) else reply))
except urllib.error.HTTPError as e:
    elapsed = time.time() - start
    body = e.read().decode("utf-8", "replace")
    print(f"HTTP {e.code} after {elapsed:.1f}s")
    print(body[:800])
except Exception as e:
    print("ERR", type(e).__name__, e)
