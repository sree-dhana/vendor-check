import re

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


def _yn(v):
    return "yes" if v else "no"


# EU VIES VAT check. Request/response from the official spec
# https://ec.europa.eu/assets/taxud/vow-information/swagger_publicVAT.yaml; base URL tested
# against its own test service (vatNumber 100 -> valid, 200 -> invalid) on 2026-11-02.
VIES_URL = "https://ec.europa.eu/taxation_customs/vies/rest-api/check-vat-number"
VIES_COUNTRY = {"GR": "EL"}  # VIES uses EL for Greece


def lookup_vies(country, vat_number, store):
    """Return {'status': 'valid'|'invalid'|'unavailable', 'name': str}. Outages, member-state
    downtime, rate limits and odd responses are all 'unavailable' (could not confirm)."""
    cc = VIES_COUNTRY.get(country, country)
    number = re.sub(r"[\s\-.]", "", vat_number or "").upper()
    if number[:2] in (cc, country):
        number = number[2:]
    key = cc + number
    hit = _cache_get(store, "vies_cache", key)
    if hit:
        return hit
    try:
        r = requests.post(VIES_URL, json={"countryCode": cc, "vatNumber": number},
                          timeout=TIMEOUT, headers=HEADERS)
        j = r.json() if r.status_code == 200 else None
    except Exception:
        j = None
    if not isinstance(j, dict) or not isinstance(j.get("valid"), bool):
        return {"status": "unavailable"}
    out = {"status": "valid" if j["valid"] else "invalid", "name": _clip(j.get("name"))}
    if out["name"] == "---":
        out["name"] = ""
    _cache_put(store, "vies_cache", key, out)
    return out


def vies_result(country, vat_number, store):
    lk = lookup_vies(country, vat_number, store)
    r = {"kind": "vat", "id": vat_number}
    if lk["status"] == "valid":
        r["level"] = "pass"
        r["text"] = "VIES lists this VAT number as valid%s." % (
            " (registered name: %s)" % lk["name"] if lk.get("name") else "")
    elif lk["status"] == "invalid":
        r["level"] = "warn"
        r["text"] = ("VIES does not list this VAT number as valid for EU cross-border trade. It may "
                     "still be a domestic number; ask the vendor.")
    else:
        r["level"] = "warn"
        r["text"] = "the EU VIES service didn't respond or couldn't check this country, so I couldn't confirm it."
    return r


def ifsc_result(code, store):
    """Check result for an IFSC: pass with the branch, fail if not found, warn if the
    lookup service did not respond."""
    lk = lookup_ifsc(code, store)
    r = {"kind": "ifsc", "id": code}
    if lk["status"] == "ok":
        d = lk["data"]
        place = ", ".join([x for x in (d["city"], d["state"]) if x])
        r["level"] = "pass"
        r["state"] = d["state"]
        r["bank"] = d["bank"]
        r["text"] = "%s, %s (%s). NEFT %s, RTGS %s, IMPS %s, UPI %s." % (
            d["bank"], d["branch"], place,
            _yn(d["neft"]), _yn(d["rtgs"]), _yn(d["imps"]), _yn(d["upi"]),
        )
    elif lk["status"] == "not_found":
        r["level"] = "fail"
        r["text"] = "well-formed, but no bank branch with this code was found."
    else:
        r["level"] = "warn"
        r["text"] = "format is valid, but the lookup service didn't respond, so I couldn't confirm the branch."
    return r


def pin_result(pin, store):
    lk = lookup_pin(pin, store)
    r = {"kind": "pin", "id": pin}
    if lk["status"] == "ok":
        d = lk["data"]
        r["level"] = "pass"
        r["state"] = d["state"]
        r["text"] = "%s, %s." % (d["district"], d["state"])
    elif lk["status"] == "not_found":
        r["level"] = "fail"
        r["text"] = "well-formed, but no area with this PIN code was found."
    else:
        r["level"] = "warn"
        r["text"] = "format is valid, but the lookup service didn't respond, so I couldn't confirm the area."
    return r


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
