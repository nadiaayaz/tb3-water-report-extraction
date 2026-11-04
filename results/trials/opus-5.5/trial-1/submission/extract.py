#!/usr/bin/env python3
"""Extract detected-contaminant result tables from a Consumer Confidence Report PDF.

Usage: python3 extract.py <report.pdf> <output.json>
"""
import json
import os
import re
import sys
import time

T0 = time.time()
TIME_BUDGET = 95.0
TMP_DIRS = []

import pdfplumber

# ----------------------------------------------------------------------------
# text helpers

PUA_MAP = {0xF06D: "µ", 0xF0A3: "≤", 0xF0B3: "≥", 0xF0B1: "±",
           0xF0B0: "°", 0xF03C: "<", 0xF03E: ">", 0xF02D: "-", 0xF0B7: "•"}
SUPERSCRIPTS = "¹²³⁰⁴⁵⁶⁷⁸⁹"


def clean_chars(s):
    out = []
    for ch in s:
        o = ord(ch)
        if o in PUA_MAP:
            out.append(PUA_MAP[o])
        elif 0xF020 <= o <= 0xF0FF:
            out.append(chr(o - 0xF000))
        elif ch == "μ":
            out.append("µ")
        elif ch in SUPERSCRIPTS:
            continue
        elif ch in "    ":
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def norm_space(s):
    return re.sub(r"\s+", " ", s or "").strip()


def join_lines(lines):
    """Join text lines of one cell; a line ending in an attached hyphen joins without space."""
    out = ""
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if not out:
            out = ln
        elif re.search(r"[A-Za-z0-9/][-‐]$", out):
            out = out + ln
        else:
            out = out + " " + ln
    return out


# ----------------------------------------------------------------------------
# words

FOOT_CHAR = re.compile(r"^[\d,*†‡§a-f]$")


def superscript_keys(chars):
    """Identify footnote-marker characters: small and/or raised glyphs directly after text."""
    sup = set()
    prev = None
    prev_sup = False
    for c in chars:
        t = c.get("text", "")
        if not t.strip():
            prev = None
            continue
        is_sup = False
        if prev is not None and FOOT_CHAR.match(t):
            p = prev
            ph = max(p["bottom"] - p["top"], 0.1)
            gap = c["x0"] - p["x1"]
            vert = c["bottom"] > p["top"] and c["top"] < p["bottom"]
            if prev_sup and -0.5 <= gap <= 1.5 and abs(c["bottom"] - p["bottom"]) < 0.6:
                is_sup = True
            elif not prev_sup and -0.5 <= gap <= 2.0 and vert and FOOT_CHAR.match(t):
                rise = p["bottom"] - c["bottom"]
                smaller = c.get("size", ph) <= 0.85 * p.get("size", ph)
                if (smaller and rise >= 0.8) or rise >= max(1.5, 0.22 * ph):
                    if re.match(r"[A-Za-z0-9)\]]", p.get("text", "")) or prev_sup:
                        is_sup = True
        if is_sup:
            sup.add((round(c["x0"], 2), round(c["top"], 2), t))
        prev = c
        prev_sup = is_sup
    return sup


def get_words(page):
    try:
        sup = superscript_keys(page.chars)
    except Exception:
        sup = set()
    src = page
    if sup:
        src = page.filter(lambda o: not (o.get("object_type") == "char" and
                                         (round(o["x0"], 2), round(o["top"], 2), o.get("text", "")) in sup))
    words = src.extract_words(x_tolerance=3, y_tolerance=3, keep_blank_chars=False, use_text_flow=False)
    # underlined "<" / ">" are printed "less/greater than or equal" signs
    under = []
    try:
        lt = [c for c in page.chars if c.get("text") in ("<", ">")]
        if lt:
            segs = [o for o in list(page.rects) + list(page.lines) if (o["bottom"] - o["top"]) < 1.5]
            for c in lt:
                cw = c["x1"] - c["x0"]
                h = c["bottom"] - c["top"]
                for o in segs:
                    if abs(o["x0"] - c["x0"]) <= 1.5 and abs(o["x1"] - c["x1"]) <= 1.5 and \
                            c["top"] + 0.5 * h <= o["top"] <= c["bottom"] + 2 and (o["x1"] - o["x0"]) <= 2 * cw + 1:
                        under.append(c)
                        break
    except Exception:
        under = []
    res = []
    for w in words:
        t = clean_chars(w["text"]).strip()
        if not t:
            continue
        w = dict(w)
        for c in under:
            if w["x0"] - 0.5 <= c["x0"] and c["x1"] <= w["x1"] + 0.5 and w["top"] - 2 <= c["top"] <= w["bottom"]:
                rel = c["x0"] - w["x0"]
                # replace the sign nearest to the char's relative position
                idxs = [k for k, ch in enumerate(t) if ch == c["text"]]
                if idxs:
                    est = int(round(rel / max(0.1, (w["x1"] - w["x0"])) * len(t)))
                    k = min(idxs, key=lambda q: abs(q - est))
                    t = t[:k] + ("\u2264" if c["text"] == "<" else "\u2265") + t[k + 1:]
        w["text"] = t
        w["size"] = w["bottom"] - w["top"]
        res.append(w)
    return res


def words_to_lines(words, ytol=None):
    """Group words into lines (list of lists) sorted top to bottom."""
    ws = sorted(words, key=lambda w: (w["top"], w["x0"]))
    lines = []
    for w in ws:
        h = w["bottom"] - w["top"]
        placed = False
        for ln in lines[-3:]:
            ltop = min(x["top"] for x in ln)
            lbot = max(x["bottom"] for x in ln)
            ov = min(lbot, w["bottom"]) - max(ltop, w["top"])
            if ov > 0.5 * min(h, lbot - ltop):
                ln.append(w)
                placed = True
                break
        if not placed:
            lines.append([w])
    for ln in lines:
        ln.sort(key=lambda w: w["x0"])
    lines.sort(key=lambda ln: min(x["top"] for x in ln))
    return lines


def words_text(words):
    lines = words_to_lines(words)
    return join_lines([" ".join(w["text"] for w in ln) for ln in lines])


# ----------------------------------------------------------------------------
# PWSID

PWS_RE = re.compile(
    r"(?:PWS\s*ID|PWSID|P\.W\.S\.\s*I\.?D\.?|Public\s+Water\s+(?:Supply|System)\s+(?:Identification|ID|I\.D\.)(?:\s+(?:Number|No\.?))?"
    r"|Public\s+Water\s+System\s+(?:Number|No\.?)|Water\s+System\s+(?:ID|I\.D\.|Number|No\.?)|System\s+(?:ID|I\.D\.)(?:\s+(?:Number|No\.?))?"
    r"|PWS\s*(?:#|No\.?|Number)|PWS\s+Identification\s+Number|System\s+(?:No\.?|Number|#))"
    r"[\s:#.\-]*(?:\(PWSID\)|\(PWS\s*ID\))?[\s:#.\-]*(?:Number|No\.?|#)?[\s:#.\-]*"
    r"([A-Z]{2}\s?-?\d[\d\-]{4,12}\d|\d[\d\-]{4,12}\d|[A-Z]{2}\d{5,9})",
    re.I)


PURCHASE_CTX = re.compile(r"purchas|\bbuy|bought|\bfrom\b|supplied|provided by|wholesale|seller|interconnect|"
                          r"emergency|consecutive|sells?\b|neighboring", re.I)


def find_pwsids(page_texts):
    """List of (page, offset, id, purchase_context)."""
    found = []
    for pi, txt in enumerate(page_texts):
        for m in PWS_RE.finditer(txt):
            v = m.group(1).strip().rstrip("-")
            if sum(c.isdigit() for c in v) < 5:
                continue
            before = txt[max(0, m.start() - 90):m.start()]
            found.append((pi, m.start(), v, bool(PURCHASE_CTX.search(before))))
    return found


def _id_key(v):
    return re.sub(r"\D", "", v)


def system_for(ids, page, frac):
    """PWSID for a table on `page` at relative position `frac` (multi-system aware)."""
    if not ids:
        return None
    own = [x for x in ids if not x[3]] or ids
    # the same ID printed in different forms counts once (first form wins)
    canon = {}
    norm_ids = []
    for x in own:
        k = _id_key(x[2])
        match = None
        for k2 in canon:
            if k == k2 or (len(k) >= 6 and len(k2) >= 6 and (k.endswith(k2) or k2.endswith(k))):
                match = k2
                break
        if match is None:
            canon[k] = x[2]
            match = k
        norm_ids.append((x[0], x[1], canon[match], x[3], x[4]))
    own = norm_ids
    distinct = []
    for x in own:
        if x[2] not in distinct:
            distinct.append(x[2])
    if len(distinct) <= 1:
        return distinct[0]
    first_pages = {}
    for x in own:
        first_pages.setdefault(x[2], x[0])
    if len(set(first_pages.values())) < len(first_pages):
        return distinct[0]
    # several systems: nearest preceding mention (page order, then offset)
    best = None
    for x in own:
        if (x[0], x[1] / max(1, x[4])) <= (page, frac):
            best = x
    return (best or own[0])[2]


# ----------------------------------------------------------------------------
# header classification

def classify_header(h):
    t = norm_space(h.lower().replace("’", "'"))
    if not t:
        return None
    if re.search(r"violat|exceed(s|ed|ance)?\b.*(mcl|al|standard|limit|\?)|mcl\s*exceeded|^exceed", t):
        if not re.search(r"number of|# of|no\. of|quarters", t):
            return "violation"
    if re.search(r"in compliance|meets? (the )?(standard|requirement|limit)|met standard|^complian|compliance\s*\(?y|compliance\s*\?", t) \
            and not re.search(r"number|#|quarters|out of", t):
        return "compliance"
    if re.search(r"90(th|%)|percentile|less than al|were less than", t) and not re.search(r"number|# of|sites|homes", t):
        return "value"
    if re.search(r"health effect|health concern|potential effect", t):
        return "health"
    if re.search(r"likely source|typical source|probable source|major source|potential source|source[s]? of|^sources?\b|origin", t):
        return "source"
    if re.search(r"comparison value|health advisory|health[- ]based|\bhrl\b|notification level|\bhbv\b|reference dose|guidance", t):
        return "advisory"
    if re.search(r"number of|# of|#\s*sites|no\. of|count\b|sites above|samples above|homes with|greater than al|"
                 r"above (the )?al|over (the )?al|exceeding (the )?al|individual results|positive samples|samples? exceeding", t):
        return "count"
    if re.search(r"mclg|mrdlg|mldg|mrldg|\bphg\b|ideal goal|\bgoal\b|\bmclg\b", t):
        return "mclg"
    if re.search(r"\bmcl\b|smcl|mrdl|\bmcls\b|action level|\bal\b|level allowed|allowed|regulatory limit|\blimit\b|treatment technique|"
                 r"\btt\b|required|minimum disinfectant|minimum residual|allowable|standard|secondary|\bepa limit|maximum contaminant", t):
        return "mcl"
    if re.search(r"\bunits?\b|unit of measure|measure-?\s?ment|^measure", t):
        return "unit"
    if re.search(r"\bdates?\b|\byears?\b|sampled|collection|test dates|period|\bmonth|quarter", t):
        return "date"
    if re.search(r"range", t):
        if re.search(r"(level|average|avg|amount|concentration|result|detected)\b.*\(\s*range", t):
            return "value"
        if re.search(r"\b(low|min|minimum|lowest)\b", t) and not re.search(r"\b(high|max|maximum|highest)\b", t):
            return "range_low"
        if re.search(r"\b(high|max|maximum|highest)\b", t) and not re.search(r"\b(low|min|minimum|lowest)\b", t):
            return "range_high"
        return "range"
    if re.search(r"^(low|min|minimum|lowest)\b", t):
        return "low?"
    if re.search(r"^(high|max|maximum|highest)\b", t):
        return "high?"
    return "value"


NAME_HDR = re.compile(r"contaminant|parameter|substance|analyte|constituent|compound|chemical|characteristic|"
                      r"inorganic|organic|microbiolog|radioactive|radiolog|disinfect|element|item|test|regulated", re.I)
HDR_ROLES = {"violation", "compliance", "source", "health", "advisory", "count", "mclg", "mcl", "unit",
             "date", "range", "range_low", "range_high"}
HDR_KEYWORD = re.compile(r"\b(mcl|mclg|mrdl|mrdlg|smcl|range|units?|violation|likely|typical|sources?|detected|"
                         r"level|levels|average|sample|sampled|date|year|contaminant|parameter|substance|"
                         r"goal|limit|action|percentile|result|results|highest|lowest|phg|exceed|compliance)\b", re.I)


def is_header_text(t):
    t = norm_space(t)
    if not t:
        return False
    r = classify_header(t)
    if r in HDR_ROLES:
        return True
    return bool(HDR_KEYWORD.search(t)) and not re.search(r"\d\.\d", t)


# ----------------------------------------------------------------------------
# value parsing

UNIT_PAT = (r"(?:ppm|ppb|ppt|ppq|mg/l|mg/kg|ug/l|µg/l|µ/l|mcg/l|ng/l|pg/l|pci/l|pci/1|mrem/yr|mrem/year|mrem|ntus?|"
            r"\btu\b|mfl|gpg|grains/gal(?:lon)?|cfu/100\s?ml|mpn/100\s?ml|umhos/cm|µmhos/cm|µs/cm|us/cm|"
            r"s\.u\.|\bsu\b|tcu|\bcu\b|t\.o\.n\.?|\bton\b|color units|mg/l\s+as\s+caco3|ppm\s+as\s+caco3)")
UNIT_RE = re.compile(r"(?<![A-Za-z])" + UNIT_PAT + r"(?![A-Za-z])", re.I)
NUM = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.?\d*|\.\d+)"
NUM_RE = re.compile(r"^" + NUM + r"$")
NULL_RE = re.compile(r"^(n/?a|n\.a\.?|none|no\s+range|not\s+applicable|-+|–+|—+|na\*?|nr|"
                     r"see\s+(foot)?note.*|see\s+below|see\s+above|\(?see\s+.*\)?|not\s+regulated|no\s+standard|"
                     r"no\s+mcl|not\s+established|n/e|ne|blank)$", re.I)


def strip_marks(s):
    s = clean_chars(s or "")
    s = re.sub(r"[*†‡§^]+", "", s)
    s = re.sub(r"\[\s*\d{1,2}\s*\]", "", s)
    return norm_space(s)


def to_number(s):
    s = s.replace(",", "")
    try:
        if re.match(r"^\d+$", s):
            return int(s)
        return float(s)
    except ValueError:
        return None


def is_null(s):
    s = norm_space(s)
    return (not s) or bool(NULL_RE.match(s))


def parse_scalar(s, unit_holder=None, keep_text=True):
    """Parse a value cell: number, number+unit, or text as printed. None for null."""
    s = strip_marks(s)
    if is_null(s):
        return None
    if NUM_RE.match(s):
        return to_number(s)
    m = re.match(r"^(" + NUM + r")\s*(" + UNIT_PAT + r")\.?$", s, re.I)
    if m:
        if unit_holder is not None:
            unit_holder.append(m.group(2))
        return to_number(m.group(1))
    return s if keep_text else None


RANGE_ITEM = r"(?:[<>≤≥]\s*=?\s*" + NUM + r"%?|" + NUM + r"%?|n\.?d\.?|nd|non-?detect|bdl|<\s*mrl|<\s*dl|<\s*rl|u)"
RANGE_RE = re.compile(r"^(" + RANGE_ITEM + r")\s*(?:" + UNIT_PAT + r")?\s*(?:-|–|—|\bto\b|~)\s*(" + RANGE_ITEM + r")\s*("
                      + UNIT_PAT + r")?$", re.I)


def range_val(s):
    s = s.strip()
    if NUM_RE.match(s):
        return to_number(s)
    return re.sub(r"\s+", "", s) if re.match(r"^[<>]", s) else s


def parse_range(s, unit_holder=None):
    """Return (low, high) from a range cell."""
    s = strip_marks(s)
    s = re.sub(r"^\(|\)$", "", s).strip()
    if is_null(s):
        return None, None
    m = RANGE_RE.match(s)
    if m:
        if m.group(3) and unit_holder is not None:
            unit_holder.append(m.group(3))
        return range_val(m.group(1)), range_val(m.group(2))
    um = re.match(r"^(" + RANGE_ITEM + r")\s*(" + UNIT_PAT + r")?$", s, re.I)
    if um:
        if um.group(2) and unit_holder is not None:
            unit_holder.append(um.group(2))
        v = range_val(um.group(1))
        return v, v
    return None, None


LABELLED_LOC = re.compile(r"([A-Za-z][A-Za-z.'&#/ ]*?)\s*:?\s+([<>]?" + NUM + r")\s*\(\s*(" + RANGE_ITEM + r")\s*(?:-|–|to)\s*("
                          + RANGE_ITEM + r")\s*\)", re.I)


def parse_level_cell(s):
    """Return dict with level, range_low, range_high, unit (list), and optional multi list."""
    res = {"level": None, "range_low": None, "range_high": None, "units": []}
    s0 = strip_marks(s)
    if is_null(s0):
        return res
    # several location-labelled values
    locs = LABELLED_LOC.findall(s0)
    if len(locs) >= 2:
        res["multi"] = []
        for lab, v, lo, hi in locs:
            res["multi"].append({"location": norm_space(lab), "level": to_number(v) if NUM_RE.match(v) else v,
                                 "range_low": range_val(lo), "range_high": range_val(hi)})
        return res
    # "X (a - b)"
    m = re.match(r"^([<>]?\s*" + NUM + r"|nd|n\.d\.)\s*(" + UNIT_PAT + r")?\s*\(\s*(" + RANGE_ITEM + r")\s*(?:-|–|—|to)\s*("
                 + RANGE_ITEM + r")\s*\)\s*(" + UNIT_PAT + r")?$", s0, re.I)
    if m:
        res["level"] = range_val(m.group(1))
        res["range_low"], res["range_high"] = range_val(m.group(3)), range_val(m.group(4))
        for g in (m.group(2), m.group(5)):
            if g:
                res["units"].append(g)
        return res
    # labelled values: "Average: 0.89 Range: 0.31-1.36", "Max 1.28 / Average 0.69 / Range 0.47-1.28"
    if re.search(r"[A-Za-z]{3,}", s0) and re.search(r"\d", s0):
        rng = re.search(r"range\s*[:=]?\s*(" + RANGE_ITEM + r")\s*(?:-|–|—|to)\s*(" + RANGE_ITEM + r")", s0, re.I)
        if rng:
            res["range_low"], res["range_high"] = range_val(rng.group(1)), range_val(rng.group(2))
            rest = s0[:rng.start()] + " " + s0[rng.end():]
        else:
            rest = s0
        lab = re.findall(r"(?:^|[\s/;,])(?:[A-Za-z0-9%#.'()]+\s+){0,5}?[A-Za-z%][A-Za-z%#.'()]*\s*[:=]\s*([<>]?\s*" + NUM + r")\s*("
                         + UNIT_PAT + r")?", rest, re.I)
        if not lab:
            lab = re.findall(r"(?:max(?:imum)?|average|avg|mean|highest|lowest|min(?:imum)?|median|raa|lraa|value)\s*[:=]?\s*([<>]?\s*"
                             + NUM + r")\s*(" + UNIT_PAT + r")?", rest, re.I)
        if lab and (rng or re.match(r"^\s*[A-Za-z]|^\s*\d+(st|nd|rd|th)\b", rest)):
            v, u = lab[0]
            res["level"] = range_val(v)
            if u:
                res["units"].append(u)
            return res
        if rng and not re.sub(r"[\s/;,]", "", rest):
            return res
    uh = []
    v = parse_scalar(s0, uh)
    res["level"] = v
    res["units"] += uh
    return res


def find_unit(s):
    m = UNIT_RE.search(strip_marks(s or ""))
    return m.group(0) if m else None


def parse_violation(s, invert=False):
    s = strip_marks(s).lower()
    if is_null(s):
        return None
    if re.match(r"^(no|n)\b", s):
        return "Y" if invert else "N"
    if re.match(r"^(yes|y)\b", s):
        return "N" if invert else "Y"
    return None


DATE_TOK = r"(?:\d{1,2}/\d{1,2}/\d{2,4}|\d{1,2}/\d{2,4}|\d{1,2}-\d{1,2}-\d{2,4}|(?:19|20)\d{2})"


def clean_contaminant(s):
    """Return (name, date, unit) from a contaminant cell."""
    s = clean_chars(s or "")
    s = re.sub(r"[*†‡§^]+", "", s)
    s = re.sub(r"\[\s*\w{1,2}\s*\]", "", s)
    date = None
    unit = None
    # parenthesized pieces
    def repl(m):
        nonlocal date, unit
        inner = m.group(1).strip()
        if UNIT_RE.fullmatch(inner.strip()) or re.fullmatch(r"(?:in\s+)?" + UNIT_PAT, inner, re.I):
            if unit is None:
                unit = UNIT_RE.search(inner).group(0)
            return " "
        dm = re.findall(DATE_TOK, inner)
        if dm and (re.fullmatch(r"\s*" + DATE_TOK + r"(\s*[-–]\s*" + DATE_TOK + r")?\s*", inner)
                   or re.search(r"\b(tested|sampled|sample|collected|test|data|monitored|from|in)\b", inner, re.I)):
            if date is None:
                mm = re.search(DATE_TOK + r"(\s*[-–]\s*" + DATE_TOK + r")?", inner)
                date = mm.group(0)
            return " "
        if re.fullmatch(r"\d|[a-e]", inner):
            return " "
        return m.group(0)
    s = re.sub(r"\(([^()]*)\)", repl, s)
    s = re.sub(r"^\s*\d{1,2}[.)]\s+(?=[A-Za-z])", "", s)
    s = norm_space(s)
    # same-size footnote digits glued to well-known names ("Copper1", "TTHM2", "HAA51")
    m = re.match(r"^(lead|copper|sodium|fluoride|nitrate|nitrite|barium|arsenic|chlorine|chloramines?|turbidity|tthms?|"
                 r"total trihalomethanes|haloacetic acids|total haloacetic acids|chromium|selenium|uranium|gross alpha|gross beta|"
                 r"hardness|sulfate|chloride|iron|manganese|zinc|toc|total organic carbon|bromate|chlorite|nickel|antimony|"
                 r"cyanide|atrazine|total coliform|e\.\s?coli|haa5|pfoa|pfos|pfas|pfna|pfhxs|genx|cadmium|mercury|thallium|"
                 r"beryllium|asbestos|radon|alkalinity|calcium|magnesium|potassium|silica|conductivity|ph)(\d{1,2})"
                 r"(\s*\(.*\))?$", s, re.I)
    if m and not (m.group(1).lower() in ("haa", "tthm") and False):
        s = norm_space(m.group(1) + (m.group(3) or ""))
    s = re.sub(r"[\s,;:]+$", "", s)
    return s, date, unit


# ----------------------------------------------------------------------------
# grid construction from ruled tables

def cluster_vals(vals, tol=2.0):
    vals = sorted(vals)
    out = []
    for v in vals:
        if out and v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [sum(c) / len(c) for c in out]


class Cell:
    __slots__ = ("bbox", "words", "text", "r0", "r1", "c0", "c1", "id")


class Grid:
    """A table as rows x columns of Cells (a cell may span several)."""

    def __init__(self, cells_bboxes, words, page_index):
        self.page = page_index
        xs = cluster_vals([b[0] for b in cells_bboxes] + [b[2] for b in cells_bboxes])
        ys = cluster_vals([b[1] for b in cells_bboxes] + [b[3] for b in cells_bboxes])
        self.xs, self.ys = xs, ys
        nidx = lambda arr, v: min(range(len(arr)), key=lambda i: abs(arr[i] - v))
        self.cells = []
        for b in cells_bboxes:
            c = Cell()
            c.bbox = b
            c.c0, c.c1 = nidx(xs, b[0]), nidx(xs, b[2])
            c.r0, c.r1 = nidx(ys, b[1]), nidx(ys, b[3])
            if c.c1 <= c.c0 or c.r1 <= c.r0:
                continue
            c.words = []
            c.id = len(self.cells)
            self.cells.append(c)
        self.nrows = len(ys) - 1
        self.ncols = len(xs) - 1
        self.g = [[None] * self.ncols for _ in range(self.nrows)]
        for c in self.cells:
            for r in range(c.r0, c.r1):
                for cc in range(c.c0, c.c1):
                    if self.g[r][cc] is None:
                        self.g[r][cc] = c
        for w in words:
            cx = (w["x0"] + w["x1"]) / 2
            cy = (w["top"] + w["bottom"]) / 2
            best = None
            for c in self.cells:
                x0, t, x1, b = c.bbox
                if x0 - 1 <= cx <= x1 + 1 and t - 1 <= cy <= b + 1:
                    best = c
                    break
            if best is not None:
                best.words.append(w)
        for c in self.cells:
            c.text = words_text(c.words)
        self.bbox = (min(xs), min(ys), max(xs), max(ys))


# ----------------------------------------------------------------------------
# table interpretation (shared by ruled and text tables)

NUMERIC_TOKEN = re.compile(r"\d|^(nd|n/?a|none|absent|present|yes|no|tt|<|>)$", re.I)
VALUE_ROLES = {"value", "range", "range_low", "range_high", "mcl", "mclg", "unit", "date", "violation", "compliance", "count",
               "advisory"}


class Table:
    """Logical table: header text per column, rows of per-column texts."""

    def __init__(self):
        self.headers = []      # header text per logical column
        self.spans = []        # list of (text, set(columns)) for header cells spanning >1 column
        self.rows = []         # list of dicts: {col: (text, own)} plus meta
        self.title = ""
        self.page = 0
        self.top = 0
        self.ncols = 0
        self.col_x = []        # (x0, x1) per logical column


def row_cells(grid, r):
    """Distinct cells in row r in column order."""
    seen = []
    for c in grid.g[r]:
        if c is not None and (not seen or seen[-1] is not c) and c not in seen:
            seen.append(c)
    return seen


def grid_to_tables(grid, title_hint=""):
    """Split a grid into header block + data rows and produce logical Tables."""
    nrows = grid.nrows
    rows_info = []
    for r in range(nrows):
        cells = row_cells(grid, r)
        own = [c for c in cells if c.r0 == r]
        texts = [c.text for c in cells]
        nonempty = [c for c in cells if c.text.strip()]
        rows_info.append((cells, own, texts, nonempty))

    def is_full_width(r):
        cells = rows_info[r][0]
        ne = [c for c in cells if c.text.strip()]
        if len(cells) == 1:
            return True
        if len(ne) == 1 and (ne[0].c1 - ne[0].c0) >= 0.6 * grid.ncols:
            return True
        return False

    def header_score(r):
        cells, own, texts, ne = rows_info[r]
        n = 0
        for c in ne:
            if is_header_text(c.text):
                n += 1
        if n < 2 and len(ne) >= 3 and not any(re.search(r"\d\.\d|^\d+$", c.text.strip()) for c in ne):
            known = sum(1 for c in ne[1:] if KNOWN_CONTAM.match(clean_contaminant(c.text)[0]))
            if known >= 2:
                n = max(n, known)
        return n

    def has_numbers(r):
        cells, own, texts, ne = rows_info[r]
        for c in ne[1:]:
            if re.search(r"\d", c.text) and not re.search(r"(90th|percentile|# of|\b20\d\d\b.*(level|result))", c.text, re.I) \
                    and not (KNOWN_CONTAM.match(clean_contaminant(c.text)[0]) and not re.search(r"\d\.\d|^\d", c.text.strip())):
                return True
        return False

    tables = []
    r = 0
    scan_from = 0
    title = title_hint
    while r < nrows:
        # find a header row
        if is_full_width(r):
            scan_from = r + 1
            cells = rows_info[r][0]
            t = " ".join(c.text for c in cells if c.text.strip())
            if t:
                title = t
            r += 1
            continue
        hs = header_score(r)
        if hs >= 2 and not has_numbers(r) or hs >= 3:
            hstart = r
            # include header lines above the keyword row (multi-line headers)
            while hstart - 1 >= scan_from and not is_full_width(hstart - 1) and not has_numbers(hstart - 1):
                hstart -= 1
            r += 1
            while r < nrows and not is_full_width(r):
                cells, own, texts, ne = rows_info[r]
                first = grid.g[r][0]
                first_text = first.text.strip() if first is not None and first.r0 == r else ""
                if first is not None and first.r0 < r and first.r0 >= hstart:
                    # first column still covered by a header cell
                    r += 1
                    continue
                if (not first_text) and not has_numbers(r):
                    r += 1
                    continue
                if header_score(r) >= 2 and not has_numbers(r):
                    r += 1
                    continue
                break
            hend = r
            dstart = r
            while r < nrows:
                if not is_full_width(r) and header_score(r) >= 3 and not has_numbers(r):
                    break
                r += 1
            tables.append(build_table(grid, list(range(hstart, hend)), list(range(dstart, r)), title))
            scan_from = r
            continue
        r += 1
    if not tables:
        # headerless grid: maybe continuation of a previous table
        data = [r for r in range(nrows)]
        tables.append(build_table(grid, [], data, title, headerless=True))
    return tables


def build_table(grid, hrows, drows, title, headerless=False):
    T = Table()
    T.page = grid.page
    T.title = title
    T.headerless = headerless
    T.top = grid.ys[hrows[0]] if hrows else (grid.ys[drows[0]] if drows else grid.ys[0])
    ncols = grid.ncols
    # logical column boundaries from multi-cell data rows
    bcount = [0] * (ncols + 1)
    nmulti = 0
    for r in drows:
        cells = row_cells(grid, r)
        if len(cells) < 2:
            continue
        ne = [c for c in cells if c.text.strip()]
        if len(ne) <= 1 and len(cells) <= 2:
            continue
        nmulti += 1
        for c in cells:
            bcount[c.c0] += 1
            bcount[c.c1] += 1
    if nmulti == 0:
        for r in hrows:
            for c in row_cells(grid, r):
                bcount[c.c0] += 1
                bcount[c.c1] += 1
        nmulti = max(1, len(hrows))
    bounds = [0] + [i for i in range(1, ncols) if bcount[i] >= max(1, 0.5 * nmulti)] + [ncols]
    groups = [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]
    T.ncols = len(groups)
    T.col_x = [(grid.xs[a], grid.xs[b]) for a, b in groups]

    def lcols_of(c):
        res = []
        for i, (a, b) in enumerate(groups):
            ov = min(b, c.c1) - max(a, c.c0)
            if ov > 0:
                res.append(i)
        return res

    # header text per logical column; words assigned by x position
    heads = [[] for _ in groups]
    spans = []
    for r in hrows:
        for c in row_cells(grid, r):
            if c.r0 != r or not c.text.strip():
                continue
            lc = lcols_of(c)
            if len(lc) == 1:
                heads[lc[0]].append(c)
            elif len(lc) > 1:
                # distribute words by x position when possible, else span
                byc = {}
                for w in c.words:
                    cx = (w["x0"] + w["x1"]) / 2
                    for i in lc:
                        x0, x1 = T.col_x[i]
                        if x0 <= cx <= x1:
                            byc.setdefault(i, []).append(w)
                            break
                lines = words_to_lines(c.words)
                if len(byc) > 1 and all(len({i for i in lc if any(T.col_x[i][0] <= (w["x0"] + w["x1"]) / 2 <= T.col_x[i][1]
                                                                 for w in ln)}) <= 1 for ln in lines):
                    for i, ws in byc.items():
                        heads[i].append(("w", ws))
                else:
                    spans.append((c.text, set(lc)))
                    for i in lc:
                        heads[i].append(c)
    T.headers = []
    for i in range(len(groups)):
        parts = []
        for h in heads[i]:
            if isinstance(h, tuple):
                parts.append(words_text(h[1]))
            else:
                parts.append(h.text)
        T.headers.append(norm_space(" ".join(parts)))
    T.spans = spans
    # data rows
    for r in drows:
        cells = row_cells(grid, r)
        # a band holding several contaminants (no rules between data rows): split by text lines
        band = [w for c in cells if c.r0 == r and (c.c1 - c.c0) < 0.6 * ncols for w in c.words]
        if band:
            blines = words_to_lines(band)
            full = 0
            for ln in blines:
                cs = {}
                for w in ln:
                    cx = (w["x0"] + w["x1"]) / 2
                    for i, (x0, x1) in enumerate(T.col_x):
                        if x0 - 1 <= cx <= x1 + 1:
                            cs.setdefault(i, []).append(w["text"])
                            break
                nval = sum(1 for i, ts in cs.items() if i != 0 and len(" ".join(ts)) <= 24 and
                           re.search(r"\d|^(nd|n/?a|na|none|yes|no|y|n|tt|absent|present)$", " ".join(ts), re.I))
                if 0 in cs and nval >= (2 if T.ncols <= 5 else 3):
                    full += 1
            if full >= 2:
                infos = [{"words": ln, "phrases": line_phrases(ln), "top": min(w["top"] for w in ln),
                          "bottom": max(w["bottom"] for w in ln), "text": " ".join(w["text"] for w in ln)} for ln in blines]
                cx = [(x0 - 1, x1 + 1) for x0, x1 in T.col_x]
                T.rows.extend(segment_text_rows(infos, cx))
                continue
        rowd = {}
        for i, (a, b) in enumerate(groups):
            seen = []
            for cc in range(a, b):
                c = grid.g[r][cc]
                if c is not None and c not in seen:
                    seen.append(c)
            txt = norm_space(" ".join(c.text for c in seen if c.text.strip()))
            own = any(c.r0 == r for c in seen if c.text.strip()) if txt else any(c.r0 == r for c in seen)
            full = any((c.c1 - c.c0) >= 0.6 * ncols for c in seen)
            x0 = min((w["x0"] for c in seen for w in c.words), default=None)
            rowd[i] = (txt, own, full, x0)
        T.rows.append(rowd)
    return T


# ----------------------------------------------------------------------------
# records from logical tables

def assign_roles(T):
    roles = [classify_header(h) if i > 0 else "name" for i, h in enumerate(T.headers)]
    # second column part of name? (header cell of name spans it)
    for txt, cols in T.spans:
        if 0 in cols and NAME_HDR.search(txt or ""):
            for i in cols:
                if i != 0 and roles[i] in (None, "value", "name") and not T.headers[i].replace(txt, "").strip():
                    roles[i] = "name2"
    if T.ncols > 2 and not T.headers[1].strip():
        vals = [norm_space(row[1][0]) for row in T.rows if row.get(1) and row[1][0].strip()]
        if vals and sum(1 for v in vals if re.match(r"^[A-Za-z%#(]", v) and not re.match(r"^(yes|no|y|n|nd|n/?a|na|none|tt)\b", v, re.I)
                        and not UNIT_RE.fullmatch(strip_marks(v))) >= 0.8 * len(vals):
            roles[1] = "name2"
    # columns without header: infer from content
    for i in range(1, T.ncols):
        if roles[i] == "name2":
            continue
        if roles[i] is None or (roles[i] == "value" and not T.headers[i].strip()):
            vals = [norm_space(row[i][0]) for row in T.rows if row.get(i) and row[i][0].strip()]
            if vals and all(re.match(r"^(yes|no|y|n)\b\.?\*?$", v, re.I) for v in vals):
                roles[i] = "violation"
            elif vals and all(UNIT_RE.fullmatch(strip_marks(v)) for v in vals):
                roles[i] = "unit"
            elif not vals:
                roles[i] = None
            else:
                roles[i] = "value"
    # low?/high? pairs
    for i in range(T.ncols):
        if roles[i] == "low?":
            roles[i] = "range_low" if "high?" in roles or "range_high" in roles else "value"
        elif roles[i] == "high?":
            roles[i] = "range_high" if "low?" in roles or "range_low" in roles else "value"
    for i in range(T.ncols):
        if roles[i] == "range_low" and "range_high" not in roles and "high?" not in roles:
            roles[i] = "range"
        if roles[i] == "range_high" and "range_low" not in roles and "low?" not in roles:
            roles[i] = "range"
    return roles


def location_groups(T, roles):
    """Detect column groups for different sources/plants: header cells spanning several value columns."""
    groups = []
    for txt, cols in T.spans:
        cols = sorted(cols)
        if 0 in cols or len(cols) < 2:
            continue
        r = classify_header(txt)
        if r != "value" or re.search(r"level|detected|result|average|range|value|concentration|amount|percentile|sample", txt, re.I):
            continue
        if not any(roles[i] in ("value", "range", "range_low", "range_high") for i in cols):
            continue
        groups.append((norm_space(txt), cols))
    if len(groups) >= 2:
        return groups
    return []


def is_results_table(T, roles):
    if T.ncols < 3:
        return False
    has_level = "value" in roles or "range" in roles or "range_low" in roles
    support = sum(1 for r in ("mcl", "mclg", "unit", "range", "range_low", "violation", "compliance", "source", "date") if r in roles)
    if not has_level:
        return False
    if support < 1:
        return False
    return True


KNOWN_CONTAM = re.compile(r"^(total\s+)?(lead|copper|nitrate|nitrite|fluoride|free\s+chlorine|total\s+chlorine|chlorine(\s+residual)?|"
                          r"chloramines?|tthms?|trihalomethanes|haa5|haa|haloacetic\s+acids|arsenic|barium|sodium|hardness|iron|manganese|"
                          r"turbidity|toc|organic\s+carbon|radium[\s\d/-]*|uranium|gross\s+alpha|gross\s+beta|coliform(\s+bacteria)?|"
                          r"e\.?\s?coli|chlorite|chlorate|bromate|pfas|pfoa|pfos|pfna|pfhxs|pfbs|genx|sulfate|chloride|ph|alkalinity|"
                          r"calcium|magnesium|zinc|chromium|selenium|atrazine|nickel|antimony|cadmium|mercury|thallium|beryllium|cyanide|"
                          r"conductivity|tds|total\s+dissolved\s+solids|potassium|silica|orthophosphate|phosphate|ammonia)\b", re.I)
DATE_LABEL = re.compile(r"^(q[1-4]|quarter|1st|2nd|3rd|4th|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|"
                        r"\d{1,2}/\d{1,2}(/\d{2,4})?|\d{1,2}/\d{4}|(19|20)\d{2})", re.I)


def transposed_records(T, system):
    """Tables whose columns are contaminants and rows are dates or places."""
    if T.ncols < 3:
        return None
    heads = [h for h in T.headers[1:] if h.strip()]
    if len(heads) < 2:
        return None
    known = [h for h in heads if KNOWN_CONTAM.match(clean_contaminant(h)[0])]
    if len(known) < 2 or len(known) < 0.7 * len(heads):
        return None
    if any(classify_header(h) in ("mcl", "mclg", "range", "unit", "violation", "compliance", "source", "date") for h in heads):
        return None
    labels = [norm_space(row[0][0]) for row in T.rows if row.get(0) and row[0][0].strip()]
    if not labels:
        return None
    recs = []
    unit_title = find_unit(T.title) if T.title else None
    for row in T.rows:
        lab = strip_marks(row[0][0]) if row.get(0) else ""
        if not lab or re.match(r"^(average|avg|max|maximum|min|minimum|range|mcl|mclg|highest|lowest|lraa|raa)\b", lab, re.I):
            continue
        for i in range(1, T.ncols):
            h = T.headers[i]
            if not h.strip() or i not in row:
                continue
            name, hdate, hunit = clean_contaminant(h)
            p = parse_level_cell(row[i][0])
            if p["level"] is None and p["range_low"] is None:
                continue
            unit = hunit or (p["units"][0] if p["units"] else None) or unit_title
            rec = {"system": system, "contaminant": name, "sample_date": None, "level": p["level"],
                   "range_low": p["range_low"], "range_high": p["range_high"], "unit": unit, "mcl": None, "mclg": None,
                   "violation": None}
            if DATE_LABEL.match(lab):
                rec["sample_date"] = lab
            else:
                rec["location"] = lab
            recs.append(rec)
    return recs


def make_records(T, roles, system):
    recs = []
    tr = transposed_records(T, system)
    if tr is not None:
        return tr
    if not is_results_table(T, roles):
        return recs
    name_cols = [i for i, r in enumerate(roles) if r in ("name", "name2")]
    value_like = {i for i, r in enumerate(roles) if r in ("value", "range", "range_low", "range_high", "mcl", "mclg", "unit",
                                                          "date", "violation", "compliance")}
    # merge physical rows into logical rows
    logical = []
    cur = None
    section = None
    last_col0 = ""
    for rowd in T.rows:
        texts = {i: rowd[i][0] for i in rowd}
        nonempty = [i for i in rowd if texts[i].strip()]
        if not nonempty:
            continue
        # full-width row: section heading or note
        if all(rowd[i][2] for i in nonempty) or (len(nonempty) == 1 and nonempty[0] == 0 and
                                                   not any(texts[i].strip() for i in value_like)):
            if len(nonempty) >= 1 and all(rowd[i][2] for i in nonempty):
                t = texts[nonempty[0]]
                if len(t) < 80:
                    section = t
                cur = None
                continue
        name_parts = [texts[i] for i in name_cols if texts.get(i, "").strip()]
        if len(name_parts) > 1 and SECTION_RE.search(name_parts[0]):
            name_parts = name_parts[1:]
        name_txt = " - ".join(name_parts)
        name_own = any(rowd[i][1] for i in name_cols if texts.get(i, "").strip())
        if len(name_cols) > 1 and not texts.get(name_cols[0], "").strip() and name_txt and last_col0:
            name_txt = last_col0 + " - " + name_txt
        if texts.get(name_cols[0], "").strip() if name_cols else False:
            last_col0 = texts[name_cols[0]]
        if name_txt and name_own:
            if cur is not None and not any(texts[i].strip() for i in value_like) and cur["vals_filled"] == 0 and False:
                pass
            cur = {"name": name_txt, "cells": {i: texts[i] for i in rowd if i not in name_cols},
                   "own": {i: rowd[i][1] for i in rowd}, "section": section, "x0": rowd[0][3]}
            cur["vals_filled"] = sum(1 for i in value_like if cur["cells"].get(i, "").strip())
            logical.append(cur)
            continue
        if cur is None:
            if not name_txt:
                continue
            cur = {"name": name_txt, "cells": {i: texts[i] for i in rowd if i not in name_cols},
                   "own": {i: rowd[i][1] for i in rowd}, "section": section, "x0": rowd[0][3]}
            cur["vals_filled"] = sum(1 for i in value_like if cur["cells"].get(i, "").strip())
            logical.append(cur)
            continue
        own_vals = {i: texts[i] for i in rowd if i not in name_cols and rowd[i][1] and texts[i].strip()}
        conflict = [i for i in own_vals if i in value_like and cur["cells"].get(i, "").strip()
                    and roles[i] not in ("unit",)]
        if conflict:
            new = {"name": cur["name"], "cells": {i: texts[i] for i in rowd if i not in name_cols},
                   "own": {i: rowd[i][1] for i in rowd}, "section": section, "x0": rowd[0][3]}
            new["vals_filled"] = sum(1 for i in value_like if new["cells"].get(i, "").strip())
            logical.append(new)
            cur = new
        else:
            for i, t in own_vals.items():
                prev = cur["cells"].get(i, "")
                cur["cells"][i] = join_lines([prev, t]) if prev.strip() else t
            cur["vals_filled"] = sum(1 for i in value_like if cur["cells"].get(i, "").strip())

    # parent label rows (no values) followed by indented sub-rows
    final = []
    parent = None
    for lr in logical:
        has_vals = any(lr["cells"].get(i, "").strip() for i in value_like)
        if parent is not None and lr.get("section") != parent.get("section"):
            parent = None
        if not has_vals:
            parent = lr
            continue
        if parent is not None and lr.get("x0") is not None and parent.get("x0") is not None and lr["x0"] > parent["x0"] + 4 \
                and not SECTION_RE.search(parent["name"]) and len(parent["name"]) < 40:
            lr["name"] = parent["name"] + " - " + lr["name"]
        else:
            parent = None
        final.append(lr)

    level_cols = [i for i, r in enumerate(roles) if r == "value"]
    lgroups = location_groups(T, roles)
    unit_title = find_unit(T.title) if T.title else None
    for lr in final:
        if lgroups:
            for gname, gcols in lgroups:
                sub_roles = [roles[i] if (i in gcols or roles[i] not in ("value", "range", "range_low", "range_high")) else None
                             for i in range(T.ncols)]
                recs.extend(build_records(T, sub_roles, lr, system, unit_title, location=gname))
        else:
            recs.extend(build_records(T, roles, lr, system, unit_title, location=lr.get("section_loc")))
    return recs


def build_records(T, roles, lr, system, unit_title, location=None):
    cells = lr["cells"]
    raw_name = lr["name"]
    name, cdate, cunit = clean_contaminant(raw_name)
    if not name or not re.search(r"[A-Za-z]", name):
        return []
    if len(name) > 120:
        return []
    level_cols = [i for i, r in enumerate(roles) if r == "value"]
    units = []
    level = rlow = rhigh = None
    multi = None
    other = {}
    lvl_units = []
    if level_cols:
        lc = level_cols[0]
        p = parse_level_cell(cells.get(lc, ""))
        level, rlow, rhigh = p["level"], p["range_low"], p["range_high"]
        lvl_units = p["units"]
        multi = p.get("multi")
        for i in level_cols[1:]:
            t = strip_marks(cells.get(i, ""))
            if t:
                other[T.headers[i] or "value%d" % i] = parse_scalar(t)
    for i, r in enumerate(roles):
        if r in ("count", "advisory"):
            t = strip_marks(cells.get(i, ""))
            if t:
                other[T.headers[i] or "col%d" % i] = parse_scalar(t)
    rng_units = []
    rc = [i for i, r in enumerate(roles) if r == "range"]
    if rc:
        lo, hi = parse_range(cells.get(rc[0], ""), rng_units)
        if lo is not None or hi is not None or rlow is None:
            rlow, rhigh = lo, hi
    rl = [i for i, r in enumerate(roles) if r == "range_low"]
    rh = [i for i, r in enumerate(roles) if r == "range_high"]
    if rl or rh:
        if rl:
            rlow = parse_scalar(cells.get(rl[0], ""), rng_units)
            if isinstance(rlow, str) and re.match(r"^[<>]\s", rlow):
                rlow = re.sub(r"\s+", "", rlow)
        if rh:
            rhigh = parse_scalar(cells.get(rh[0], ""), rng_units)
    unit = None
    uc = [i for i, r in enumerate(roles) if r == "unit"]
    if uc:
        u = strip_marks(cells.get(uc[0], ""))
        if not is_null(u) and not re.search(r"\bnote\b", u, re.I):
            unit = u
    has_unit_col = bool(uc)

    def limit_val(txt, holder):
        v = parse_scalar(txt, holder)
        if has_unit_col and holder and not isinstance(v, str):
            # with a unit column, a limit printed with its unit is kept as printed
            return strip_marks(txt)
        return v
    mcl_units = []
    mcl = None
    for i, r in enumerate(roles):
        if r == "mcl":
            v = limit_val(cells.get(i, ""), mcl_units)
            if v is not None:
                mcl = v
                break
    mclg = None
    mclg_units = []
    for i, r in enumerate(roles):
        if r == "mclg":
            v = limit_val(cells.get(i, ""), mclg_units)
            if v is not None:
                mclg = v
                break
    viol = None
    for i, r in enumerate(roles):
        if r in ("violation", "compliance"):
            viol = parse_violation(cells.get(i, ""), invert=(r == "compliance"))
            if viol is not None:
                break
    date = None
    dc = [i for i, r in enumerate(roles) if r == "date"]
    if dc:
        d = strip_marks(cells.get(dc[0], ""))
        if not is_null(d):
            date = d
    if date is None and cdate:
        date = cdate
    if unit is None:
        unit = cunit
    if unit is None and lvl_units:
        unit = lvl_units[0]
    if unit is None and level_cols:
        u = find_unit(cells.get(level_cols[0], "")) if isinstance(level, str) else None
        if u:
            unit = u
    if unit is None and rng_units:
        unit = rng_units[0]
    if unit is None and mcl_units:
        unit = mcl_units[0]
    if unit is None:
        for i, r in enumerate(roles):
            if r == "mcl":
                u = find_unit(cells.get(i, ""))
                if u:
                    unit = u
                    break
    if unit is None and mclg_units:
        unit = mclg_units[0]
    if unit is None and level_cols:
        u = find_unit(T.headers[level_cols[0]])
        if u:
            unit = u
    if unit is None and unit_title:
        unit = unit_title
    if isinstance(level, str) and not re.search(r"\d", level) and not re.match(
            r"^(n\.?d\.?|non[- ]?detects?|non[- ]?detected|not detected|undetected|absent|present|negative|positive|none detected|"
            r"bdl|below .*|trace|tt|pass(ed)?|meets?|met|complies|in compliance|yes|no|detected|nil|<.*|>.*)$", level.strip(), re.I):
        level = None
    if level is None and rlow is None and rhigh is None and not multi:
        return []
    base = {"system": system, "contaminant": name, "sample_date": date, "level": level, "range_low": rlow,
            "range_high": rhigh, "unit": unit, "mcl": mcl, "mclg": mclg, "violation": viol}
    out = []
    if multi:
        for m in multi:
            rec = dict(base)
            rec.update({"level": m["level"], "range_low": m["range_low"], "range_high": m["range_high"],
                        "location": m["location"]})
            out.append(rec)
        return out
    if location:
        base["location"] = location
    if other:
        base["other_values"] = other
    out.append(base)
    return out


# ----------------------------------------------------------------------------
# unruled (whitespace-aligned) tables

SECTION_RE = re.compile(r"contaminants?|inorganic|organic|radioactive|radiological|microbiolog|disinfect|by-?products|"
                        r"regulated|unregulated|lead\s+and\s+copper|secondary|ucmr|other\s+(parameters|substances)|"
                        r"volatile|synthetic|treated\s+water|source\s+water|entry\s+point|distribution|\bwells?\b|\bplant\b|"
                        r"station|reservoir|\bzone\b|purchased|wholesale", re.I)


def line_phrases(ln, gap=None):
    """Split a line (sorted words) into phrases separated by wide gaps."""
    out = []
    for w in ln:
        g = gap if gap is not None else max(4.0, 0.42 * (w["bottom"] - w["top"]))
        if out and w["x0"] - out[-1][-1]["x1"] <= g:
            out[-1].append(w)
        else:
            out.append([w])
    return out


def ph_text(ph):
    return " ".join(w["text"] for w in ph)


def header_roles_in(text):
    roles = set()
    t = text.lower()
    pats = {"mclg": r"\bmclg|mrdlg|\bphg\b|ideal goal|\bgoal\b", "mcl": r"\bmcl\b|\bmrdl\b|action level|\bsmcl|limit|allowed",
            "range": r"\brange|\blow\b|\bhigh\b|\bmin\b|\bmax\b", "unit": r"\bunits?\b", "violation": r"violation|exceed|complian",
            "source": r"source", "date": r"\bdate|\byear|sampled", "value": r"detected|level|average|result|found|percentile|amount",
            "name": r"contaminant|parameter|substance|analyte|constituent",
            "advisory": r"comparison|advisory|health[- ]based|notification"}
    for k, p in pats.items():
        if re.search(p, t):
            roles.add(k)
    return roles


PURE_VAL = re.compile(r"^([<>\u2264\u2265]?\s*\d[\d.,%\s\-\u2013/]*|nd|n/?a|na|none|yes|no|y|n|tt|absent|present)$", re.I)


HEADER_VOCAB = set("""contaminant contaminants parameter parameters substance substances analyte analytes constituent constituents
compound compounds chemical chemicals level levels detected detection detections detect range ranges low high lowest highest min max
minimum maximum average avg unit units measure measurement ment mcl mclg mrdl mrdlg smcl phg al tt goal goals ideal epa epa's limit
limits allowed allowable action regulatory violation violations yes no y/n date dates sample samples sampled sampling year years
collection test tested tests results result likely typical major probable potential source sources contamination contaminant's
of the or and in if a to at for per by percentile 90th value values found amount your water our sites site number # homes
exceeding exceeds exceed exceedance above over less than were health effects effect compliance comparison standard standards
secondary primary running annual locational raa lraa treatment technique required removal residual residuals disinfectant
previous individual single quarterly monthly detected? violation? range? reporting reported mrl dl concentration concentrations
highest? lowest? date(s) tap system entry point plant well wells average/range state federal goal/limit drinking typical
in compliance? meets met unregulated regulated inorganic organic radioactive microbiological disinfection byproducts by-products
max. min. avg. no. ppm ppb ntu pci/l mg/l ug/l yes/no in? as is be been""".split())


def _vocab_ratio(text):
    toks = [t.strip(".,:;()[]*").lower() for t in text.split()]
    toks = [t for t in toks if t and re.search(r"[a-z]", t)]
    if not toks:
        return 0.0
    return sum(1 for t in toks if t in HEADER_VOCAB or re.match(r"^(mcl|mrdl|phg|quarter|month)", t) or KNOWN_CONTAM.match(t)) \
        / float(len(toks))


def _is_header_line(inf):
    if re.search(r"\d+\.\d+|\d+\s*-\s*\d+|[=<>]\s*\d", inf["text"]):
        return False
    if re.search(r"\b\d+\s*" + UNIT_PAT + r"(?![A-Za-z])", inf["text"], re.I):
        return False
    if _vocab_ratio(inf["text"]) < 0.5:
        return False
    if sum(1 for p in inf["phrases"][1:] if PURE_VAL.match(ph_text(p))) >= 2:
        return False
    if any(len(ph_text(p)) > 48 for p in inf["phrases"]):
        return False
    return True


def find_text_tables(words, page_index, page_width, prev_layout=None):
    lines = words_to_lines(words)
    if not lines:
        return []
    infos = []
    for ln in lines:
        phs = line_phrases(ln)
        infos.append({"words": ln, "phrases": phs, "top": min(w["top"] for w in ln),
                      "bottom": max(w["bottom"] for w in ln), "text": " ".join(w["text"] for w in ln)})
    n = len(infos)
    # 1. header blocks
    blocks = []
    i = 0
    while i < n:
        if not _is_header_line(infos[i]) or not (header_roles_in(infos[i]["text"]) or
                                                 sum(1 for p in infos[i]["phrases"][1:] if KNOWN_CONTAM.match(clean_contaminant(ph_text(p))[0])) >= 2):
            i += 1
            continue
        i0 = i
        j = i
        while j < n and j - i < 16:
            inf = infos[j]
            if j > i and inf["top"] - infos[j - 1]["bottom"] > 1.2 * (inf["bottom"] - inf["top"]) + 3:
                break
            if not _is_header_line(inf):
                break
            j += 1
        # leading title-like lines (one long phrase, or set apart by a gap) are not header lines
        while j - i >= 2:
            inf = infos[i]
            h = inf["bottom"] - inf["top"]
            gap = infos[i + 1]["top"] - inf["bottom"]
            if len(inf["phrases"]) == 1 and (len(inf["text"]) > 22 or gap > 0.6 * h):
                i += 1
                continue
            break
        left = min(w["x0"] for inf in infos[i:j] for w in inf["words"])
        while i - 1 >= 0 and _is_header_line(infos[i - 1]):
            up = infos[i - 1]
            h = up["bottom"] - up["top"]
            if infos[i]["top"] - up["bottom"] > 0.6 * h:
                break
            if len(up["phrases"]) >= 2 or (len(up["text"]) <= 22 and up["words"][0]["x0"] > left + 15):
                i -= 1
                continue
            break
        roles = set()
        maxph = 0
        known = 0
        for inf in infos[i:j]:
            roles |= header_roles_in(inf["text"])
            maxph = max(maxph, len(inf["phrases"]))
            known = max(known, sum(1 for p in inf["phrases"][1:] if KNOWN_CONTAM.match(clean_contaminant(ph_text(p))[0])))
        if known >= 2 and maxph >= 3:
            roles |= {"value", "unit", "mcl"}
        if len(roles - {"name"}) >= 3 and maxph >= 3 and (not blocks or i >= blocks[-1][1]):
            blocks.append((i, j))
            i = max(j, i0 + 1)
        else:
            i = i0 + 1
    tables = []
    if prev_layout is not None:
        first = blocks[0][0] if blocks else n
        cont = []
        bounds = [b for (a, b) in prev_layout.col_x[:-1]]
        for li, inf in enumerate(infos[:first]):
            vals = sum(1 for p in inf["phrases"][1:] if PURE_VAL.match(ph_text(p)) or re.search(r"\d", ph_text(p)))
            crossing = any(p[0]["x0"] < b - 3 and max(w["x1"] for w in p) > b + 3 for p in inf["phrases"] for b in bounds)
            if not cont:
                if li > 4 or crossing or not (len(inf["phrases"]) >= 3 and vals >= 2):
                    if li > 4:
                        break
                    continue
            elif crossing and not (len(inf["phrases"]) == 1 and len(inf["text"]) < 45):
                break
            if cont and inf["top"] - cont[-1]["bottom"] > max(3.0 * (inf["bottom"] - inf["top"]), 26):
                break
            if max(len(ph_text(p)) for p in inf["phrases"]) > 60 and len(inf["phrases"]) <= 2:
                break
            cont.append(inf)
        if len(cont) >= 1:
            pT = prev_layout
            T = Table()
            T.page = page_index
            T.top = cont[0]["top"]
            T.ncols = pT.ncols
            T.col_x = pT.col_x
            T.headers = pT.headers
            T.spans = pT.spans
            T.headerless = True
            T.title = pT.title
            T.rows = segment_text_rows(cont, [(a if k else -1e6, b if k < pT.ncols - 1 else 1e6) for k, (a, b) in enumerate(pT.col_x)])
            tables.append(T)
    for bi, (i, j) in enumerate(blocks):
        hdr = infos[i:j]
        nxt = blocks[bi + 1][0] if bi + 1 < len(blocks) else n
        x_left = min(w["x0"] for inf in hdr for w in inf["words"])
        x_right = max(w["x1"] for inf in hdr for w in inf["words"])
        data = []
        k = j
        while k < nxt:
            inf = infos[k]
            h = inf["bottom"] - inf["top"]
            if inf["top"] - infos[k - 1]["bottom"] > max(3.0 * h, 26):
                break
            longest = max(len(ph_text(p)) for p in inf["phrases"])
            if longest > 60 and len(inf["phrases"]) <= 2:
                break
            if re.match(r"^\s*(\*|†|note|notes|footnote|\d\s*[.)]?\s+[A-Z][a-z]+\s+\w+\s+\w+\s+\w+)", inf["text"], re.I) and len(inf["phrases"]) <= 2 \
                    and len(inf["text"]) > 40:
                break
            data.append(inf)
            k += 1
        # drop trailing title lines of the next table (single phrase lines just above next header)
        if k == nxt and nxt < n:
            ref_top = infos[nxt]["top"]
            while data and len(data[-1]["phrases"]) <= 2 and \
                    ref_top - data[-1]["bottom"] < 2.5 * (data[-1]["bottom"] - data[-1]["top"]) + 4 and \
                    (len(data) < 2 or data[-1]["top"] - data[-2]["bottom"] >= ref_top - data[-1]["bottom"] - 0.5) and \
                    not re.search(r"\d", data[-1]["text"]) and (len(data[-1]["phrases"]) == 1 or SECTION_RE.search(data[-1]["text"])):
                ref_top = data[-1]["top"]
                data.pop()
        if not data:
            continue
        # 2. columns from whitespace rivers; grow the data region while lines fit the columns
        def columns_for(dlines):
            core = [inf for inf in dlines if len(inf["phrases"]) >= 2 and re.search(r"\d", inf["text"])] or \
                [inf for inf in dlines if len(inf["phrases"]) >= 2] or dlines
            xs0 = int(min(x_left, min(w["x0"] for inf in dlines for w in inf["words"]))) - 1
            xs1 = int(max(x_right, max(w["x1"] for inf in dlines for w in inf["words"]))) + 2
            occ = [0] * (xs1 - xs0 + 1)
            for inf in core:
                for w in inf["words"]:
                    for x in range(int(w["x0"]) - xs0, int(w["x1"]) - xs0 + 1):
                        if 0 <= x < len(occ):
                            occ[x] += 1
            hocc = [0] * len(occ)
            for inf in hdr:
                for p in inf["phrases"]:
                    for x in range(int(p[0]["x0"]) - xs0, int(max(w["x1"] for w in p)) - xs0 + 1):
                        if 0 <= x < len(hocc):
                            hocc[x] += 1
            rivers = []
            x = 0
            while x < len(occ):
                if occ[x] == 0:
                    y = x
                    while y < len(occ) and occ[y] == 0:
                        y += 1
                    if y - x >= 4 and x > 0 and y < len(occ):
                        rivers.append((x + xs0, y + xs0))
                    x = y
                else:
                    x += 1
            edges = [xs0 - 1e6]
            for a, b in rivers:
                best = None
                for cx in range(int(a), int(b) + 1):
                    hv = hocc[cx - xs0] if 0 <= cx - xs0 < len(hocc) else 0
                    key = (hv, abs(cx - (a + b) / 2))
                    if best is None or key < best[0]:
                        best = (key, cx)
                edges.append(best[1])
            edges.append(xs1 + 1e6)
            # a cut through a header phrase is false unless that phrase heads sub-columns on both sides
            hph = [(li, p[0]["x0"], max(w["x1"] for w in p)) for li, inf in enumerate(hdr) for p in inf["phrases"]]
            keep_e = [edges[0]]
            for e in edges[1:-1]:
                bad = False
                for li, px0, px1 in hph:
                    if px0 < e - 2 and px1 > e + 2:
                        left = any(l2 > li and q1 <= e + 1 and q1 > px0 - 12 for l2, q0, q1 in hph)
                        right = any(l2 > li and q0 >= e - 1 and q0 < px1 + 12 for l2, q0, q1 in hph)
                        if not (left and right):
                            bad = True
                            break
                if not bad:
                    keep_e.append(e)
            keep_e.append(edges[-1])
            edges = keep_e
            cx_ = [(edges[c], edges[c + 1]) for c in range(len(edges) - 1)]
            return [c for c in cx_ if any(c[0] <= (w["x0"] + w["x1"]) / 2 < c[1] for inf in dlines for w in inf["words"])]

        def hdr_in(a, b):
            return any(a <= (w["x0"] + w["x1"]) / 2 < b for inf in hdr for w in inf["words"])

        def merge_textonly(cols_, dlines):
            """Merge columns without header words whose cells are plain text into the left neighbour."""
            out = []
            for c in cols_:
                if len(out) >= 2 and not hdr_in(c[0], c[1]):
                    cells = [" ".join(w["text"] for w in inf["words"] if c[0] <= (w["x0"] + w["x1"]) / 2 < c[1]) for inf in dlines]
                    cells = [t for t in cells if t.strip()]
                    if cells and not any(re.search(r"\d|^(yes|no|y|n|nd|n/?a|na|none|tt)$", t.strip(), re.I) for t in cells):
                        out[-1] = (out[-1][0], c[1])
                        continue
                out.append(c)
            return out

        def crosses(inf, cols_):
            bounds = [c[1] for k, c in enumerate(cols_[:-1]) if hdr_in(c[0], c[1]) and hdr_in(cols_[k + 1][0], cols_[k + 1][1])]
            for p in inf["phrases"]:
                px0, px1 = p[0]["x0"], max(w["x1"] for w in p)
                for b in bounds:
                    if px0 < b - 3 and px1 > b + 3:
                        return True
            return False

        seed = [data[0]]
        for inf in data[1:]:
            h = inf["bottom"] - inf["top"]
            if inf["top"] - seed[-1]["bottom"] > 1.6 * h + 2 and len(seed) >= 2:
                break
            if len(inf["phrases"]) >= 3 and _vocab_ratio(inf["text"]) >= 0.6 and not re.search(r"\d\.\d", inf["text"]) \
                    and len(header_roles_in(inf["text"]) - {"name"}) >= 2:
                break
            seed.append(inf)
            if len(seed) >= 6:
                break
        col_x = merge_textonly(columns_for(seed), seed)
        accepted = list(seed)
        for inf in data[len(seed):]:
            if len(inf["phrases"]) >= 3 and _vocab_ratio(inf["text"]) >= 0.6 and not re.search(r"\d", inf["text"]) \
                    and len(header_roles_in(inf["text"]) - {"name"}) >= 2:
                break
            if crosses(inf, col_x):
                # allow a short section heading confined to the first columns
                if len(inf["phrases"]) == 1 and len(inf["text"]) < 45 and inf["phrases"][0][0]["x0"] < col_x[0][1]:
                    accepted.append(inf)
                    continue
                break
            accepted.append(inf)
        # drop trailing section-heading lines with nothing after them
        while accepted and len(accepted[-1]["phrases"]) == 1 and len(accepted) > 1 and not re.search(r"\d", accepted[-1]["text"]) \
                and accepted[-1] not in seed and \
                accepted[-1]["top"] - accepted[-2]["bottom"] > 0.8 * (accepted[-1]["bottom"] - accepted[-1]["top"]):
            accepted.pop()
        data = accepted
        col_x = merge_textonly(columns_for(data), data)
        if len(col_x) < 3:
            continue
        # merge first columns if header for col 0 is a name and col1 has no header but data looks like names
        T = Table()
        T.page = page_index
        T.top = hdr[0]["top"]
        T.ncols = len(col_x)
        T.col_x = [(max(e0, 0), min(e1, page_width)) for e0, e1 in col_x]
        def cols_over(x0, x1):
            return [c for c, (a, b) in enumerate(col_x) if min(b, x1) - max(a, x0) > 2]
        span_words = set()
        spans = []
        for li, inf in enumerate(hdr):
            for p in inf["phrases"]:
                px0, px1 = p[0]["x0"], max(w["x1"] for w in p)
                cs = cols_over(px0, px1)
                if len(cs) < 2:
                    continue
                gx0 = min(col_x[c][0] for c in cs)
                gx1 = max(col_x[c][1] for c in cs)
                below = [q for inf2 in hdr[li + 1:] for q in inf2["phrases"]
                         if gx0 <= (q[0]["x0"] + max(w["x1"] for w in q)) / 2 <= gx1]
                bcols = {c for q in below for c in cols_over(q[0]["x0"], max(w["x1"] for w in q))}
                if len(below) >= 2 and len(bcols) >= 2:
                    spans.append((ph_text(p), set(cs)))
                    for w in p:
                        span_words.add(id(w))
        heads = []
        for c in range(len(col_x)):
            ws = [w for inf in hdr for w in inf["words"] if col_x[c][0] <= (w["x0"] + w["x1"]) / 2 < col_x[c][1]
                  and id(w) not in span_words]
            pre = " ".join(t for t, cs in spans if c in cs)
            heads.append(norm_space(pre + " " + words_text(ws)))
        T.headers = heads
        T.spans = spans
        T.headerless = False
        T.title = infos[i - 1]["text"] if i > 0 and hdr[0]["top"] - infos[i - 1]["bottom"] < 30 else ""
        T.rows = segment_text_rows(data, col_x)
        tables.append(T)
    return tables


def segment_text_rows(data, col_x):
    """Turn data lines into physical rows (dict col -> (text, own, full, x0))."""
    def cols_of(inf):
        d = {}
        for w in inf["words"]:
            cx = (w["x0"] + w["x1"]) / 2
            for ci, (a, b) in enumerate(col_x):
                if a <= cx < b:
                    d.setdefault(ci, []).append(w)
                    break
        return d
    lines = []
    for inf in data:
        d = cols_of(inf)
        lines.append({"cols": d, "top": inf["top"], "bottom": inf["bottom"], "inf": inf})
    if not lines:
        return []
    # gap-based blocks
    gaps = [lines[t + 1]["top"] - lines[t]["bottom"] for t in range(len(lines) - 1)]
    hs = sorted(l["bottom"] - l["top"] for l in lines)
    hmed = hs[len(hs) // 2]
    blocks = [[lines[0]]]
    if gaps:
        gmin = max(0.0, sorted(gaps)[max(0, len(gaps) // 5)])
        thr = gmin + 0.45 * hmed
        bimodal = max(gaps) > thr
    else:
        bimodal = False
    for t in range(1, len(lines)):
        if bimodal and gaps[t - 1] > thr:
            blocks.append([lines[t]])
        else:
            blocks[-1].append(lines[t])
    rows = []

    def emit(group):
        row = {}
        for ci in range(len(col_x)):
            ws = [w for l in group for w in l["cols"].get(ci, [])]
            txt = words_text(ws) if ws else ""
            x0 = min((w["x0"] for w in ws), default=None)
            row[ci] = (txt, True, False, x0)
        rows.append(row)

    def is_cont(prev_name, name):
        if not prev_name:
            return False
        if re.match(r"^[a-z(\[&]", name) or re.search(r"[-,/&(]$|\b(and|or|of|as|in|the|total)$", prev_name, re.I):
            return True
        return False

    def vtext(l, ci):
        return " ".join(w["text"] for w in sorted(l["cols"].get(ci, []), key=lambda w: w["x0"]))

    def is_valuey(t):
        t = t.strip()
        return bool(t) and len(t) <= 16 and bool(re.search(r"\d|^(nd|n/?a|na|none|yes|no|y|n|tt|absent|present)\b", t, re.I))

    for blk in blocks:
        fulls = []
        for t, l in enumerate(blk):
            vals = [ci for ci in l["cols"] if ci != 0 and is_valuey(vtext(l, ci))]
            cont = bool(vals) and t > 0 and any(re.search(r"[-\u2013(,/]$|\bto$", vtext(blk[t - 1], ci).strip()) for ci in vals)
            name = vtext(l, 0)
            last_name = next((vtext(blk[u], 0) for u in range(t - 1, -1, -1) if vtext(blk[u], 0)), "")
            if name and t > 0 and is_cont(last_name, name):
                cont = True
            if not name and t > 0 and not cont:
                # value line inside a multi-line name block (vertically centred cells)
                prev_name = next((vtext(blk[u], 0) for u in range(t - 1, -1, -1) if vtext(blk[u], 0)), "")
                nxt_name = next((vtext(blk[u], 0) for u in range(t + 1, len(blk)) if vtext(blk[u], 0)), "")
                if prev_name and nxt_name and is_cont(prev_name, nxt_name):
                    cont = True
            full = (not cont) and ((name and len(vals) >= 1) or len(vals) >= 3)
            fulls.append(full)
        idx = [t for t, f in enumerate(fulls) if f]
        if len(idx) <= 1:
            emit(blk)
            continue
        groups = {t: [] for t in idx}
        for t, l in enumerate(blk):
            if fulls[t]:
                groups[t].append(l)
                continue
            cy = (l["top"] + l["bottom"]) / 2
            best = None
            for f in idx:
                fl = blk[f]
                d = abs(cy - (fl["top"] + fl["bottom"]) / 2)
                key = (d, 0 if f < t else 1)
                if best is None or key < best[0]:
                    best = (key, f)
            groups[best[1]].append(l)
        for f in idx:
            emit(sorted(groups[f], key=lambda l: l["top"]))
    return rows


# ----------------------------------------------------------------------------
# main

def _grid_has_header(cells, words):
    try:
        g = Grid(cells, words, 0)
    except Exception:
        return True
    for r in range(g.nrows):
        cs = row_cells(g, r)
        n = sum(1 for c in cs if c.text.strip() and is_header_text(c.text))
        if n >= 2 and not any(PURE_VAL.match(c.text.strip()) for c in cs[1:] if c.text.strip()):
            return True
    return False


def merge_fragments(keep, ws):
    """Join header-less ruled fragments (e.g. shaded zebra rows) to the aligned table above them."""
    if len(keep) < 2:
        return keep
    def xb(cells):
        return cluster_vals([c[0] for c in cells] + [c[2] for c in cells])
    def words_in(bb):
        return [w for w in ws if bb[0] - 1 <= (w["x0"] + w["x1"]) / 2 <= bb[2] + 1 and bb[1] - 1 <= (w["top"] + w["bottom"]) / 2 <= bb[3] + 1]
    out = [keep[0]]
    for bb, cells in keep[1:]:
        pbb, pcells = out[-1]
        gap = bb[1] - pbb[3]
        merged = False
        if -2 <= gap <= 90:
            xa, xbb = xb(pcells), xb(cells)
            aligned = len(xbb) >= 3 and sum(1 for x in xbb if any(abs(x - y) <= 3 for y in xa)) >= 0.8 * len(xbb)
            band = (min(bb[0], pbb[0]), pbb[3], max(bb[2], pbb[2]), bb[1])
            bw = words_in(band)
            fits = not any(w["x0"] < x - 2 and w["x1"] > x + 2 for w in bw for x in xa[1:-1])
            if aligned and fits and not _grid_has_header(cells, words_in(bb)):
                extra = []
                if gap > 3 and words_in(band):
                    for a, b in zip(xa, xa[1:]):
                        extra.append((a, pbb[3], b, bb[1]))
                nbb = (min(bb[0], pbb[0]), pbb[1], max(bb[2], pbb[2]), bb[3])
                out[-1] = (nbb, pcells + extra + cells)
                merged = True
        if not merged:
            out.append((bb, cells))
    return out


def _upright_ratio(page):
    chs = page.chars
    if len(chs) < 30:
        return 1.0
    def ok(c):
        m = c.get("matrix")
        return c.get("upright", True) and (m is None or m[0] > 0)
    return sum(1 for c in chs if ok(c)) / float(len(chs))


def fix_rotation(pdf_path):
    """Return a path to a copy whose sideways pages are turned upright (or the original path)."""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            bad = [i for i, p in enumerate(pdf.pages) if _upright_ratio(p) < 0.5]
    except Exception:
        return pdf_path
    if not bad:
        return pdf_path
    try:
        import tempfile
        from pypdf import PdfReader, PdfWriter
        from pypdf.generic import NameObject, NumberObject
        tmpdir = tempfile.mkdtemp(prefix="ccr_")
        TMP_DIRS.append(tmpdir)
        chosen = {}
        for delta in (90, 270):
            todo = [i for i in bad if i not in chosen]
            if not todo:
                break
            r = PdfReader(pdf_path)
            w = PdfWriter()
            for i, p in enumerate(r.pages):
                if i in todo:
                    rot = int(p.get("/Rotate", 0) or 0)
                    p[NameObject("/Rotate")] = NumberObject((rot + delta) % 360)
                w.add_page(p)
            out = os.path.join(tmpdir, "try%d.pdf" % delta)
            with open(out, "wb") as fh:
                w.write(fh)
            with pdfplumber.open(out) as pdf:
                for i in todo:
                    if _upright_ratio(pdf.pages[i]) >= 0.8:
                        chosen[i] = delta
        if not chosen:
            return pdf_path
        r = PdfReader(pdf_path)
        w = PdfWriter()
        for i, p in enumerate(r.pages):
            if i in chosen:
                rot = int(p.get("/Rotate", 0) or 0)
                p[NameObject("/Rotate")] = NumberObject((rot + chosen[i]) % 360)
            w.add_page(p)
        out = os.path.join(tmpdir, "fixed.pdf")
        with open(out, "wb") as fh:
            w.write(fh)
        return out
    except Exception as e:
        sys.stderr.write("rotation fix failed: %r\n" % (e,))
        return pdf_path


def extract(pdf_path):
    records = []
    orig_path = pdf_path
    pdf_path = fix_rotation(pdf_path)
    with pdfplumber.open(pdf_path) as pdf:
        page_texts = []
        page_words = []
        for page in pdf.pages:
            if time.time() - T0 > TIME_BUDGET * 0.6:
                page_words.append([])
                page_texts.append("")
                continue
            try:
                ws = get_words(page)
            except Exception:
                ws = []
            page_words.append(ws)
            page_texts.append(" ".join(" ".join(w["text"] for w in ln) for ln in words_to_lines(ws)))
        try:
            import subprocess
            out = subprocess.run(["pdftotext", "-layout", orig_path, "-"], capture_output=True, timeout=60).stdout.decode("utf-8", "replace")
            ptexts = [norm_space(clean_chars(t)) for t in out.split("\f")]
        except Exception:
            ptexts = []
        ids = find_pwsids(ptexts)
        src_texts = ptexts
        if not ids:
            ids = find_pwsids(page_texts)
            src_texts = page_texts
        ids = [x + (len(src_texts[x[0]]),) for x in ids]
        default_sys = system_for(ids, 0, 0.0)
        prev = None
        prev_text_T, prev_text_roles = None, None
        for pi, page in enumerate(pdf.pages):
            ws = page_words[pi]
            if not ws:
                continue
            if time.time() - T0 > TIME_BUDGET:
                sys.stderr.write("time budget exhausted at page %d\n" % pi)
                break
            if not re.search(r"\b(mcl|mclg|mrdl|action level|range|detected|violation|units?|ppm|ppb|ntu|pci/l)\b", page_texts[pi], re.I):
                continue
            try:
                found = [] if os.environ.get("CCR_NO_LATTICE") else page.find_tables()
            except Exception:
                found = []
            tbls = []
            for t in found:
                cells = [c for c in t.cells if c]
                if len(cells) < 4:
                    continue
                tbls.append((t.bbox, cells))
            # drop tables nested inside other tables
            keep = []
            for i, (bb, cells) in enumerate(tbls):
                nested = False
                for j, (bb2, cells2) in enumerate(tbls):
                    if i != j and bb2[0] <= bb[0] + 1 and bb2[1] <= bb[1] + 1 and bb2[2] >= bb[2] - 1 and bb2[3] >= bb[3] - 1 \
                            and len(cells2) >= len(cells) and (bb2 != bb or j < i):
                        nested = True
                        break
                if not nested:
                    keep.append((bb, cells))
            keep.sort(key=lambda x: x[0][1])
            try:
                keep = merge_fragments(keep, ws)
            except Exception as e:
                sys.stderr.write("merge error: %r\n" % (e,))
            used_boxes = []
            for bb, cells in keep:
                wsin = [w for w in ws if bb[0] - 1 <= (w["x0"] + w["x1"]) / 2 <= bb[2] + 1 and bb[1] - 1 <= (w["top"] + w["bottom"]) / 2 <= bb[3] + 1]
                def in_other(w):
                    cx, cy = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
                    return any(b2[0] - 1 <= cx <= b2[2] + 1 and b2[1] - 1 <= cy <= b2[3] + 1 for b2, _ in keep)
                above = [w for w in ws if w["bottom"] <= bb[1] + 1 and w["bottom"] > bb[1] - 40 and
                         w["x1"] > bb[0] - 5 and w["x0"] < bb[2] + 5 and not in_other(w)]
                cap = ""
                if above:
                    alines = words_to_lines(above)
                    cap = " ".join(w["text"] for w in alines[-1])
                try:
                    grid = Grid(cells, wsin, pi)
                    if grid.nrows < 1 or grid.ncols < 2:
                        continue
                    gtables = grid_to_tables(grid, cap)
                except Exception as e:
                    sys.stderr.write("grid error: %r\n" % (e,))
                    continue
                for T in gtables:
                    if T.headerless:
                        aligned = False
                        if prev is not None and prev[0].ncols == T.ncols and prev[0].page in (pi, pi - 1) and T.col_x:
                            diffs = [abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b in zip(prev[0].col_x, T.col_x)]
                            aligned = sum(diffs) / len(diffs) < 16
                        if aligned:
                            T.headers = prev[0].headers
                            T.spans = prev[0].spans
                            T.title = T.title or prev[0].title
                            roles = prev[1]
                        else:
                            continue
                    else:
                        roles = assign_roles(T)
                    try:
                        recs = make_records(T, roles, system_for(ids, pi, T.top / float(page.height)))
                    except Exception as e:
                        sys.stderr.write("record error: %r\n" % (e,))
                        recs = []
                    if recs:
                        prev = (T, roles)
                        if bb not in used_boxes:
                            used_boxes.append(bb)
                    records.extend(recs)
            # unruled tables among the remaining words
            rest = [w for w in ws if not any(bb[0] - 1 <= (w["x0"] + w["x1"]) / 2 <= bb[2] + 1 and
                                             bb[1] - 1 <= (w["top"] + w["bottom"]) / 2 <= bb[3] + 1 for bb in used_boxes)]
            try:
                prev_text = prev_text_T if (prev_text_T is not None and prev_text_T.page == pi - 1) else None
                ttables = find_text_tables(rest, pi, float(page.width), prev_text)
            except Exception as e:
                sys.stderr.write("text table error: %r\n" % (e,))
                ttables = []
            for T in ttables:
                try:
                    roles = prev_text_roles if (T.headerless and prev_text_roles) else assign_roles(T)
                    recs = make_records(T, roles, system_for(ids, pi, T.top / float(page.height)))
                except Exception as e:
                    sys.stderr.write("text record error: %r\n" % (e,))
                    continue
                if recs:
                    prev = (T, roles)
                    prev_text_T, prev_text_roles = T, roles
                    T.page = pi
                records.extend(recs)
    return records


def main(pdf_path, out_path):
    try:
        records = extract(pdf_path)
    except Exception as e:  # never fail without output
        sys.stderr.write("extract error: %r\n" % (e,))
        records = []
    finally:
        for d in TMP_DIRS:
            try:
                import shutil
                shutil.rmtree(d, ignore_errors=True)
            except Exception:
                pass
    with open(out_path, "w") as fh:
        json.dump({"records": records}, fh, indent=1, ensure_ascii=False)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
