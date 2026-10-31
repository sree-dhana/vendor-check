import requests

IFSC_URL = "https://ifsc.razorpay.com/{}"
PIN_URLS = [
    "https://api.postalpincode.in/pincode/{}",
    "http://www.postalpincode.in/api/pincode/{}",
]
TIMEOUT = 4
CACHE_MAX = 150
# postalpincode.in drops connections that use the default python-requests User-Agent.
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; VendorCheck/0.1)", "Accept": "application/json"}


def _clip(v, n=80):
    if v is None:
        return ""
    return str(v).strip()[:n]


def _cache_get(store, name, key):
    try:
        cache = store.get(name, {})
        if isinstance(cache, dict):
            return cache.get(key)
    except Exception:
        pass
    return None


def _cache_put(store, name, key, value):
    try:
        cache = store.get(name, {})
        if not isinstance(cache, dict):
            cache = {}
        cache[key] = value
        while len(cache) > CACHE_MAX:
            cache.pop(next(iter(cache)))
        store.set(name, cache)
    except Exception:
        pass


def lookup_ifsc(code, store):
    """Return {'status': 'ok'|'not_found'|'unavailable', 'data': {...}}."""
    hit = _cache_get(store, "ifsc_cache", code)
    if hit:
        return {"status": "ok", "data": hit}
    try:
        r = requests.get(IFSC_URL.format(code), timeout=TIMEOUT, headers=HEADERS)
    except Exception:
        return {"status": "unavailable"}
    if r.status_code == 404:
        return {"status": "not_found"}
    if r.status_code != 200:
        return {"status": "unavailable"}
    try:
        j = r.json()
    except Exception:
        return {"status": "unavailable"}
    if not isinstance(j, dict) or not j.get("BANK"):
        return {"status": "unavailable"}
    data = {
        "bank": _clip(j.get("BANK")),
        "branch": _clip(j.get("BRANCH")),
        "city": _clip(j.get("CITY")),
        "district": _clip(j.get("DISTRICT")),
        "state": _clip(j.get("STATE")),
        "neft": bool(j.get("NEFT")),
        "rtgs": bool(j.get("RTGS")),
        "imps": bool(j.get("IMPS")),
        "upi": bool(j.get("UPI")),
    }
    _cache_put(store, "ifsc_cache", code, data)
    return {"status": "ok", "data": data}


def _parse_pin_payload(j):
    """Return ('ok', data) | ('not_found', None) | ('unavailable', None). Handles both
    list-shaped and dict-shaped payloads."""
    if isinstance(j, list):
        j = j[0] if j else {}
    if not isinstance(j, dict):
        return "unavailable", None
    status = str(j.get("Status", "")).lower()
    message = str(j.get("Message", "")).lower()
    po = j.get("PostOffice")
    if isinstance(po, dict):
        po = [po]
    if isinstance(po, list) and po and isinstance(po[0], dict) and po[0].get("State"):
        first = po[0]
        return "ok", {
            "area": _clip(first.get("Name")),
            "district": _clip(first.get("District")),
            "state": _clip(first.get("State")),
        }
    if status == "error" or "no records" in message or "not found" in message:
        return "not_found", None
    return "unavailable", None


def lookup_pin(pin, store):
    hit = _cache_get(store, "pin_cache", pin)
    if hit:
        return {"status": "ok", "data": hit}
    for url in PIN_URLS:
        try:
            r = requests.get(url.format(pin), timeout=TIMEOUT, headers=HEADERS)
        except Exception:
            continue
        if r.status_code != 200:
            continue
        try:
            j = r.json()
        except Exception:
            continue
        status, data = _parse_pin_payload(j)
        if status == "ok":
            _cache_put(store, "pin_cache", pin, data)
            return {"status": "ok", "data": data}
        if status == "not_found":
            return {"status": "not_found"}
    return {"status": "unavailable"}
