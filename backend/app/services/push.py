"""Web push notifications (phone/browser notifications, even when FINN is closed).

The browser gives us a "subscription" (an address at Google/Apple/Mozilla's push service)
when you allow notifications. We send small encrypted messages to it with pywebpush.
The signing keys (VAPID) are made once and kept in the database.
"""

import base64
import json
import logging
import time

from app import db
from app.obs import timed

log = logging.getLogger("finn.push")

def _contact() -> str:
    """Who push services can contact about these messages: PUSH_CONTACT, else the deployed
    frontend's https address, else a placeholder (fine for local testing)."""
    from app.config import settings

    if settings.push_contact:
        return settings.push_contact
    origin = settings.frontend_origin.split(",")[0].strip().rstrip("/")
    if origin.startswith("https://"):
        return origin
    return "mailto:alerts@finn-app.dev"

QUIET_HOURS = (22, 7)  # IST: no pushes from 10 pm to 7 am; they're held and sent at 7 am

_vapid = None


def _keys():
    global _vapid
    if _vapid is None:
        from py_vapid import Vapid

        pem = db.kv_get("vapid_pem")
        if pem:
            _vapid = Vapid.from_pem(pem.encode())
        else:
            _vapid = Vapid()
            _vapid.generate_keys()
            db.kv_set("vapid_pem", _vapid.private_pem().decode())
            log.info("push: created new signing keys")
    return _vapid


def public_key() -> str:
    """The key the browser needs to subscribe (base64url, uncompressed P-256 point)."""
    from cryptography.hazmat.primitives import serialization

    raw = _keys().public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def subscribe(sub: dict) -> None:
    db.execute("INSERT OR REPLACE INTO push_subs (endpoint, sub, created) VALUES (?, ?, ?)",
               (sub["endpoint"], json.dumps(sub), time.time()))
    log.info("push: device subscribed (%d total)", count())


def unsubscribe(endpoint: str) -> None:
    db.execute("DELETE FROM push_subs WHERE endpoint = ?", (endpoint,))


def count() -> int:
    return db.query("SELECT COUNT(*) FROM push_subs")[0][0]


def _send(sub: dict, payload: dict) -> bool:
    from pywebpush import WebPushException, webpush

    try:
        webpush(
            subscription_info=sub,
            data=json.dumps(payload),
            vapid_private_key=_keys(),
            vapid_claims={"sub": _contact()},
            ttl=12 * 3600,
            timeout=10,
        )
        return True
    except WebPushException as e:
        status = getattr(e.response, "status_code", None)
        if status in (404, 410):  # the browser unsubscribed / the address expired
            unsubscribe(sub["endpoint"])
            log.info("push: removed an expired device")
        else:
            log.warning("push: send failed (%s): %s", status, str(e)[:200])
        return False
    except Exception as e:  # noqa: BLE001 - network trouble with one device must not stop the others
        log.warning("push: couldn't reach a device (%s): %s", type(e).__name__, str(e)[:200])
        return False


def test_send(endpoint: str, payload: dict) -> dict:
    """Send one message to one device and report exactly what the push service answered
    (used by "Send me a test alert" so problems can be told apart)."""
    from urllib.parse import urlparse

    from pywebpush import WebPushException, webpush

    rows = db.query("SELECT sub FROM push_subs WHERE endpoint = ?", (endpoint,))
    service = urlparse(endpoint).netloc
    if not rows:
        return {"ok": False, "service": service, "status": None,
                "reason": "This device isn't registered on the server (it may have restarted). Turn notifications off and on."}
    try:
        with timed(log, "push: test", service=service) as t:
            r = webpush(subscription_info=json.loads(rows[0][0]), data=json.dumps(payload),
                        vapid_private_key=_keys(), vapid_claims={"sub": _contact()}, ttl=600, timeout=10)
            t["status"] = r.status_code
        return {"ok": True, "service": service, "status": r.status_code, "reason": None}
    except WebPushException as e:
        status = getattr(e.response, "status_code", None)
        body = getattr(e.response, "text", "")[:200] if e.response is not None else str(e)[:200]
        if status in (404, 410):
            unsubscribe(endpoint)
        return {"ok": False, "service": service, "status": status, "reason": body}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "service": service, "status": None, "reason": f"{type(e).__name__}: {str(e)[:200]}"}


def send(payload: dict, endpoint: str | None = None) -> int:
    """Send to every subscribed device (or just one). Returns how many got it."""
    rows = db.query("SELECT sub FROM push_subs" + (" WHERE endpoint = ?" if endpoint else ""),
                    (endpoint,) if endpoint else ())
    if not rows:
        return 0
    with timed(log, "push: send", devices=len(rows), title=payload.get("title", "")[:40]) as t:
        sent = sum(_send(json.loads(r[0]), payload) for r in rows)
        t["delivered"] = sent
    return sent


def quiet_now() -> bool:
    import datetime as dt

    h = dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30))).hour
    start, end = QUIET_HOURS
    return h >= start or h < end


def flush(pending: list[dict]) -> list[str]:
    """Push waiting alerts: one notification if there's one, a bundled one if several.
    Returns the alert ids handled (also when nobody is subscribed, so they don't pile up)."""
    if not pending or quiet_now():
        return []
    if count():
        if len(pending) == 1:
            a = pending[0]
            payload = {"title": a["title"], "body": a["body"], "url": a["url"], "tag": a["id"]}
        else:
            payload = {
                "title": f"{len(pending)} new alerts",
                "body": " · ".join(a["title"] for a in pending[:4]) + (" …" if len(pending) > 4 else ""),
                "url": "/alerts",
                "tag": "finn-bundle",
            }
        send(payload)
    return [a["id"] for a in pending]
