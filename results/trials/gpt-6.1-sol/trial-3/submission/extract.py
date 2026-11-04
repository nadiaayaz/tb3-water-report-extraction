#!/usr/bin/env python3
"""Offline, geometry-aware extractor for Consumer Confidence Report result tables."""
import collections
import dataclasses
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
import pdfplumber
from pdfplumber.utils import extract_text as chars_text

START = time.monotonic()
DEBUG = bool(os.environ.get("CCR_DEBUG"))


def log(*args):
    if DEBUG:
        print(*args, file=sys.stderr)


def norm(s):
    return re.sub(r"\s+", " ", s or "").strip()


def fix(s):
    for a, b in {
        "\uf06d": "µ",
        "μ": "µ",
        "\uf0a3": "≤",
        "\uf0b3": "≥",
        "\uf02d": "-",
        "\uf0fc": "✓",
        "\uf0d6": "✓",
        "ﬀ": "ff",
        "ﬁ": "fi",
        "ﬂ": "fl",
        "ﬃ": "ffi",
        "ﬄ": "ffl",
        "\u00ad": "",
        "\xa0": " ",
        "\u200b": "",
        "\u2212": "-",
    }.items():
        s = (s or "").replace(a, b)
    return s


NULL_RE = re.compile(
    r"^(?:n\s*[./]?\s*a\.?|not\s+applicable|none|no\s+range|[-–—]+|see\s+(?:foot)?note\s*\d*\.?|refer\s+to\s+note\s*\d*\.?)$",
    re.I,
)
NUM = r"(?:[+-]?(?:\d[\d,]*\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)"
ATOM = rf"(?:(?:[<>]=?|[≤≥])\s*)?(?:{NUM}|N[./]?D\.?|B\.?D\.?L\.?|not\s+detected|absent|non[- ]detect(?:ed)?)\s*%?"
UNIT_PAT = r"(?:p\s?Ci\s*/\s*[lL]|[pnumµμ]g\s*/\s*[lL]|[mµμu]S\s*/\s*cm|mrem\s*/\s*(?:yr|year)|mrem|ppm|ppb|ppt|ppq|NTU|[mµμu]mho(?:s)?\s*/\s*cm|CFU\s*/\s*(?:100\s*)?m[lL]|MPN\s*/\s*(?:100\s*)?m[lL]|grains?\s*(?:per|/)\s*gall?on|gpg|SU|s\.u\.|standard\s+units?|color\s+units|units?|TON|°[CF]|degrees\s*[CF])"
UNIT_RE = re.compile(
    r"(?<![A-Za-z])("
    + UNIT_PAT
    + r")(?:\s+as\s+(?:CaCO3|CaCO₃|N(?:itrogen)?|P|Cl2|Cl₂|C))?(?![A-Za-z])",
    re.I,
)


def strip_notes(s):
    s = fix(s or "")
    s = re.sub(r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁺†‡*]+", "", s)
    s = re.sub(r"\[\s*\d+(?:\s*[,–-]\s*\d+)*\s*\]", "", s)
    s = re.sub(r"(?<=\d)\s*\([1-9]\)\s*$", "", s)
    s = re.sub(r"(?<=\d)[a-f]\s*$", "", s)
    return re.sub(r"(?<=\d)\s*\([a-f]\)(?=\s|$)", "", s, flags=re.I).strip()


def unit_in(s):
    s = norm(fix(s))
    if re.fullmatch(r"(?:percent\s+removal|removal\s+ratio|ratio|percent|%)", s, re.I):
        return None
    matches = list(UNIT_RE.finditer(s))
    proper = [m for m in matches if m.group().lower() not in ("unit", "units")]
    if proper:
        return norm(proper[0].group())
    if matches and re.search(r"\d", s[: matches[0].start()]):
        return norm(matches[0].group())
    return None


def value(s, field="level"):
    s = norm(strip_notes(s))
    if not s or NULL_RE.fullmatch(s):
        return None
    s = re.sub(r"(?<=\d)\s+([a-d])$", "", s)
    paren = re.fullmatch(r"\(\s*(" + ATOM + r"\s*(?:" + UNIT_PAT + r")?)\s*\)", s, re.I)
    if paren:
        s = paren.group(1)
    if field == "level":
        s = re.sub(
            r"\s*\((?:RAA|LRAA|MPA|annual average|average|avg\.?|maximum|max\.?|90th percentile)\)\s*$",
            "",
            s,
            flags=re.I,
        )
    assignment = re.fullmatch(
        r"((?:MCL|MRDLG?|AL|TT)\s*[:=]?\s*" + NUM + r")\s*(?:" + UNIT_PAT + r")(?:\s+as\s+\w+)?",
        s,
        re.I,
    )
    if assignment:
        s = assignment.group(1)
    m = re.fullmatch(
        r"((?:[<>]=?|[≤≥])?\s*"
        + NUM
        + r")\s*(?:\(?("
        + UNIT_PAT
        + r")(?:\s+as\s+[A-Za-z0-9₃₂]+)?\)?)?",
        s,
        re.I,
    )
    if m:
        n = re.sub(r"\s+", "", m.group(1))
        if n[0] in "<>≤≥":
            return n
        try:
            f = float(n.replace(",", ""))
            return int(f) if f.is_integer() else f
        except ValueError:
            pass
    if field == "level":
        v = re.sub(
            r"^(?:90(?:th|%)?\s*percentile(?:\s+value)?|90th\s+%ile|highest\s+(?:single\s+)?(?:measurement|result|value|level)|max(?:imum)?(?:\s+(?:value|result))?|average(?:\s+(?:value|result))?|avg\.?|mean|LRAA|RAA|result|level(?:\s+detected)?)\s*(?:value)?\s*[:=–-]?\s*",
            "",
            s,
            flags=re.I,
        )
        if v != s:
            return value(v, "plain")
        label = re.fullmatch(
            r"[A-Za-z][^:=]*[:=]\s*(" + ATOM + r")\s*(?:" + UNIT_PAT + r")?", s, re.I
        )
        if label:
            return value(label.group(1), "plain")
    return s or None


def value_like(s):
    s = norm(strip_notes(s))
    if not s:
        return False
    if NULL_RE.fullmatch(s) or re.fullmatch(
        r"(?:yes|no|Y|N|ND|BDL|absent|present|negative|positive|TT|✓|✔|✗|✘)", s, re.I
    ):
        return True
    if re.match(r"^(?:MCL|MRDLG?|AL|TT)\s*[:= -]?\s*\d", s, re.I):
        return True
    if re.fullmatch(
        r"\(?\s*"
        + ATOM
        + r"(?:\s*[-–—−/]\s*"
        + ATOM
        + r")?\s*\)?\s*(?:"
        + UNIT_PAT
        + r")?(?:\s+as\s+\w+)?",
        s,
        re.I,
    ):
        return True
    if re.match(r"^\d+\s+(?:positive|negative|out\s+of|of\s+\d|samples?|detections?)\b", s, re.I):
        return True
    return bool(
        re.match(
            r"^(?:average|avg\.?|maximum|max\.?|highest|90th percentile|LRAA|RAA)\s*[:=]", s, re.I
        )
    )


def range_values(s):
    s = norm(strip_notes(s))
    if not s or NULL_RE.fullmatch(s):
        return None, None, None
    extra = None
    lab = re.search(r"\brange(?:\s+of\s+(?:results|detections))?\s*[:=]?\s*", s, re.I)
    if lab:
        s = s[lab.end() :]
    other = re.search(
        r"\b(?:highest|lowest|average|avg\.?|max(?:imum)?|min(?:imum)?|LRAA|RAA|result)\b\s*(?:single\s*)?(?:result|level|value|measurement)?\s*[:=]",
        s,
        re.I,
    )
    if other:
        extra = s[other.start() :]
        s = s[: other.start()].strip(" /;,")
    s = s.strip("()[] ")
    m = re.search(
        r"(?<![\w.])(" + ATOM + r")\s*(?:" + UNIT_PAT + r")?\s*(?:[-–—−]|\bto\b)\s*(" + ATOM + r")",
        s,
        re.I,
    )
    if m:
        return value(m.group(1), "plain"), value(m.group(2), "plain"), extra
    if value_like(s):
        v = value(s, "plain")
        if isinstance(v, (int, float)) or (
            isinstance(v, str) and re.fullmatch(ATOM + r"\s*(?:" + UNIT_PAT + r")?", v, re.I)
        ):
            return v, v, extra
    return None, None, extra or s or None


DATE_RE = re.compile(r"(?<!\d)(?:\d{1,2}[/.-]\d{1,2}(?:[/.-]\d{2,4})?|(?:19|20)\d{2})(?!\d)")


def clean_name(s):
    s = strip_notes(s)
    s = re.sub(r"(?<=\w)-\s*\n\s*(?=[a-z])", "", s)
    s = norm(s)
    s = re.sub(r"^\s*\d+[.)]\s*", "", s)
    s = re.sub(r"^(?:\(\d+\)|\d+(?=[A-Za-z])|\d{1,2}\s+(?=[A-Z]))\s*", "", s)
    s = re.sub(r"(?:\s*\(?Next\s+(?:Testing|Test|Sample|Sampling)\b.*)$", "", s, flags=re.I)
    date = None
    unit = None
    for m in list(re.finditer(r"\([^()]*\)", s)):
        inside = m.group()[1:-1].strip()
        dm = DATE_RE.search(inside)
        plain_date = bool(re.fullmatch(r"[\d\s/,–.\-]+", inside))
        if dm and (
            plain_date
            or re.search(r"\b(?:sampled?|tested|testing|collected|test)\b", inside, re.I)
            or unit_in(inside)
        ):
            if not re.search(r"\bnext\b", inside, re.I):
                date = inside if plain_date else dm.group()
            unit = unit_in(inside) or unit
            s = s.replace(m.group(), "")
    dm = re.search(
        r"\b(?:tested|sampled|collected)(?:\s+in)?\s+(" + DATE_RE.pattern + r")", s, re.I
    )
    if dm:
        date = dm.group(1)
        s = s[: dm.start()]
    testing = re.search(r"\b(?:tested|sampled|testing|collected)\b", s, re.I)
    if testing:
        dm = DATE_RE.search(s[testing.end() :])
        if dm:
            date = dm.group()
        s = s[: testing.start()]
    for m in list(re.finditer(r"\([^()]*\)", s)):
        inside = m.group()[1:-1].strip()
        u = unit_in(inside)
        if u and (
            len(inside) < len(u) + 8
            or re.fullmatch(r"(?:" + UNIT_PAT + r")(?:\s+as\s+\w+)?", inside, re.I)
        ):
            unit = u
            s = s.replace(m.group(), "")
    m = re.search(r"[,;]\s*(" + UNIT_PAT + r")(?:\s+as\s+\w+)?\s*$", s, re.I)
    if m:
        unit = norm(m.group()[1:])
        s = s[: m.start()]
    suffix = re.search(r"\s+\[?(" + UNIT_PAT + r"(?:\s+as\s+\w+)?)\]?\s*$", s, re.I)
    if suffix:
        unit = norm(suffix.group(1))
        s = s[: suffix.start()]
    year = re.search(r"(?:\s*[-–,]\s*|\s+)((?:19|20)\d{2})\s*$", s)
    if year:
        date = year.group(1)
        s = s[: year.start()]
    s = re.sub(r"\(\s*[1-9]\s*\)", "", s)
    s = re.sub(r"(?<=\))(?:[1-9])(?=\s|$)", "", s)
    s = re.sub(r"\b(HAA[569])[1-9](?=\s|$)", r"\1", s, flags=re.I)

    def normal_note(m):
        before = s[: m.start()]
        if re.search(
            r"(?:\bHAA|\bchromium|\bstage|\bwell|\bplant|\bzone|\bsource|\bsite|\barea)\s*$",
            before,
            re.I,
        ):
            return m.group()
        return ""

    s = re.sub(r"(?<=[A-Za-z)])([1-9])(?=\s|\(|$)", normal_note, s)
    s = re.sub(r"(?<=\w)['’](?=\s*\(|$)", "", s)
    s = re.sub(r"\s+\([a-f]\)\s*$", "", s, flags=re.I)
    return norm(s).strip(" ,;:"), date, unit


NAME_HEADER = re.compile(
    r"^(?:(?:detected|regulated|unregulated)\s+)?(?:contaminants?|parameters?|substances?|constituents?|chemicals?|analytes?|compounds?|characteristics?)(?:\s*\([^)]*\))?\s*(?:name|detected|and\s+units?(?:\s+of\s+measurement)?|\(?units?\)?|\(date.*)?\s*$",
    re.I,
)


def name_header(s):
    s = norm(s)
    plain = norm(re.sub(r"\([^)]*\)", "", s))
    broad = re.fullmatch(
        r"(?:(?:primary|secondary|unregulated|regulated|detected|inorganic|organic|synthetic|volatile|disinfection|radiological|microbial|microbiological|water quality|PFAS|UCMR\s*\d*)\s*){0,3}(?:contaminants?|substances?|parameters?|constituents?|chemicals?|analytes?|compounds?|characteristics?)(?:\s*(?:name|tested|detected|and\s+units?(?:\s+of\s+measurement)?|[/–-]\s*units?))?",
        plain,
        re.I,
    )
    return bool(
        NAME_HEADER.fullmatch(s)
        or broad
        or re.fullmatch(r"(?:test\s*)?name", plain, re.I)
        or re.match(
            r"^(?:contaminants?|substances?|parameters?)\s*\((?:date|unit|year|sample)", s, re.I
        )
    )


def role(s):
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", norm(fix(s))).lower()
    s = re.sub(r"\b(?:epa[’\']?s|state|federal)\s*", "", s)
    s = norm(re.sub(r"\(\s*" + UNIT_PAT + r"(?:\s+as\s+\w+)?\s*\)", "", s, flags=re.I))
    if not s:
        return "unknown"
    if re.fullmatch(UNIT_PAT + r"(?:\s+as\s+\w+)?", s, re.I):
        return "unit" if s in ("unit", "units") else "unknown"
    if name_header(s):
        return "name"
    if re.search(
        r"likely\s+source|typical\s+source|source.*contamina|sources?\s+of|health\s+effect|origin",
        s,
    ):
        return "source"
    if re.search(
        r"frequency|sampling\s+schedule|next\s+(?:test|sample|sampling)|required\s+(?:samples|monitoring)|monitoring\s+schedule",
        s,
    ):
        return "schedule"
    if re.search(
        r"(?:number|#|no\.)\s+(?:of\s+)?(?:sites|homes|samples|quarters|service[- ]?\s*lines|connections|assessments|pipes|tests)|sites?\s+(?:above|over)|individual\s+results|samples?\s+(?:above|over)|out\s+of\s+compliance",
        s,
    ):
        return "count_positive" if re.search(r"positive|detection", s) else "count"
    if re.search(
        r"violation|exceed(?:s|ed)?\s*(?:the\s*)?(?:mcl|al|action|standard|limit)|(?:mcl|al|action level)\s*exceed|90th\s+percentile\s+al\s*\(?yes",
        s,
    ) or (
        re.search(r"yes\s*/\s*no|y\s*/\s*n", s) and re.search(r"\bAL\b|action level|90th", s, re.I)
    ):
        return "violation"
    if re.search(
        r"compliance|meets?.*(?:standard|mcl|limit)|met.*standard|meeting.*standard|within.*limit|\bpass\b",
        s,
    ):
        return "compliance"
    if re.search(
        r"\bmclg\b|\bmrdlg\b|\bphg\b|\balg\b|\bmldg\b|ideal\s+goal|public\s+health\s+goal|maximum.*level\s+goal|^goal$",
        s,
    ):
        return "mclg"
    if re.search(
        r"comparison|health[- ]based|health\s+advisory|\bhrl\b|notification|reference\s+(?:level|value)|advisory|guideline|reporting\s+(?:limit|level)|detection\s+limit|quantitation|\b(?:MRL|MDL|PQL|RL)\b",
        s,
        re.I,
    ):
        return "other"
    if re.search(r"standard\s+deviation|std\.?\s*dev\.?", s):
        return "value"
    if re.search(
        r"\bmcl\b|\bsmcl\b|\bmrdl\b|\bminrdl\b|\bmin?rdl\b|action\s+level|\bal\b|regulatory\s+limit|highest\s+(?:level\s+)?allowed|maximum.*(?:allowed|allowable|permissible|permitted)|maximum\s+(?:contaminant|residual.*disinfectant)\s+level|minimum.*(?:residual|disinfectant)|removal\s+required|range.*required|required\s+(?:(?:percent|percentage)\s+)?(?:removal|level|minimum)|minimum\s+required|level\s+required|standard|\btt\b|(?:^|\s)limits?(?:\s|$)",
        s,
    ):
        if re.search(r"90|percentile|were\s+less", s) and not re.search(r"^action|^al\b", s):
            return "value"
        return "mcl"
    if re.search(
        r"date|collection|sampling\s+period|^tested$|(?:last|most\s+recent)\s+(?:test|sample)", s
    ) or (
        re.search(r"\byear\b|years?\s+sampled|\bsampled\b|^sample$|\bmonth\b", s)
        and not re.search(r"average|avg|mean|median|level|result|range", s)
    ):
        return "date"
    if re.search(r"\bunits?\b|unit\s+of\s+measure|measure\s*-?\s*ment", s):
        return "unit"
    if re.search(r"range\s*(?:or|/)\s*(?:level|result)|range\s+or\s+amount", s):
        return "combined"
    if re.search(r"\b(?:min(?:imum)?|low(?:est)?)\.?\s*[-/–]\s*(?:max(?:imum)?|high(?:est)?)\b", s):
        return "range"
    if "range" in s:
        if re.search(r"level\s+(?:detected|found)|average|avg\.", s):
            return "value_range"
        low = bool(re.search(r"\b(?:low(?:est)?|min(?:imum)?)\b", s))
        high = bool(re.search(r"\b(?:high(?:est)?|max(?:imum)?)\b", s))
        if low and not high:
            return "low"
        if high and not low:
            return "high"
    if re.fullmatch(
        r"(?:low(?:est)?|min(?:imum)?)\.?\s*(?:(?:level|result|detected|value|of|the|range|concentration)\s*)*",
        s,
    ):
        return "low"
    if re.fullmatch(
        r"(?:high(?:est)?|max(?:imum)?)\.?\s*(?:(?:level|result|detected|value|of|the|range|concentration)\s*)*",
        s,
    ):
        return "high"
    if re.search(r"\brange\b", s):
        return (
            "value_range"
            if re.search(r"level\s+(?:detected|found)|amount\s+(?:detected|found)|average|avg\.", s)
            else "range"
        )
    if re.fullmatch(r"(?:low(?:est)?|min(?:imum)?)(?:\s+(?:level|result|detected|value))?", s):
        return "low"
    if re.fullmatch(r"(?:high(?:est)?|max(?:imum)?)(?:\s+(?:level|result|detected|value))?", s):
        return "high"
    if re.search(r"(?:number|no\.|#).*positive|highest.*detections?", s):
        return "count_positive"
    if re.search(
        r"level|detected|found|average|avg|\bave\b|\bmean\b|median|\b(?:RAA|LRAA|MPA)\b|highest|maximum|90|percentile|result|amount|concentration|removal|ratio|measured|measurement|value|(?:your|our|system)\s*water",
        s,
        re.I,
    ):
        return "value"
    if re.search(
        r"location|plant|site|station|facility|entry\s+point|well|area|sample\s+point|^sources?$|^wtp$",
        s,
    ):
        return "location"
    return "unknown"


def plausible_name(s):
    s = norm(s)
    return (
        bool(re.search(r"[A-Za-z]", s))
        and len(s) < 180
        and not name_header(s)
        and not value_like(s)
        and not re.search(
            r"^(?:note\b|\d+\s*(?:During|Turbidity is)|definitions?\b|source[s]? of contamination|likely sources|typical sources)",
            s,
            re.I,
        )
    )


@dataclasses.dataclass
class Cell:
    x0: float
    top: float
    x1: float
    bottom: float
    text: str

    @property
    def bbox(self):
        return self.x0, self.top, self.x1, self.bottom


def cluster_numbers(xs, tolerance=1.7):
    groups = []
    for x in sorted(xs):
        if groups and x - statistics.mean(groups[-1]) < tolerance:
            groups[-1].append(x)
        else:
            groups.append([x])
    return [statistics.mean(g) for g in groups]


class PageData:
    def __init__(self, page, index):
        self.page = page
        self.index = index
        try:
            chars = page.dedupe_chars(tolerance=0.6).chars
        except Exception:
            chars = page.chars
        self.chars = []
        nearby = collections.defaultdict(list)
        for c in chars:
            nearby[(int(c["x0"] // 16), int(c["top"] // 16))].append(c)
        for ch in chars:
            if not ch.get("upright", True):
                continue
            t = fix(ch.get("text", ""))
            if len(t) == 1 and re.fullmatch(r"[0-9a-f†‡*]", t):
                size = ch.get("size", 10)
                gx = int(ch["x0"] // 16)
                gy = int(ch["top"] // 16)
                neighbors = [
                    o
                    for xx in range(gx - 2, gx + 1)
                    for yy in range(gy - 2, gy + 3)
                    for o in nearby.get((xx, yy), [])
                ]
                raised = any(
                    o.get("size", 10) > size * 1.23
                    and o.get("text", "").strip()
                    and abs(o["x1"] - ch["x0"]) < size * 1.1
                    and o["bottom"] - ch["bottom"] > size * 0.24
                    and abs(o["top"] - ch["top"]) < size
                    for o in neighbors
                    if o["x0"] < ch["x0"] and o["x1"] > ch["x0"] - size * 1.1
                )
                if raised:
                    continue
            c = dict(ch)
            c["text"] = t
            self.chars.append(c)
        self.cache = {}
        self.text = self.get_text((0, 0, page.width, page.height))

    def get_text(self, bbox):
        key = tuple(round(v, 2) for v in bbox)
        if key in self.cache:
            return self.cache[key]
        x0, top, x1, bottom = bbox
        cs = [
            c
            for c in self.chars
            if x0 - 0.5 <= (c["x0"] + c["x1"]) / 2 < x1 + 0.2
            and top - 0.5 <= (c["top"] + c["bottom"]) / 2 < bottom + 0.2
        ]
        text = chars_text(cs, x_tolerance=1.7, y_tolerance=2.8) if cs else ""
        self.cache[key] = fix(text)
        return self.cache[key]

    def cells(self, table):
        return [Cell(*b, self.get_text(b)) for b in table.cells]


def canonical_xs(cells, bbox):
    best = None
    for y in cluster_numbers([c.top for c in cells]):
        active = [c for c in cells if c.top - 0.2 <= y + 0.5 < c.bottom]
        good = sum(value_like(c.text) and bool(norm(c.text)) for c in active)
        occupied = sum(bool(norm(c.text)) for c in active)
        if good < 2:
            continue
        score = good * 10 + min(occupied, 15) - sum(c.x1 - c.x0 < 7 for c in active) * 2
        if best is None or score > best[0]:
            best = score, active
    if best:
        xs = cluster_numbers([v for c in best[1] for v in (c.x0, c.x1)])
    else:
        support = collections.defaultdict(float)
        for c in cells:
            for x in (c.x0, c.x1):
                support[round(x, 1)] += c.bottom - c.top
        xs = cluster_numbers([x for x, h in support.items() if h > (bbox[3] - bbox[1]) * 0.6])
    xs = cluster_numbers([bbox[0], *xs, bbox[2]])
    while len(xs) > 2:
        bad = next((i for i in range(len(xs) - 1) if xs[i + 1] - xs[i] < 7), None)
        if bad is None:
            break
        if bad == 0:
            xs.pop(1)
        elif bad == len(xs) - 2:
            xs.pop(-2)
        else:
            xs.pop(bad + 1)
    return xs


def raw_grid(cells, xs):
    rows = []
    for y in cluster_numbers([c.top for c in cells], 1.2):
        columns = [[] for _ in xs[:-1]]
        wide = []
        originals = []
        for c in cells:
            if abs(c.top - y) > 1.25 or not c.text.strip():
                continue
            hits = [
                i
                for i in range(len(xs) - 1)
                if min(c.x1, xs[i + 1]) - max(c.x0, xs[i]) > (xs[i + 1] - xs[i]) * 0.52
            ]
            if len(hits) > 1:
                wide.append((hits, c.text, c))
                if len(hits) >= len(xs) - 2:
                    originals.append(c)
                    continue
            col = max(
                range(len(xs) - 1), key=lambda i: max(0, min(c.x1, xs[i + 1]) - max(c.x0, xs[i]))
            )
            columns[col].append(c.text)
            originals.append(c)
        rows.append(
            {
                "top": y,
                "bottom": max([c.bottom for c in originals] or [y]),
                "texts": ["\n".join(c) for c in columns],
                "wide": wide,
                "cells": originals,
            }
        )
    return rows


def data_start(rows, ni=0):
    header_seen = False
    pending = None
    measured_columns = set()
    for i, row in enumerate(rows):
        ts = row["texts"]
        if any(name_header(s) for s in ts):
            header_seen = True
            pending = None
            measured_columns.update(
                j
                for j, s in enumerate(ts)
                if role(s)
                in ("value", "value_range", "range", "low", "high", "combined", "count_positive")
            )
            continue
        if not any(ts):
            continue
        name = ts[ni] if ni < len(ts) else ""
        nums = sum(value_like(s) for j, s in enumerate(ts) if j != ni and norm(s))
        if (
            header_seen
            and plausible_name(name)
            and any(
                norm(ts[j])
                and (value_like(ts[j]) or re.search(r"\d", ts[j]) or role(ts[j]) == "unknown")
                for j in measured_columns
            )
        ):
            return pending if pending is not None else i
        known = sum(
            role(s) not in ("unknown", "location", "source", "name") and not value_like(s)
            for s in ts
            if norm(s)
        )
        if known >= 1 and nums == 0:
            measured_columns.update(
                j
                for j, s in enumerate(ts)
                if role(s)
                in ("value", "value_range", "range", "low", "high", "combined", "count_positive")
            )
            header_seen = True
            pending = None
            continue
        if plausible_name(name) and not re.search(
            r"\b(?:goal|limit|results were|detected test|number of)\b", name, re.I
        ):
            if pending is None:
                pending = i
            if nums >= 1:
                return pending
        elif nums >= 2 and (pending is not None or header_seen):
            return pending if pending is not None else i
        elif (
            header_seen
            and any(value_like(t) for t in ts if t)
            and any(
                plausible_name(t) and not re.fullmatch(UNIT_PAT, norm(t), re.I) for t in ts if t
            )
        ):
            return pending if pending is not None else i
    return None


def infer_name_column(rows, headings, start, default=0):
    choices = []
    for j, h in enumerate(headings):
        if role(h) not in ("name", "unknown"):
            continue
        vals = [norm(r["texts"][j]) for r in rows[start:] if norm(r["texts"][j])]
        if not vals:
            continue
        good = sum(
            plausible_name(v)
            and not re.fullmatch(UNIT_PAT, v, re.I)
            and violation_value(v) is None
            and len(v.split()) < 12
            for v in vals
        ) / len(vals)
        if good < 0.7:
            continue
        bonus = (
            0.7
            if re.search(r"parameter|chemical|analyte|substance|name|contaminant", h, re.I)
            else 0
        )
        choices.append((good + bonus - 0.001 * statistics.mean(len(v) for v in vals), j))
    return max(choices)[1] if choices else default


def merge_text(a, b):
    if not a:
        return b
    if not b or norm(a) == norm(b):
        return a
    return a + "\n" + b


def violation_value(s, invert=False):
    s = norm(strip_notes(s))
    if not s or NULL_RE.fullmatch(s):
        return None
    if re.match(r"^(?:no\b|n\b|false\b|✗|✘|×)", s, re.I):
        return "Y" if invert else "N"
    if re.match(r"^(?:yes\b|y\b|true\b|✓|✔|√)", s, re.I):
        return "N" if invert else "Y"
    if re.match(r"^(?:met|pass|in compliance|compliant)", s, re.I):
        return "N"
    return None


def split_places(s):
    s = strip_notes(s)
    if re.search(r"\b(?:average|avg|max|highest|range|90th|LRAA|RAA)\b\s*[:=]", s, re.I):
        return []
    labelled = re.compile(
        r"(?:^|[;\n])\s*([A-Za-z][A-Za-z0-9 .#/-]*?)\s*[:=]\s*("
        + ATOM
        + r")\s*(?:"
        + UNIT_PAT
        + r")?(?:\s*\(\s*("
        + ATOM
        + r")\s*[-–—]\s*("
        + ATOM
        + r")\s*\))?",
        re.I,
    )
    ms = list(labelled.finditer(s))
    if len(ms) > 1 and not any(
        re.search(r"\b(?:max|average|range|highest|LRAA|RAA)\b", m.group(1), re.I) for m in ms
    ):
        return [(norm(m.group(1)), m.group(2), m.group(3), m.group(4)) for m in ms]
    pat = re.compile(
        r"([A-Za-z][A-Za-z0-9\s.,#/-]*?)\s+("
        + ATOM
        + r")\s*(?:"
        + UNIT_PAT
        + r")?\s*\(\s*("
        + ATOM
        + r")\s*[-–—]\s*("
        + ATOM
        + r")\s*\)",
        re.I,
    )
    matches = list(pat.finditer(s))
    if len(matches) < 2 or not all(re.search(r"[A-Za-z]", m.group(1)) for m in matches):
        return []
    return [(norm(m.group(1)).strip(" ;/,"), m.group(2), m.group(3), m.group(4)) for m in matches]


def level_and_range(s, kind="value", header=""):
    s = strip_notes(s)
    full_value_text = norm(s)
    lo = hi = None
    if kind == "combined":
        lo, hi, extra = range_values(s)
        if lo is not None and re.search(r"[-–—]|\bto\b", norm(s)):
            return None, lo, hi
        return value(s), None, None
    m = re.search(
        r"\(\s*("
        + ATOM
        + r")\s*(?:"
        + UNIT_PAT
        + r")?\s*[-–—]\s*("
        + ATOM
        + r")\s*(?:"
        + UNIT_PAT
        + r")?\s*\)",
        norm(s),
        re.I,
    )
    if m:
        lo, hi = value(m.group(1), "plain"), value(m.group(2), "plain")
        s = (norm(s)[: m.start()] + " " + norm(s)[m.end() :]).strip()
    lab = re.search(r"\brange\s*(?:of\s+(?:results|detections))?\s*[:=]?\s*", s, re.I)
    if lab:
        lo, hi, _ = range_values(s[lab.start() :])
        s = s[: lab.start()].strip(" /;,")
    if kind == "value_range" and lo is None:
        ls = [norm(t) for t in s.split("\n") if norm(t)]
        if len(ls) > 1 and value_like(ls[0]):
            rlo, rhi, _ = range_values(" ".join(ls[1:]))
            if rlo is not None and re.search(r"[-–—]|\bto\b", " ".join(ls[1:])):
                s = ls[0]
                lo, hi = rlo, rhi
    label = re.search(
        r"\b(?:max(?:imum)?|min(?:imum)?|lowest|median|average|avg\.?|mean|highest(?:\s+single\s+(?:measurement|result))?|90th\s+percentile(?:\s+value)?|LRAA|RAA)\s*[:=]?\s*("
        + ATOM
        + r")",
        full_value_text,
        re.I,
    )
    if label:
        return value(label.group(1)), lo, hi
    if lo is not None:
        place = re.fullmatch(r"[A-Za-z][A-Za-z0-9\s./-]+?\s+(" + ATOM + r")", norm(s), re.I)
        if place:
            return value(place.group(1)), lo, hi
    return value(s), lo, hi


def read_flat_table(cells, bbox, system, page_index=0, origin="lines", context=""):
    if len(cells) < 3:
        return []
    xs = canonical_xs(cells, bbox)
    if len(xs) < 3:
        return []
    rows = raw_grid(cells, xs)
    n = len(xs) - 1
    name_votes = collections.Counter(
        j for r in rows[:15] for j, s in enumerate(r["texts"]) if name_header(s)
    )
    ni = name_votes.most_common(1)[0][0] if name_votes else 0
    start = data_start(rows, ni)
    if start is None:
        return []
    headings = ["" for _ in range(n)]
    titles = []
    for r in rows[:start]:
        for j, s in enumerate(r["texts"]):
            headings[j] = merge_text(headings[j], s)
        for hits, s, _ in r["wide"]:
            titles.append(s)
    if re.search(
        r"(?:type|nature)\s+of\s+violation|violation\s+(?:type|period|begin|end)|when\s+(?:the\s+)?violation",
        norm(" ".join(headings)),
        re.I,
    ):
        return []
    if re.search(r"service[- ]line\s+inventory|assessment\s+counts?", context, re.I):
        return []
    if re.search(r"assessment", norm(" ".join(headings)), re.I) and re.search(
        r"required|completed|conducted", norm(" ".join(headings)), re.I
    ):
        return []
    if not name_votes:
        ni = infer_name_column(rows, headings, start, ni)
    roles = [role(h) for h in headings]
    roles[ni] = "name"
    for j, rr in enumerate(roles):
        vals = [norm(r["texts"][j]) for r in rows[start:] if norm(r["texts"][j])]
        if (
            j != ni
            and rr == "unknown"
            and vals
            and sum(
                bool(
                    re.match(
                        r"^(?:highest|lowest|single|monthly|annual|minimum|maximum|percent|average)",
                        v,
                        re.I,
                    )
                )
                for v in vals
            )
            >= len(vals) * 0.7
        ):
            roles[j] = "sublabel"
    for j in range(n):
        if roles[j] == "unknown":
            vals = [norm(r["texts"][j]) for r in rows[start:] if norm(r["texts"][j])]
            if vals and sum(violation_value(v) is not None for v in vals) >= len(vals) * 0.75:
                roles[j] = "violation"
            elif (
                vals
                and sum(bool(re.fullmatch(UNIT_PAT + r"(?:\s+as\s+\w+)?", v, re.I)) for v in vals)
                >= len(vals) * 0.8
            ):
                roles[j] = "unit"
    if "low" in roles and "high" not in roles:
        roles = ["value" if r == "low" else r for r in roles]
    if "high" in roles and "low" not in roles:
        roles = ["value" if r == "high" else r for r in roles]
    candidates = [j for j, r in enumerate(roles) if r in ("value", "value_range", "combined")]
    if not candidates:
        candidates = [
            j
            for j, r in enumerate(roles)
            if r == "unknown" and j != ni and any(value_like(x["texts"][j]) for x in rows[start:])
        ]
    if not candidates:
        candidates = [j for j, r in enumerate(roles) if r == "count_positive"]
    li = candidates[0] if candidates else None
    ranges = [j for j, r in enumerate(roles) if r in ("range", "low", "high")]
    if li is None and not ranges:
        return []
    if (
        li is None
        and not name_votes
        and not any(
            re.search(r"achiev|detect|result|found|measur", headings[j], re.I) for j in ranges
        )
    ):
        return []
    primary = li if li is not None else ranges[0]
    if not name_votes and not any(
        re.search(
            r"found|detect|measur|percentile|average|range|result|level|amount|concentration|(?:your|our|system)\s*water",
            h,
            re.I,
        )
        for h in headings
    ):
        return []
    log("TABLE", page_index + 1, origin, bbox, list(zip(headings, roles)), "start", start)
    logical = []
    cur = None
    last_name = ""
    section = None

    def merged_fields(ts, top):
        ts = list(ts)
        for j in range(n):
            if ts[j] or j == ni:
                continue
            cover = [
                c
                for c in cells
                if c.top < top - 1
                and c.bottom > top + 1
                and c.x0 - 1 <= (xs[j] + xs[j + 1]) / 2 < c.x1 + 1
                and c.x1 - c.x0 < (xs[j + 1] - xs[j]) * 1.6
            ]
            if cover:
                ts[j] = cover[0].text
        return ts

    for r in rows[start:]:
        ts = r["texts"]
        name = ts[ni]
        if any(name_header(s) for s in ts) and sum(role(s) != "unknown" for s in ts) >= 3:
            continue
        if not any(ts):
            if r["wide"]:
                text = r["wide"][0][1]
                if len(norm(text)) < 160:
                    section = norm(text)
            continue
        measured = bool(norm(ts[primary]))
        other_data = sum(
            value_like(ts[j])
            for j in range(n)
            if roles[j] not in ("name", "source", "unit") and ts[j]
        )
        is_sub = bool(
            last_name
            and re.match(
                r"^(?:highest\s+single|lowest\s+monthly|percent(?:age)?\s+of\s+samples|minimum\s+monthly|monthly\s+average|lowest\s+detected|average\s+value)",
                norm(name),
                re.I,
            )
        )
        if is_sub:
            ts = list(ts)
            name = last_name.split(" - ")[0] + " - " + norm(name)
            ts[ni] = name
        new_name = plausible_name(name)
        subrange = bool(re.match(r"^(?:range\b|range of|range \()", norm(name), re.I))
        if subrange:
            target = cur if cur is not None else (logical[-1] if logical else None)
            if target is not None:
                for j in [primary, *ranges]:
                    if ts[j]:
                        target["inline_range"] = ts[j]
                        break
            continue
        if (
            new_name
            and not measured
            and not other_data
            and cur is not None
            and norm(cur["texts"][primary])
        ):
            if re.search(
                r"contaminants|byproducts|substances|results|chemical|microbiolog|radiolog|inorganic|organic|unregulated|secondary",
                name,
                re.I,
            ):
                section = norm(name)
                continue
        if new_name and (measured or other_data or len(norm(name)) < 100):
            if cur is not None:
                logical.append(cur)
            ts = merged_fields(ts, r["top"])
            cur = {"texts": list(ts), "top": r["top"], "bottom": r["bottom"], "section": section}
            if not is_sub:
                last_name = name
        elif (
            cur is not None and measured and norm(cur["texts"][primary]) and value_like(ts[primary])
        ):
            logical.append(cur)
            nts = list(ts)
            nts[ni] = last_name
            nts = merged_fields(nts, r["top"])
            cur = {"texts": nts, "top": r["top"], "bottom": r["bottom"], "section": section}
        elif cur is not None:
            for j, s in enumerate(ts):
                if j == ni and s and not new_name:
                    continue
                cur["texts"][j] = merge_text(cur["texts"][j], s)
            cur["bottom"] = max(cur["bottom"], r["bottom"])
    if cur is not None:
        logical.append(cur)
    # Some PDFs print parallel per-location entries line by line inside cells.
    # Split only when another column has the corresponding set of entries.
    if li is not None:
        expanded = []
        for row in logical:
            ls = [t.strip() for t in row["texts"][li].split("\n") if t.strip()]
            good = len(ls) > 1 and all(
                re.fullmatch(
                    ATOM + r"\s*(?:" + UNIT_PAT + r")?\s*(?:\([A-Za-z .%]+\))?",
                    strip_notes(t),
                    re.I,
                )
                for t in ls
            )
            parallel = (
                any(
                    j != li
                    and roles[j] not in ("source", "name", "range")
                    and len([t for t in row["texts"][j].split("\n") if t.strip()]) == len(ls)
                    for j in range(n)
                )
                if good
                else False
            )
            if good and parallel:
                for k in range(len(ls)):
                    rr = dict(row)
                    rr["texts"] = list(row["texts"])
                    for j, t in enumerate(row["texts"]):
                        parts = [p.strip() for p in t.split("\n") if p.strip()]
                        if len(parts) == len(ls) and roles[j] != "name":
                            rr["texts"][j] = parts[k]
                    expanded.append(rr)
            else:
                expanded.append(row)
        logical = expanded
    output = []
    for row in logical:
        ts = row["texts"]
        name, namedate, nameunit = clean_name(ts[ni])
        if not name or not plausible_name(name):
            continue
        if not any(norm(ts[j]) for j in range(n) if roles[j] not in ("name", "source", "location")):
            continue
        if len(name) > 130 or re.search(
            r"\b(?:is a measure|the concentration|we monitor|the highest level|means that|during \d{4})\b",
            name,
            re.I,
        ):
            continue
        for j, rr in enumerate(roles):
            if rr == "sublabel" and norm(ts[j]):
                name += " - " + norm(strip_notes(ts[j]))
        rec = dict(
            system=system,
            contaminant=name,
            sample_date=namedate,
            level=None,
            range_low=None,
            range_high=None,
            unit=None,
            mcl=None,
            mclg=None,
            violation=None,
        )
        if row.get("section") and not re.search(
            r"contaminants|substances|results|inorganic|organic|secondary|disinfection|microbiolog|radiolog",
            row["section"],
            re.I,
        ):
            rec["location"] = row["section"]
        for j, rr in enumerate(roles):
            if rr == "date":
                printed = norm(strip_notes(ts[j])) or None
                if printed and NULL_RE.fullmatch(printed):
                    printed = None
                rec["sample_date"] = printed or namedate
                if rec["sample_date"] and "-\n" in ts[j]:
                    rec["sample_date"] = re.sub(r"(?<=\d)-\s+(?=\d)", "-", rec["sample_date"])
            elif rr in ("mcl", "mclg") and rec[rr] is None:
                rec[rr] = value(ts[j], rr)
            elif rr in ("violation", "compliance"):
                rec["violation"] = violation_value(ts[j], rr == "compliance")
            elif rr == "range":
                rec["range_low"], rec["range_high"], _ = range_values(ts[j])
            elif rr == "low":
                rec["range_low"] = value(ts[j], "plain")
            elif rr == "high":
                rec["range_high"] = value(ts[j], "plain")
            elif rr == "location" and norm(ts[j]):
                rec["location"] = norm(ts[j])
        if li is not None:
            rec["level"], lo, hi = level_and_range(ts[li], roles[li], headings[li])
            if lo is not None or hi is not None:
                rec["range_low"], rec["range_high"] = lo, hi
        if row.get("inline_range"):
            rec["range_low"], rec["range_high"], _ = range_values(row["inline_range"])
        ui = next(
            (j for j, rr in enumerate(roles) if rr == "unit" and value(ts[j], "plain") is not None),
            None,
        )
        rec["unit"] = (norm(strip_notes(ts[ui])) if ui is not None else None) or nameunit
        if rec["unit"] and NULL_RE.fullmatch(rec["unit"]):
            rec["unit"] = None
        if rec["unit"] and re.fullmatch(
            r"(?:(?:percent|%)\s*removal|removal ratio|ratio|percent)", rec["unit"], re.I
        ):
            rec["unit"] = None
        if not rec["unit"]:
            options = (
                ([ts[li]] if li is not None else [])
                + [ts[j] for j, rr in enumerate(roles) if rr == "mcl"]
                + ([headings[li]] if li is not None else [])
                + [ts[j] for j in ranges]
                + [headings[ni]]
                + titles
            )
            caption = [
                t
                for t in context.split("\n")
                if unit_in(t)
                and (
                    re.search(
                        r"units?|results?|values?|concentrations?|levels?|parameters?", t, re.I
                    )
                    or re.search(r"\([^)]*" + UNIT_PAT + r"[^)]*\)", t, re.I)
                )
            ]
            options += caption[-1:]
            rec["unit"] = next((unit_in(s) for s in options if unit_in(s)), None)
        places = split_places(ts[li]) if li is not None else []
        if places:
            for place, lv, low, high in places:
                rr = dict(rec)
                rr.update(
                    location=place, level=value(lv), range_low=value(low), range_high=value(high)
                )
                output.append(rr)
        else:
            output.append(rec)
    return output


def minimal_record(system, name):
    return dict(
        system=system,
        contaminant=name,
        sample_date=None,
        level=None,
        range_low=None,
        range_high=None,
        unit=None,
        mcl=None,
        mclg=None,
        violation=None,
    )


def table_profile(cells, bbox):
    xs = canonical_xs(cells, bbox)
    if len(xs) < 3:
        return None
    rows = raw_grid(cells, xs)
    votes = collections.Counter(
        j for r in rows[:15] for j, t in enumerate(r["texts"]) if name_header(t)
    )
    ni = votes.most_common(1)[0][0] if votes else 0
    start = data_start(rows, ni) if votes else None
    if not votes:
        for i, r in enumerate(rows):
            if i == 0:
                continue
            ts = r["texts"]
            count = sum(value_like(t) for t in ts[1:] if t)
            prior = " ".join(rr["texts"][0] for rr in rows[:i])
            if count >= 2 or (
                count >= 1
                and (DATE_RE.fullmatch(norm(ts[0])) or role(prior) in ("date", "location"))
            ):
                start = i
                break
    if start is None:
        start = next(
            (i for i, r in enumerate(rows) if sum(value_like(t) for t in r["texts"] if t) >= 2),
            None,
        )
    if start is None:
        return None
    hs = [""] * (len(xs) - 1)
    for r in rows[:start]:
        for j, t in enumerate(r["texts"]):
            hs[j] = merge_text(hs[j], t)
    if not votes and role(hs[0]) not in ("date", "location"):
        ni = infer_name_column(rows, hs, start, ni)
    return xs, rows, ni, start, hs


def subset_cells(rows, indices, xs, header_end):
    # Repack chosen columns into a contiguous grid.  All content remains the
    # original printed cell text; virtual coordinates only encode adjacency.
    out = []
    for r in rows:
        for k, j in enumerate(indices):
            text = r["texts"][j]
            if text:
                out.append(Cell(k * 100, r["top"], (k + 1) * 100, r["bottom"], text))
    return out, (0, min(r["top"] for r in rows), len(indices) * 100, max(r["bottom"] for r in rows))


def transposed_table(cells, bbox, system, context=""):
    profile = table_profile(cells, bbox)
    if not profile:
        return None
    xs, rows, ni, start, hs = profile
    rs = [role(h) for h in hs]
    if any(name_header(h) for h in hs):
        return None
    first = rs[0]
    if first not in ("date", "location") and not re.search(
        r"^(?:sample|sampling|month|area|plant|well|site|location|entry|zone|source|date|system|treatment|distribution)",
        norm(hs[0]),
        re.I,
    ):
        if norm(hs[0]) or not context:
            return None
    contaminantcols = []
    for j in range(1, len(hs)):
        name, date, unit = clean_name(hs[j])
        if re.fullmatch(UNIT_PAT + r"(?:\s+as\s+\w+)?", norm(hs[j]), re.I):
            continue
        if (
            name
            and plausible_name(name)
            and rs[j]
            not in ("mcl", "mclg", "source", "date", "violation", "compliance", "count", "other")
            and sum(value_like(r["texts"][j]) for r in rows[start:] if r["texts"][j]) >= 1
        ):
            contaminantcols.append((j, name, unit))
    captions = [
        c.text
        for c in cells
        if c.top < rows[start]["top"] and c.x1 - c.x0 > (bbox[2] - bbox[0]) * 0.7
    ]
    caption = norm(" ".join(captions) or context)
    # A single-parameter table often has sites as rows and "Result" or units
    # in its measured columns.  Its parameter is named in the caption.
    single = bool(
        re.search(
            r"\b(?:TOC|organic carbon|removal ratio|hardness|turbidity|alkalinity|sodium|chlorine|chloramine|fluoride)\b",
            caption,
            re.I,
        )
    )
    generic = all(
        rs[j]
        in (
            "value",
            "value_range",
            "combined",
            "unit",
            "range",
            "low",
            "high",
            "unknown",
            "mcl",
            "mclg",
            "date",
            "violation",
            "compliance",
            "other",
            "count",
            "count_positive",
            "source",
        )
        and (rs[j] != "unknown" or re.fullmatch(UNIT_PAT + r"(?:\s+as\s+\w+)?", norm(hs[j]), re.I))
        for j in range(1, len(hs))
    )
    contextlines = [norm(t) for t in (context or "\n".join(captions)).split("\n") if norm(t)]
    if (
        generic
        and contextlines
        and len(contextlines[-1]) < 90
        and not re.search(
            r"\b(?:report|contaminants|results|monitoring|data|sources)\b", contextlines[-1], re.I
        )
    ):
        single = True
    if single and generic:
        lines = [norm(t) for t in re.split(r"\n", context or "\n".join(captions)) if norm(t)]
        title = next(
            (
                t
                for t in reversed(lines)
                if re.search(
                    r"\b(?:TOC|organic carbon|removal ratio|hardness|turbidity|alkalinity|sodium|chlorine|chloramine|fluoride)\b",
                    t,
                    re.I,
                )
            ),
            caption,
        )
        title = re.sub(r"^\s*(?:Table\s*\d+[.:]?\s*|20\d\d\s*)", "", title, flags=re.I)
        title = re.sub(r"\s*[–—]\s*.*$", "", title)
        title = re.sub(r"\s+(?:Results|Data|Monitoring)\s*$", "", title, flags=re.I)
        name, _, titleunit = clean_name(title)
        if not name:
            return None
        if "low" in rs and "high" not in rs:
            rs = ["value" if r == "low" else r for r in rs]
        if "high" in rs and "low" not in rs:
            rs = ["value" if r == "high" else r for r in rs]
        vals = [
            j
            for j in range(1, len(hs))
            if rs[j] in ("value", "value_range", "combined")
            or (rs[j] == "unknown" and unit_in(hs[j]))
        ]
        if not vals:
            vals = [j for j in range(1, len(hs)) if rs[j] == "count_positive"]
        ranges = [j for j in range(1, len(hs)) if rs[j] in ("range", "low", "high")]
        li = vals[0] if vals else None
        primary = li if li is not None else (ranges[0] if ranges else None)
        if primary is None:
            return None
        out = []
        for r in rows[start:]:
            ts = r["texts"]
            if not norm(ts[0]) or NULL_RE.fullmatch(norm(ts[0])) or not value_like(ts[primary]):
                continue
            rec = minimal_record(system, name)
            rec["location"] = norm(ts[0])
            if li is not None:
                rec["level"], rec["range_low"], rec["range_high"] = level_and_range(ts[li], rs[li])
            rec["unit"] = unit_in(hs[primary]) or unit_in(ts[primary]) or titleunit
            for j, rr in enumerate(rs):
                if rr == "range":
                    rec["range_low"], rec["range_high"], _ = range_values(ts[j])
                elif rr == "low":
                    rec["range_low"] = value(ts[j], "plain")
                elif rr == "high":
                    rec["range_high"] = value(ts[j], "plain")
                elif rr in ("mcl", "mclg"):
                    rec[rr] = value(ts[j], rr)
                elif rr == "date":
                    rec["sample_date"] = (
                        None
                        if not norm(ts[j]) or NULL_RE.fullmatch(norm(ts[j]))
                        else norm(strip_notes(ts[j]))
                    )
                elif rr in ("violation", "compliance"):
                    rec["violation"] = violation_value(ts[j], rr == "compliance")
                elif rr == "unit" and value(ts[j], "plain") is not None:
                    rec["unit"] = norm(strip_notes(ts[j]))
            out.append(rec)
        return out or None
    if not contaminantcols:
        return None
    # Reject generic value headings: they do not name contaminants.
    if all(rs[j] in ("value", "unit", "range", "low", "high") for j, n, u in contaminantcols):
        return None
    out = []
    unique = []
    seen = set()
    for col in contaminantcols:
        key = re.sub(r"\W+", "", col[1].lower())
        if key not in seen:
            unique.append(col)
            seen.add(key)
    contaminantcols = unique
    for row in rows[start:]:
        ts = row["texts"]
        label = norm(ts[0])
        if (
            not label
            or name_header(label)
            or re.search(r"^(?:note|source|MCL|MCLG)\b", label, re.I)
        ):
            continue
        if not any(value_like(ts[j]) for j, n, u in contaminantcols):
            continue
        isdate = first == "date" or bool(DATE_RE.fullmatch(label))
        for j, name, unit in contaminantcols:
            rec = minimal_record(system, name)
            rec["level"], rec["range_low"], rec["range_high"] = level_and_range(ts[j])
            tableunits = [
                unit_in(t)
                for t in contextlines
                if unit_in(t) and (re.search(r"unit|concentration|value|result|\(", t, re.I))
            ]
            rec["unit"] = unit or unit_in(ts[j]) or (tableunits[-1] if tableunits else None)
            if isdate:
                rec["sample_date"] = value(label, "plain") if NULL_RE.fullmatch(label) else label
            else:
                rec["location"] = label
            out.append(rec)
    return out or None


def split_schemas(cells, bbox):
    hs = sorted(
        [c for c in cells if name_header(c.text) and c.x1 - c.x0 < (bbox[2] - bbox[0]) * 0.5],
        key=lambda c: c.top,
    )
    tops = cluster_numbers([c.top for c in hs], 5)
    if len(tops) < 2:
        return None
    cuts = [bbox[1]]
    for y in tops[1:]:
        heads = [c for c in hs if abs(c.top - y) < 5]
        bottom = max(c.bottom for c in heads)
        relevant = [
            c.top
            for c in cells
            if y - 45 < c.top <= y + 2
            and c.bottom > y - 1
            and c.top < bottom
            and role(c.text)
            in (
                "name",
                "date",
                "unit",
                "mcl",
                "mclg",
                "violation",
                "compliance",
                "value",
                "range",
                "combined",
                "count",
            )
            and not value_like(c.text)
        ]
        cut = min(relevant + [y])
        if cut > cuts[-1] + 10:
            cuts.append(cut)
    cuts.append(bbox[3] + 0.1)
    parts = []
    for lo, hi in zip(cuts, cuts[1:]):
        cc = [c for c in cells if lo - 0.5 <= c.top < hi - 0.5]
        if len(cc) > 2:
            parts.append((cc, (bbox[0], lo, bbox[2], min(hi, bbox[3]))))
    return parts if len(parts) > 1 else None


def read_table(cells, bbox, system, page_index=0, origin="lines", context="", allow_split=True):
    if allow_split:
        parts = split_schemas(cells, bbox)
        if parts:
            out = []
            for cc, bb in parts:
                out.extend(read_table(cc, bb, system, page_index, origin + "-part", context, False))
            if out:
                return out
    transposed = transposed_table(cells, bbox, system, context)
    if transposed is not None:
        return transposed
    profile = table_profile(cells, bbox)
    if profile:
        xs, rows, ni, start, hs = profile
        n = len(xs) - 1
        # Source headings spanning repeated value/range column groups.
        groups = []
        for c in cells:
            if c.bottom > rows[start]["top"] + 1 or not norm(c.text):
                continue
            hits = [
                j
                for j in range(n)
                if min(c.x1, xs[j + 1]) - max(c.x0, xs[j]) > (xs[j + 1] - xs[j]) * 0.6
            ]
            if not 1 < len(hits) < n - 1 or ni in hits:
                continue
            if role(c.text) not in ("unknown", "location"):
                continue
            if re.search(
                r"contaminant|result|detected|range|regulated|maximum|average|unit|report|water quality",
                c.text,
                re.I,
            ):
                continue
            if any(role(hs[j]) in ("value", "range", "combined", "value_range") for j in hits):
                groups.append((hits, norm(c.text)))
        groups.sort(key=lambda g: (-len(g[0]), g[0][0]))
        chosen = []
        for g in groups:
            if not any(set(g[0]) & set(h[0]) for h in chosen):
                chosen.append(g)
        if len(chosen) >= 2:
            covered = set(j for g, t in chosen for j in g)
            common = [
                j
                for j in range(n)
                if j not in covered
                and (j == ni or role(hs[j]) not in ("value", "value_range", "combined"))
            ]
            out = []
            for indices, title in sorted(chosen):
                take = sorted(set(common + indices))
                sub, bb = subset_cells(rows, take, xs, rows[start]["top"])
                rr = read_flat_table(sub, bb, system, page_index, origin + "-group", context)
                for r in rr:
                    r["location"] = title
                out.extend(rr)
            if out:
                return out
        # The source name may itself head a level column, without a spanning
        # group heading (for example "Well 1 | Well 2").
        sources = [
            j
            for j, h in enumerate(hs)
            if j != ni
            and re.search(
                r"\b(?:well|plant|WTP|entry point|zone|area|raw water|finished water|treated water|source water)\b",
                h,
                re.I,
            )
            and role(h)
            not in ("range", "mcl", "mclg", "source", "date", "count", "violation", "compliance")
            and any(value_like(r["texts"][j]) for r in rows[start:])
        ]
        if len(sources) < 2 and not any(
            role(h) in ("value", "value_range", "combined") for h in hs
        ):
            unknown = [
                j
                for j, h in enumerate(hs)
                if j != ni
                and norm(h)
                and role(h) == "unknown"
                and not value_like(h)
                and not unit_in(h)
                and sum(value_like(r["texts"][j]) for r in rows[start:] if r["texts"][j])
                >= max(1, sum(bool(r["texts"][j]) for r in rows[start:]) * 0.7)
            ]
            if len(unknown) >= 2:
                sources = unknown
        if len(sources) >= 2:
            gr = []
            for k, j in enumerate(sources):
                stop = sources[k + 1] if k + 1 < len(sources) else n
                local = [j] + [
                    z
                    for z in range(j + 1, stop)
                    if role(hs[z])
                    in ("range", "low", "high", "unit", "date", "violation", "compliance")
                ]
                gr.append((local, norm(hs[j])))
            covered = set(j for gg, t in gr for j in gg)
            common = [
                j
                for j in range(n)
                if j not in covered
                and (j == ni or role(hs[j]) not in ("value", "value_range", "combined"))
            ]
            out = []
            for gg, title in gr:
                take = sorted(common + gg)
                sub, bb = subset_cells(rows, take, xs, rows[start]["top"])
                level_col = take.index(gg[0])
                hx = level_col * 100
                sub = [
                    (
                        dataclasses.replace(c, text="Level Detected")
                        if c.x0 == hx and c.top < rows[start]["top"]
                        else c
                    )
                    for c in sub
                ]
                rr = read_flat_table(sub, bb, system, page_index, origin + "-source", context)
                for r in rr:
                    r["location"] = title
                out.extend(rr)
            if out:
                return out
    return read_flat_table(cells, bbox, system, page_index, origin, context)


def word_lines(pd):
    from pdfplumber.utils import extract_words

    words = extract_words(pd.chars, x_tolerance=1.6, y_tolerance=2.8)
    lines = []
    for w in sorted(words, key=lambda w: ((w["top"] + w["bottom"]) / 2, w["x0"])):
        cy = (w["top"] + w["bottom"]) / 2
        found = next(
            (
                l
                for l in lines[-5:]
                if abs(l["cy"] - cy) < max(2.2, (w["bottom"] - w["top"]) * 0.28)
            ),
            None,
        )
        if found is None:
            lines.append({"cy": cy, "words": [w]})
        else:
            found["words"].append(w)
    for line in lines:
        line["words"].sort(key=lambda w: w["x0"])
        chunks = []
        for w in line["words"]:
            gap = w["x0"] - chunks[-1][-1]["x1"] if chunks else 100
            if chunks and gap < max(5, (w["bottom"] - w["top"]) * 0.55):
                chunks[-1].append(w)
            else:
                chunks.append([w])
        line["chunks"] = [
            Cell(
                c[0]["x0"],
                min(w["top"] for w in c),
                c[-1]["x1"],
                max(w["bottom"] for w in c),
                " ".join(w["text"] for w in c),
            )
            for c in chunks
        ]
        line["top"] = min(w["top"] for w in line["words"])
        line["bottom"] = max(w["bottom"] for w in line["words"])
    return sorted(lines, key=lambda l: l["cy"])


def header_blocks(pd):
    """Infer compact header blocks from text, independently of ruling lines."""
    lines = word_lines(pd)
    seeds = []
    for i, line in enumerate(lines):
        for c in line["chunks"]:
            if name_header(c.text):
                seeds.append((i, c))
        if (
            line["chunks"]
            and role(line["chunks"][0].text) in ("date", "location")
            and len(line["chunks"]) >= 2
        ):
            seeds.append((i, line["chunks"][0]))
    blocks = []
    for idx, seed in seeds:
        fs = max(5, seed.bottom - seed.top)
        # Numeric rows mark the lower edge of a heading, including headings
        # whose components have different vertical alignments.
        first = None
        for k in range(idx + 1, len(lines)):
            l = lines[k]
            if l["top"] - seed.bottom > 110:
                break
            cs = l["chunks"]
            score = sum(
                value_like(c.text) or (unit_in(c.text) is not None and len(c.text) < 15) for c in cs
            )
            numeric = any(
                (
                    re.fullmatch(ATOM + r"\s*(?:" + UNIT_PAT + r")?", norm(c.text), re.I)
                    or re.match(r"^\d+%\s+of\b", norm(c.text), re.I)
                )
                and not re.match(r"^90%?\s+of\b", norm(c.text), re.I)
                for c in cs
            )
            if score >= 2 or numeric:
                first = k
                break
        if first is None:
            continue
        end = lines[first]["top"] - 1
        begin = max(0, seed.top - max(45, fs * 4))
        # Use only short chunks.  A line of prose above a table must not join
        # all the column headings into one connected component.
        chunks = [
            c
            for l in lines
            if begin <= l["top"] and l["bottom"] <= end + 0.5
            for c in l["chunks"]
            if len(norm(c.text)) < 65 and c.x1 - c.x0 < max(130, pd.page.width * 0.24)
        ]
        if seed not in chunks:
            chunks.append(seed)
        chunks = [
            c
            for c in chunks
            if not (value_like(c.text) and not re.search(r"90|\bof\b", c.text, re.I))
        ]
        named = [c for c in chunks if name_header(c.text)]
        if named and not name_header(seed.text):
            seed = min(named, key=lambda c: c.top)
            fs = max(5, seed.bottom - seed.top)
        # Build column components by horizontal overlap; unknown fragments such
        # as "Sample" and "Year" can then form a complete heading.
        groups = []
        for c in sorted(chunks, key=lambda c: c.x1 - c.x0):
            hits = [
                g
                for g in groups
                if min(c.x1, max(q.x1 for q in g)) - max(c.x0, min(q.x0 for q in g))
                > min(c.x1 - c.x0, max(q.x1 for q in g) - min(q.x0 for q in g)) * 0.22
            ]
            if len(hits) > 1:
                # A group title above multiple columns is not a column itself.
                if role(c.text) in ("unknown", "location") or c.x1 - c.x0 > 85:
                    continue
                hit = hits[0]
                for g in hits[1:]:
                    hit.extend(g)
                    groups.remove(g)
                hit.append(c)
            elif hits:
                hits[0].append(c)
            else:
                groups.append([c])
        cols = []
        for g in groups:
            text = "\n".join(c.text for c in sorted(g, key=lambda c: c.top))
            rr = role(text)
            # Exclude detached captions and unknown material above the header.
            if rr == "unknown" and (
                not any(abs(c.top - seed.top) < fs * 2 for c in g)
                or re.fullmatch(r"(?:of|in|and|or|for|the|by|at)", norm(text), re.I)
            ):
                continue
            cols.append((min(c.x0 for c in g), max(c.x1 for c in g), text, rr))
        cols.sort()
        # Centered source headings can sit in the whitespace between their two
        # child columns.  They are a hierarchy, rather than additional columns.
        candidates = [
            c
            for c in chunks
            if (
                c.top < seed.top - fs * 0.45
                or (
                    c.top <= seed.top + fs * 0.4
                    and any(
                        role(q.text) in ("value", "range", "low", "high") and q.top > c.bottom + 1
                        for q in chunks
                    )
                )
            )
            and role(c.text) in ("unknown", "location")
            and len(norm(c.text)) < 50
            and re.search(r"[A-Za-z]", c.text)
            and not re.fullmatch(
                r"(?:EPA[’']?s|ideal|goal|action|number|# of|of|the|high|low|typical|sources|sample|year|date|limit|level|test|single|results?|were less|than|with|homes|sites)",
                norm(c.text),
                re.I,
            )
        ]
        bands = []
        for c in sorted(candidates, key=lambda c: c.top):
            hit = next((g for g in bands if abs(g[0].top - c.top) < max(3, fs * 0.4)), None)
            if hit is None:
                bands.append([c])
            else:
                hit.append(c)
        bands = [
            g for g in bands if len(g) >= 2 and max(c.x0 for c in g) - min(c.x0 for c in g) > 35
        ]
        parents = max(bands, key=lambda g: (len(g), g[0].top)) if bands else []
        if parents:
            texts = {norm(c.text) for c in parents}
            cols = [c for c in cols if norm(c[2]) not in texts]
        transposed = bool(
            cols and cols[0][3] in ("date", "location") and not any(c[3] == "name" for c in cols)
        )
        if len(cols) < 2 or (not any(c[3] == "name" for c in cols) and not transposed):
            continue
        if sum(c[3] not in ("unknown", "name") for c in cols) < 1:
            continue
        # Avoid a paragraph that happened to contain "contaminant".
        if not any(
            c[3] in ("value", "value_range", "range", "combined", "count_positive") for c in cols
        ) and not (transposed and all(plausible_name(c[2]) for c in cols[1:])):
            continue
        # Some publishers leave the violation-column heading blank.
        yesno = []
        for l in lines[first : first + 35]:
            for c in l["chunks"]:
                if re.fullmatch(r"(?:Yes|No|Y|N|✓|✔|✗|✘)", norm(c.text), re.I):
                    yesno.append(c)
        for cy in cluster_numbers([(c.x0 + c.x1) / 2 for c in yesno], 7):
            cc = [c for c in yesno if abs((c.x0 + c.x1) / 2 - cy) < 7]
            if any(
                a - 8 <= cy <= z + 8 and rr in ("violation", "compliance") for a, z, t, rr in cols
            ):
                continue
            if any(a - 3 <= cy <= z + 3 for a, z, t, rr in cols):
                continue
            if cy < cols[0][0] or cy > cols[-1][1] + 110:
                continue
            if len(cc) >= 2 or any(c[3] == "mcl" for c in cols):
                cols.append(
                    (min(c.x0 for c in cc), max(c.x1 for c in cc), "Violation", "violation")
                )
        cols.sort()
        ht = min(c.top for g in groups for c in g if c.bottom <= end + 0.5)
        xs = [max(0, cols[0][0] - max(8, fs))]
        for a, b in zip(cols, cols[1:]):
            xs.append((a[1] + b[0]) / 2)
        last = cols[-1]
        if last[3] == "source":
            right = pd.page.width - 15
        else:
            right = min(pd.page.width - 5, last[1] + max(15, (last[1] - last[0]) * 0.6))
        xs.append(right)
        if any(xs[j + 1] - xs[j] < 10 for j in range(len(xs) - 1)):
            continue
        # Centered headings can be narrower than the long regulatory text below
        # them.  Move a separator out of words, within the gap between headings.
        bodywords = [
            w
            for l in lines
            if end < l["top"] < end + 100
            for w in l["words"]
            if len(l["words"]) < 14
            or max([v["x0"] - u["x1"] for u, v in zip(l["words"], l["words"][1:])] or [0]) > 14
        ]
        for j in range(1, len(xs) - 1):
            left = cols[j - 1][1] + 0.7
            rightgap = cols[j][0] - 0.7
            if rightgap - left < 4:
                continue

            def cuts(x):
                return sum(w["x0"] + 0.6 < x < w["x1"] - 0.6 for w in bodywords)

            if not cuts(xs[j]):
                continue
            opts = [left + k * (rightgap - left) / 60 for k in range(61)]
            xx = min(opts, key=lambda x: cuts(x) + 0.003 * abs(x - xs[j]))
            if cuts(xx) < cuts(xs[j]):
                xs[j] = xx
        spans = []
        if parents:
            assignment = collections.defaultdict(list)
            for j, col in enumerate(cols):
                if col[3] not in ("value", "value_range", "range", "low", "high", "combined"):
                    continue
                cx = (col[0] + col[1]) / 2
                p = min(
                    range(len(parents)), key=lambda k: abs((parents[k].x0 + parents[k].x1) / 2 - cx)
                )
                assignment[p].append(j)
            if len(assignment) >= 2:
                for k, jj in assignment.items():
                    c = parents[k]
                    spans.append(Cell(xs[min(jj)], c.top, xs[max(jj) + 1], c.bottom, c.text))
        blocks.append(
            {
                "top": ht,
                "end": end,
                "first": first,
                "cols": cols,
                "xs": xs,
                "lines": lines,
                "seed": seed,
                "parents": spans,
            }
        )
    # One multi-line heading can contain more than one contaminant/name token.
    uniq = []
    for b in sorted(blocks, key=lambda b: (b["top"], -len(b["cols"]))):
        if any(abs(b["end"] - a["end"]) < 4 and abs(b["xs"][0] - a["xs"][0]) < 25 for a in uniq):
            continue
        uniq.append(b)
    return uniq


def text_candidates(pd, accepted, blocks=None):
    blocks = header_blocks(pd) if blocks is None else blocks
    out = []
    for b in blocks:
        xs = b["xs"]
        cols = b["cols"]
        roles = [c[3] for c in cols]
        n = len(cols)
        # This header is already enclosed by an extracted, drawn table.
        if any(
            a[0] - 8 <= b["seed"].x0 < a[2] and a[1] - 8 <= b["seed"].top < a[3] for a in accepted
        ):
            continue
        ni = next((j for j, r in enumerate(roles) if r == "name"), 0)
        primary = next(
            (j for j, r in enumerate(roles) if r in ("value", "value_range", "combined")), None
        )
        if primary is None:
            primary = next(
                (j for j, r in enumerate(roles) if r in ("range", "count_positive")), None
            )
        if primary is None and roles[0] in ("date", "location") and len(cols) > 1:
            primary = 1
        if primary is None:
            continue
        next_top = min(
            [
                z["top"]
                for z in blocks
                if z["top"] > b["end"] + 5 and max(z["xs"][0], xs[0]) < min(z["xs"][-1], xs[-1])
            ]
            + [pd.page.height - 12]
        )
        all_lines = []
        anchors = []
        paren = False
        previous_primary = ""
        for line in b["lines"]:
            if line["bottom"] <= b["end"] or line["top"] >= next_top:
                continue
            ts = ["" for _ in cols]
            for w in line["words"]:
                cx = (w["x0"] + w["x1"]) / 2
                for j in range(n):
                    if xs[j] <= cx < xs[j + 1]:
                        ts[j] = norm(ts[j] + " " + w["text"])
                        break
            if not any(ts):
                continue
            # A prose line that crosses the value columns is a table terminator.
            wide = any(
                c.x0 < xs[min(ni, primary)] + 10
                and c.x1 > xs[min(n - 1, primary + 2)]
                and len(c.text.split()) > 10
                for c in line["chunks"]
            )
            rawline = " ".join(w["text"] for w in line["words"])
            gaps = [v["x0"] - u["x1"] for u, v in zip(line["words"], line["words"][1:])]
            prose = (
                len(line["words"]) > 13
                and max(gaps or [0]) < 14
                and line["words"][0]["x0"] < xs[min(ni + 1, n - 1)]
                and line["words"][-1]["x1"] > xs[min(primary + 2, n - 1)]
                and sum(
                    value_like(t)
                    for t, r in zip(ts, roles)
                    if r not in ("name", "source", "location")
                )
                < 2
            )
            footnote = bool(
                re.match(
                    r"^\s*(?:[*†‡]|\d+[.)]\s|\d+\s+This\b|Note\s*[:.]|EPA[’\']s\s+MCL)",
                    rawline,
                    re.I,
                )
            )
            if (wide or prose or footnote) and anchors:
                break
            if any(
                re.fullmatch(
                    r"(?:inorganic|synthetic organic|radioactive|microbiological|organic|unregulated|regulated|secondary|disinfection)\s+(?:contaminants|byproducts|substances)\d*",
                    norm(c.text),
                    re.I,
                )
                for c in line["chunks"]
            ):
                continue
            raw = norm(ts[primary])
            score = sum(
                value_like(t) or (r == "unit" and unit_in(t) is not None)
                for t, r in zip(ts, roles)
                if r not in ("name", "source", "location", "unknown")
            )
            candidate = (
                value_like(raw)
                or bool(
                    re.match(
                        r"^(?:\d+(?:\.\d+)?%\s+of\b|(?:average|max(?:imum)?|highest|90th percentile)\s*[:=])",
                        raw,
                        re.I,
                    )
                )
            ) and not NULL_RE.fullmatch(raw or "-")
            if raw.startswith("("):
                paren = True
                candidate = False
            elif paren:
                candidate = False
            if ")" in raw:
                paren = False
            # Units and a broken range endpoint are continuations.
            if raw.endswith(("–", "-")) or (re.fullmatch(r"[\d.]+\)", raw)):
                candidate = False
            if candidate and score < 2 and anchors:
                if (
                    re.search(r"than|less|below|above|samples", previous_primary, re.I)
                    and line["cy"] - all_lines[anchors[-1]]["line"]["cy"] < 35
                ):
                    candidate = False
            if candidate:
                # Single-column fragments of a narrative do not qualify.
                if score >= 2 or len(raw) < 35:
                    anchors.append(len(all_lines))
            all_lines.append({"line": line, "texts": ts})
            if raw:
                previous_primary = raw
        if not anchors:
            continue
        # Cluster wrapped row labels before assigning them to result anchors.
        # This also recovers one merged contaminant label beside two locations.
        namegroups = []
        acys = [all_lines[a]["line"]["cy"] for a in anchors]
        for row in all_lines:
            if not norm(row["texts"][ni]):
                continue
            l = row["line"]
            t = row["texts"][ni]
            if namegroups:
                g = namegroups[-1]
                gap = l["top"] - g["bottom"]
                fs = max(5, l["bottom"] - l["top"])
                hit = sum(g["top"] - 2 <= a <= l["bottom"] + 2 for a in acys)
                if gap < fs * 0.85 and hit < 2:
                    g["text"] = merge_text(g["text"], t)
                    g["bottom"] = l["bottom"]
                    continue
            namegroups.append({"top": l["top"], "bottom": l["bottom"], "text": t})
        # Thin empty lines do not occur in extract_words.  A large gap between
        # result anchors is acceptable: source-description cells can be tall.
        bounds = [b["end"] + 0.2]
        for a, z in zip(anchors, anchors[1:]):
            ya = all_lines[a]["line"]["cy"]
            yz = all_lines[z]["line"]["cy"]
            mid = (ya + yz) / 2
            names = [
                r["line"]
                for r in all_lines
                if r["texts"][ni] and ya - 16 < r["line"]["cy"] < yz + 16
            ]
            gaps = [
                ((u["bottom"] + v["top"]) / 2, v["top"] - u["bottom"])
                for u, v in zip(names, names[1:])
                if ya + 1 < (u["bottom"] + v["top"]) / 2 < yz - 1 and v["top"] - u["bottom"] > 5
            ]
            if gaps:
                good = max(gaps, key=lambda q: q[1] - 0.2 * abs(q[0] - mid))
                mid = good[0]
            bounds.append(mid)
        bounds.append(
            min(
                next_top,
                all_lines[anchors[-1]]["line"]["bottom"] + 23,
                all_lines[-1]["line"]["bottom"] + 0.5,
            )
        )
        cells = [Cell(xs[j], b["top"], xs[j + 1], b["end"], cols[j][2]) for j in range(n)]
        cells.extend(b.get("parents", []))
        last_name = ""
        prepared = []
        for k, anchor in enumerate(anchors):
            top, bottom = bounds[k], bounds[k + 1]
            ts = ["" for _ in cols]
            for row in all_lines:
                if top <= row["line"]["cy"] < bottom:
                    for j, t in enumerate(row["texts"]):
                        ts[j] = merge_text(ts[j], t)
            if namegroups:
                cy = all_lines[anchor]["line"]["cy"]
                ng = min(namegroups, key=lambda g: abs((g["top"] + g["bottom"]) / 2 - cy))
                ts[ni] = ng["text"]
            # An empty name is a genuine continuation of a merged label only
            # when there was a measured result in this band.
            if not ts[ni] and last_name:
                ts[ni] = last_name
            elif ts[ni]:
                last_name = ts[ni]
            prepared.append((top, bottom, ts))
        for top, bottom, ts in prepared:
            for j, rr in enumerate(roles):
                if not ts[j] and rr in ("unit", "mcl", "mclg", "date", "violation", "compliance"):
                    peers = [
                        zz[j] for tt, bb, zz in prepared if norm(zz[ni]) == norm(ts[ni]) and zz[j]
                    ]
                    if len(set(peers)) == 1:
                        ts[j] = peers[0]
            for j, t in enumerate(ts):
                cells.append(Cell(xs[j], top, xs[j + 1], bottom, t))
        bbox = (xs[0], b["top"], xs[-1], bounds[-1])
        out.append((cells, bbox))
    return out


_OCR_ENGINE = None


class RasterPage:
    """The subset of the pdfplumber page interface needed by TableFinder."""

    def __init__(self, width, height, chars, edges):
        self.width = width
        self.height = height
        self.chars = chars
        self.edges = edges
        self.bbox = (0, 0, width, height)
        self.images = []

    def dedupe_chars(self, **kwargs):
        return self

    def extract_words(self, **kwargs):
        from pdfplumber.utils import extract_words

        return extract_words(self.chars, **kwargs)

    def find_tables(self, settings=None):
        from pdfplumber.table import TableFinder

        return TableFinder(self, settings).tables


def raster_page(image, width, height, engine=None):
    import numpy as np
    import cv2

    global _OCR_ENGINE
    if engine is None:
        if _OCR_ENGINE is None:
            from rapidocr_onnxruntime import RapidOCR

            modeldir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
            _OCR_ENGINE = RapidOCR(
                intra_op_num_threads=2,
                inter_op_num_threads=1,
                use_cls=False,
                det_limit_side_len=2400,
                det_limit_type="max",
                text_score=0.45,
                det_model_path=os.path.join(modeldir, "ch_PP-OCRv4_det_infer.onnx"),
                rec_model_path=os.path.join(modeldir, "ch_PP-OCRv4_rec_infer.onnx"),
                cls_model_path=os.path.join(modeldir, "ch_ppocr_mobile_v2.0_cls_infer.onnx"),
            )
        engine = _OCR_ENGINE
    arr = np.asarray(image.convert("RGB"))
    result, _ = engine(arr, return_word_box=True)
    if not result:
        return None
    sy = height / arr.shape[0]
    sx = width / arr.shape[1]
    chars = []
    for item in result:
        box, text, score = item[:3]
        if score < 0.4:
            continue
        cb = item[3] if len(item) > 3 else None
        ct = item[4] if len(item) > 4 else None
        if cb is None or ct is None:
            x0 = min(p[0] for p in box)
            x1 = max(p[0] for p in box)
            top = min(p[1] for p in box)
            bottom = max(p[1] for p in box)
            ct = list(text)
            cb = [
                [
                    [x0 + k * (x1 - x0) / len(ct), top],
                    [x0 + (k + 1) * (x1 - x0) / len(ct), top],
                    [x0 + (k + 1) * (x1 - x0) / len(ct), bottom],
                    [x0 + k * (x1 - x0) / len(ct), bottom],
                ]
                for k in range(len(ct))
            ]
        itemchars = []
        for t, bb in zip(ct, cb):
            x0 = min(p[0] for p in bb) * sx
            x1 = max(p[0] for p in bb) * sx
            top = min(p[1] for p in bb) * sy
            bottom = max(p[1] for p in bb) * sy
            itemchars.append(
                dict(
                    text=fix(t),
                    x0=x0,
                    x1=x1,
                    top=top,
                    bottom=bottom,
                    doctop=top,
                    size=(bottom - top) * 0.85,
                    upright=True,
                    fontname="OCR",
                    width=x1 - x0,
                    height=bottom - top,
                )
            )
        for a, b in zip(itemchars, itemchars[1:]):
            if a["text"].strip() and b["text"].strip():
                a["x1"] = max(a["x1"], b["x0"] - 0.15)
        chars.extend(itemchars)
    return RasterPage(width, height, chars, image_edges(arr, width, height))


def image_edges(arr, width, height):
    import cv2

    sx = width / arr.shape[1]
    sy = height / arr.shape[0]
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 19, 12)
    edges = []
    for orient in ["h", "v"]:
        size = (
            (max(25, arr.shape[1] // 35), 1) if orient == "h" else (1, max(25, arr.shape[0] // 40))
        )
        lines = cv2.morphologyEx(
            bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, size)
        )
        contours, _ = cv2.findContours(lines, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if orient == "h" and w > 45 and w > h * 6:
                xx0 = x * sx
                xx1 = (x + w) * sx
                yy = (y + h / 2) * sy
                edges.append(
                    dict(
                        x0=xx0,
                        x1=xx1,
                        top=yy,
                        bottom=yy,
                        width=xx1 - xx0,
                        height=0,
                        orientation="h",
                        object_type="line",
                    )
                )
            elif orient == "v" and h > 35 and h > w * 6:
                xx = (x + w / 2) * sx
                yy0 = y * sy
                yy1 = (y + h) * sy
                edges.append(
                    dict(
                        x0=xx,
                        x1=xx,
                        top=yy0,
                        bottom=yy1,
                        width=0,
                        height=yy1 - yy0,
                        orientation="v",
                        object_type="line",
                    )
                )
    return edges


def tesseract_page(image, width, height):
    executable = shutil.which("tesseract")
    if not executable:
        return None
    import csv
    from io import BytesIO, StringIO
    import numpy as np
    import cv2
    from PIL import Image

    arr = np.asarray(image.convert("RGB"))
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 19, 12)
    horizontal = cv2.morphologyEx(
        bw,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(45, image.width // 28), 1)),
    )
    vertical = cv2.morphologyEx(
        bw,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(45, image.height // 30))),
    )
    rules = cv2.dilate(horizontal | vertical, cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)))
    clean = arr.copy()
    clean[rules > 0] = 255
    png = BytesIO()
    Image.fromarray(clean).save(png, format="PNG")
    modeldir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
    env = dict(os.environ)
    env["OMP_THREAD_LIMIT"] = "2"
    cmd = [
        executable,
        "stdin",
        "stdout",
        "--tessdata-dir",
        modeldir,
        "-l",
        "eng",
        "--psm",
        "3",
        "-c",
        "tessedit_create_tsv=1",
    ]
    p = subprocess.run(
        cmd,
        input=png.getvalue(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=18,
        env=env,
    )
    if p.returncode or not p.stdout:
        return None
    sx = width / image.width
    sy = height / image.height
    chars = []
    for word in csv.DictReader(
        StringIO(p.stdout.decode("utf-8", errors="replace")), delimiter="\t", quoting=csv.QUOTE_NONE
    ):
        if word.get("level") != "5":
            continue
        text = fix(word.get("text", "")).strip()
        if not text:
            continue
        try:
            x = float(word["left"]) * sx
            top = float(word["top"]) * sy
            w = float(word["width"]) * sx
            h = float(word["height"]) * sy
        except (KeyError, ValueError):
            continue
        for j, ch in enumerate(text):
            a = x + w * j / len(text)
            b = x + w * (j + 1) / len(text)
            chars.append(
                dict(
                    text=ch,
                    x0=a,
                    x1=b,
                    top=top,
                    bottom=top + h,
                    doctop=top,
                    size=h,
                    upright=True,
                    fontname="OCR",
                    width=b - a,
                    height=h,
                )
            )
        chars.append(
            dict(
                text=" ",
                x0=x + w,
                x1=x + w + 0.2,
                top=top,
                bottom=top + h,
                doctop=top,
                size=h,
                upright=True,
                fontname="OCR",
                width=0.2,
                height=h,
            )
        )
    if len(chars) < 10:
        return None
    return RasterPage(width, height, chars, image_edges(arr, width, height))


def should_ocr(pd, accepted):
    images = getattr(pd.page, "images", [])
    bad = sum("\ue000" <= c.get("text", " ")[:1] <= "\uf8ff" for c in pd.chars)
    if "(cid:" in pd.text or (pd.chars and bad > len(pd.chars) * 0.2):
        return True
    if not images:
        return (
            len(pd.chars) < 80
            and len(getattr(pd.page, "curves", [])) + len(getattr(pd.page, "rects", [])) > 100
        )
    pagearea = pd.page.width * pd.page.height
    for im in images:
        bb = (im["x0"], im["top"], im["x1"], im["bottom"])
        area = (bb[2] - bb[0]) * (bb[3] - bb[1])
        if area < pagearea * 0.05:
            continue
        if any(contains(a, bb, 5) for a in accepted):
            continue
        inside = sum(bb[0] <= c["x0"] < bb[2] and bb[1] <= c["top"] < bb[3] for c in pd.chars)
        if len(pd.chars) < 250 or (inside < 45 and bb[2] - bb[0] > 150 and bb[3] - bb[1] > 55):
            return True
    return False


def crop_ocr(image, bbox, page_width, page_height):
    executable = shutil.which("tesseract")
    if not executable:
        return None
    from PIL import ImageOps
    from io import BytesIO

    sx = image.width / page_width
    sy = image.height / page_height
    x0, y0, x1, y1 = bbox
    box = (
        max(0, int(x0 * sx) + 3),
        max(0, int(y0 * sy) + 3),
        min(image.width, int(x1 * sx) - 3),
        min(image.height, int(y1 * sy) - 3),
    )
    if box[2] - box[0] < 5 or box[3] - box[1] < 5:
        return None
    im = image.crop(box).convert("RGB")
    factor = 2 if image.width < 3000 else 1.5
    im = im.resize((int(im.width * factor), int(im.height * factor)))
    im = ImageOps.expand(im, border=12, fill="white")
    png = BytesIO()
    im.save(png, format="PNG")
    env = dict(os.environ)
    env["OMP_THREAD_LIMIT"] = "1"
    modeldir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
    cmd = [
        executable,
        "stdin",
        "stdout",
        "--tessdata-dir",
        modeldir,
        "-l",
        "eng",
        "--psm",
        "6",
        "-c",
        "preserve_interword_spaces=1",
    ]
    try:
        p = subprocess.run(
            cmd,
            input=png.getvalue(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=3,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return fix(p.stdout.decode("utf-8", errors="replace")).strip() if p.returncode == 0 else None


def accept_crop(old, new, kind):
    if not new:
        return False
    a = norm(old)
    b = norm(new)
    if a == b:
        return False
    if kind == "unit":
        if not unit_in(b) or len(b) > 40:
            return False
        if unit_in(a) and re.sub(r"\s+", "", a).lower() != re.sub(r"\s+", "", b).lower():
            return False
        return True
    if not a:
        return value_like(b) and len(b) < 30
    if a in ("O", "o") and b == "0":
        return True

    def numbers(s):
        out = []
        for m in re.finditer(NUM, s):
            try:
                out.append(abs(float(m.group().replace(",", ""))))
            except ValueError:
                pass
        return out

    if numbers(a) != numbers(b):
        return False

    def letters(s):
        return re.sub(r"[^a-z]", "", s.lower().replace("µ", "u"))

    return letters(a) == letters(b)


def refine_raster_cells(pd, table, image):
    cells = pd.cells(table)
    if not shutil.which("tesseract"):
        return cells
    profile = table_profile(cells, table.bbox)
    if not profile:
        return cells
    xs, rows, ni, start, hs = profile
    roles = [role(h) for h in hs]
    roles[ni] = "name"
    first = rows[start]["top"]
    out = []
    cache = getattr(pd, "crop_cache", None)
    if cache is None:
        cache = {}
        pd.crop_cache = cache

    def unit_glyphs(s):
        s = re.sub(r"\b([mnuµp]g|pCi)\s*/\s*[1I|](?=\s|$)", r"\1/l", s, flags=re.I)
        s = re.sub(r"\b([mnuµp]g|pCi)\s*/\s*$", r"\1/l", s, flags=re.I)
        return s

    for c in cells:
        rr = None
        j = max(range(len(xs) - 1), key=lambda j: max(0, min(c.x1, xs[j + 1]) - max(c.x0, xs[j])))
        if c.top >= first - 1:
            rr = roles[j]
        if rr == "unit":
            c = dataclasses.replace(c, text=unit_glyphs(c.text))
        text = norm(c.text)
        needed = rr in (
            "date",
            "unit",
            "mcl",
            "mclg",
            "range",
            "low",
            "high",
            "value",
            "value_range",
            "combined",
        ) and bool(text)
        if not text and rr in (
            "date",
            "unit",
            "mcl",
            "mclg",
            "range",
            "low",
            "high",
            "value",
            "value_range",
            "combined",
        ):
            sx = image.width / pd.page.width
            sy = image.height / pd.page.height
            box = (
                int(c.x0 * sx) + 8,
                int(c.top * sy) + 8,
                int(c.x1 * sx) - 8,
                int(c.bottom * sy) - 8,
            )
            if box[2] > box[0] and box[3] > box[1]:
                patch = image.crop(box).convert("L")
                hist = patch.histogram()
                needed = sum(hist[:150]) > max(4, patch.width * patch.height * 0.0006)
        if needed and (
            NULL_RE.fullmatch(text)
            or re.fullmatch(r"(?:" + NUM + r"|ND|TT|[<>≤≥]=?\s*" + NUM + r")", text, re.I)
        ):
            needed = False
        if rr == "range":
            lo, hi, _ = range_values(text)
            if lo is not None and hi is not None:
                needed = False
        if rr in ("value", "value_range"):
            v, lo, hi = level_and_range(c.text, rr)
            if isinstance(v, (float, int)):
                needed = False
        if needed and time.monotonic() - START < 100:
            key = tuple(round(x, 2) for x in c.bbox)
            if key not in cache:
                cache[key] = crop_ocr(image, c.bbox, pd.page.width, pd.page.height)
            new = cache[key]
            if new and rr == "unit":
                new = unit_glyphs(new)
            if accept_crop(c.text, new, rr):
                c = dataclasses.replace(c, text=new)
        out.append(c)
    return out


def ocr_pdf_page(pdf_path, pd, accepted, system):
    if time.monotonic() - START > 85:
        return []
    from PIL import Image
    from io import BytesIO

    cmd = [
        "pdftoppm",
        "-f",
        str(pd.index + 1),
        "-l",
        str(pd.index + 1),
        "-singlefile",
        "-scale-to",
        "4000",
        "-png",
        pdf_path,
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
    if proc.returncode or not proc.stdout:
        return []
    im = Image.open(BytesIO(proc.stdout))
    if getattr(pd, "raster_rotation", 0):
        im = im.rotate(-pd.raster_rotation, expand=True)
    try:
        page = raster_page(im, pd.page.width, pd.page.height)
    except Exception as e:
        log("Neural OCR unavailable:", repr(e))
        page = tesseract_page(im, pd.page.width, pd.page.height)
    if page is None:
        return []
    op = PageData(page, pd.index)
    out = []
    used = list(accepted)
    for t in sorted(
        page.find_tables(), key=lambda t: -(t.bbox[2] - t.bbox[0]) * (t.bbox[3] - t.bbox[1])
    ):
        if any(overlap(a, t.bbox) > 0.5 for a in used):
            continue
        context = op.get_text((t.bbox[0], max(0, t.bbox[1] - 90), t.bbox[2], t.bbox[1]))
        rs = read_table(
            refine_raster_cells(op, t, im), t.bbox, system, pd.index, "OCR grid", context
        )
        if rs:
            out.extend(rs)
            used.append(t.bbox)
    for cells, bbox in text_candidates(op, used):
        if any(overlap(a, bbox) > 0.5 for a in used):
            continue
        context = op.get_text((bbox[0], max(0, bbox[1] - 90), bbox[2], bbox[1]))
        rs = read_table(cells, bbox, system, pd.index, "OCR aligned", context)
        if rs:
            out.extend(rs)
            used.append(bbox)
    return out


SYSTEM_PATTERNS = [
    r"\bPWS\s*I\s*D\s*(?:number|no\.?)?\s*[:#=–-]*\s*\(?\s*([A-Z]{0,2}\s*\d[A-Z\d-]{5,11})",
    r"\b(?:public\s+)?water\s+(?:supply|system)\s*(?:identification|ID|number|no\.?)(?:\s*\(PWSID\))?\s*[:#=–-]*\s*([A-Z]{0,2}\s*\d[A-Z\d-]{5,11})",
    r"\b(?:public\s+)?water\s+(?:supply|system)\s+identification\s*\([^)]*\)\s*[:#=–-]*\s*([A-Z]{0,2}\s*\d[A-Z\d-]{5,11})",
    r"\bPWS\s*(?:number|no\.?|#)\s*[:#=–-]*\s*([A-Z]{0,2}\s*\d[A-Z\d-]{5,11})",
    r"\b(?:system|supply)\s+ID\s*[:#=–-]+\s*([A-Z]{0,2}\s*\d[A-Z\d-]{5,11})",
    r"\b(?:public\s+)?water\s+(?:supply|system)\s*(?:identification|ID)\s*(?:number|no\.?)?\s*[:#=–-]*\s*([A-Z]{0,2}\s*\d[\d-]{5,11})",
    r"\b(?:public\s+)?water\s+(?:supply|system)\s*#\s*([A-Z]{0,2}\s*\d[\d-]{5,11})",
    r"\bPWS\s*(?:identification|ID)\s*(?:number|no\.?)?\s*[:#=–-]*\s*([A-Z]{0,2}\s*\d[\d-]{5,11})",
    r"\bPWS\s*[:#]\s*([A-Z]{0,2}\s*\d[\d-]{5,11})",
]


def system_ids(text):
    ids = []
    matches = []
    text = norm(text)
    for pat in SYSTEM_PATTERNS:
        for m in re.finditer(pat, text, re.I):
            ident = re.sub(r"\s+", "", m.group(1)).rstrip("-")
            if len(ident) >= 6:
                matches.append((m.start(), ident))
    # Letter-spaced footers are printed using tracking, which PDF text tools
    # sometimes render as one word per character.
    for m in re.finditer(
        r"P\s*W\s*S\s*I\s*D\s*[\s):#=]*((?:[A-Z]\s*){0,2}(?:\d\s*){6,10})", text, re.I
    ):
        ident = re.sub(r"\s+", "", m.group(1))
        matches.append((m.start(), ident))
    for _, ident in sorted(matches):
        if ident not in ids:
            ids.append(ident)
    return ids


REPORT_TITLE_RE = re.compile(
    r"consumer\s+confidence\s+report|(?:annual|drinking|water).*?(?:water|quality).*?report|PWSID\s*[:#].*?\bNAME\s*:",
    re.I,
)


def supplier_ids(pages):
    suppliers = set()
    for page in pages:
        text = norm(page.text)
        for pat in SYSTEM_PATTERNS:
            for m in re.finditer(pat, text, re.I):
                before = text[max(0, m.start() - 150) : m.start()]
                if re.search(
                    r"purchas|supplied\s+by|supplier|wholesale|provided\s+by|water\s+from",
                    before,
                    re.I,
                ):
                    suppliers.add(re.sub(r"\s+", "", m.group(1)).rstrip("-"))
    return suppliers


def local_system(pd, bbox, current, suppliers):
    # A second complete report can begin midway down a compiled page.
    context = pd.get_text((0, max(0, bbox[1] - 140), pd.page.width, bbox[1]))
    ids = system_ids(context)
    if ids and ids[-1] != current and REPORT_TITLE_RE.search(norm(context)):
        if ids[-1] not in suppliers or re.search(
            r"consumer confidence|annual.*report", context, re.I
        ):
            return ids[-1]
    return current


def contains(a, b, pad=2):
    return a[0] - pad <= b[0] and a[1] - pad <= b[1] and a[2] + pad >= b[2] and a[3] + pad >= b[3]


def overlap(a, b):
    area = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    denom = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return area / denom if denom else 0


def template_for(cells, bbox, page_width, page_height, system):
    profile = table_profile(cells, bbox)
    if not profile:
        return None
    xs, rows, ni, start, hs = profile
    if not any(name_header(h) for h in hs):
        return None
    return dict(
        xs=[x / page_width for x in xs], headings=hs, bottom=bbox[3] / page_height, system=system
    )


def inherit_header(cells, bbox, templates, page_width, context, system):
    if any(name_header(c.text) for c in cells):
        return cells, bbox
    xs = canonical_xs(cells, bbox)
    for t in reversed(templates):
        if t["system"] != system or len(xs) != len(t["xs"]):
            continue
        if t["bottom"] < 0.65 and not re.search(r"continu", context, re.I):
            continue
        if sum(abs(a / page_width - b) for a, b in zip(xs, t["xs"])) / len(xs) > 0.025:
            continue
        headers = [
            Cell(xs[j], bbox[1] - 6, xs[j + 1], bbox[1] - 2, h) for j, h in enumerate(t["headings"])
        ]
        return headers + cells, (bbox[0], bbox[1] - 6, bbox[2], bbox[3])
    return cells, bbox


def continuation_candidates(pd, templates, accepted, system):
    lines = None
    for t in reversed(templates):
        if t["system"] != system or t["bottom"] < 0.65:
            continue
        hs = t["headings"]
        rr = [role(h) for h in hs]
        ni = next((j for j, r in enumerate(rr) if r == "name"), None)
        li = next(
            (
                j
                for j, r in enumerate(rr)
                if r in ("value", "value_range", "combined", "range", "count_positive")
            ),
            None,
        )
        if ni is None or li is None:
            continue
        if lines is None:
            lines = word_lines(pd)
        xs = [x * pd.page.width for x in t["xs"]]
        first = None
        for i, l in enumerate(lines):
            if l["top"] > pd.page.height * 0.5:
                break
            ts = [""] * len(hs)
            for w in l["words"]:
                cx = (w["x0"] + w["x1"]) / 2
                for j in range(len(hs)):
                    if xs[j] <= cx < xs[j + 1]:
                        ts[j] = norm(ts[j] + " " + w["text"])
                        break
            if len(l["chunks"]) < 2 or any(
                c.x0 < xs[ni] + 8 and c.x1 > xs[li + 1] and len(c.text.split()) > 6
                for c in l["chunks"]
            ):
                continue
            score = sum(
                value_like(s) or (rr[j] == "unit" and unit_in(s) is not None)
                for j, s in enumerate(ts)
                if s
            )
            if value_like(ts[li]) and (score >= 2 or plausible_name(ts[ni])):
                first = i
                break
        if first is None:
            continue
        end = max(3, lines[first]["top"] - 25)
        top = max(0, end - 5)
        cols = [(xs[j] + 2, xs[j + 1] - 2, h, rr[j]) for j, h in enumerate(hs)]
        seed = Cell(xs[ni], top, xs[ni + 1], end, hs[ni])
        block = dict(
            top=top, end=end, first=first, cols=cols, xs=xs, lines=lines, seed=seed, parents=[]
        )
        found = text_candidates(pd, accepted, [block])
        if found:
            return found
    return []


def orientation_score(page):
    chars = [c for c in page.chars if c.get("text", "").strip()]
    if not chars:
        return 1
    return sum(
        c.get("upright", True)
        and c.get("matrix", (1, 0, 0, 1, 0, 0))[0] > 0
        and c.get("matrix", (1, 0, 0, 1, 0, 0))[3] > 0
        for c in chars
    ) / len(chars)


def upright_pages(pdf, pdf_path):
    # Consumer reports are often landscape pages stored with a rotation flag.
    # Normalize only pages whose text is predominantly sideways or upside down.
    out = []
    reader = None
    holders = []
    for i, page in enumerate(pdf.pages):
        angle = 0
        if page.chars and orientation_score(page) < 0.4:
            from pypdf import PdfReader, PdfWriter
            from io import BytesIO
            import copy

            if reader is None:
                reader = PdfReader(pdf_path)
            best = (orientation_score(page), page, 0, None)
            for rot in (90, 180, 270):
                writer = PdfWriter()
                p = copy.copy(reader.pages[i])
                p.rotate(rot)
                writer.add_page(p)
                stream = BytesIO()
                writer.write(stream)
                stream.seek(0)
                doc = pdfplumber.open(stream)
                candidate = doc.pages[0]
                score = orientation_score(candidate)
                if score > best[0]:
                    best = (score, candidate, rot, doc)
                if score > 0.85:
                    break
            _, page, angle, holder = best
            if holder is not None:
                holders.append(holder)
        pd = PageData(page, i)
        pd.raster_rotation = angle
        out.append(pd)
    return out, holders


def main(pdf_path, out_path):
    records = []
    with pdfplumber.open(pdf_path) as pdf:
        pages, rotated_documents = upright_pages(pdf, pdf_path)
        ids = [system_ids(p.text) for p in pages]
        all_ids = [s for ii in ids for s in ii]
        primary_id = all_ids[0] if all_ids else None
        current_id = primary_id
        templates = []
        suppliers = supplier_ids(pages)
        for i, pd in enumerate(pages):
            if time.monotonic() - START > 105:
                break
            if ids[i] and len(set(all_ids)) > 1:
                top_text = pd.get_text((0, 0, pd.page.width, min(200, pd.page.height)))
                top_ids = system_ids(top_text)
                if top_ids:
                    candidate = top_ids[0]
                    if (
                        candidate == current_id
                        or REPORT_TITLE_RE.search(norm(top_text))
                        or (
                            candidate not in suppliers
                            and current_id not in ids[i]
                            and not re.search(
                                r"purchas|supplier|supplied|wholesale|source water", top_text, re.I
                            )
                        )
                    ):
                        current_id = candidate
            accepted = []
            new_templates = []
            try:
                tables = pd.page.find_tables()
            except Exception as e:
                log("table detection failed:", e)
                tables = []
            tables = sorted(
                tables, key=lambda t: -(t.bbox[2] - t.bbox[0]) * (t.bbox[3] - t.bbox[1])
            )
            for t in tables:
                if any(contains(b, t.bbox) for b in accepted):
                    continue
                cells = pd.cells(t)
                bbox = t.bbox
                table_system = local_system(pd, bbox, current_id, suppliers)
                context = pd.get_text((bbox[0], max(0, bbox[1] - 90), bbox[2], bbox[1]))
                if bbox[1] < pd.page.height * 0.45:
                    cells, bbox = inherit_header(
                        cells, bbox, templates, pd.page.width, context, table_system
                    )
                try:
                    rs = read_table(cells, bbox, table_system, i, context=context)
                except Exception as e:
                    log("parse failed:", i, t.bbox, repr(e))
                    rs = []
                if rs:
                    accepted.append(t.bbox)
                    records.extend(rs)
                    tpl = template_for(cells, bbox, pd.page.width, pd.page.height, table_system)
                    if tpl:
                        new_templates.append(tpl)
            for cells, bbox in text_candidates(pd, accepted):
                if any(overlap(a, bbox) > 0.5 for a in accepted):
                    continue
                context = pd.get_text((bbox[0], max(0, bbox[1] - 90), bbox[2], bbox[1]))
                table_system = local_system(pd, bbox, current_id, suppliers)
                try:
                    rs = read_table(cells, bbox, table_system, i, "aligned", context)
                except Exception as e:
                    log("aligned parse failed:", i, repr(e))
                    rs = []
                if rs:
                    accepted.append(bbox)
                    records.extend(rs)
                    tpl = template_for(cells, bbox, pd.page.width, pd.page.height, table_system)
                    if tpl:
                        new_templates.append(tpl)
            for cells, bbox in continuation_candidates(pd, templates, accepted, current_id):
                if any(overlap(a, bbox) > 0.5 for a in accepted):
                    continue
                try:
                    rs = read_table(cells, bbox, current_id, i, "continued")
                except Exception as e:
                    log("continuation parse failed:", i, repr(e))
                    rs = []
                if rs:
                    accepted.append(bbox)
                    records.extend(rs)
                    tpl = template_for(cells, bbox, pd.page.width, pd.page.height, current_id)
                    if tpl:
                        new_templates.append(tpl)
            if should_ocr(pd, accepted):
                try:
                    records.extend(ocr_pdf_page(pdf_path, pd, accepted, current_id))
                except Exception as e:
                    log("OCR fallback failed:", i, repr(e))
            templates = new_templates
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({"records": records}, fh, ensure_ascii=False, indent=2, allow_nan=False)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
