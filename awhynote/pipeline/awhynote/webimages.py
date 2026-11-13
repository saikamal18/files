"""Fill shots the creator has no photo for with openly licensed pictures from the web.

Sources: Wikimedia Commons and Openverse (no key), Pexels (free key, optional). Only licences that allow reuse with credit
(public domain, CC0, CC BY, CC BY-SA; CC BY / CC0 / PD only in strict mode) are accepted, and every pick is credited.
Shots about banknotes are never fetched: the creator photographs the real note.
"""
from __future__ import annotations

import ipaddress
import logging
import re
from dataclasses import asdict, dataclass
from html import unescape
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image

from . import media, prompts
from .config import Config
from .llm import LLM
from .store import Episode
from .util import read_json, write_json

log = logging.getLogger("awhynote")

UA = "awhynote/0.2 (personal Reels project; https://instagram.com/awhynote)"
MAX_BYTES = 15 * 1024 * 1024
MIN_SHORT, MIN_LONG = 700, 1000
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
OPENVERSE_API = "https://api.openverse.org/v1/images/"
PEXELS_API = "https://api.pexels.com/v1/search"
GOOD_TYPES = {"image/jpeg", "image/png", "image/webp"}

# The creator photographs the real note. Reproducing banknotes is regulated, so never auto-fetch them.
NOTE_TOPIC = re.compile(r"(?:₹|\brs\.?|\binr)\s?\d+\s*(?:rupee\s+)?notes?\b|\b(?:bank|currency|rupee)\s?notes?\b|\bbanknotes?\b", re.I)
NOTE_TITLE = re.compile(r"bank ?notes?|currency|rupees?\b|₹|\brs\.? ?\d|\bnotes? (?:of|series)\b|\bmahatma gandhi (?:new )?series", re.I)
STOP = set("a an the of on in at to for with and or by from is are was were this that it its as into over close close-up closeup up wide shot shots footage clip scene showing real photo photos image picture your own macro detail view establishing beat group".split())


@dataclass
class Candidate:
    source: str
    title: str
    url: str
    page_url: str
    author: str
    license: str
    width: int
    height: int
    rank: int = 0
    score: float = 0.0

    def credit(self) -> str:
        who = f"{self.author}, " if self.author else ""
        return f"{self.title} ({who}{self.license}, {self.source}) {self.page_url}".strip()


# -- licences ----------------------------------------------------------------
def license_tier(name: str, strict: bool = False) -> int | None:
    """3 = public domain / CC0, 2 = CC BY, 1 = CC BY-SA. None = not usable."""
    n = re.sub(r"[\s_-]+", " ", (name or "").lower()).strip()
    if not n or re.search(r"\b(nc|nd|noncommercial|no derivatives|fair use|copyrighted|all rights reserved)\b", n):
        return None
    if re.search(r"\bcc0\b|public domain|\bpdm\b|\bpd\b|no known copyright|pexels", n):
        return 3
    if re.search(r"\bcc by sa\b|\bby sa\b|attribution share ?alike|^by sa", n):
        return None if strict else 1
    if re.search(r"\bcc by\b|^by\b|attribution", n):
        return 2
    return None


def _plain(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html or ""))).strip()


def is_note_topic(*texts: str) -> bool:
    return any(NOTE_TOPIC.search(t or "") for t in texts)


# -- queries -----------------------------------------------------------------
def heuristic_queries(shot: dict, topic_title: str = "") -> list[str]:
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'-]+|\d{3,4}", shot.get("brief", "")) if w.lower() not in STOP]
    base = " ".join(words[:5])
    out = []
    loc = (shot.get("location_tag") or "").strip().title()
    year = (shot.get("year_stamp") or "").strip()
    if loc and base:
        out.append(f"{loc} {base}")
    if base:
        out.append(f"{base} {year}".strip())
    if loc:
        out.append(f"{loc} {year}".strip())
    if topic_title and not is_note_topic(topic_title):
        out.append(re.sub(r"[^\w\s]", " ", topic_title).strip())
    seen, uniq = set(), []
    for q in out:
        q = re.sub(r"\s+", " ", q).strip()
        if q and q.lower() not in seen:
            seen.add(q.lower())
            uniq.append(q)
    return uniq[:4]


def make_queries(cfg: Config, ep: Episode, shots: list[dict]) -> dict[int, list[str]]:
    """Two or three short search queries per shot: from the LLM when available, otherwise from the shot brief."""
    out = {s["id"]: heuristic_queries(s, ep.topic.get("hook_title", "")) for s in shots}
    if not shots:
        return out
    try:
        system, user = prompts.web_queries_prompt(ep.topic, shots)
        data = LLM(cfg).complete_json(system, user, task="web_queries", mock_payload={"topic": ep.topic, "shots": shots})
        for sid, qs in (data.get("queries") or {}).items():
            qs = [re.sub(r"\s+", " ", q).strip() for q in qs if isinstance(q, str) and 2 <= len(q.strip()) <= 80]
            if qs and int(sid) in out:
                out[int(sid)] = qs[:3] + [q for q in out[int(sid)] if q not in qs][:1]
    except Exception as e:  # the heuristic queries are good enough to carry on
        log.warning("web query generation failed, using the shot briefs: %s", e)
    return out


# -- sources -----------------------------------------------------------------
def _get_json(http, url: str, **kw) -> dict:
    r = http.get(url, timeout=(5, 20), headers={"User-Agent": UA, **(kw.pop("headers", {}))}, **kw)
    if getattr(r, "status_code", 200) != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    return r.json()


def search_commons(http, q: str, limit: int = 10) -> list[Candidate]:
    data = _get_json(http, COMMONS_API, params={
        "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6, "gsrlimit": limit,
        "gsrsearch": f"{q} filetype:bitmap", "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 1600,
        "iiextmetadatafilter": "LicenseShortName|Artist|Restrictions|ObjectName"})
    out = []
    for page in (data.get("query") or {}).get("pages", {}).values():
        ii = (page.get("imageinfo") or [{}])[0]
        em = ii.get("extmetadata") or {}
        if ii.get("mime") not in GOOD_TYPES or _plain((em.get("Restrictions") or {}).get("value", "")):
            continue
        w, h = int(ii.get("width") or 0), int(ii.get("height") or 0)
        thumb = ii.get("thumburl") if ii.get("thumbwidth") and int(ii["thumbwidth"]) < w else ii.get("url")
        out.append(Candidate("Wikimedia Commons", re.sub(r"^File:|\.\w+$", "", page.get("title", "")), thumb or "", ii.get("descriptionurl", ""),
                             _plain((em.get("Artist") or {}).get("value", ""))[:80], _plain((em.get("LicenseShortName") or {}).get("value", "")),
                             w, h, int(page.get("index") or 99)))
    return out


def search_openverse(http, q: str, limit: int = 10) -> list[Candidate]:
    data = _get_json(http, OPENVERSE_API, params={"q": q, "page_size": limit, "license_type": "commercial", "mature": "false"})
    out = []
    for i, r in enumerate(data.get("results") or [], 1):
        lic = f"{r.get('license', '')} {r.get('license_version', '')}".strip()
        lic = ("CC " + lic.upper()) if not lic.lower().startswith(("cc", "pd")) and lic else lic
        ftype = (r.get("filetype") or "").lower()
        if ftype and ftype not in ("jpg", "jpeg", "png", "webp"):
            continue
        out.append(Candidate("Openverse", r.get("title") or "untitled", r.get("url") or "", r.get("foreign_landing_url") or "",
                             (r.get("creator") or "")[:80], lic, int(r.get("width") or 0), int(r.get("height") or 0), i))
    return out


def search_pexels(http, key: str, q: str, limit: int = 10) -> list[Candidate]:
    data = _get_json(http, PEXELS_API, params={"query": q, "per_page": limit, "orientation": "portrait"}, headers={"Authorization": key})
    out = []
    for i, p in enumerate(data.get("photos") or [], 1):
        src = p.get("src") or {}
        out.append(Candidate("Pexels", (p.get("alt") or f"Pexels photo {p.get('id', '')}")[:80], src.get("large2x") or src.get("large") or "", p.get("url", ""),
                             p.get("photographer", ""), "Pexels License", int(p.get("width") or 0), int(p.get("height") or 0), i))
    return out


def search(cfg: Config, q: str, http=None) -> list[Candidate]:
    http = http or requests
    sources = [s.strip() for s in (cfg.web_sources or "commons,openverse,pexels").split(",") if s.strip()]
    out: list[Candidate] = []
    for s in sources:
        try:
            if s == "commons":
                out += search_commons(http, q)
            elif s == "openverse":
                out += search_openverse(http, q)
            elif s == "pexels" and cfg.pexels_key:
                out += search_pexels(http, cfg.pexels_key, q)
        except Exception as e:
            log.warning("%s search for %r failed: %s", s, q, e)
    return out


# -- choosing ----------------------------------------------------------------
def _usable(c: Candidate, strict: bool) -> bool:
    short, long_ = sorted((c.width, c.height))
    if not c.url.startswith("https://") or license_tier(c.license, strict) is None or NOTE_TITLE.search(c.title):
        return False
    host = urlparse(c.url).hostname or ""
    try:
        ipaddress.ip_address(host)
        return False  # a bare IP address is never an image CDN
    except ValueError:
        pass
    if host == "localhost" or host.endswith((".local", ".internal")):
        return False
    return not c.width or (short >= MIN_SHORT and long_ >= MIN_LONG)


def rank(cands: list[Candidate], strict: bool = False) -> list[Candidate]:
    for c in cands:
        short = min(c.width, c.height) if c.width and c.height else 800
        portrait = 0.6 if c.height > c.width else 0.0
        c.score = min(short, 2000) / 2000 * 3 + portrait + license_tier(c.license, strict) * 0.5 - 0.12 * c.rank
    return sorted(cands, key=lambda c: (-c.score, c.url))


def pick(cfg: Config, queries: list[str], skip_urls: set[str], http=None) -> Candidate | None:
    strict = cfg.web_licenses == "strict"
    pool: dict[str, Candidate] = {}
    for q in queries:
        for c in search(cfg, q, http):
            if _usable(c, strict) and c.url not in skip_urls:
                pool.setdefault(c.url, c)
        if len(pool) >= 6:
            break
    ranked = rank(list(pool.values()), strict)
    return ranked[0] if ranked else None


def download(c: Candidate, http=None) -> tuple[bytes, str]:
    http = http or requests
    r = http.get(c.url, stream=True, timeout=(5, 30), headers={"User-Agent": UA})
    if getattr(r, "status_code", 200) != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    ctype = (r.headers.get("content-type", "") or "").split(";")[0].strip().lower()
    if ctype and ctype not in GOOD_TYPES:
        raise ValueError(f"not a photo (content-type {ctype})")
    buf = bytearray()
    for chunk in r.iter_content(1 << 16):
        buf.extend(chunk)
        if len(buf) > MAX_BYTES:
            raise ValueError("image is larger than 15 MB")
    im = Image.open(BytesIO(bytes(buf)))
    im.verify()
    w, h = Image.open(BytesIO(bytes(buf))).size
    if min(w, h) < MIN_SHORT or max(w, h) < MIN_LONG:
        raise ValueError(f"image too small ({w}x{h})")
    return bytes(buf), {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}.get(im.format, ".jpg")


# -- episode bookkeeping -----------------------------------------------------
def _path(ep: Episode) -> Path:
    return ep.path("web_images.json")


def state(ep: Episode) -> dict:
    s = read_json(_path(ep), {}) or {}
    s.setdefault("shots", {})  # shot id -> {"queries": [...], "tried": [urls], "file": name, "credit": {...}}
    return s


def credits(ep: Episode) -> list[dict]:
    """Credits for pictures that are still in the media folder."""
    out = []
    for sid, d in sorted(state(ep)["shots"].items(), key=lambda kv: int(kv[0])):
        f = d.get("file")
        if f and (media.inbox(ep) / f).exists():
            out.append({"shot": int(sid), **d["credit"]})
    return out


def credit_lines(ep: Episode) -> str:
    cs = credits(ep)
    if not cs:
        return ""
    return "Images:\n" + "\n".join(f"• shot {c['shot']}: {c['text']}" for c in cs)


def web_files(ep: Episode) -> dict[int, str]:
    return {int(sid): d["file"] for sid, d in state(ep)["shots"].items() if d.get("file") and (media.inbox(ep) / d["file"]).exists()}


@dataclass
class Result:
    shot: int
    ok: bool
    message: str
    path: Path | None = None


def fill_shot(cfg: Config, ep: Episode, shot: dict, queries: list[str], http=None) -> Result:
    sid = shot["id"]
    if is_note_topic(shot.get("brief", ""), shot.get("stat_text", ""), *queries):
        return Result(sid, False, "this shot is about the note itself: photograph the real one (I never fetch banknote images)")
    st = state(ep)
    entry = st["shots"].setdefault(str(sid), {"tried": []})
    used = {u for d in st["shots"].values() for u in d.get("tried", [])}
    cand = pick(cfg, queries, used, http)
    for _ in range(3):  # a few attempts when a download turns out unusable
        if not cand:
            break
        entry["tried"].append(cand.url)
        try:
            data, ext = download(cand, http)
        except Exception as e:
            log.info("skipping %s: %s", cand.url, e)
            write_json(_path(ep), st)
            cand = pick(cfg, queries, used | set(entry["tried"]), http)
            continue
        name = re.sub(r"[^A-Za-z0-9]+", "-", cand.title).strip("-")[:30] or "web"
        dest = media.add_file(ep, sid, data, f"web-{name}{ext}")
        entry.update({"queries": queries, "file": dest.name, "credit": {**{k: v for k, v in asdict(cand).items() if k in ("title", "author", "license", "source")},
                                                                         "url": cand.page_url, "text": cand.credit()}})
        write_json(_path(ep), st)
        return Result(sid, True, f"{cand.title} · {cand.author or 'unknown author'} · {cand.license} · {cand.source}", dest)
    write_json(_path(ep), st)
    return Result(sid, False, "no suitable open-licence image found: send your own or /skip it")


def fill(cfg: Config, ep: Episode, script: dict, shot_ids: list[int], http=None) -> list[Result]:
    """Fetch one picture for each requested shot. Sources and queries are looked up once for the batch."""
    by_id = {s["id"]: s for s in script["shots"]}
    shots = [by_id[i] for i in shot_ids if i in by_id]
    queries = make_queries(cfg, ep, shots)
    return [fill_shot(cfg, ep, s, queries[s["id"]], http) for s in shots]


def discard(ep: Episode, shot: int) -> None:
    """Move a previously fetched picture out of the way so the next candidate can take its place."""
    st = state(ep)
    d = st["shots"].get(str(shot))
    if d and d.get("file"):
        f = media.inbox(ep) / d["file"]
        if f.exists():
            trash = ep.path("media/trash")
            trash.mkdir(parents=True, exist_ok=True)
            f.replace(trash / f.name)
        d["file"] = None
        write_json(_path(ep), st)


def preview(ep: Episode, path: Path) -> Path:
    """A small JPEG for Telegram (sendPhoto refuses files over 10 MB)."""
    out = ep.path("media/web_preview")
    out.mkdir(parents=True, exist_ok=True)
    dest = out / (Path(path).stem + ".jpg")
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((1100, 1100))
        im.save(dest, quality=88)
    return dest
