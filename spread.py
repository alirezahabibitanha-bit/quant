import sys, time, requests

URL = "https://api.wallex.ir/v1/depth"

def prices(side):
    return [float(x["price"] if isinstance(x, dict) else x[0]) for x in side]

def check(sym):
    d = requests.get(URL, params={"symbol": sym}, timeout=15).json()
    r = d.get("result", d)
    try:
        asks = prices(r.get("ask") or r.get("asks") or [])
        bids = prices(r.get("bid") or r.get("bids") or [])
    except Exception:
        print(sym, "unexpected format:", str(d)[:300])
        return
    if not asks or not bids:
        print(sym, "no data:", str(d)[:300])
        return
    buy, sell = min(asks), max(bids)
    gap = (buy - sell) / ((buy + sell) / 2) * 100
    print(f"{sym:10s} buy at {buy:,.2f} | sell at {sell:,.2f} | gap {gap:.3f}%")

symbols = sys.argv[1:] or ["USDTTMN", "PAXGTMN", "PAXGUSDT"]
for i in range(5):
    for s in symbols:
        check(s)
    print("-" * 60)
    if i < 4:
        time.sleep(10)
