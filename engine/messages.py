"""
WhatsApp message text, built only from data passed in.

No number is typed into a template: quantities, sizes, prices, hours and times all come from
the arguments. Every message names a business, carries a link to the relevant page, stays
under 400 characters, has no emoji, writes rupees as "Rs 1,23,456", and ends with the data
label of its source ("Sample data", "Measured data").

Nothing here sends anything. wa_link() only builds a https://wa.me/ link; a person opens it
and presses send in WhatsApp.
"""
from __future__ import annotations

import re
import urllib.parse

MAX_CHARS = 399  # strictly under 400
FOOD_NOTE = "Food rules need expert review."
_EMOJI = re.compile("[\U00010000-\U0010FFFF☀-➿️‍]")


def inr(value: float) -> str:
    """Rupees in the Indian format, rounded: 1250 -> 'Rs 1,250', 123456 -> 'Rs 1,23,456'."""
    n = int(round(abs(value)))
    s = str(n)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        head = ",".join(re.findall(r"\d{1,2}", head[::-1]))[::-1] if len(head) > 2 else head
        s = head + "," + tail
    return ("-" if value < 0 and n else "") + "Rs " + s


def _n(v) -> str:
    """A plain number without trailing zeros: 6.0 -> '6', 1.5 -> '1.5', 1250 -> '1,250'."""
    if v is None:
        return ""
    if float(v).is_integer():
        return f"{int(v):,}"
    return f"{v:,.1f}".rstrip("0").rstrip(".")


def label_line(label: str | None) -> str:
    return f"{(label or 'sample').strip().capitalize()} data"


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", _EMOJI.sub("", str(text))).strip()


def _compose(body: str, link: str, label: str | None) -> str:
    """'<body> Details: <link> | <Label> data', with the body shortened if the whole would pass 400 characters."""
    tail = f" Details: {link} | {label_line(label)}" if link else f" | {label_line(label)}"
    body = _clean(body)
    room = MAX_CHARS - len(tail)
    if len(body) > room:
        body = body[: max(0, room - 3)].rstrip(" ,.;:") + "..."
    return body + tail


def _part(order: dict) -> str:
    """'6 spacers, 120 x 90 x 6 mm' or '4 rail posts, 450 mm' or '20 kg of vegetables'."""
    if order.get("categories"):
        return f"{_n(order['quantity'])} kg of {', '.join(c.replace('_', ' ') for c in order['categories'])}"
    name = order.get("part_name") or "parts"
    if order.get("cut_length_mm"):
        return f"{_n(order['quantity'])} x {name}, {_n(order['cut_length_mm'])} mm"
    return (f"{_n(order['quantity'])} x {name}, {_n(order['length_mm'])} x {_n(order['width_mm'])} x "
            f"{_n(order['thickness_mm'])} mm")


def new_match(order: dict, match: dict, seller: dict) -> str:
    """A buyer's order fits a seller's sheet before cutting (Gap Fill) or the seller's stock.
    match: {kind: 'gapfill'|'stock', units, unit_name, value (Rs, optional), link, label}."""
    head = ("New order fits your sheet before cutting" if match.get("kind") == "gapfill"
            else "New order matches your stock")
    unit = match.get("unit_name") or "parts"
    share = f"You can supply {_n(match['units'])} {unit}"
    if match.get("value") is not None:
        share += f", worth {inr(match['value'])} more than scrap value" if match.get("kind") != "gapfill" else f", worth {inr(match['value'])}"
    body = (f"{seller['name']}: {head}. {order['buyer_name']} needs {_part(order)}"
            f"{' (order ' + order['id'] + ')' if order.get('id') else ''}. {share}.")
    return _compose(body, match.get("link", ""), match.get("label") or order.get("label"))


def _deal_ref(deal: dict) -> str:
    return f"{'demo ' if deal.get('is_simulated', True) else ''}deal #{deal['id']}"


def order_accepted(deal: dict) -> str:
    """deal: {id, buyer_name, sellers [names], units, unit_name, part_name, is_simulated, link, label}"""
    body = (f"{deal['buyer_name']}: your order is accepted ({_deal_ref(deal)}). {_n(deal['units'])} {deal.get('unit_name') or 'parts'}"
            f"{' of ' + deal['part_name'] if deal.get('part_name') else ''} from {', '.join(deal['sellers'])}. Next: book the pickup.")
    return _compose(body, deal.get("link", ""), deal.get("label"))


def pickup_booked(deal: dict) -> str:
    """deal: {id, buyer_name, sellers [names, in pickup order], date, slot, vehicle (optional), is_simulated, link, label}"""
    body = (f"Pickup booked for {_deal_ref(deal)}: {deal['date']}, {deal['slot']}. Collect from {', then '.join(deal['sellers'])}. "
            f"Deliver to {deal['buyer_name']}.{' Vehicle: ' + deal['vehicle'] + '.' if deal.get('vehicle') else ''}")
    return _compose(body, deal.get("link", ""), deal.get("label"))


def delivered(deal: dict) -> str:
    """deal: {id, buyer_name, sellers [names], units, unit_name, evidence [..], is_simulated, link, label}"""
    ev = deal.get("evidence") or []
    body = (f"{deal['buyer_name']}: {_deal_ref(deal)} is marked delivered. {_n(deal['units'])} {deal.get('unit_name') or 'parts'} "
            f"from {', '.join(deal['sellers'])}.{' Evidence: ' + ', '.join(ev) + '.' if ev else ''} Please confirm you received it.")
    return _compose(body, deal.get("link", ""), deal.get("label"))


def fresh_expiring(item: dict) -> str:
    """item: {seller_name, product, quantity_kg, storage, hours_left, pickup_by, link, label}"""
    facts = (f"{item['seller_name']}: {item['product']}, {_n(item['quantity_kg'])} kg, {item['storage']}, "
             f"{_n(item['hours_left'])} h of shelf life left. Pick up by {item['pickup_by']}.")
    link, label = item.get("link", ""), item.get("label")
    tail = f" {FOOD_NOTE} Details: {link} | {label_line(label)}" if link else f" {FOOD_NOTE} | {label_line(label)}"
    facts, room = _clean(facts), MAX_CHARS - len(tail)
    if len(facts) > room:  # shorten the facts, never the food-rules line, the link or the label
        facts = facts[: max(0, room - 3)].rstrip(" ,.;:") + "..."
    return facts + tail


def offcut_share(item: dict, passport_url: str) -> str:
    """One stock item, shared by hand. item: {seller_name, description, kg, scrap_value (optional), label}"""
    price = f" | Scrap value {inr(item['scrap_value'])}" if item.get("scrap_value") is not None else ""
    body = f"{item['seller_name']} | {item['description']} | {_n(item['kg'])} kg{price} |"
    return _compose(body, passport_url, item.get("label"))


# ---------------- wa.me links ----------------
PHONE_ERROR = "Enter a 10-digit mobile number, or the full number with its country code (11 to 15 digits)."


def normalize_phone(phone: str) -> str:
    """Digits for wa.me. Spaces, +, -, brackets and dots are removed. A 10-digit number gets 91 (India);
    a 0 followed by 10 digits becomes 91 and those 10 digits. 11 to 15 digits are kept. Anything else is refused."""
    digits = re.sub(r"[\s+\-().]", "", str(phone or ""))
    if not digits.isdigit():
        raise ValueError(PHONE_ERROR)
    if len(digits) == 10:
        return "91" + digits
    if len(digits) == 11 and digits.startswith("0"):
        return "91" + digits[1:]
    if 11 <= len(digits) <= 15:
        return digits
    raise ValueError(PHONE_ERROR)


def mask_phone(digits: str) -> str:
    """'919876543210' -> '+91 ******3210'."""
    cc, rest = (digits[:-10], digits[-10:]) if len(digits) > 10 else ("", digits)
    return (f"+{cc} " if cc else "") + "*" * max(0, len(rest) - 4) + rest[-4:]


def wa_link(text: str, phone: str | None = None) -> str:
    """https://wa.me/<digits>?text=<urlencoded>, or https://wa.me/?text=<urlencoded> to let the person choose the chat."""
    q = urllib.parse.quote(text, safe="")
    return f"https://wa.me/{normalize_phone(phone)}?text={q}" if phone else f"https://wa.me/?text={q}"
