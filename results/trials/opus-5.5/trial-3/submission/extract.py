#!/usr/bin/env python3
"""Extract detected-contaminant result tables from a Consumer Confidence Report PDF.

usage: python3 extract.py <report.pdf> <output.json>

Approach: words with positions (pdfplumber) -> text lines -> table header blocks
found by heading keywords -> column model from header cells (+ headerless value
columns) -> body rows grouped by vertical gaps / ruling lines -> cells parsed per
the column role (name, level, range, unit, mcl, mclg, date, violation, ...).
"""
import json
import re
import statistics
import sys
import time

T0 = time.time()

import pdfplumber

DEBUG = False


def dbg(*a):
    if DEBUG:
        print(*a, file=sys.stderr)


# ----------------------------------------------------------------------------
# text helpers
# ----------------------------------------------------------------------------
DASHES = "–—‒−‐‑"
DASH_RE = "[-" + DASHES + "]"
NUM = r"(?:\d{1,3}(?:,\d{3})+|\d+)?(?:\.\d+)?"
NUM_STRICT = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)"

UNIT_PATTERNS = [
    r"pci\s*/\s*l", r"mrem\s*/\s*y(?:ea)?r", r"mrem", r"[µμu]g\s*/\s*l", r"ug/l", r"mg\s*/\s*l(?:\s+as\s+\w+)?",
    r"ng\s*/\s*l", r"pg\s*/\s*l", r"ppm(?:\s+as\s+caco3)?", r"ppb", r"ppt", r"ppq", r"ntus?", r"ton",
    r"cfu\s*/\s*100\s*ml", r"mfl", r"grains?\s*/\s*gal(?:lon)?", r"gpg", r"[µμu]s\s*/\s*cm", r"[µμu]mhos?\s*/\s*cm",
    r"units", r"s\.?u\.?", r"color units", r"pcu", r"cu", r"°\s*[fc]", r"mg/kg", r"g/l",
]
UNIT_RE = re.compile(r"(?<![A-Za-z])(" + "|".join(UNIT_PATTERNS) + r")(?![A-Za-z])", re.I)
# units we accept when found free-standing in a value/heading (excludes ambiguous "units", "cu", "su")
STRONG_UNIT_RE = re.compile(
    r"(?<![A-Za-z])(pci\s*/\s*l|mrem\s*/\s*y(?:ea)?r|mrem|[µμu]g\s*/\s*l|mg\s*/\s*l(?:\s+as\s+(?:caco3|n|no3|p|po4))?|ng\s*/\s*l|pg\s*/\s*l|"
    r"ppm(?:\s+as\s+(?:caco3|n|no3))?|ppb|ppt|ppq|ntus?|cfu\s*/\s*100\s*ml|mfl|grains?\s*/\s*gal(?:lon)?|gpg|[µμu]s\s*/\s*cm|"
    r"[µμu]mhos?\s*/\s*cm|g/l)(?![A-Za-z])",
    re.I,
)

FOOTNOTE_CHARS = "*†‡§¶#^"


def clean_ws(s):
    return re.sub(r"\s+", " ", s or "").strip()


def strip_footnotes(s):
    s = clean_ws(s)
    s = re.sub(r"[" + re.escape("*†‡§¶^¹²³⁴⁵⁶⁷⁸⁹⁰⁽⁾ᵃᵇᶜᵈ") + r"]+", "", s)
    s = re.sub(r"\[\s*\d+\s*\]", "", s)
    s = re.sub(r"\(\s*(?:note|see note)\s*\d*\s*\)", "", s, flags=re.I)
    return clean_ws(s)


NULL_TOKENS = {"", "n/a", "na", "none", "no range", "-", "--", "---", "n.a.", "n / a", "not applicable", "nr"}


def is_null_text(s):
    t = clean_ws(s).lower().strip(".")
    t2 = t.replace(" ", "")
    if t in NULL_TOKENS or t2 in NULL_TOKENS:
        return True
    if all(ch in "-" + DASHES + " " for ch in t):
        return True
    if re.fullmatch(r"\(?\s*see\s+(?:foot)?notes?\s*[\w,\s]*\)?", t):
        return True
    return False


def to_number(s):
    """Return int/float if s is a plain number, else None."""
    t = clean_ws(s)
    t = t.replace(",", "") if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", t) else t
    if re.fullmatch(r"[-+]?(\d+(\.\d*)?|\.\d+)", t):
        try:
            if re.fullmatch(r"[-+]?\d+", t):
                return int(t)
            return float(t)
        except ValueError:
            return None
    return None


def find_unit(s):
    if not s:
        return None
    m = STRONG_UNIT_RE.search(s)
    if m:
        return clean_ws(m.group(1))
    return None


# ----------------------------------------------------------------------------
# value parsing
# ----------------------------------------------------------------------------
def split_value_unit(s):
    """'2.8 ppb' -> (2.8, 'ppb'); 'ND' -> ('ND', None); '<0.5 ppb' -> ('<0.5', 'ppb')."""
    t = strip_footnotes(s)
    if is_null_text(t):
        return None, None
    m = re.fullmatch(r"([<>≤≥]?\s*[-+]?" + NUM_STRICT + r")\s*(%)?\s*(.*)", t)
    if m:
        numtxt, pct, rest = m.group(1), m.group(2), m.group(3)
        rest_unit = None
        if rest:
            u = STRONG_UNIT_RE.fullmatch(rest.strip())
            if u:
                rest_unit = clean_ws(rest.strip())
            else:
                return t, find_unit(rest)
        if pct:
            return numtxt.replace(" ", "") + "%", rest_unit
        n = to_number(numtxt)
        if n is not None:
            return n, rest_unit
        return numtxt.replace(" ", ""), rest_unit
    return t, None


RANGE_SEP = r"\s*(?:" + DASH_RE + r"|to|through|thru)\s*"
RANGE_VAL = r"(?:[<>≤≥]?\s*" + NUM_STRICT + r"\s*%?|nd|n\.d\.|non-detect|bdl|<\s*mdl|<\s*mrl|<\s*rl|absent|0)"


def parse_range(s):
    """Return (low, high, unit) or None when s is not a range."""
    t = strip_footnotes(s)
    if is_null_text(t):
        return None
    t = re.sub(r"^\(|\)$", "", t).strip()
    t = re.sub(r"^range\s*[:=]?\s*", "", t, flags=re.I)
    unit = find_unit(t)
    t2 = STRONG_UNIT_RE.sub(" ", t).strip() if unit else t
    t2 = clean_ws(t2)
    m = re.fullmatch(r"(" + RANGE_VAL + r")" + RANGE_SEP + r"(" + RANGE_VAL + r")", t2, re.I) or \
        re.fullmatch(r"(" + RANGE_VAL + r")\s+(" + RANGE_VAL + r")", t2, re.I)
    if m:
        lo, _ = split_value_unit(m.group(1))
        hi, _ = split_value_unit(m.group(2))
        return lo, hi, unit
    return None


def parse_level_cell(s):
    """Parse a level cell. Returns dict(level, low, high, unit, label_values)."""
    t = strip_footnotes(s)
    out = {"level": None, "low": None, "high": None, "unit": None}
    if is_null_text(t):
        return out
    unit = find_unit(t)
    # value with parenthesised range: 0.145 (0.141-0.252)
    m = re.fullmatch(r"(.+?)\s*\(\s*([^()]+?)\s*\)", t)
    if m:
        r = parse_range(m.group(2))
        if r:
            lv, lu = split_value_unit(m.group(1))
            out.update(level=lv, low=r[0], high=r[1], unit=lu or r[2] or unit)
            return out
    m = re.fullmatch(r"([<>≤≥]?\s*" + NUM_STRICT + r")\s*(" + STRONG_UNIT_RE.pattern + r")?\s*\(([^()]*[A-Za-z][^()]*)\)", t, re.I)
    if m and not parse_range(m.group(m.lastindex)):
        lv, lu = split_value_unit(m.group(1))
        out.update(level=lv, unit=find_unit(t))
        return out
    # labelled values: "Max 1.28 / Average 0.69 / Range 0.47-1.28" or "Average: 0.89 Range: 0.31-1.36"
    if re.search(r"\brange\b", t, re.I) and re.search(r"\d", t):
        mm = re.search(r"\brange\b\s*[:=]?\s*(" + RANGE_VAL + RANGE_SEP + RANGE_VAL + r")", t, re.I)
        if mm:
            r = parse_range(mm.group(1))
            if r:
                out["low"], out["high"] = r[0], r[1]
            before = t[: mm.start()]
            after = t[mm.end():]
            vm = re.search(r"([<>≤≥]?\s*" + NUM_STRICT + r")", before) or re.search(
                r"([<>≤≥]?\s*" + NUM_STRICT + r")", after
            )
            if vm:
                out["level"], _ = split_value_unit(vm.group(1))
            out["unit"] = unit
            return out
    # "90th percentile value = 0.21" / "Average: 0.89"
    m = re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .%/#()]*?[A-Za-z][A-Za-z0-9 .%/#()]*?\s*[:=]\s*(.+)", t)
    if m and re.search(r"\d", m.group(1)):
        v, u = split_value_unit(m.group(1))
        out.update(level=v, unit=u or unit)
        return out
    v, u = split_value_unit(t)
    out.update(level=v, unit=u)
    return out


def yes_no(s, invert=False):
    t = strip_footnotes(s).lower()
    if not t:
        return None
    y = None
    if re.match(r"^(y|yes|✓|✔|x)\b", t) or t in ("y", "yes", "✓", "✔"):
        y = "Y"
    elif re.match(r"^(n|no|✗|✘)\b", t) or t in ("n", "no"):
        y = "N"
    elif t.startswith("✓") or t.startswith("✔"):
        y = "Y"
    elif t.startswith("✗") or t.startswith("✘"):
        y = "N"
    if y is None:
        return None
    if invert:
        y = "N" if y == "Y" else "Y"
    return y


# ----------------------------------------------------------------------------
# heading classification
# ----------------------------------------------------------------------------
def classify_heading(h):
    t = clean_ws(h).lower()
    t = t.replace("’", "'")
    if not t:
        return "blank"
    if re.search(r"\b(comparison|advisory|health[- ]based|hrl|notification level|reference)\b", t):
        return "other"
    if re.search(r"\b(health effects?|health concern|description)\b", t):
        return "health"
    if re.search(r"(number|#|no\.|count)\s*(of)?\s*(homes|sites|samples|positive|quarters|months|tests|results|exceed|locations|tap|households)", t) \
            or re.search(r"\b(sites|homes|samples|results)\b.*\b(above|over|exceed\w*|greater)\b", t) \
            or re.search(r"\bindividual results\b", t) or re.search(r"out of compliance", t):
        return "count"
    if re.search(r"\b(violations?|exceed\w*|y\s*/\s*n|yes\s*/\s*no|in compliance|compliance\s*\(?y|meets? (?:standard|limit)|met standards?|pass(?:ed)?)\b", t) \
            or re.fullmatch(r"(compliance|in compliance\??|compliant\??)", t):
        return "violation"
    if re.search(r"\b(typical|likely|major|probable|possible)\s+sources?\b|\bsources? of\b|^sources?$", t):
        return "source"
    if re.search(r"\b(contaminants?|parameters?|substances?|analytes?|constituents?|compounds?|characteristics?|chemicals?)\b", t) \
            and not re.search(r"\b(level|mcl|range|result|detected)\b", t.split("(")[0]):
        return "name"
    if re.search(r"\b(90th|90%|percentile)\b", t) or "90%" in t:
        return "level"
    if re.search(r"\b(mclg|mclgs|mrdlg|phg|mldg|goal|ideal)\b", t):
        return "mclg"
    if re.search(r"\b(mcl|mcls|smcl|mrdl|action level|al|limits?|allowed|allowable|tt|treatment technique|standards?|regulatory|required|minimum disinfectant|minimum residual|sdwa)\b", t):
        return "mcl"
    if re.search(r"\b(units?|unit of measure\w*|measure-?\s*ment)\b", t) and not re.search(r"\b(level|detected|result)\b", t):
        return "unit"
    if re.search(r"\b(dates?|years?|sampled|collected|collection|period|when)\b", t) and not re.search(r"\b(level|detected|result|range)\b", t):
        return "date"
    if re.search(r"\brange\b", t):
        if re.search(r"\brange\b.*\bor\b.*\b(level|detected|result)|\b(level|result|detected)\b.*\bor\b.*\brange", t):
            return "range_or_level"
        lv = re.search(r"\b(level|detected|result|average|avg|amount|concentration|value|found)", t)
        rg = re.search(r"\brange\b", t)
        if lv and lv.start() < rg.start() and re.search(r"[(/&]\s*$|\band\s*$", t[:rg.start()]):
            return "level"
        return "range"
    if re.search(r"\b(low|min)\w*\s*[-–/]\s*(high|max)", t):
        return "range"
    if re.fullmatch(r"(low|lowest|min|minimum)( (level|value|result|detected|detection))?", t):
        return "low"
    if re.fullmatch(r"(high|highest|max|maximum)( (level|value|result|detected|detection))?", t):
        return "high"
    if re.search(r"\b(levels?|detected|detections?|results?|average|avg|amount|concentrations?|found|values?|measure\w*|highest|lowest|your water|reading|running|lraa|raa)\b", t):
        return "level"
    if re.search(r"\b(sources?|origin|contamination)\b", t):
        return "source"
    if re.search(r"\b(units?)\b", t):
        return "unit"
    return "unknown"


HEADER_KW = re.compile(
    r"\b(contaminants?|parameters?|substances?|analytes?|constituents?|mclg|mcl|mrdl|mrdlg|smcl|phg|action|"
    r"level|levels|detected|range|violations?|units?|sources?|likely|typical|date|year|sample|sampled|goal|"
    r"ideal|limit|allowed|average|results?|found|percentile|90th|compliance|exceed\w*|y/n|yes/no|highest|lowest|"
    r"min|max|low|high|health|effects|measure\w*|detections?)\b",
    re.I,
)


def heading_hits(text):
    t = text.lower()
    cats = set()
    for cat, rx in (
        ("name", r"\b(contaminants?|parameters?|substances?|analytes?|constituents?)\b"),
        ("mclg", r"\b(mclg|mrdlg|phg|goal|ideal|mldg)\b"),
        ("mcl", r"\b(mcl|mrdl|smcl|action level|limit|allowed|al)\b"),
        ("level", r"\b(level|levels|detected|average|results?|found|percentile|90th|highest|lowest|amount)\b"),
        ("range", r"\b(range|min|max|low|high)\b"),
        ("violation", r"\b(violations?|exceed\w*|y/n|yes/no|compliance)\b"),
        ("unit", r"\b(units?|measure-?|measurement)\b"),
        ("date", r"\b(date|year|sampled|dates)\b"),
        ("source", r"\b(sources?|likely|typical)\b"),
    ):
        if re.search(rx, t):
            cats.add(cat)
    return cats


# ----------------------------------------------------------------------------
# geometry: words, lines, chunks
# ----------------------------------------------------------------------------
class W:
    __slots__ = ("x0", "x1", "top", "bottom", "text", "size", "bold")

    def __init__(self, x0, x1, top, bottom, text, size, bold=False):
        self.x0, self.x1, self.top, self.bottom, self.text, self.size = x0, x1, top, bottom, text, size
        self.bold = bold

    @property
    def h(self):
        return self.bottom - self.top

    def __repr__(self):
        return "W(%r %.0f-%.0f @%.0f)" % (self.text, self.x0, self.x1, self.top)


def page_words(page):
    try:
        p = page.dedupe_chars()
    except Exception:
        p = page
    kw = dict(keep_blank_chars=False, use_text_flow=False, extra_attrs=["size", "fontname"])
    try:
        raw = p.extract_words(x_tolerance_ratio=0.2, y_tolerance=2.5, **kw)
    except TypeError:
        raw = p.extract_words(x_tolerance=1.5, y_tolerance=2.5, **kw)
    words = []
    for w in raw:
        if not w.get("upright", True):
            continue
        t = w["text"]
        if not t.strip():
            continue
        fn = str(w.get("fontname", ""))
        bold = bool(re.search(r"bold|black|heavy|semibold|demi", fn, re.I))
        words.append(W(w["x0"], w["x1"], w["top"], w["bottom"], t, w.get("size", w["bottom"] - w["top"]), bold))
    # drop footnote markers: short tokens in a clearly smaller font than their neighbours
    sizes = [w.size for w in words if len(w.text) > 3]
    if not sizes:
        return words
    import bisect
    words.sort(key=lambda w: w.top)
    tops = [w.top for w in words]
    page_med = statistics.median(sizes)
    keep = []
    for w in words:
        if len(w.text) <= 3 and re.fullmatch(r"[0-9a-zA-Z,*†‡§#]+", w.text):
            win = 3 * max(w.size, 4)
            lo = bisect.bisect_left(tops, w.top - win)
            hi = bisect.bisect_right(tops, w.top + win)
            cx = (w.x0 + w.x1) / 2
            near = [o.size for o in words[lo:hi] if o is not w and len(o.text) > 1
                    and abs((o.x0 + o.x1) / 2 - cx) < 150]
            ref = statistics.median(near) if near else page_med
            if w.size < 0.8 * ref:
                run = [o for o in words[lo:hi] if o is not w and abs(o.top - w.top) < 1.5 and o.size < 0.8 * ref
                       and (abs(o.x0 - w.x1) < 3.5 or abs(w.x0 - o.x1) < 3.5)]
                if not run:
                    continue
        keep.append(w)
    return keep


class Line:
    def __init__(self, words):
        self.words = sorted(words, key=lambda w: w.x0)
        self.top = min(w.top for w in words)
        self.bottom = max(w.bottom for w in words)
        self.fs = statistics.median([w.bottom - w.top for w in words])

    @property
    def text(self):
        return " ".join(w.text for w in self.words)

    @property
    def x0(self):
        return min(w.x0 for w in self.words)

    @property
    def x1(self):
        return max(w.x1 for w in self.words)

    def __repr__(self):
        return "Line(%.0f %r)" % (self.top, self.text[:60])


def make_lines(words):
    words = sorted(words, key=lambda w: (w.top + w.bottom) / 2)
    lines = []
    for w in words:
        c = (w.top + w.bottom) / 2
        best = None
        for ln in lines[-6:]:
            lc_top, lc_bot = ln["top"], ln["bottom"]
            ov = min(w.bottom, lc_bot) - max(w.top, lc_top)
            if ov > 0.5 * min(w.bottom - w.top, lc_bot - lc_top) and abs(c - ln["c"]) < 0.6 * max(w.bottom - w.top, 1):
                # avoid two words overlapping horizontally on the same line
                if any(o.x0 < w.x1 - 0.5 and w.x0 < o.x1 - 0.5 for o in ln["words"]):
                    continue
                best = ln
                break
        if best is None:
            lines.append({"top": w.top, "bottom": w.bottom, "c": c, "words": [w]})
        else:
            best["words"].append(w)
            n = len(best["words"])
            best["c"] = (best["c"] * (n - 1) + c) / n
    out = [Line(l["words"]) for l in lines]
    out.sort(key=lambda l: l.top)
    return out


def chunks_of(words, gap_ratio=0.5):
    """Split words (one line) into chunks separated by gaps larger than gap_ratio * font height."""
    ws = sorted(words, key=lambda w: w.x0)
    out = []
    for w in ws:
        if out:
            last = out[-1]
            fs = max(w.h, last[-1].h, 1)
            if w.x0 - last[-1].x1 <= gap_ratio * fs:
                last.append(w)
                continue
        out.append([w])
    return out


class Chunk:
    def __init__(self, words, line_idx):
        self.words = words
        self.x0 = min(w.x0 for w in words)
        self.x1 = max(w.x1 for w in words)
        self.top = min(w.top for w in words)
        self.bottom = max(w.bottom for w in words)
        self.text = " ".join(w.text for w in words)
        self.li = line_idx

    def __repr__(self):
        return "Chunk(%r %.0f-%.0f @%.0f)" % (self.text, self.x0, self.x1, self.top)


def xov(a0, a1, b0, b1):
    return min(a1, b1) - max(a0, b0)


NUMERIC_TOKEN = re.compile(r"^[<>≤≥]?[-+]?(\d+(\.\d+)?|\.\d+)(" + DASH_RE + r"[<>]?(\d+(\.\d+)?|\.\d+))?[a-zA-Z/%]*$")


def is_data_line(line):
    if bold_share(line) >= 0.8 and heading_hits(line.text):
        return False
    ws = line.words
    for k, w in enumerate(ws):
        t = w.text.strip("()*,;")
        if k + 1 < len(ws) and re.fullmatch(r"\d{1,3}", t) and re.match(r"(th|st|percentile|%)", ws[k + 1].text, re.I):
            continue
        if re.fullmatch(r"(19|20)\d\d", t):
            continue
        if re.fullmatch(r"\d{1,3}(st|nd|rd|th|%)", t):
            continue
        if NUMERIC_TOKEN.match(t) or t.upper() in ("ND", "N/A"):
            return True
    soft = sum(1 for w in ws if w.text.strip("()*,;.") in ("Absent", "Present", "ABSENT", "PRESENT", "NA", "No", "Yes", "NO", "YES", "None", "TT"))
    return soft >= 2


# ----------------------------------------------------------------------------
# table detection
# ----------------------------------------------------------------------------
class Column:
    def __init__(self, x0, x1, heading="", role=None):
        self.x0, self.x1 = x0, x1
        self.heading = heading
        self.parent = ""
        self.role = role
        self.idx = 0

    def __repr__(self):
        return "Col(%s %.0f-%.0f %r)" % (self.role, self.x0, self.x1, self.heading)


def build_header_columns(hlines):
    """From header lines build leaf columns. Returns (columns, title_text, cut_index)."""
    chunks = []
    for li, ln in enumerate(hlines):
        for cw in chunks_of(ln.words, 0.6):
            if len(cw) >= 8:
                continue
            chunks.append(Chunk(cw, li))
    if not chunks:
        return [], "", None
    # spanning chunks: overlap >=2 mutually disjoint chunks of other lines
    spanning = set()
    for i, c in enumerate(chunks):
        ovs = [d for j, d in enumerate(chunks) if j != i and d.li != c.li and xov(c.x0, c.x1, d.x0, d.x1) > 1]
        ovs.sort(key=lambda d: d.x0)
        disjoint = []
        for d in ovs:
            if not disjoint or d.x0 > disjoint[-1].x1 - 1:
                disjoint.append(d)
        if len(disjoint) >= 2:
            # it must be wider than each of them for a spanning header
            if (c.x1 - c.x0) > 1.2 * max(d.x1 - d.x0 for d in disjoint) or len(disjoint) >= 3 \
                    or all(d.top > c.bottom - 1 for d in disjoint):
                spanning.add(i)
    leaf = [c for i, c in enumerate(chunks) if i not in spanning]
    # union-find leaf chunks by horizontal overlap
    parent = list(range(len(leaf)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for i in range(len(leaf)):
        for j in range(i + 1, len(leaf)):
            a, b = leaf[i], leaf[j]
            if xov(a.x0, a.x1, b.x0, b.x1) > 0.5:
                parent[find(i)] = find(j)
    groups = {}
    for i, c in enumerate(leaf):
        groups.setdefault(find(i), []).append(c)
    cols = []
    for g in groups.values():
        g.sort(key=lambda c: (c.top, c.x0))
        col = Column(min(c.x0 for c in g), max(c.x1 for c in g), " ".join(c.text for c in g))
        col.top = min(c.top for c in g)
        col.bottom = max(c.bottom for c in g)
        cols.append(col)
    cols.sort(key=lambda c: c.x0)
    title = []
    for i in sorted(spanning, key=lambda i: (chunks[i].top, chunks[i].x0)):
        c = chunks[i]
        covered = [col for col in cols if xov(c.x0, c.x1, col.x0, col.x1) > 0]
        if covered and all(c.top > col.bottom - 1 for col in covered):
            # below the headings it spans -> a body group label, not a heading
            continue
        if len(covered) >= max(3, 0.6 * len(cols)):
            title.append(c.text)
            continue
        for col in covered:
            col.parent = (col.parent + " " + c.text).strip()
    return cols, " ".join(title), None


def is_title_like(ln, ref_width, ref_x0=None):
    chs = chunks_of(ln.words, 0.6)
    if len(chs) != 1:
        return False
    w = ln.x1 - ln.x0
    if w > 0.5 * ref_width:
        return True
    if ref_x0 is not None and ln.x0 > ref_x0 + 0.15 * ref_width:
        return False
    return len(ln.words) >= 4 or w > 0.3 * ref_width


def bold_share(ln):
    return sum(1 for w in ln.words if w.bold) / max(1, len(ln.words))


def find_header_blocks(lines):
    blocks = []
    n = len(lines)
    used = set()
    for i, ln in enumerate(lines):
        if i in used:
            continue
        hits = heading_hits(ln.text)
        if len(hits) < 2 or is_data_line(ln):
            continue
        # avoid prose lines
        if len(ln.words) > 14 and not any(ln.words[k + 1].x0 - ln.words[k].x1 > 1.2 * ln.fs for k in range(len(ln.words) - 1)):
            continue
        if len(chunks_of(ln.words, 0.6)) < 2 and len(ln.words) > 3:
            continue
        ref_w = ln.x1 - ln.x0
        seed_bold = bold_share(ln) >= 0.6
        lo, hi = i, i
        # extend upward
        while lo - 1 >= 0:
            prev = lines[lo - 1]
            gap = lines[lo].top - prev.bottom
            if gap > 0.9 * max(prev.fs, lines[lo].fs) or is_data_line(prev) or (lo - 1) in used:
                break
            if len(prev.words) > 12 and (prev.x1 - prev.x0) > 300:
                break
            if is_title_like(prev, ref_w, ln.x0):
                break
            if seed_bold and bold_share(prev) == 0 and gap > 0.15 * prev.fs:
                break
            lo -= 1
        while hi + 1 < n:
            nxt = lines[hi + 1]
            gap = nxt.top - lines[hi].bottom
            if gap > 0.9 * max(nxt.fs, lines[hi].fs) or is_data_line(nxt):
                break
            if len(nxt.words) > 12 and (nxt.x1 - nxt.x0) > 300:
                break
            if seed_bold and bold_share(nxt) == 0 and gap > 0.15 * nxt.fs:
                break
            hi += 1
        allhits = set()
        for k in range(lo, hi + 1):
            allhits |= heading_hits(lines[k].text)
        if len(allhits) < 3:
            continue
        if not (allhits & {"mcl", "mclg", "level", "range"}):
            continue
        for k in range(lo, hi + 1):
            used.add(k)
        # title: nearest title-like line just above the block
        title = ""
        if lo - 1 >= 0 and is_title_like(lines[lo - 1], ref_w, ln.x0) and lines[lo].top - lines[lo - 1].bottom < 3 * ln.fs:
            title = lines[lo - 1].text
        blocks.append((lo, hi, title))
    return blocks


ROLE_KEEP = {"name", "level", "range", "range_or_level", "low", "high", "unit", "mcl", "mclg", "date", "violation"}


def assign_roles(cols):
    for c in cols:
        h = c.heading
        role = classify_heading(h)
        if role in ("unknown", "blank") and c.parent:
            prole = classify_heading(c.parent)
            if prole in ("range",):
                r2 = classify_heading(h)
                if re.search(r"\b(low|min)", h, re.I):
                    role = "low"
                elif re.search(r"\b(high|max)", h, re.I):
                    role = "high"
                else:
                    role = prole
            elif role == "unknown":
                role = prole if prole != "unknown" else role
        if role in ("level", "unknown") and c.parent and classify_heading(c.parent) == "range":
            if re.search(r"\b(low|min)", h, re.I):
                role = "low"
            elif re.search(r"\b(high|max)", h, re.I):
                role = "high"
        c.role = role
    # first column with a name-ish role; if none, leftmost column is the name column
    if not any(c.role == "name" for c in cols) and cols:
        cols[0].role = "name"
    # only one name column: the leftmost "name"; others become unknown
    seen = False
    for c in cols:
        if c.role == "name":
            if seen:
                c.role = "unknown"
            seen = True
    roles = [c.role for c in cols]
    if not any(r in roles for r in ("level", "range", "range_or_level", "low", "high")):
        unk = [c for c in cols if c.role == "unknown" and c.heading]
        if unk:
            for c in unk:
                c.role = "level"
                c.loc_group = len(unk) >= 2
        else:
            for c in cols:
                if c.role == "count":
                    c.role = "level"
                    break
    # low/high pairs need each other
    roles = [c.role for c in cols]
    if "low" in roles and "high" not in roles:
        for c in cols:
            if c.role == "low":
                c.role = "level"
    if "high" in roles and "low" not in roles:
        for c in cols:
            if c.role == "high":
                c.role = "level"


def h_edges(page_edges, y0, y1, x0, x1):
    out = []
    for e in page_edges:
        if e["orientation"] != "h":
            continue
        if not (y0 - 2 <= e["top"] <= y1 + 2):
            continue
        ov = xov(e["x0"], e["x1"], x0, x1)
        if ov <= 0:
            continue
        out.append((e["top"], max(e["x0"], x0), min(e["x1"], x1)))
    return out


def collect_body(lines, start, end_limit, cols, fs0, prev_bottom):
    tx0 = min(c.x0 for c in cols)
    tx1 = max(c.x1 for c in cols)
    body = []
    last_k = start - 1
    for k in range(start, end_limit):
        ln = lines[k]
        # drop prose-like chunks lying outside the table's horizontal extent (multi-column layouts)
        keep = []
        for cw in chunks_of(ln.words, 0.5):
            if len(cw) >= 4 and (cw[-1].x1 < tx0 - 2 * fs0 or cw[0].x0 > tx1 + 2 * fs0):
                continue
            keep.extend(cw)
        if not keep:
            continue
        if len(keep) != len(ln.words):
            ln = Line(keep)
        if ln.top - prev_bottom > 3.2 * max(fs0, ln.fs):
            break
        chs = chunks_of(ln.words, 0.5)
        prose = False
        for cw in chs:
            cx0, cx1 = cw[0].x0, cw[-1].x1
            covered = [c for c in cols if xov(cx0, cx1, c.x0, c.x1) > 0.3 * min(c.x1 - c.x0, cx1 - cx0)]
            if len(covered) >= 3 and len(cw) >= 6:
                prose = True
        width = ln.x1 - ln.x0
        if prose or (len(ln.words) >= 10 and width > 0.6 * (tx1 - tx0) and len(chs) <= 2):
            break
        if ln.x1 < tx0 - 5 or ln.x0 > tx1 + 40:
            continue
        body.append(ln)
        last_k = k
        prev_bottom = max(prev_bottom, ln.bottom)
    return body, last_k


def parse_tables_on_page(page, lines, edges, carry=None):
    blocks = find_header_blocks(lines)
    results = []
    new_carry = None
    ph = float(page.height)
    # continuation of a table from the previous page (no repeated header)
    if carry is not None:
        cols, title, fs0 = carry
        first = blocks[0][0] if blocks else len(lines)
        k0 = 0
        while k0 < first and lines[k0].bottom < 0.1 * ph and not is_data_line(lines[k0]):
            k0 += 1
        if k0 < first and is_data_line(lines[k0]):
            body, last_k = collect_body(lines, k0, first, cols, fs0, lines[k0].top)
            aligned = 0
            for ln in body[:3]:
                chs = chunks_of(ln.words, 0.5)
                ok = sum(1 for cw in chs if any(xov(cw[0].x0, cw[-1].x1, c.x0, c.x1) > 0 for c in cols))
                if chs and ok >= 0.8 * len(chs) and len(chs) >= 3:
                    aligned += 1
            if body and aligned >= 1:
                results.extend(parse_body(cols, title, body, edges, page, None))
                if not blocks and body[-1].bottom > 0.75 * ph:
                    new_carry = carry
    for bi, (lo, hi, title0) in enumerate(blocks):
        hlines = lines[lo:hi + 1]
        cols, title, _ = build_header_columns(hlines)
        title = (title0 + " " + title).strip()
        if len(cols) < 3:
            continue
        assign_roles(cols)
        if not any(c.role in ("level", "range", "range_or_level", "low", "high") for c in cols):
            continue
        end_limit = blocks[bi + 1][0] if bi + 1 < len(blocks) else len(lines)
        fs0 = statistics.median([l.fs for l in hlines])
        body, last_k = collect_body(lines, hi + 1, end_limit, cols, fs0, lines[hi].bottom)
        if not body:
            continue
        recs = parse_body(cols, title, body, edges, page, None)
        results.extend(recs)
        if bi == len(blocks) - 1:
            rest = [l for l in lines[last_k + 1:] if l.top < 0.92 * ph]
            if body[-1].bottom > 0.7 * ph and not rest:
                new_carry = (cols, title, fs0)
    return results, new_carry


def assign_chunks(cols, body):
    """Assign body chunks to columns; create headerless columns for orphan chunks."""
    placed = []  # (line_index, col, chunk)
    orphans = []
    for li, ln in enumerate(body):
        for cw in chunks_of(ln.words, 0.5):
            ch = Chunk(cw, li)
            best, bov = None, 0
            ovl = []
            for c in cols:
                ov = xov(ch.x0, ch.x1, c.x0, c.x1)
                if ov > 0:
                    ovl.append((ov, c))
                if ov > bov:
                    best, bov = c, ov
            if best is None:
                orphans.append(ch)
                continue
            if len(ovl) >= 2 and len(cw) > 1:
                # chunk straddles columns: split word-wise
                for w in cw:
                    wb, wov = None, -1e9
                    for c in cols:
                        ov = xov(w.x0, w.x1, c.x0, c.x1)
                        if ov <= 0:
                            ov = -min(abs(w.x0 - c.x1), abs(c.x0 - w.x1))
                        if ov > wov:
                            wb, wov = c, ov
                    placed.append((li, wb, Chunk([w], li)))
                continue
            placed.append((li, best, ch))
    # orphan clustering into headerless columns
    extra = []
    for ch in sorted(orphans, key=lambda c: c.x0):
        for e in extra:
            if xov(ch.x0, ch.x1, e.x0 - 3, e.x1 + 3) > 0:
                e.x0, e.x1 = min(e.x0, ch.x0), max(e.x1, ch.x1)
                e.items.append(ch)
                break
        else:
            col = Column(ch.x0, ch.x1, "", "unknown")
            col.items = [ch]
            extra.append(col)
    for e in extra:
        texts = [strip_footnotes(c.text) for c in e.items]
        yn = sum(1 for t in texts if re.fullmatch(r"(yes|no|y|n)", t, re.I))
        if texts and yn >= max(1, 0.6 * len(texts)):
            e.role = "violation"
            for ch in e.items:
                placed.append((ch.li, e, ch))
            cols.append(e)
            continue
        # attach to nearest column if close
        near, nd = None, 1e9
        for c in cols:
            d = min(abs(e.x0 - c.x1), abs(c.x0 - e.x1))
            if d < nd:
                near, nd = c, d
        fs = statistics.median([w.h for ch in e.items for w in ch.words])
        if near is not None and nd < 2.5 * fs:
            for ch in e.items:
                placed.append((ch.li, near, ch))
    cols.sort(key=lambda c: c.x0)
    return placed


def is_fresh_value(t, role):
    t = strip_footnotes(t)
    if role == "violation":
        return bool(re.match(r"(yes|no|y|n)\b", t, re.I))
    if role == "unit":
        return bool(STRONG_UNIT_RE.match(t))
    return bool(re.match(r"([<>≤≥]?\s*\d|nd\b|n/a\b|na\b|absent|present|tt\b|none\b)", t, re.I))


def parse_body(cols, title, body, edges, page, sysid_for):
    placed = assign_chunks(cols, body)
    compact_roles = {"name", "level", "range", "range_or_level", "low", "high", "unit", "mcl", "mclg", "date", "violation", "count", "other"}
    # line-level presence in compact columns
    line_cols = {}
    for li, c, ch in placed:
        line_cols.setdefault(li, []).append((c, ch))
    tx0 = min(c.x0 for c in cols)
    tx1 = max(c.x1 for c in cols)
    # ruling lines in the body
    ys = h_edges(edges, body[0].top - 2, body[-1].bottom + 2, tx0, tx1)
    seps = sorted(set(round(y, 0) for y, a, b in ys if (b - a) > 0.3 * (tx1 - tx0)))
    # group lines into rows
    compact_lines = [li for li in sorted(line_cols) if any(c.role in compact_roles for c, ch in line_cols[li])]
    rows = []
    cur = None
    prev_li = None
    fs = statistics.median([l.fs for l in body])
    cext = {}
    for li in compact_lines:
        chs = [ch for c, ch in line_cols[li] if c.role in compact_roles]
        cext[li] = (min(ch.top for ch in chs), max(ch.bottom for ch in chs))
    for li in compact_lines:
        ln = body[li]
        newrow = False
        if cur is None:
            newrow = True
        else:
            ptop, pbot = cext[prev_li]
            ltop, lbot = cext[li]
            gap = ltop - pbot
            if seps and any(pbot - 1 < s_ < ltop + 1 for s_ in seps):
                newrow = True
            elif gap > 0.55 * fs:
                newrow = True
            if not newrow:
                fresh = set()
                for c, ch in line_cols[li]:
                    if c.role in ("level", "violation", "date", "unit", "mclg", "mcl", "range") and c in cur["filled"]:
                        prevtxt = cur["text"].get(id(c), "")
                        if is_fresh_value(ch.text, c.role) and not re.search(r"([-" + DASHES + r"(,/=]|\b(to|or|of|and|than|less|as))\s*$", prevtxt, re.I):
                            fresh.add(c.role)
                strong = fresh & {"level", "violation", "unit", "date"}
                namehere = any(c.role == "name" and re.match(r"[A-Z0-9]", ch.text) for c, ch in line_cols[li])
                if len(strong) >= 2 or (namehere and "level" in fresh):
                    newrow = True
        if newrow:
            cur = {"lines": [li], "filled": set(), "text": {}}
            rows.append(cur)
        else:
            cur["lines"].append(li)
        for c, ch in line_cols[li]:
            if c.role in compact_roles:
                cur["filled"].add(c)
                cur["text"][id(c)] = (cur["text"].get(id(c), "") + " " + ch.text).strip()
        prev_li = li
    # build cell texts
    out = []
    prev_rec = None
    for r in rows:
        lis = set(r["lines"])
        cells = {}
        for li in sorted(lis):
            for c, ch in sorted(line_cols[li], key=lambda t: t[1].x0):
                cells.setdefault(id(c), []).append(ch.text)
        cell = {}
        for c in cols:
            if id(c) in cells:
                cell[id(c)] = clean_ws(" ".join(cells[id(c)]))
        recs = []
        nonname = " ".join(v for c in cols for k, v in cell.items() if k == id(c) and c.role != "name")
        if not re.search(r"\d", nonname) and not re.search(
                r"\b(nd|n/d|absent|present|yes|no|y|n|tt|none|n/a|na|detected|positive|negative)\b", nonname, re.I):
            continue
        lvl = [c for c in cols if c.role == "level"]
        multi = split_located_values(cell.get(id(lvl[0]), "")) if lvl else None
        groups = None
        if len(lvl) >= 2:
            parents = [c.parent for c in lvl]
            if all(parents) and len(set(parents)) == len(parents):
                groups = [(c.parent, [d for d in cols if d.parent == c.parent]) for c in lvl]
            elif all(getattr(c, "loc_group", False) for c in lvl):
                groups = [(c.heading, [c]) for c in lvl]
        if groups:
            grouped = set(id(d) for _, g in groups for d in g)
            for loc, gcols in groups:
                gids = set(id(d) for d in gcols)
                cell2 = {k: v for k, v in cell.items() if k not in grouped or k in gids}
                if not any(cell2.get(id(d)) for d in gcols):
                    continue
                cols2 = [d for d in cols if id(d) not in grouped or id(d) in gids]
                for rec in make_records(cols2, cell2, title, prev_rec):
                    rec["location"] = loc
                    recs.append(rec)
        elif multi:
            for loc, val in multi:
                cell2 = dict(cell)
                cell2[id(lvl[0])] = val
                for rec in make_records(cols, cell2, title, prev_rec):
                    rec["location"] = loc
                    recs.append(rec)
        else:
            recs = make_records(cols, cell, title, prev_rec)
        for rec in recs:
            out.append(rec)
        if recs:
            prev_rec = recs[-1]
    return out


DATE_IN_NAME = re.compile(
    r"\(\s*(?:(?:tested|sampled|sample|collected|test(?:ed)?\s+in|sampled\s+in|data|date|year)\s*(?:in|on|:)?\s*)?"
    r"((?:\d{1,2}/\d{1,2}/\d{2,4})|(?:\d{1,2}/\d{2,4})|(?:(?:19|20)\d\d(?:\s*[-–]\s*(?:19|20)?\d\d)?))\s*\)",
    re.I,
)


def clean_name(raw):
    """Return (name, date, unit)."""
    t = strip_footnotes(raw)
    date = None
    unit = None
    m = DATE_IN_NAME.search(t)
    if m:
        date = m.group(1)
        t = (t[: m.start()] + " " + t[m.end():]).strip()
    m = re.search(r"\(\s*(?:in\s+)?(" + STRONG_UNIT_RE.pattern + r")\s*\)", t, re.I)
    if m:
        unit = clean_ws(m.group(1))
        t = (t[: m.start()] + " " + t[m.end():]).strip()
    # testing notes
    m = re.search(r"\(?\s*(?:next\s+(?:testing|sampl\w*|test)|tested|sampled)\b[^)]*\)?\s*$", t, re.I)
    if m and m.start() > 0:
        note = m.group(0)
        dm = re.search(r"(?:19|20)\d\d", note)
        if dm and date is None and not re.search(r"next", note, re.I):
            date = dm.group(0)
        t = t[: m.start()].strip()
    t = re.sub(r"^\d{1,2}[.)]\s+", "", t)
    t = re.sub(r"\s+\d$", "", t) if re.search(r"[a-z]\s+\d$", t) else t
    t = clean_ws(t).strip(" ,;:")
    return t, date, unit


LOC_VAL_RE = re.compile(r"([A-Za-z][A-Za-z .'&#/-]*?)\s*[:=]?\s*([<>]?\s*\d[\d.,]*|ND)\s*(\(\s*[^()]*\d[^()]*\))?")


def split_located_values(t):
    """'Gulf Street 41 (27-54) Maple Ridge Rd 18 (14-29)' -> [(loc, '41 (27-54)'), ...]"""
    t = strip_footnotes(t)
    if not t or not re.match(r"[A-Za-z]", t):
        return None
    out = []
    pos = 0
    for m in LOC_VAL_RE.finditer(t):
        if t[pos:m.start()].strip(" ;,/"):
            return None
        label = clean_ws(m.group(1)).strip(" :;,-")
        if re.fullmatch(r"(?i)(range|average|avg|max|min|maximum|minimum|highest|lowest|mean|level|result|lraa|raa|high|low)", label):
            return None
        out.append((label, clean_ws(m.group(2) + " " + (m.group(3) or ""))))
        pos = m.end()
    if t[pos:].strip(" ;,/"):
        return None
    return out if len(out) >= 2 else None


def norm_unit(u):
    if u is None:
        return None
    u = u.replace("\uf06d", "µ").replace("μ", "µ")
    u = re.sub(r"µ\s+", "µ", u)
    return clean_ws(u) or None


def make_records(cols, cell, title, prev_rec):
    get = lambda role: [c for c in cols if c.role == role]
    namecol = get("name")
    name_raw = cell.get(id(namecol[0]), "") if namecol else ""
    # value columns present?
    value_roles = ("level", "range", "range_or_level", "low", "high", "mcl", "mclg", "violation", "unit", "date", "count", "other", "unknown")
    has_val = any(cell.get(id(c)) for c in cols if c.role in ("level", "range", "range_or_level", "low", "high", "violation", "count"))
    if not has_val:
        return []
    name, ndate, nunit = clean_name(name_raw)
    if not name:
        if prev_rec is None:
            return []
        name = prev_rec["contaminant"]
    if re.match(r"^range\b", name, re.I) and prev_rec is not None:
        # range sub-row fills the parent's range
        for c in cols:
            v = cell.get(id(c))
            if v and c.role in ("level", "range", "range_or_level"):
                r = parse_range(v)
                if r:
                    prev_rec["range_low"], prev_rec["range_high"] = r[0], r[1]
                    break
        return []
    if not re.search(r"[A-Za-z]", name):
        return []
    rec = {
        "system": None, "contaminant": name, "sample_date": None, "level": None, "range_low": None,
        "range_high": None, "unit": None, "mcl": None, "mclg": None, "violation": None,
    }
    other = {}
    units = {"col": None, "name": nunit, "level": None, "mcl": None, "heading": None, "range": None, "title": None}
    # level columns (leftmost value column)
    levelcols = [c for c in cols if c.role in ("level", "range_or_level", "unknown") and c is not (namecol[0] if namecol else None)]
    # prefer real level columns; unknown columns only if they hold values and lie left of others
    lvl_done = False
    for c in sorted(cols, key=lambda c: c.x0):
        v = cell.get(id(c))
        role = c.role
        if role == "name":
            continue
        if role in ("level", "range_or_level") and not lvl_done:
            lvl_done = True
            if v is None:
                units["heading"] = find_unit(c.heading)
                continue
            if role == "range_or_level":
                r = parse_range(v)
                if r:
                    rec["range_low"], rec["range_high"] = r[0], r[1]
                    units["range"] = r[2]
                    continue
            p = parse_level_cell(v)
            rec["level"] = p["level"]
            if p["low"] is not None or p["high"] is not None:
                rec["range_low"], rec["range_high"] = p["low"], p["high"]
            units["level"] = p["unit"]
            units["heading"] = find_unit(c.heading)
            continue
        if v is None:
            continue
        if role in ("level", "range_or_level", "unknown"):
            other[c.heading or "value"] = v
        elif role == "range":
            r = parse_range(v)
            if r:
                rec["range_low"], rec["range_high"] = r[0], r[1]
                units["range"] = r[2]
            else:
                sv, su = split_value_unit(v)
                if isinstance(sv, (int, float)):
                    rec["range_low"] = rec["range_high"] = sv
                    units["range"] = su
                elif sv is not None and re.match(r"^[<>]?\s*\d", str(sv)) and len(str(sv)) < 12:
                    rec["range_low"] = rec["range_high"] = sv
                elif sv is not None and str(sv).lower() in ("nd", "absent"):
                    rec["range_low"] = rec["range_high"] = sv
                else:
                    other[c.heading] = v
        elif role == "low":
            rec["range_low"], u = split_value_unit(v)
        elif role == "high":
            rec["range_high"], u = split_value_unit(v)
            units["range"] = units["range"] or u
        elif role == "unit":
            if not is_null_text(v):
                units["col"] = strip_footnotes(v)
            else:
                units["col"] = ""
        elif role == "mcl":
            sv, su = split_value_unit(v)
            rec["mcl"] = sv
            units["mcl"] = su if su else (find_unit(v) if isinstance(sv, str) else None)
        elif role == "mclg":
            sv, su = split_value_unit(v)
            rec["mclg"] = sv
        elif role == "date":
            if not is_null_text(v):
                rec["sample_date"] = strip_footnotes(v)
        elif role == "violation":
            inv = bool(re.search(r"compliance|meets?|met\b|pass", c.heading, re.I)) and not re.search(r"violation|exceed", c.heading, re.I)
            rec["violation"] = yes_no(v, invert=inv)
        elif role in ("count", "other"):
            sv, su = split_value_unit(v)
            other[c.heading or "value"] = sv
    if rec["sample_date"] is None and ndate:
        rec["sample_date"] = ndate
    # unit precedence
    if units["col"] is not None:
        rec["unit"] = units["col"] or None
    else:
        for k in ("name", "level", "mcl", "heading", "range"):
            if units[k]:
                rec["unit"] = units[k]
                break
        if rec["unit"] is None and title:
            rec["unit"] = find_unit(title)
    rec["unit"] = norm_unit(rec["unit"])
    if not name_raw.strip() and prev_rec is not None:
        # continuation sub-row of a merged cell: inherit merged limit/goal cells
        for f in ("mclg", "mcl", "unit"):
            if rec[f] is None and prev_rec.get(f) is not None and not any(c.role == f and cell.get(id(c)) for c in cols):
                rec[f] = prev_rec[f]
    if other:
        rec["other_values"] = other
    return [rec]


# ----------------------------------------------------------------------------
# PWSID
# ----------------------------------------------------------------------------
PWSID_RE = re.compile(
    r"(?:PWS\s*ID|PWSID|P\.W\.S\.\s*ID|Public\s+Water\s+(?:Supply|System)\s+(?:ID|Identification)(?:\s+(?:Number|No\.?|#))?|"
    r"Water\s+System\s+(?:ID|No\.?|Number)|System\s+(?:ID|I\.D\.)(?:\s+(?:Number|No\.?))?|Federal\s+ID|"
    r"PWS\s*(?:#|No\.?|Number))"
    r"\s*(?:\(\s*PWSID\s*\))?\s*(?:#|No\.?|Number|Num\.?)?\s*[:#.\-]?\s*#?\s*([A-Z]{2}\s?-?\d{5,9}|\d{5,9}|[A-Z]{2}\d{2}-\d{5}|\d{2}-\d{2}-\d{3})\b",
    re.I,
)


def find_pwsid(texts):
    """texts: list of page texts. Returns list of (page_index, char_pos, id)."""
    found = []
    for pi, t in enumerate(texts):
        for m in PWSID_RE.finditer(t):
            found.append((pi, m.start(), re.sub(r"\s", "", m.group(1))))
    return found


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------
def extract(pdf_path):
    records = []
    with pdfplumber.open(pdf_path) as pdf:
        page_texts = []
        page_data = []
        for page in pdf.pages:
            if time.time() - T0 > 80:
                break
            try:
                words = page_words(page)
            except Exception:
                words = []
            lines = make_lines(words) if words else []
            page_texts.append("\n".join(l.text for l in lines))
            try:
                edges = page.edges
            except Exception:
                edges = []
            page_data.append((page, lines, edges))
        ids = []
        ptexts = []
        try:
            import subprocess
            res = subprocess.run(["pdftotext", "-layout", pdf_path, "-"], capture_output=True, timeout=60)
            ptexts = res.stdout.decode("utf-8", "replace").split("\f")
            ids = find_pwsid(ptexts)
        except Exception:
            ids = []
        if not ids:
            ids = find_pwsid(page_texts)
        if not ids:
            states = ("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY "
                      "NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY PR DC GU").split()
            for t in ptexts + page_texts:
                m = re.search(r"\b(" + "|".join(states) + r")(\d{7})\b", t)
                if m:
                    ids = [(0, 0, m.group(0))]
                    break
        primary = ids[0][2] if ids else None
        # multi-system files: a different labelled ID near the top of a page starts another system's part
        page_sys = []
        cur_id = primary
        src = ptexts if ptexts and len(ptexts) >= len(page_data) else page_texts
        for pi in range(len(page_data)):
            t = src[pi] if pi < len(src) else ""
            lim = max(200, int(0.25 * len(t)))
            tops = [m for (p_, pos, m) in ids if p_ == pi and pos <= lim]
            if tops and tops[0] != cur_id:
                cur_id = tops[0]
            page_sys.append(cur_id)
        carry = None
        for pi, (page, lines, edges) in enumerate(page_data):
            if time.time() - T0 > 100:
                break
            if not lines:
                carry = None
                continue
            try:
                recs, carry = parse_tables_on_page(page, lines, edges, carry)
            except Exception as e:  # never crash the whole report
                dbg("page", pi, "error", repr(e))
                import traceback
                if DEBUG:
                    traceback.print_exc()
                recs = []
                carry = None
            sysid = page_sys[pi] if pi < len(page_sys) else primary
            for r in recs:
                r["system"] = sysid
            records.extend(recs)
    return records


def main(pdf_path, out_path):
    try:
        records = extract(pdf_path)
    except Exception as e:
        dbg("fatal", repr(e))
        records = []
    with open(out_path, "w") as fh:
        json.dump({"records": records}, fh, indent=1, ensure_ascii=False)


if __name__ == "__main__":
    if len(sys.argv) > 3 and sys.argv[3] == "--debug":
        DEBUG = True
    main(sys.argv[1], sys.argv[2])
