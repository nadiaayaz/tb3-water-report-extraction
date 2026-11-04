#!/usr/bin/env python3
"""Extract detected-contaminant result tables from a Consumer Confidence Report PDF.

    python3 extract.py <report.pdf> <output.json>

Output follows FORMAT.md. Pipeline:
  * words from pdfplumber, with footnote superscripts (smaller / raised glyphs) removed;
  * ruled tables: pdfplumber's line/rect lattice gives the geometry only - logical columns come
    from the cell edges of the data rows (header shading rectangles are ignored) and row bands
    from the cells of the value columns, so merged name / MCLG cells are copied to every row
    they span;
  * unruled tables: header lines are found by vocabulary, columns from whitespace gaps of the
    tabular lines, rows from horizontal rules or vertical gaps;
  * each column is classified from its heading (name, level, range, low/high, unit, MCL, MCLG,
    date, violation/compliance, count, advisory, source, ...), then every row becomes a record;
    column groups, transposed tables, per-plant tables and labelled values in one cell are
    expanded as FORMAT.md describes.
A watchdog writes whatever has been extracted if a report runs long.
"""
import json
import re
import sys

import pdfplumber

# ----------------------------------------------------------------------------
# text normalisation
# ----------------------------------------------------------------------------

PUA = {
    "": "µ", "": "°", "": "≤", "": "≥",
    "": "-", "": "±", "": "<", "": ">", "": "=",
    "": "✓", "": "✗", "": "√", "": "×",
    "": "§", "": "*", "": "•",
}
LIG = {"\u03bc": "\u00b5", "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
       " ": " ", " ": " ", " ": " ", " ": " ", "​": ""}


def norm(s):
    if not s:
        return ""
    out = []
    for ch in s:
        if ch in PUA:
            out.append(PUA[ch])
        elif ch in LIG:
            out.append(LIG[ch])
        else:
            out.append(ch)
    return "".join(out)


def squash(s):
    return re.sub(r"\s+", " ", s or "").strip()


# ----------------------------------------------------------------------------
# words
# ----------------------------------------------------------------------------

FOOT_TOKEN = re.compile(r"^(?:[\d]{1,2}(?:,\d{1,2})*|[*†‡§#]+|[a-z])$")
SUPER_CHARS = "¹²³⁰⁴⁵⁶⁷⁸⁹"


class Word:
    __slots__ = ("text", "x0", "x1", "top", "bottom", "size", "sup")

    def __init__(self, text, x0, x1, top, bottom, size):
        self.text, self.x0, self.x1, self.top, self.bottom, self.size = text, x0, x1, top, bottom, size
        self.sup = False

    @property
    def cx(self):
        return (self.x0 + self.x1) / 2

    @property
    def cy(self):
        return (self.top + self.bottom) / 2

    def __repr__(self):
        return "W(%r %.0f,%.0f)" % (self.text, self.x0, self.top)


def underline_segments(page):
    segs = []
    try:
        for r in list(page.rects) + list(page.lines):
            if abs(r["bottom"] - r["top"]) <= 1.5 and 1.5 <= r["x1"] - r["x0"] <= 14:
                segs.append((r["x0"], r["x1"], r["top"]))
    except Exception:
        pass
    return segs


WINGDINGS = {"\u00fc": "\u2713", "\u00fb": "\u2717", "\u00fe": "\u2713", "\u00fd": "\u2717",
             "\uf0fc": "\u2713", "\uf0fb": "\u2717", "\uf0fe": "\u2713", "\uf0fd": "\u2717"}


def page_words(page):
    ul = underline_segments(page)
    try:
        seen = set()
        dup = 0
        chars = page.chars
        for c in chars:
            key = (c.get("text"), int(c["x0"]), int(c["top"]))
            if key in seen:
                dup += 1
            seen.add(key)
        if chars and dup > 0.05 * len(chars):
            page = page.dedupe_chars(tolerance=1)
    except Exception:
        pass
    try:
        raw = page.extract_words(x_tolerance=2, y_tolerance=2.5, keep_blank_chars=False,
                                 use_text_flow=False, extra_attrs=["size"], return_chars=True)
    except Exception:
        try:
            raw = page.extract_words(x_tolerance=2, y_tolerance=2.5, extra_attrs=["size"])
        except Exception:
            raw = page.extract_words()
    words = []
    for w in raw:
        chars = w.get("chars") or []
        text = w["text"]
        if chars and any("wingding" in (c.get("fontname") or "").lower() for c in chars):
            text = "".join(WINGDINGS.get(c["text"], c["text"]) if "wingding" in (c.get("fontname") or "").lower()
                           else c["text"] for c in chars)
        text = norm(text)
        if chars:
            # strip trailing raised / smaller footnote characters inside one word
            main = max(c.get("size", 0) for c in chars)
            base = max(c["bottom"] for c in chars)
            keep = len(chars)
            while keep > 1:
                c = chars[keep - 1]
                ct = norm(c["text"])
                small = c.get("size", main) < main * 0.8
                raised = c["bottom"] < base - main * 0.25
                if (small or raised) and re.match(r"^[\d*†‡,]$", ct):
                    keep -= 1
                else:
                    break
            if keep < len(chars):
                text = norm("".join(c["text"] for c in chars[:keep]))
            if ul and ("<" in text or ">" in text):
                tl = list(text)
                for c in chars[:keep]:
                    if c["text"] in "<>":
                        for (sx0, sx1, sy) in ul:
                            if sx0 <= c["x0"] + 1.5 and sx1 >= c["x1"] - 1.5 and \
                                    c["top"] + 0.55 * (c["bottom"] - c["top"]) <= sy <= c["bottom"] + 3:
                                k = text.find(c["text"])
                                if k >= 0:
                                    tl[k] = "\u2264" if c["text"] == "<" else "\u2265"
                                    text = "".join(tl)
                                break
        text = "".join(ch for ch in text if ch not in SUPER_CHARS) or text
        if not text.strip():
            continue
        words.append(Word(text, w["x0"], w["x1"], w["top"], w["bottom"], w.get("size", 0) or 0))
    # superscript words (footnote markers printed small / raised right after a word)
    buckets = {}
    for w in words:
        buckets.setdefault(int(w.top // 8), []).append(w)
    for w in words:
        if not FOOT_TOKEN.match(w.text):
            continue
        b = int(w.top // 8)
        near = buckets.get(b - 1, []) + buckets.get(b, []) + buckets.get(b + 1, [])
        for p in near:
            if p is w or p.sup:
                continue
            gap = w.x0 - p.x1
            if gap < -0.8 or gap > 2.5:
                continue
            ov = min(w.bottom, p.bottom) - max(w.top, p.top)
            if ov < 0.4 * min(p.bottom - p.top, w.bottom - w.top):
                continue
            if p.size >= w.size * 1.18 and w.bottom <= p.bottom + 0.5:
                w.sup = True
                break
            if abs(p.size - w.size) < 0.5 and w.top < p.top - 2 and re.match(r"^\d{1,2}$", w.text):
                w.sup = True
                break
    return [w for w in words if not w.sup]


def lines_of(words, tol=None):
    """Group words into text lines (list of lists sorted by x)."""
    ws = sorted(words, key=lambda w: (w.top, w.x0))
    lines = []
    for w in ws:
        placed = False
        for ln in lines[-4:]:
            ref = ln[0]
            h = max(1.0, min(ref.bottom - ref.top, w.bottom - w.top))
            t = tol if tol is not None else h * 0.55
            if abs(w.cy - ref.cy) <= t:
                ln.append(w)
                placed = True
                break
        if not placed:
            lines.append([w])
    for ln in lines:
        ln.sort(key=lambda w: w.x0)
    lines.sort(key=lambda ln: min(w.top for w in ln))
    return lines


def join_words(words):
    """Text of a cell: lines top-down, words left-right, with hyphen handling."""
    if not words:
        return ""
    out = ""
    for ln in lines_of(words):
        t = " ".join(w.text for w in ln)
        if not out:
            out = t
        elif re.search(r"[A-Za-z]{4}-$", out) and re.match(r"^[a-z]", t):
            out = out[:-1] + t
        elif re.search(r"\S[-–—/]$", out):
            out = out + t
        else:
            out = out + " " + t
    return squash(out)


# ----------------------------------------------------------------------------
# value parsing helpers
# ----------------------------------------------------------------------------

UNIT_CORE = (r"(?:ppm|ppb|ppt|ppq|ppth|mg/l|ug/l|µg/l|μg/l|ng/l|pg/l|pci/l|mrem/yr|mrem/year|mrem|"
             r"millirems?/yr|ntus?|mfl|cfu(?:/\s?(?:100\s?)?ml)?|mpn(?:/\s?(?:100\s?)?ml)?|gpg|grains?/gal(?:lon)?|"
             r"grains per gallon|µs/cm|us/cm|µmhos/cm|umhos/cm|μs/cm|ms/cm|tcu|t\.o\.n\.|ton|"
             r"s\.u\.|std\.?\s?units|su|units|unit|color units|°[cf]|deg\s?[cf]|pcu|mg/kg|g/l)")
UNIT_RE = re.compile(r"^(?:" + UNIT_CORE + r")(?:\s+as\s+[A-Za-z0-9]+)?$", re.I)
UNIT_TAIL = re.compile(r"\s*\b(" + UNIT_CORE + r"(?:\s+as\s+[A-Za-z0-9]+)?)\s*$", re.I)
UNIT_PAREN = re.compile(r"\(\s*(" + UNIT_CORE + r"(?:\s+as\s+[A-Za-z0-9]+)?)\s*\)", re.I)
UNIT_ANY = re.compile(r"(?<![A-Za-z])(" + UNIT_CORE + r")(?![A-Za-z])", re.I)

NUM = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)"
VAL = r"(?:[<>≤≥]\s*=?\s*)?" + r"-?" + NUM + r"%?|ND|N\.D\.|nd|BDL|Not Detected|non-detect|<\s*MRL|<\s*DL"
VAL_RE = re.compile(r"^(?:" + VAL + r")$", re.I)
RANGE_RE = re.compile(r"^\(?\s*(" + VAL + r")\s*(?:-|–|—|to|~)\s*(" + VAL + r")\s*\)?$", re.I)
NULLS = {"", "n/a", "na", "n.a.", "n.a", "none", "no range", "-", "--", "---", "–", "—", "nr ",
         "not applicable", "n/a.", "none.", "na*", "n/a*"}
FOOT_TRAIL = re.compile(r"(?:\s*[*†‡§]+|\s*\[\d{1,2}\])+$")


def strip_marks(s):
    s = squash(s)
    if re.match(r"^\[\s*[\d.]+\s*\]$", s):
        return s
    s = FOOT_TRAIL.sub("", s)
    s = re.sub(r"^[*†‡§]+\s*", "", s)
    return s.strip()


def is_null(s):
    t = strip_marks(s).lower().strip(" .")
    if t in NULLS or t + "." in NULLS:
        return True
    if re.match(r"^(see|refer to)\b.*\bnote", t) or re.match(r"^see\s*(note|footnote|below|above)", t):
        return True
    if re.match(r"^\(?see note\s*\d*\)?$", t):
        return True
    return False


def to_num(s):
    t = s.replace(",", "")
    try:
        if re.match(r"^-?\d+$", t):
            return int(t)
        return float(t)
    except ValueError:
        return None


def parse_scalar(s):
    """Return (value, unit) from a value cell."""
    s = strip_marks(s)
    s = re.sub(r"^(" + VAL + r")\s*\(\d\)$", r"\1", s, flags=re.I)
    if is_null(s):
        return None, None
    unit = None
    m = UNIT_TAIL.search(s)
    if m and m.start() > 0:
        rest = s[:m.start()].strip()
        if VAL_RE.match(rest):
            unit = m.group(1)
            s = rest
    if re.match(r"^-?" + NUM + r"$", s):
        v = to_num(s)
        if v is not None:
            return v, unit
    return s, unit


def parse_range(s):
    """Return (low, high, unit) or None."""
    s = strip_marks(s)
    if is_null(s):
        return None
    unit = None
    m = UNIT_TAIL.search(s)
    if m and m.start() > 0:
        unit = m.group(1)
        s = s[:m.start()].strip()
    s = re.sub(r"^range\s*[:=]?\s*", "", s, flags=re.I)
    m = RANGE_RE.match(s)
    if m:
        lo, hi = m.group(1), m.group(2)
        return conv(lo), conv(hi), unit
    if VAL_RE.match(s):
        v = conv(s)
        return v, v, unit
    return None


def conv(v):
    v = squash(v)
    if re.match(r"^-?" + NUM + r"$", v):
        n = to_num(v)
        if n is not None:
            return n
    return v


# ----------------------------------------------------------------------------
# column classification
# ----------------------------------------------------------------------------

def classify(h):
    h = squash(h).lower()
    h = h.replace("’", "'")
    if not h:
        return None
    if re.match(r"^(?:regulated |unregulated |detected |secondary )?(?:contaminants?|parameters?|substances?|analytes?|"
                r"constituents?|compounds?|chemicals?|characteristics?)\b", h) and \
            not re.search(r"level|detected|result|average|range|amount|value|source|violation|type$|category|"
                          r"class$|group$", h[12:]):
        return "name"
    if re.search(r"likely source|typical source|source of|sources? of contam|major source|potential source|"
                 r"probable source|^sources?$|^likely$|origin of|where it comes|common source", h):
        return "source"
    if re.search(r"health effect|health concern|potential health|health risk|potential effect", h):
        return "health"
    if re.search(r"\bunits? of meas|^units?$|^unit of|^measure|^unit\b|^units\b|^\(?units?\)?$", h):
        return "unit"
    if re.search(r"\blocations?\b|sampling points?|sample points?|sampling sites?|sample sites?|"
                 r"entry points?$|^sites?$|^wells?\b|^plants?\b|^facility|^treatment plant|^zone\b|^area\b", h) and \
            not re.search(r"number|#|\bno\.|level|result|detect|average", h):
        return "location"
    if re.search(r"samples? (?:taken|collected|tested|analy[sz]ed|required)|^samples?$", h):
        return "count"
    if re.search(r"comparison value|health advisory|health[- ]based|\bhrl\b|notification level|"
                 r"advisory level|reference level|\bhbv\b|response level|\bmrl\b|reporting limit|"
                 r"detection limit|method reporting|\bdlr\b|trigger level|\bnl\b", h):
        return "advisory"
    if re.search(r"(?:\bnumber\b|\bno\.|#|\bnum\b).{0,40}(sites?|homes?|samples?|quarters?|results?|"
                 r"exceed|above|over|positive|detection|location|houses|taps?|months?)|"
                 r"sites? (?:over|above|exceed)|homes? with|out of compliance|samples? (?:above|over|exceed)|"
                 r"results? (?:greater|over|above|exceed)|exceeding|# of|number of", h):
        if re.search(r"violation|y/n|yes/no", h) and not re.search(r"number|#|\bno\.|\bnum\b", h):
            return "violation"
        return "count"
    if re.search(r"in compliance|meets? (?:the )?standards?|met standards?|^pass|compliance\s*\(?y|"
                 r"complies|compliant|\bmet\s*\??$|\bmeets?\b|^compliance\s*(?:\?|\(|$)|within limits", h):
        return "compliance"
    if re.match(r"^(?:contaminant |substance )?(?:category|type|class|group)\b", h) and \
            not re.search(r"violation|level|result", h):
        return "category"
    if re.search(r"violat|exceed|mcl exceeded|y\s*/\s*n|yes\s*/\s*no|\(y/n\)", h):
        return "violation"
    if re.search(r"90\s*(?:th)?\s*%|90th|percentile", h) and not re.search(r"action level\s*\(?al\)?$", h):
        return "value"
    if re.search(r"meeting (?:the )?(?:limit|standard|requirement)|% of (?:monthly )?samples|percent(?:age)? of samples|"
                 r"samples (?:below|less than|meeting|under)|single measurement", h):
        return "value"
    if re.search(r"mclg|\bphg\b|mrdlg|public health goal|ideal goal|\bgoal\b|\bmldg\b|\bmrdl goal|"
                 r"health goal", h):
        return "mclg"
    if re.search(r"removal required|required removal|\brequired\b|minimum (?:disinfectant )?residual|min\.? residual|"
                 r"minimum required|\bmcl\b|\bsmcl\b|\bmcls\b|\bal\b|action level|\bmrdl\b|"
                 r"highest level allowed|level allowed|allowable|allowed|regulatory limit|\blimit\b|"
                 r"treatment technique|^tt$|\btt\b|^standard|state standard|epa standard|"
                 r"maximum contaminant level|max\.? (?:allowed|level allowed)|secondary standard", h):
        return "mcl"
    if re.search(r"range or level|level or range", h):
        return "range_or_level"
    mr = re.search(r"\brange\b", h)
    if mr and re.search(r"level|average|\bavg|result|detected|value|amount|concentration", h[:mr.start()]):
        return "value"
    if re.search(r"low\s*[-–/]\s*high|min\.?\s*[-–/]\s*max|minimum\s*[-–/]\s*maximum|"
                 r"lowest\s*[-–/]\s*highest", h):
        return "range"
    if re.search(r"^(?:range\s*(?:of\s*\w+\s*)?[-:]?\s*)?(?:low|lowest|min|minimum)\b", h) and \
            not re.search(r"residual", h):
        return "low"
    if re.search(r"^(?:range\s*(?:of\s*\w+\s*)?[-:]?\s*)?(?:high|highest|max|maximum)\b", h):
        return "high"
    if re.search(r"\brange\b", h):
        return "range"
    if re.search(r"\bdates?\b|\byears?\b|sampled|collect|tested|monitoring period|sample period", h) and \
            not re.search(r"level|result|detect|average|value|amount", h):
        return "date"
    if re.search(r"contaminant|parameter|substance|analyte|constituent|compound|chemical|^item|"
                 r"characteristic|^element|inorganic|organic|microbiolog|radiolog|disinfect|^name", h) and \
            not re.search(r"level|detected|amount|result|average|value", h):
        return "name"
    return "value"


def classify_content(texts):
    vals = [strip_marks(t) for t in texts if t and not is_null(t)]
    if not vals:
        return None
    low = [v.lower() for v in vals]
    if all(re.match(r"^(yes|no|y|n|✓|✗|✔|✘|x)$", v) for v in low):
        return "violation"
    if all(UNIT_RE.match(v) for v in vals):
        return "unit"
    if all(re.match(r"^(\d{1,2}/)?(\d{1,2}/)?\d{2,4}(\s*[-–]\s*(\d{1,2}/)?(\d{1,2}/)?\d{2,4})?$", v)
           and re.search(r"/|^(19|20)\d\d$", v) for v in vals):
        return "date"
    return None


HEADER_KW = re.compile(
    r"contaminant|parameter|substance|analyte|\bmclg?\b|mrdlg?|\bsmcl\b|\bphg\b|\bunits?\b|\brange\b|"
    r"\blevel|detect|violat|likely|source|\bsample|\bdates?\b|\byear|average|highest|lowest|result|"
    r"typical|\bgoal|action|limit|allowed|percentile|complian|exceed|amount|regulat|ideal|measure|"
    r"health|\bal\b|\btt\b", re.I)


PLACE_COL = re.compile(r"^(?:well|site|plant|entry point|ep|zone|area|station|tap|source|location|treatment plant|"
                       r"reservoir|tank|intake|system)\b", re.I)
PLACE_WORD = re.compile(r"^(?:wells?|sites?|plants?|points?|zones?|stations?|tanks?|sources?|areas?|ep|taps?|"
                        r"locations?|loc\.?|no\.?|#|systems?|districts?|reservoirs?|intakes?|units?)$", re.I)


def count_numeric(words):
    """Numeric tokens, ignoring numbers that belong to labels such as "Well 1" or "Entry Point 2"."""
    n = 0
    for w in words:
        if not numericish(w.text):
            continue
        if re.match(r"^#?\d{1,2}[A-Za-z]?$", w.text) and any(
                PLACE_WORD.match(p.text) and 0 <= w.x0 - p.x1 < 8 and abs(p.cy - w.cy) < 3 for p in words):
            continue
        n += 1
    return n


def header_score(text):
    return len(set(m.group(0).lower() for m in HEADER_KW.finditer(text)))


def numericish(t):
    t = strip_marks(t)
    return bool(re.match(r"^[<>≤≥]?\s*\d[\d,]*\.?\d*%?$", t) or
                re.match(r"^(nd|n/d|bdl)$", t, re.I) or RANGE_RE.match(t))


# ----------------------------------------------------------------------------
# grid model shared by lattice and text tables
# ----------------------------------------------------------------------------

class Grid:
    """cols: list of dicts {x0,x1,own,groups}; rows: list of {col: text}; title: str"""

    def __init__(self):
        self.cols = []
        self.rows = []
        self.title = ""
        self.page = 0
        self.top = 0
        self.bbox = None


def merge_close(vals, tol):
    out = []
    for v in sorted(vals):
        if out and v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [sum(g) / len(g) for g in out]


def bands_from_cells(ys, cells, tol=1.5):
    good = []
    for y in ys:
        crossed = False
        for c in cells:
            if c[1] < y - tol and c[3] > y + tol:
                crossed = True
                break
        if not crossed:
            good.append(y)
    return [(good[i], good[i + 1]) for i in range(len(good) - 1) if good[i + 1] - good[i] > 1.0]


def in_box(w, b, pad=0.5):
    return b[0] - pad <= w.cx <= b[2] + pad and b[1] - pad <= w.cy <= b[3] + pad


def overlap(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


def col_of(x, cols):
    for i, c in enumerate(cols):
        if c["x0"] - 0.5 <= x <= c["x1"] + 0.5:
            return i
    best, bd = None, 1e9
    for i, c in enumerate(cols):
        d = min(abs(x - c["x0"]), abs(x - c["x1"]))
        if d < bd:
            best, bd = i, d
    return best


def sticky_cols(words, cols, gap=3.5, cell_of=None):
    """Column per word by centre, but words joined by a normal space stay in one column."""
    res = {}
    for ln in lines_of(words):
        prev = None
        for w in ln:
            ci = col_of(w.cx, cols)
            if prev is not None and 0 <= w.x0 - prev.x1 < gap and res[id(prev)] != ci and \
                    not (numtok(prev.text) and numtok(w.text)) and \
                    (cell_of is None or cell_of(prev) == cell_of(w)):
                ci = res[id(prev)]
            res[id(w)] = ci
            prev = w
    return res


def lattice_grids(page, words, page_no):
    try:
        found = page.find_tables({"vertical_strategy": "lines", "horizontal_strategy": "lines",
                                  "snap_tolerance": 3, "join_tolerance": 3, "intersection_tolerance": 3})
    except Exception:
        return []
    cands = []
    for t in found:
        nrows = len(t.rows)
        ncols = max((len(r.cells) for r in t.rows), default=0)
        if nrows < 2 or ncols < 3:
            continue
        cands.append(t)
    keep = []
    for t in cands:
        inside = False
        for o in cands:
            if o is t:
                continue
            ob, tb = o.bbox, t.bbox
            if ob[0] <= tb[0] + 2 and ob[1] <= tb[1] + 2 and ob[2] >= tb[2] - 2 and ob[3] >= tb[3] - 2 and \
                    (ob[2] - ob[0]) * (ob[3] - ob[1]) > (tb[2] - tb[0]) * (tb[3] - tb[1]) + 1:
                inside = True
                break
        if not inside:
            keep.append(t)
    grids = []
    for t in keep:
        g = grid_from_cells(t, words, page_no)
        if g:
            grids.extend(split_inner_headers(g))
    return grids


def split_inner_headers(g):
    """A header row repeated inside a grid starts a new table with its own column meanings."""
    out = []
    cur = g
    i = 0
    rows = g.rows
    start = 0
    segs = []
    while i < len(rows):
        r = rows[i]
        texts = [t for t in r["cells"].values() if t]
        if i > 0 and texts and len(texts) >= 3 and header_score(" ".join(texts)) >= 3 and \
                sum(1 for t in texts if valueish(t)) <= 1 and \
                sum(1 for t in texts if header_score(t) >= 1) >= 2:
            j = i
            hdr_rows = []
            while j < len(rows):
                tj = [t for t in rows[j]["cells"].values() if t]
                if tj and sum(1 for t in tj if valueish(t)) <= 1 and (j == i or header_score(" ".join(tj)) >= 1) \
                        and not any(valueish(t) for t in tj[1:]):
                    hdr_rows.append(rows[j])
                    j += 1
                    if j - i >= 3:
                        break
                else:
                    break
            if j == i:
                hdr_rows = [rows[i]]
                j = i + 1
            segs.append((start, i, None))
            start = j
            segs.append((None, None, hdr_rows))
            i = j
            continue
        i += 1
    if not segs:
        return [g]
    segs.append((start, len(rows), None))
    hdr = None
    for a, b, h in segs:
        if h is not None:
            hdr = h
            continue
        part = rows[a:b]
        if not part:
            continue
        ng = Grid()
        ng.page, ng.top, ng.bbox, ng.kind = g.page, g.top, g.bbox, getattr(g, "kind", "")
        ng.title, ng.inner_title = g.title, getattr(g, "inner_title", "")
        ng.has_header = True
        if hdr is None:
            ng.cols = g.cols
            ng.has_header = g.has_header
        else:
            ng.cols = []
            for ci, c in enumerate(g.cols):
                txt = " ".join(hr["cells"].get(ci, "") for hr in hdr).strip()
                ng.cols.append({"x0": c["x0"], "x1": c["x1"], "own": [], "groups": [], "header": squash(txt), "group": ""})
        ng.rows = part
        out.append(ng)
    return out


def split_tall_bands(bands, dwords, cols, kinds):
    """Rows without horizontal rules: one band holds several single-line rows."""
    if "name" in kinds:
        ni = kinds.index("name")
    else:
        ni = 0
    lv = [i for i, k in enumerate(kinds) if k == "value"] or [i for i, k in enumerate(kinds) if k == "range"]
    if not lv:
        return bands
    li = lv[0]
    out = []
    for (y0, y1) in bands:
        bw = [w for w in dwords if y0 - 0.5 <= w.cy <= y1 + 0.5]
        lw = [w for w in bw if col_of(w.cx, cols) == li]
        nw = [w for w in bw if col_of(w.cx, cols) == ni]
        llines = lines_of(lw)
        nlines = lines_of(nw)
        vlines = [ln for ln in llines if len(ln) <= 3 and
                  (numtok(" ".join(w.text for w in ln)) or valueish(" ".join(w.text for w in ln))) and
                  not re.search(r"[(\-\u2013]$|^\S*\)$", " ".join(w.text for w in ln))]
        ok = len(vlines) >= 2 and len(nlines) >= 2
        if ok:
            for vl in vlines:
                cy = (ltop(vl) + lbot(vl)) / 2
                hit = [nl for nl in nlines if abs((ltop(nl) + lbot(nl)) / 2 - cy) <= 0.6 * (lbot(vl) - ltop(vl))]
                if not hit or not re.match(r"^[A-Z0-9]", hit[0][0].text):
                    ok = False
                    break
        if not ok:
            out.append((y0, y1))
            continue
        cs = [(ltop(vl) + lbot(vl)) / 2 for vl in vlines]
        cuts = [(lbot(vlines[k]) + ltop(vlines[k + 1])) / 2 for k in range(len(vlines) - 1)]
        bounds = [y0] + cuts + [y1]
        out.extend((bounds[k], bounds[k + 1]) for k in range(len(bounds) - 1))
    return out


def grid_from_cells(t, words, page_no):
    bx0, btop, bx1, bbot = t.bbox
    cells = [tuple(c) for c in t.cells]
    tw = [w for w in words if in_box(w, t.bbox, 1.0)]
    if not tw:
        return None
    ys = merge_close([c[1] for c in cells] + [c[3] for c in cells], 1.5)
    cbands = bands_from_cells(ys, cells)
    if not cbands:
        return None
    # ---- header detection on coarse bands
    hdr_end = None
    title_parts = []
    for i, (y0, y1) in enumerate(cbands):
        bw = [w for w in tw if y0 - 0.5 <= w.cy <= y1 + 0.5]
        if not bw:
            continue
        txt = join_words(bw)
        bcells = sorted([c for c in cells if c[1] >= y0 - 1 and c[3] <= y1 + 1], key=lambda c: c[0])
        first_txt = ""
        short = []
        for c in bcells:
            cw = [w for w in bw if in_box(w, c)]
            if cw and not first_txt:
                first_txt = join_words(cw)
            if 0 < len(cw) <= 14:
                short.extend(cw)
        kw = header_score(join_words(short)) if short else header_score(txt)
        nnum = count_numeric(bw)
        datalike = nnum >= 2
        first_hdr = header_score(first_txt) >= 1 and len(first_txt) < 80
        if (first_hdr and kw >= 2 and nnum <= 1) or (kw >= 3 and nnum <= 1) or (kw >= 2 and nnum == 0) or \
                (hdr_end is not None and i == hdr_end + 1 and kw >= 1 and nnum == 0):
            hdr_end = i
            continue
        if hdr_end is None:
            if datalike or i >= 8:
                break
            title_parts.append(txt)
            continue
        break
    if hdr_end is None:
        nonempty = [i for i, (y0, y1) in enumerate(cbands) if any(y0 - 0.5 <= w.cy <= y1 + 0.5 for w in tw)]
        if len(nonempty) >= 2:
            b0 = cbands[nonempty[0]]
            b1 = cbands[nonempty[1]]
            w0 = [w for w in tw if b0[0] - 0.5 <= w.cy <= b0[1] + 0.5]
            w1 = [w for w in tw if b1[0] - 0.5 <= w.cy <= b1[1] + 0.5]
            if not any(numericish(w.text) for w in w0) and sum(1 for w in w1 if numericish(w.text)) >= 1 and \
                    len(set(round(w.x0 / 25) for w in w0)) >= 2:
                hdr_end = nonempty[0]
    hdr_bottom = cbands[hdr_end][1] if hdr_end is not None else btop
    data_bands0 = [b for b in cbands if b[0] >= hdr_bottom - 0.5]
    if not data_bands0:
        return None
    # ---- logical columns from data cells
    dcells = [c for c in cells if c[1] >= hdr_bottom - 1.0]
    edge_count = {}
    for (y0, y1) in data_bands0:
        es = set()
        for c in dcells:
            if c[1] >= y0 - 1 and c[3] <= y1 + 1:
                es.add(round(c[0], 0))
                es.add(round(c[2], 0))
        for e in es:
            edge_count[e] = edge_count.get(e, 0) + 1
    thr = max(1, 0.3 * len(data_bands0))
    edges = [e for e, n in edge_count.items() if n >= thr]
    edges = merge_close(edges + [bx0, bx1], 3.0)
    cols = [{"x0": edges[i], "x1": edges[i + 1], "own": [], "groups": []}
            for i in range(len(edges) - 1) if edges[i + 1] - edges[i] > 2]
    if len(cols) < 2:
        return None
    # ---- header texts
    hwords = [w for w in tw if w.cy < hdr_bottom + 0.5]
    title_words = []
    if hdr_end is not None:
        hcells = [c for c in cells if c[3] <= hdr_bottom + 1.0]
        used = set()
        for c in hcells:
            cw = [w for w in hwords if in_box(w, c) and id(w) not in used]
            if not cw:
                continue
            span = [i for i, col in enumerate(cols) if overlap(c[0], c[2], col["x0"], col["x1"]) > 0.5 * (col["x1"] - col["x0"])]
            if len(span) >= 2:
                for w in cw:
                    used.add(id(w))
                txt = join_words(cw)
                if len(span) >= max(2, 0.75 * len(cols)):
                    title_words.extend(cw)
                    continue
                for i in span:
                    cols[i]["groups"].append((c[1], txt))
        for w in hwords:
            if id(w) in used:
                continue
            ci = col_of(w.cx, cols)
            cols[ci]["own"].append(w)
    if hdr_end is None:
        # header printed just above the ruled grid
        above = [w for w in words if btop - 100 <= w.top and w.bottom <= btop + 1.5 and
                 bx0 - 5 <= w.cx <= bx1 + 5]
        lns = lines_of(above)
        take = []
        prev_top = btop
        for ln in reversed(lns):
            lh_ = max(4.0, max(w.bottom - w.top for w in ln))
            if prev_top - lbot(ln) > 1.3 * lh_ or any(numericish(w.text) for w in ln) or is_paragraph_line(ln, bx1 - bx0):
                break
            take.insert(0, ln)
            prev_top = ltop(ln)
        hw = [w for ln in take for w in ln]
        if hw and header_score(" ".join(w.text for w in hw)) >= 2 and len(set(col_of(w.cx, cols) for w in hw)) >= 2:
            for w in hw:
                cols[col_of(w.cx, cols)]["own"].append(w)
            hdr_end = -1
    for col in cols:
        col["header"] = join_words(col["own"])
        col["group"] = " ".join(t for _, t in sorted(col["groups"]))
    g = Grid()
    g.cols = cols
    g.page = page_no
    g.top = btop
    g.bbox = t.bbox
    g.has_header = hdr_end is not None
    g.kind = "lattice"
    g.title = squash(" ".join(title_parts + ([join_words(title_words)] if title_words else [])))
    g.inner_title = g.title
    # ---- classify (needed for row banding)
    kinds = classify_columns(g, None)
    rowdef = [i for i, k in enumerate(kinds) if k in ("value", "range", "low", "high", "range_or_level")]
    if not rowdef:
        rowdef = [i for i, k in enumerate(kinds) if k not in ("name", "source", "health", None)]
    rcells = [c for c in dcells if any(overlap(c[0], c[2], cols[i]["x0"], cols[i]["x1"]) > 0.5 * min(c[2] - c[0], cols[i]["x1"] - cols[i]["x0"]) for i in rowdef)]
    ys2 = merge_close([c[1] for c in dcells] + [c[3] for c in dcells] + [hdr_bottom, bbot], 1.5)
    ys2 = [y for y in ys2 if y >= hdr_bottom - 1]
    bands = bands_from_cells(ys2, rcells)
    dwords = [w for w in tw if w.cy >= hdr_bottom - 0.5]
    bands = split_tall_bands(bands, dwords, cols, kinds)
    rows = []
    for (y0, y1) in bands:
        rows.append({"y0": y0, "y1": y1, "w": {}})
    if not rows:
        return None

    def band_of(y):
        for i, r in enumerate(rows):
            if r["y0"] - 0.5 <= y <= r["y1"] + 0.5:
                return i
        return None

    span_ids = set()
    width = bx1 - bx0
    for c in dcells:
        ncov = sum(1 for col in cols if overlap(c[0], c[2], col["x0"], col["x1"]) > 0.5 * (col["x1"] - col["x0"]))
        if ncov >= 3 and (c[2] - c[0]) >= 0.5 * width:
            for w in dwords:
                if in_box(w, c):
                    span_ids.add(id(w))
    def cell_of(w):
        for k_, c in enumerate(dcells):
            if in_box(w, c):
                return k_
        return None

    stick = sticky_cols([w for w in dwords if id(w) not in span_ids], cols, cell_of=cell_of)
    for w in dwords:
        bi = band_of(w.cy)
        if bi is None:
            continue
        if id(w) in span_ids:
            rows[bi].setdefault("span", []).append(w)
            continue
        ci = stick.get(id(w), col_of(w.cx, cols))
        rows[bi]["w"].setdefault(ci, []).append(w)
    # vertical merged cells: copy text into every band they span
    for c in dcells:
        span = [i for i, r in enumerate(rows) if overlap(c[1], c[3], r["y0"], r["y1"]) > 0.6 * (r["y1"] - r["y0"])]
        if len(span) < 2:
            continue
        cw = [w for w in dwords if in_box(w, c) and id(w) not in span_ids]
        if not cw:
            continue
        ci = col_of((c[0] + c[2]) / 2, cols)
        for i in span:
            have = rows[i]["w"].get(ci)
            if not have:
                rows[i]["w"][ci] = list(cw)
    g.rows = []
    for r in rows:
        cells_t = {ci: join_words(ws) for ci, ws in r["w"].items()}
        if any(v for v in cells_t.values()):
            g.rows.append({"cells": cells_t, "y0": r["y0"], "y1": r["y1"]})
        elif r.get("span"):
            g.rows.append({"cells": {}, "section": join_words(r["span"]), "y0": r["y0"], "y1": r["y1"]})
    return g



# ----------------------------------------------------------------------------
# tables without ruling lines: rebuild a grid from text positions
# ----------------------------------------------------------------------------

SUBLABEL_RE = re.compile(r"^(?:highest|lowest|single|monthly|average|avg|maximum|max|minimum|min|%|percent|"
                         r"\d{1,3}\s*%|samples? meeting|"
                         r"samples?|measurement|running|annual|quarterly|level|entry point|distribution|treated|"
                         r"raw|source water|finished|plant|daily|yearly)\b", re.I)
SECTION_RE = re.compile(r"contaminants?\b|compounds?$|substances$|by-?products$|parameters$|constituents$|^regulated\b|"
                        r"^unregulated\b|^secondary\b|^radio(?:active|logical)\b|^microbio|^inorganic\b|^organic\b|"
                        r"^volatile\b|^synthetic\b|^disinfect(?:ion|ants?)\b|^lead and copper\b|^metals?\b", re.I)


def ltop(ln):
    return min(w.top for w in ln)


def lbot(ln):
    return max(w.bottom for w in ln)


def ltext(ln):
    return " ".join(w.text for w in ln)


def is_paragraph_line(ln, width):
    if len(ln) < 7:
        return False
    gaps = [ln[i + 1].x0 - ln[i].x1 for i in range(len(ln) - 1)]
    span = ln[-1].x1 - ln[0].x0
    small = sum(1 for g_ in gaps if g_ < 6)
    big = [k for k, g_ in enumerate(gaps) if g_ >= 12]
    if big:
        # one wide gap is allowed only after a list marker ("1.", "*", "a)")
        if len(big) > 1 or big[0] > 1:
            return False
        first = " ".join(w.text for w in ln[:big[0] + 1])
        if not re.match(r"^(?:\d{1,2}[.)]?|[*\u2022\u2020\u2021\u00a7]+|\(?[a-z0-9]\)|note:?)$", first, re.I):
            return False
    return span > 0.42 * width and small >= 0.85 * len(gaps)


def spread_groups(ln, gap=7.5):
    groups = 1
    for a, b in zip(ln, ln[1:]):
        if b.x0 - a.x1 > gap:
            groups += 1
    return groups


def word_groups(ln, gap=7.5):
    groups = []
    cur = [ln[0]]
    for a, b in zip(ln, ln[1:]):
        if b.x0 - a.x1 > gap:
            groups.append(cur)
            cur = [b]
        else:
            cur.append(b)
    groups.append(cur)
    return groups


def short_header_groups(ln):
    return sum(1 for gr in word_groups(ln) if len(gr) <= 4 and header_score(" ".join(w.text for w in gr)) >= 1)


def is_text_header(L, i, page_w):
    ln = L[i]
    if spread_groups(ln) < 3 or is_paragraph_line(ln, page_w):
        return False
    if max(len(gr) for gr in word_groups(ln)) > 6:
        return False
    if header_score(ltext(ln)) >= (2 if RELAX else 3):
        return True
    if header_score(ltext(ln)) >= 1 and not any(numtok(w.text) for w in ln) and \
            max(len(gr) for gr in word_groups(ln)) <= 5:
        txt = [ltext(ln)]
        lh = max(4.0, sorted(w.bottom - w.top for w in ln)[len(ln) // 2])
        for step in (-1, 1):
            j = i
            for _ in range(2):
                nj = j + step
                if not (0 <= nj < len(L)):
                    break
                a, b = (L[nj], L[j]) if step < 0 else (L[j], L[nj])
                if ltop(b) - lbot(a) > 1.0 * lh or is_paragraph_line(L[nj], page_w) or \
                        any(numtok(w.text) and not re.match(r"^\d{1,2}(%|th)$", w.text) for w in L[nj]):
                    break
                txt.append(ltext(L[nj]))
                j = nj
        if header_score(" ".join(txt)) >= 3:
            nxt = [L[j] for j in range(i + 1, min(len(L), i + 6))]
            if any(sum(1 for w in l2 if valueish(w.text)) >= 2 for l2 in nxt):
                return True
    # transposed header: several contaminant names across the columns
    groups = []
    cur = [ln[0]]
    for a, b in zip(ln, ln[1:]):
        if b.x0 - a.x1 > 7.5:
            groups.append(cur)
            cur = [b]
        else:
            cur.append(b)
    groups.append(cur)
    named = sum(1 for gr in groups if CONTAM_VOCAB.search(" ".join(w.text for w in gr)))
    if named >= 2 and i + 1 < len(L) and sum(1 for w in L[i + 1] if numericish(w.text)) >= 2:
        return True
    return False


def horizontal_rules(page):
    segs = []
    try:
        for r in list(page.rects) + list(page.lines):
            x0, x1, t, b = r["x0"], r["x1"], r["top"], r["bottom"]
            if x1 - x0 < 20:
                continue
            if b - t <= 2.5:
                segs.append(((t + b) / 2, x0, x1))
            else:
                segs.append((t, x0, x1))
                segs.append((b, x0, x1))
    except Exception:
        pass
    # merge collinear pieces (cell borders drawn per cell) into long rules
    segs.sort(key=lambda t: (round(t[0]), t[1]))
    merged = []
    for y, x0, x1 in segs:
        if merged and abs(merged[-1][0] - y) <= 1.0 and x0 <= merged[-1][2] + 3:
            m = merged[-1]
            merged[-1] = (m[0], min(m[1], x0), max(m[2], x1))
        else:
            merged.append((y, x0, x1))
    return merged


def text_grids(words, exclude, page_no, page_w, hsegs=()):
    ws = [w for w in words if not any(in_box(w, b, 2) for b in exclude)]
    if not ws:
        return []
    L = lines_of(ws)
    out = []
    i = 0
    while i < len(L):
        ln = L[i]
        if is_text_header(L, i, page_w):
            try:
                g, j = build_text_table(L, i, page_no, page_w, hsegs)
            except Exception:
                g, j = None, i + 1
            if g is not None:
                out.append(g)
                i = max(j, i + 1)
                continue
        i += 1
    return out


def build_text_table(L, i, page_no, page_w, hsegs=()):
    n = len(L)
    lh = max(4.0, sorted(w.bottom - w.top for w in L[i])[len(L[i]) // 2])
    hdr = [i]
    hx0 = min(w.x0 for w in L[i])
    hx1 = max(w.x1 for w in L[i])

    def prose(ln, ref_w):
        return spread_groups(ln) == 1 and len(ln) >= 3 and (ln[-1].x1 - ln[0].x0) > 0.3 * ref_w and \
            ln[0].x0 <= hx0 + 15

    k = i - 1
    while k >= 0 and i - k <= 6:
        if ltop(L[k + 1]) - lbot(L[k]) > 1.0 * lh:
            break
        nnum = count_numeric([w for w in L[k] if not re.match(r"^\d{1,2}(%|th)$", w.text)])
        if nnum >= 2 or (nnum == 1 and header_score(ltext(L[k])) == 0) or is_paragraph_line(L[k], page_w):
            break
        if prose(L[k], hx1 - hx0) or (ltext(L[k]).isupper() and len(L[k]) >= 2 and len(ltext(L[k])) >= 12):
            break
        hdr.insert(0, k)
        k -= 1
    k = i + 1
    while k < n and k - i <= 7:
        if ltop(L[k]) - lbot(L[k - 1]) > 1.0 * lh:
            break
        nnum = count_numeric(L[k])
        if nnum >= 2 or (nnum >= 1 and header_score(ltext(L[k])) < 2) or is_paragraph_line(L[k], page_w):
            break
        fg = " ".join(w.text for w in word_groups(L[k])[0])
        if L[k][0].x0 <= hx0 + 5 and CONTAM_VOCAB.search(fg) and header_score(fg) == 0 and \
                short_header_groups(L[k]) < 3:
            break
        hdr.append(k)
        k += 1
    hwords = [w for h in hdr for w in L[h]]
    tx0 = min(w.x0 for w in hwords) - 70
    tx1 = max(w.x1 for w in hwords) + 45
    data = []
    last_b = lbot(L[hdr[-1]])
    new_header = False
    while k < n:
        ln = [w for w in L[k] if tx0 <= w.cx <= tx1]
        if not ln:
            break
        if data and is_text_header(L, k, page_w):
            new_header = True
            break
        if ltop(ln) - last_b > 3.2 * lh:
            break
        if is_paragraph_line(ln, page_w):
            break
        if data and len(ln) >= 4 and not any(numtok(w.text) for w in ln) and \
                max(b.x0 - a.x1 for a, b in zip(ln, ln[1:])) < 20 and (ln[-1].x1 - ln[0].x0) > 0.4 * (tx1 - tx0):
            break
        if data and short_header_groups(ln) >= 2 and spread_groups(ln) >= 3 and \
                not any(numtok(w.text) for w in ln) and \
                (short_header_groups(ln) >= 3 or not CONTAM_VOCAB.search(" ".join(w.text for w in word_groups(ln)[0]))):
            new_header = True
            break
        if data and len(re.findall(r"\b[A-Z&]{4,}\b", ltext(ln))) >= 2 and not any(numtok(w.text) for w in ln) and \
                len(ln) >= 3:
            break
        if data and header_score(ltext(ln)) >= 3 and spread_groups(ln) >= 3 and \
                max(len(gr) for gr in word_groups(ln)) <= 6 and not any(numtok(w.text) for w in ln):
            new_header = True
            break
        if re.match(r"^(\*|†|\d\s+[A-Z][a-z]+\s+\w+\s+\w+\s+\w+|note|notes|footnote)", ltext(ln), re.I) and len(ln) >= 6 and spread_groups(ln) <= 2:
            break
        data.append(ln)
        last_b = lbot(ln)
        k += 1
    if new_header:
        # header fragments printed above the next table's main header line
        while len(data) > 1 and spread_groups(data[-1]) <= 2 and \
                ltop(L[k]) - lbot(data[-1]) <= 1.0 * lh and \
                not any(numtok(w.text) and not re.match(r"^\d{1,2}(%|th)$", w.text) for w in data[-1]):
            data.pop()
            k -= 1
    if not data:
        return None, i + 1
    allw = hwords + [w for ln in data for w in ln]
    xs0 = min(w.x0 for w in allw)
    xs1 = max(w.x1 for w in allw)
    width = int(xs1 - xs0) + 3
    cov = [0] * width
    tab = [ln for ln in data if spread_groups(ln) >= 3]
    if len(tab) < 2:
        tab = data
    for ln in tab:
        seen = set()
        for w in ln:
            for x in range(int(w.x0 - xs0), int(w.x1 - xs0) + 1):
                if 0 <= x < width:
                    seen.add(x)
        for x in seen:
            cov[x] += 1
    thr = int(0.1 * len(tab))
    edges = [xs0 - 1]
    x = 0
    while x < width:
        if cov[x] <= thr:
            st = x
            while x < width and cov[x] <= thr:
                x += 1
            if x - st >= 5 and st > 0 and x < width:
                edges.append(xs0 + (st + x) / 2.0)
        else:
            x += 1
    edges.append(xs1 + 1)
    cols = [{"x0": edges[j], "x1": edges[j + 1], "own": [], "groups": []} for j in range(len(edges) - 1)]
    if len(cols) < 3:
        return None, i + 1
    last_h = hdr[-1]
    for h in hdr:
        hl = [w for w in L[h] if tx0 <= w.cx <= tx1]
        if not hl:
            continue
        for gr in word_groups(hl):
            gx0, gx1 = gr[0].x0, gr[-1].x1
            gc = (gx0 + gx1) / 2.0
            span = [ci for ci, c in enumerate(cols) if overlap(gx0, gx1, c["x0"], c["x1"]) >= 0.2 * (gx1 - gx0)]
            near = False
            if len(span) >= 2:
                ux0, ux1 = cols[span[0]]["x0"], cols[span[-1]]["x1"]
                near = abs(gc - (ux0 + ux1) / 2.0) < 0.15 * (ux1 - ux0)
            if h != last_h and len(span) >= 2 and near and len(gr) <= 5:
                for ci in span:
                    cols[ci]["groups"].append((ltop(gr), " ".join(w.text for w in gr)))
                continue
            for w in gr:
                cols[col_of(w.cx, cols)]["own"].append(w)
    for c in cols:
        c["header"] = join_words(c["own"])
        c["group"] = " ".join(t for _, t in sorted(c["groups"]))
    g = Grid()
    g.cols = cols
    g.page = page_no
    g.top = ltop(L[hdr[0]])
    g.bbox = (xs0, g.top, xs1, lbot(data[-1]))
    g.has_header = True
    g.kind = "text"
    g.title = ""
    g.title_lines = []
    j = hdr[0] - 1
    while j >= 0 and len(g.title_lines) < 2 and ltop(L[hdr[0]]) - lbot(L[j]) < 40:
        if sum(1 for w in L[j] if numtok(w.text)) >= 2:
            break
        g.title_lines.append(ltext(L[j]))
        j -= 1
    g.title = " ".join(g.title_lines[::-1])

    stick = sticky_cols([w for ln in data for w in ln], cols)

    def cells_of(lines):
        d = {}
        for ln in lines:
            for w in ln:
                d.setdefault(stick.get(id(w), col_of(w.cx, cols)), []).append(w)
        return {ci: join_words(v) for ci, v in d.items()}

    g.rows = [{"cells": cells_of([ln])} for ln in data]
    kinds = classify_columns(g, None)
    ni = kinds.index("name") if "name" in kinds else 0
    noval = ("name", "label", "source", "health", "location", "category", "text", None)
    primary = set(i for i, k in enumerate(kinds) if k in ("range", "low", "high", "range_or_level"))
    lv = [i for i, k in enumerate(kinds) if k == "value"]
    if lv:
        primary.add(lv[0])
    else:
        primary |= set(i for i, k in enumerate(kinds) if k == "count")
    anchors = []
    strong = set()
    unnamed_split = set()
    nvalcols = sum(1 for k in kinds if k not in noval)
    for li, ln in enumerate(data):
        bycol = {}
        for w in ln:
            bycol.setdefault(col_of(w.cx, cols), []).append(w)
        vcols = [c for c in bycol if kinds[c] not in noval]
        prim = [c for c in vcols if c in primary and valueish(" ".join(w.text for w in bycol[c]))]
        if prim or len(vcols) >= 2 or (vcols and ni in bycol):
            anchors.append(li)
            need = 2 if nvalcols >= 3 else 1
            if (ni in bycol and len(vcols) >= need and (prim or len(vcols) >= 3)) or len(vcols) >= 3:
                strong.add(li)
            if ni not in bycol and lv and lv[0] in bycol and len(vcols) >= 2 and \
                    valueish(" ".join(w.text for w in bycol[lv[0]])):
                unnamed_split.add(li)
    if not anchors:
        return None, i + 1
    # row blocks: rules between rows, else vertical whitespace
    d_top, d_bot = ltop(data[0]), lbot(data[-1])
    seps = [y for (y, sx0, sx1) in hsegs if overlap(sx0, sx1, xs0, xs1) >= 0.5 * (xs1 - xs0) and
            d_top - 1 <= y <= d_bot + 1]
    seps = merge_close(seps, 2.0)
    inner = [y for y in seps if d_top + 1 < y < d_bot - 1]
    block = []
    if len(inner) >= 1:
        bounds = [-1e9] + inner + [1e9]
        for ln in data:
            cy = (ltop(ln) + lbot(ln)) / 2
            for bi in range(len(bounds) - 1):
                if bounds[bi] <= cy < bounds[bi + 1]:
                    block.append(bi)
                    break
    else:
        gaps = [ltop(data[x]) - lbot(data[x - 1]) for x in range(1, len(data))]
        med = sorted(gaps)[len(gaps) // 2] if gaps else 0
        thr = max(0.55 * lh, 1.6 * med + 0.5)
        bi = 0
        block.append(0)
        for gp in gaps:
            if gp > thr:
                bi += 1
            block.append(bi)
    named = set()
    for li in anchors:
        if any(col_of(w.cx, cols) == ni for w in data[li]):
            named.add(li)
    groups = {}
    sections = {}
    blk_anchor = {}
    orphan = set()
    for b in sorted(set(block)):
        members = [li for li in range(len(data)) if block[li] == b]
        ba = [li for li in members if li in anchors]
        if not ba:
            for li in members:
                cs = set(col_of(w.cx, cols) for w in data[li])
                t = ltext(data[li])
                if (cs == {ni} and SECTION_RE.search(t) and not re.search(r"\d", t)) or \
                        (cs == {ni} and re.match(r"^(?:19|20)\d\d$", t.strip())):
                    sections[li] = t
                else:
                    orphan.add(li)
            continue
        nb = [li for li in ba if (li in named and li in strong) or (li in unnamed_split and li != ba[0])]
        sb = [li for li in ba if li in strong]
        if not sb and not any(li in named for li in ba):
            for li in members:
                orphan.add(li)
            continue
        if len(nb) <= 1:
            head = nb[0] if nb else (sb[0] if sb else ba[0])
            groups[head] = []
            for li in members:
                cs = set(col_of(w.cx, cols) for w in data[li])
                t = ltext(data[li])
                if li != head and li < head and ((cs == {ni} and SECTION_RE.search(t) and not re.search(r"\d", t)) or
                                                  (cs == {ni} and re.match(r"^(?:19|20)\d\d$", t.strip())) or
                                                  (spread_groups(data[li]) <= 2 and SECTION_RE.search(t) and
                                                   not any(numtok(w.text) for w in data[li]) and not CONTAM_VOCAB.search(t))):
                    sections[li] = t
                    continue
                groups[head].append(li)
                blk_anchor[li] = head
    anchors = [a for a in anchors if (a in groups or a not in blk_anchor) and (a in groups or a in strong)]
    for a in anchors:
        groups.setdefault(a, [a])
    for li, ln in enumerate(data):
        if li in groups or li in blk_anchor or li in sections or li in orphan:
            continue
        cs = set(col_of(w.cx, cols) for w in ln)
        t = ltext(ln)
        if (cs == {ni} and SECTION_RE.search(t) and not re.search(r"\d", t)) or \
                (cs == {ni} and re.match(r"^(?:19|20)\d\d$", t.strip())) or \
                (spread_groups(ln) <= 2 and SECTION_RE.search(t) and not any(numtok(w.text) for w in ln) and
                 not CONTAM_VOCAB.search(t)):
            sections[li] = t
            continue
        prev_a = [a for a in anchors if a < li and not any(a < s_ < li for s_ in sections)]
        next_a = [a for a in anchors if a > li and not any(li < s_ < a for s_ in sections)]
        cand = []
        if prev_a:
            pa = prev_a[-1]
            cand.append((ltop(ln) - lbot(data[pa]) if pa < li else 0, 0, pa))
        if next_a:
            na = next_a[0]
            # name text above the value line belongs to the next row when it starts a new name
            d = ltop(data[na]) - lbot(ln)
            starts_new = bool(cs & {ni}) and not re.match(r"^[a-z(]", t)
            cand.append((d - (0.01 if starts_new else -0.01), 1, na))
        if not cand:
            continue
        cand.sort()
        groups[cand[0][2]].append(li)
    rows = []
    order = sorted(list(groups.keys()) + list(sections.keys()))
    for li in order:
        if li in sections:
            rows.append({"cells": {}, "section": sections[li]})
        else:
            rows.append({"cells": cells_of([data[x] for x in sorted(groups[li])]),
                         "inherit": li in unnamed_split})
    g.rows = rows
    return g, k


# ----------------------------------------------------------------------------
# column roles and records
# ----------------------------------------------------------------------------

def classify_columns(g, prev_kinds):
    kinds = []
    for i, col in enumerate(g.cols):
        k = classify(col.get("header", ""))
        if k is None and col.get("group"):
            k2 = classify(col["group"])
            if k2 not in ("value", "name") or (k2 == "name" and i <= 1):
                k = k2
        kinds.append(k)
    # content-based fallbacks
    for i, k in enumerate(kinds):
        if k is None or k == "value":
            texts = [r["cells"].get(i, "") for r in g.rows] if g.rows else []
            kc = classify_content(texts) if texts else None
            if kc and (k is None or kc in ("violation", "date", "unit")):
                kinds[i] = kc
            elif k is None:
                kinds[i] = "value" if any(t for t in texts) else None
    # first non-empty column is the name column unless something else claimed it
    if "name" not in kinds:
        for i, k in enumerate(kinds):
            if k is not None:
                kinds[i] = "name"
                break
    else:
        first = kinds.index("name")
        for i in range(first + 1, len(kinds)):
            if kinds[i] == "name":
                kinds[i] = "label"
    # a generic value column full of prose (comments, "what it means", sources) is not a level column
    if g.rows:
        for i, k in enumerate(kinds):
            if k != "value":
                continue
            texts = [r["cells"].get(i, "") for r in g.rows]
            texts = [t for t in texts if t and not is_null(t)]
            if len(texts) >= 2:
                prose = sum(1 for t in texts if not valueish(t) and len(t.split()) >= 3)
                if prose >= 0.6 * len(texts):
                    kinds[i] = "text"
    # several value columns headed by places ("Well 1", "Entry Point 2") hold one level each
    pc = []
    for i, k in enumerate(kinds):
        h = squash(g.cols[i].get("header", ""))
        if k in ("location", "value") and h and len(h) <= 30 and PLACE_COL.match(h) and g.rows:
            texts = [r["cells"].get(i, "") for r in g.rows if r["cells"].get(i)]
            if texts and sum(1 for t in texts if valueish(t) or is_null(t)) >= 0.7 * len(texts):
                pc.append(i)
    if len(pc) >= 2:
        for i in pc:
            kinds[i] = "value"
    g.place_cols = pc if len(pc) >= 2 else []
    # a second "name" column holding only units is a unit column
    for i, k in enumerate(kinds):
        if k == "name" and i > 0 and g.rows:
            texts = [strip_marks(r["cells"].get(i, "")) for r in g.rows if r["cells"].get(i)]
            if texts and all(UNIT_RE.match(t) for t in texts):
                kinds[i] = "unit"
    # an unlabeled text column right after the name column holds sub-row labels
    if "name" in kinds:
        ni = kinds.index("name")
        j = ni + 1
        if j < len(kinds) and kinds[j] == "value" and not g.cols[j].get("header") and g.rows:
            texts = [r["cells"].get(j, "") for r in g.rows]
            texts = [t for t in texts if t and not is_null(t)]
            if texts and all(re.search(r"[A-Za-z]{3}", t) and not re.match(r"^[<>\d.]", t) for t in texts):
                kinds[j] = "label"
    # two unlabeled sub-columns under one "Range" group are its low and high
    for i in range(len(kinds) - 1):
        if kinds[i] == "range" and kinds[i + 1] == "range" and not g.cols[i].get("header") and \
                not g.cols[i + 1].get("header") and g.cols[i].get("group") and \
                g.cols[i].get("group") == g.cols[i + 1].get("group"):
            kinds[i], kinds[i + 1] = "low", "high"
    # low / high pairs
    for i, k in enumerate(kinds):
        if k == "low":
            if not any(kinds[j] == "high" for j in range(i + 1, min(len(kinds), i + 3))):
                kinds[i] = "value"
        elif k == "high":
            if not any(kinds[j] == "low" for j in range(max(0, i - 2), i)):
                kinds[i] = "value"
    return kinds


def clean_name(raw):
    """Return (name, date, unit)."""
    s = squash(raw)
    date = None
    unit = None
    s = re.sub(r"^[\u2022\u25aa\u25cf\u2023\u2043\u00b7>\-\u2013]+\s*", "", s)
    s = re.sub(r"^\(?\d{1,3}[.)]\s+(?=[A-Za-z])", "", s)
    s = FOOT_TRAIL.sub("", s)
    # parenthetical testing notes holding a year: "(2023 sample)", "(sampled in 2022)"
    m = re.search(r"\(([^()]*\b(?:19|20)\d\d\b[^()]*)\)\s*$", s)
    if m and m.start() > 0 and re.search(r"test|sampl|collect|monitor|analy", m.group(1), re.I):
        dm = re.search(r"\b((?:\d{1,2}/){0,2}(?:19|20)\d\d)\b", m.group(1))
        if dm:
            date = dm.group(1)
        s = s[:m.start()].strip()
    # testing notes
    m = re.search(r"\(?\b(?:next|last)?\s*(?:tested|testing|sampled|sample[ds]?|collected|monitored)\b[^()]*\)?$", s, re.I)
    if m and m.start() > 0:
        note = m.group(0)
        dm = re.search(r"\b((?:\d{1,2}/){0,2}(?:19|20)\d\d)\b", note)
        if dm:
            date = dm.group(1)
        s = s[:m.start()].strip()
    # parenthesised / bracketed unit
    m = UNIT_PAREN.search(s) or re.search(r"\[\s*(" + UNIT_CORE + r"(?:\s+as\s+[A-Za-z0-9]+)?)\s*\]", s, re.I)
    if m:
        unit = m.group(1)
        s = (s[:m.start()] + s[m.end():]).strip()
    m = re.search(r"\s*[,\u2013\-]\s*(" + UNIT_CORE + r")\s*$", s, re.I)
    if m and not unit and m.start() > 0:
        unit = m.group(1)
        s = s[:m.start()].strip()
    # parenthesised dates / years
    m = re.search(r"\(\s*((?:\d{1,2}/){0,2}\d{2,4}(?:\s*[-–&,]\s*(?:\d{1,2}/){0,2}\d{2,4})*)\s*\)", s)
    if m and re.search(r"/|(?:19|20)\d\d", m.group(1)):
        date = date or m.group(1)
        s = (s[:m.start()] + s[m.end():]).strip()
    m = re.search(r"\s+((?:\d{1,2}/){0,2}(?:19|20)\d\d)$", s)
    if m and m.start() > 0 and re.search(r"[A-Za-z]{2}", s[:m.start()]):
        date = date or m.group(1)
        s = s[:m.start()].strip()
    s = FOOT_TRAIL.sub("", s)
    s = re.sub(r"\s*\[\d{1,2}\]", "", s)
    s = re.sub(r"^[*†‡]+", "", s)
    s = squash(s).strip(" ,;:")
    return s, date, unit


def parse_violation(s, compliance=False):
    t = strip_marks(s).lower().strip(" .")
    if not t or is_null(t):
        return None
    yes = re.match(r"^(yes|y)\b", t) or t[:1] in ("✓", "✔", "☑")
    no = re.match(r"^(no|n)\b", t) or t[:1] in ("✗", "✘", "☒") or t == "x"
    if yes and no:
        no = None
    if compliance:
        if yes:
            return "N"
        if no:
            return "Y"
        return None
    if yes:
        return "Y"
    if no:
        return "N"
    return None


STAT_LABEL = re.compile(r"^(max(imum)?|min(imum)?|average|avg\.?|mean|median|highest|lowest|high|low|"
                        r"range|90th percentile( value)?|90%|value|result|level|raa|lraa|highest lraa|"
                        r"running annual average|annual average|detected)$", re.I)


def parse_level_cell(s):
    """Return dict(level, lo, hi, unit, locs=[(label, level, lo, hi)])."""
    res = {"level": None, "lo": None, "hi": None, "unit": None, "locs": [], "text": False}
    s = strip_marks(s)
    if is_null(s):
        return res
    lv, u = parse_scalar(s)
    if not isinstance(lv, str):
        res["level"], res["unit"] = lv, u
        return res
    m = re.match(r"^(" + VAL + r")\s*(" + UNIT_CORE + r")?\s*\(?\s*(?:avg\.?|average|max\.?|maximum|min\.?|minimum|"
                 r"highest|lowest|mean|median|raa|lraa|running annual average|annual average|high|low)\s*\)?$", s, re.I)
    if m:
        res["level"] = conv(m.group(1))
        res["unit"] = m.group(2)
        return res
    # "X / a-b" or "X; a-b"
    m = re.match(r"^(" + VAL + r")\s*(" + UNIT_CORE + r")?\s*[/;]\s*(" + VAL + r")\s*(?:-|\u2013|\u2014|to)\s*(" + VAL + r")\s*(" + UNIT_CORE + r")?$", s, re.I)
    if m:
        res["level"] = conv(m.group(1))
        res["lo"], res["hi"] = conv(m.group(3)), conv(m.group(4))
        res["unit"] = m.group(2) or m.group(5)
        return res
    # "X (a-b)"
    m = re.match(r"^(" + VAL + r")\s*(" + UNIT_CORE + r")?\s*\(\s*(" + VAL + r")\s*(?:-|–|—|to)\s*(" + VAL + r")\s*\)\s*(" + UNIT_CORE + r")?$", s, re.I)
    if m:
        res["level"] = conv(m.group(1))
        res["lo"], res["hi"] = conv(m.group(3)), conv(m.group(4))
        res["unit"] = m.group(2) or m.group(5)
        return res
    m = re.match(r"^(?:90th percentile|90%|average|avg\.?|max(?:imum)?|highest|result|level|value)[A-Za-z0-9 .'%]*?[:=]?\s*(" + VAL + r")\s*(" + UNIT_CORE + r")?$", s, re.I)
    if m:
        res["level"] = conv(m.group(1))
        res["unit"] = m.group(2)
        return res
    parts = [x.strip() for x in re.split(r"\s*;\s*", s) if x.strip()]
    if len(parts) >= 2:
        locs = []
        for part in parts:
            m = re.match(r"^([A-Za-z][A-Za-z0-9 .'&#/\-]*?)\s*[:=]?\s*(" + VAL + r")\s*(" + UNIT_CORE + r")?\s*"
                         r"(?:\(\s*(" + VAL + r")\s*(?:-|\u2013|\u2014|to)\s*(" + VAL + r")\s*\))?\s*(" + UNIT_CORE + r")?$",
                         part, re.I)
            if not m or STAT_LABEL.match(squash(m.group(1)).lower()):
                locs = []
                break
            locs.append((squash(m.group(1)).strip(" :="), conv(m.group(2)),
                         conv(m.group(4)) if m.group(4) else None, conv(m.group(5)) if m.group(5) else None,
                         m.group(3) or m.group(6)))
        if len(locs) >= 2:
            res["locs"] = locs
            return res
    # labelled values
    seg_re = re.compile(r"([A-Za-z][A-Za-z0-9 .'&#/]*?)\s*[:=]?\s*(" + VAL + r")(?:\s*(?:-|–|—|to)\s*(" + VAL + r"))?"
                        r"(?:\s*(" + UNIT_CORE + r"))?(?:\s*\(\s*(" + VAL + r")\s*(?:-|–|—|to)\s*(" + VAL + r")\s*\))?", re.I)
    segs = list(seg_re.finditer(s))
    if segs and len(segs) >= 1 and sum(len(x.group(0)) for x in segs) >= 0.6 * len(re.sub(r"[\s/;,|]", "", s)) * 0.9:
        labels = [squash(x.group(1)).strip(" :=").lower() for x in segs]
        if len(segs) >= 2 and not any(STAT_LABEL.match(l) or l.startswith("range") for l in labels):
            for x in segs:
                lab = squash(x.group(1)).strip(" :=;,")
                if x.group(3):
                    res["locs"].append((lab, conv(x.group(2)), conv(x.group(2)), conv(x.group(3)), x.group(4)))
                else:
                    res["locs"].append((lab, conv(x.group(2)), conv(x.group(5)) if x.group(5) else None,
                                        conv(x.group(6)) if x.group(6) else None, x.group(4)))
            return res
        if any(STAT_LABEL.match(l) or l.startswith("range") for l in labels):
            for x, l in zip(segs, labels):
                if l.startswith("range"):
                    if x.group(3):
                        res["lo"], res["hi"] = conv(x.group(2)), conv(x.group(3))
                    continue
                if res["level"] is None:
                    if x.group(3):
                        continue
                    res["level"] = conv(x.group(2))
                    res["unit"] = res["unit"] or x.group(4)
            if res["level"] is not None or res["lo"] is not None:
                return res
    # "label = value" e.g. "90th percentile value = 0.21"
    m = re.match(r"^[A-Za-z0-9][A-Za-z0-9 .'%]*?[:=]\s*(" + VAL + r")\s*(" + UNIT_CORE + r")?$", s, re.I)
    if m:
        res["level"] = conv(m.group(1))
        res["unit"] = m.group(2)
        return res
    res["level"] = s
    res["text"] = True
    return res


def unit_in(s):
    if not s:
        return None
    s2 = strip_marks(s)
    for m in list(UNIT_TAIL.finditer(s2)) + list(UNIT_ANY.finditer(s2)):
        if not re.match(r"^units?$", m.group(1), re.I):
            return m.group(1)
    return None


def unit_in_value(s):
    """unit printed with a value: '2.8 ppb', '15 pCi/l', 'TT=1 NTU for ...'."""
    if not s:
        return None
    m = re.search(NUM + r"\s*(" + UNIT_CORE + r")(?![A-Za-z])", strip_marks(s), re.I)
    if m:
        return m.group(1)
    return None


def numtok(t):
    t = strip_marks(t)
    return bool(numericish(t) or re.match(r"^(nd|n\.d\.|bdl|<\S+)$", t, re.I))


def valueish(t):
    t = strip_marks(t)
    if not t or is_null(t):
        return False
    return bool(re.match(r"^[<>\u2264\u2265=]?\s*[\d.]", t) or re.match(r"^[<\u2264]\s*[A-Za-z]{1,5}$", t) or
                re.match(r"^(nd|n\.d\.|bdl|absent|present|negative|positive|not detected|non-detect|none detected|"
                         r"detected|trace|pass|\d)", t, re.I) or
                (re.search(r"(?:^|\s)\d[\d.,]*\s*(?:%|" + UNIT_CORE + r")?(?:\s|$|\()", t, re.I) and
                 (re.search(r"\d\s*(?:-|\u2013|to)\s*\d|[:=]\s*[<\d]", t) or len(re.findall(r"\d+(?:\.\d+)?", t)) >= 2)))


NOT_RESULTS = re.compile(r"violation type|type of violation|violation begin|violation end|begin date|end date|"
                         r"frequency|next due|how often|schedule|water pumped|water sold|meter size|rate per|"
                         r"base charge|gallons\)|unaccounted|"
                         r"explanation|corrective action|steps taken|service lines?\b|material type|"
                         r"line material|number of lines|due date|next sample|waiver|assessments? (?:required|completed)|"
                         r"duration|compliance period|monitoring period begin|well depth|depth \(|aquifer|"
                         r"pump(?:ing)? capacity|capacity \(", re.I)


def is_results_table(g, kinds, place_ok=False):
    allh = " ".join((c.get("header", "") + " " + c.get("group", "")) for c in g.cols)
    if NOT_RESULTS.search(allh):
        return False
    regul = any(k in ("mcl", "mclg", "range", "low", "high", "violation", "compliance", "unit", "advisory",
                      "range_or_level") for k in kinds)
    if not regul and not place_ok:
        ni = kinds.index("name") if "name" in kinds else 0
        names = [r["cells"].get(ni, "") for r in g.rows if r["cells"].get(ni)]
        if not names or sum(1 for n in names if CONTAM_VOCAB.search(n)) < 0.5 * len(names):
            return False
    lvl = [i for i, k in enumerate(kinds) if k in ("value", "range", "low", "high", "range_or_level")]
    if not lvl:
        lvl = [i for i, k in enumerate(kinds) if k == "count" and
               re.search(r"positive|detection|present|presence", g.cols[i].get("header", ""), re.I)]
    if not lvl:
        return False
    tot = num = 0
    for r in g.rows:
        for i in lvl:
            t = r["cells"].get(i, "")
            if t and not is_null(t):
                tot += 1
                if valueish(t):
                    num += 1
    return tot > 0 and num >= 0.5 * tot


CONTAM_VOCAB = re.compile(
    r"arsenic|barium|cadmium|chromium|copper|lead\b|fluoride|nitrate|nitrite|sodium|chloride|sulfate|sulphate|iron|"
    r"manganese|hardness|calcium|magnesium|potassium|alkalinity|\bph\b|conductiv|dissolved solids|\btds\b|turbidity|"
    r"chlorine|chloramine|tthm|haa5|haa\b|trihalomethane|haloacetic|radium|uranium|gross alpha|gross beta|alpha emitters|"
    r"atrazine|simazine|pfas|pfoa|pfos|pfna|pfhxs|pfbs|pfba|genx|hfpo|\btoc\b|organic carbon|bromate|chlorite|chlorate|"
    r"selenium|antimony|beryllium|mercury|thallium|cyanide|nickel|zinc|aluminum|silver|coliform|e\.? ?coli|"
    r"temperature|phosphate|silica|strontium|vanadium|boron|lithium|molybdenum|ammonia|bromide|color|odor|"
    r"perchlorate|ethylene|benzene|toluene|xylene|styrene|vinyl|methane|mtbe|chloroform|bromoform|radon|"
    r"germanium|cobalt|asbestos|dioxin|endrin|lindane|chlordane|heptachlor|glyphosate|dalapon|picloram|"
    r"carbofuran|oxamyl|di\(2|phthalate|adipate|benzo|pcb|toxaphene|2,4|silvex|diquat|endothall|metolachlor|"
    r"dibromo|dichloro|trichloro|tetrachloro|chlorobenzene|cryptosporidium|giardia|microcystin|cyanotoxin|"
    r"geosmin|mib|hexavalent|disinfect|residual|heterotrophic|hpc|sodium|total solids|saturation|langelier|"
    r"orthophosphate|carbon dioxide|dissolved oxygen|bicarbonate|carbonate|fluor|nitrogen|phosphorus|sulfide", re.I)
PLACE_HDR = re.compile(r"plant|well|site|location|area|zone|facility|station|entry point|^source|treatment|"
                       r"sampling point|sample point|^tap|district|region|system|reservoir|tank|pressure", re.I)
DATE_HDR = re.compile(r"date|month|quarter|year|period|week", re.I)
DATE_CELL = re.compile(r"^(?:\d{1,2}/\d{1,2}/\d{2,4}|\d{1,2}/\d{2,4}|(?:19|20)\d\d|"
                       r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*\d{0,4}|q[1-4]|"
                       r"(?:1st|2nd|3rd|4th|first|second|third|fourth) quarter.*)$", re.I)


def title_parameter(title):
    t = squash(title)
    if not t:
        return None
    t = re.sub(r"^(?:table\s*\d*[:.\-]?\s*)", "", t, flags=re.I)
    t = re.sub(r"\s*[\-\u2013:]\s*(?:tested|sampled|monitored|collected)\b.*$", "", t, flags=re.I)
    t = re.sub(r"\s+(?:by|per|for each|at each|in each)\s+(?:area|plant|zone|well|site|location|source|facility|"
               r"entry point|treatment plant)s?\b.*$", "", t, flags=re.I)
    t = re.sub(r"\s+(?:results?|data|levels?|summary|table|monitoring|information)$", "", t, flags=re.I)
    t = re.sub(r"\s*\(\s*(?:" + UNIT_CORE + r")\s*\)", "", t, flags=re.I)
    t = squash(t).strip(" :-\u2013")
    if not t or len(t) > 60 or not CONTAM_VOCAB.search(t):
        return None
    return t


def transposed_records(g, kinds, system):
    """Columns are contaminants, rows are dates or places."""
    if len(g.cols) < 2 or not g.rows:
        return None
    h0 = g.cols[0].get("header", "")
    heads = [g.cols[i].get("header", "") for i in range(1, len(g.cols))]
    named = [i + 1 for i, h in enumerate(heads) if h and CONTAM_VOCAB.search(h) and classify(h) in ("value", "name", "unit", None)]
    if len(named) < 2 or len(named) < 0.6 * len([h for h in heads if h]):
        return None
    labels = [r["cells"].get(0, "") for r in g.rows if r["cells"].get(0)]
    if not labels:
        return None
    is_date = bool(DATE_HDR.search(h0)) or all(DATE_CELL.match(strip_marks(l)) for l in labels)
    is_place = bool(PLACE_HDR.search(h0)) or not any(CONTAM_VOCAB.search(l) for l in labels)
    if not (is_date or is_place):
        return None
    out = []
    for r in g.rows:
        lab = strip_marks(r["cells"].get(0, ""))
        if not lab:
            continue
        for ci in named:
            txt = r["cells"].get(ci, "")
            if not txt:
                continue
            name, ndate, nunit = clean_name(g.cols[ci]["header"])
            lv = parse_level_cell(txt)
            rec = {"system": system, "contaminant": name, "sample_date": lab if is_date else ndate,
                   "level": lv["level"], "range_low": lv["lo"], "range_high": lv["hi"],
                   "unit": nunit or lv["unit"], "mcl": None, "mclg": None, "violation": None}
            if not is_date:
                rec["location"] = lab
            rec["_raw"] = name
            out.append(rec)
    return out


def records_from_grid(g, system, prev):
    kinds = classify_columns(g, prev)
    if "name" not in kinds:
        return []
    tr = transposed_records(g, kinds, system)
    if tr:
        return tr
    ni = kinds.index("name")
    place_param = None
    if PLACE_HDR.search(g.cols[ni].get("header", "")) and not re.search(r"contaminant|parameter|substance|analyte",
                                                                       g.cols[ni].get("header", ""), re.I):
        labels = [r["cells"].get(ni, "") for r in g.rows if r["cells"].get(ni)]
        if labels and not any(CONTAM_VOCAB.search(l) for l in labels):
            for cand in ([g.inner_title] if getattr(g, "inner_title", "") else []) + getattr(g, "title_lines", []) + [g.title]:
                place_param = title_parameter(cand)
                if place_param:
                    break
    if not is_results_table(g, kinds, place_ok=bool(place_param)):
        return []
    # first-column heading that is itself a contaminant ("Turbidity") over sub-row labels
    hdr_parent = None
    h0 = squash(g.cols[ni].get("header", ""))
    if h0 and CONTAM_VOCAB.search(h0) and len(h0) < 40 and \
            not re.search(r"contaminant|parameter|substance|analyte|constituent|name|compound|chemical", h0, re.I):
        hdr_parent = clean_name(h0)[0]
    if getattr(g, "kind", "") == "text" and not place_param and not hdr_parent:
        names = [r["cells"].get(ni, "") for r in g.rows if r["cells"].get(ni)]
        if names and sum(1 for n_ in names if CONTAM_VOCAB.search(n_)) < 0.5 * len(names):
            return []
    label_i = kinds.index("label") if "label" in kinds else None
    # groups (column groups for different sources)
    groups = []
    gl = [g.cols[i].get("group", "") for i in range(len(kinds))]
    distinct = []
    for i, k in enumerate(kinds):
        if k in ("value", "range", "low", "high", "range_or_level") and gl[i] and gl[i] not in distinct:
            distinct.append(gl[i])
    group_sets = []
    place_cols = getattr(g, "place_cols", [])
    if len(place_cols) >= 2:
        shared = [i for i in range(len(kinds)) if i not in place_cols]
        for i in place_cols:
            group_sets.append((squash(g.cols[i]["header"]), sorted([i] + shared)))
    elif len(distinct) >= 2:
        shared = [i for i in range(len(kinds)) if gl[i] not in distinct]
        for d in distinct:
            idx = [i for i in range(len(kinds)) if gl[i] == d]
            if any(kinds[i] == "value" for i in idx):
                group_sets.append((d, sorted(idx + shared)))
    if len(group_sets) < 2:
        group_sets = [(None, list(range(len(kinds))))]
    title_unit = None
    m = UNIT_PAREN.search(g.title or "")
    if m:
        title_unit = m.group(1)
    else:
        m = re.search(r"(?:units?|reported in|expressed in|measured in|results in|values in|levels in|in units of)\s*[:=]?\s*(" + UNIT_CORE + r")\b", g.title or "", re.I)
        if m:
            title_unit = m.group(1)
    out = []
    last = None
    pending = None
    parent = None
    val_cols = [i for i, k in enumerate(kinds) if k not in ("name", "label", "source", "health", "location", "category", "text", None)]
    rowdef = [i for i, k in enumerate(kinds) if k in ("value", "range", "low", "high", "range_or_level", "count", "mcl", "mclg", "advisory")]
    rows = []
    for r in g.rows:
        if r.get("section"):
            rows.append(r)
            continue
        cells = r["cells"]
        has_def = any(cells.get(i) for i in rowdef)
        if not has_def and rows and not rows[-1].get("section") and any(cells.get(i) for i in val_cols) and \
                (not cells.get(ni) or cells.get(ni) == rows[-1]["cells"].get(ni)):
            prevc = rows[-1]["cells"] = dict(rows[-1]["cells"])
            for i, t in cells.items():
                if i == ni:
                    continue
                if t and t != prevc.get(i):
                    prevc[i] = (prevc.get(i, "") + " " + t).strip()
            continue
        rows.append(r)
    for r in rows:
        if r.get("section"):
            pending = None
            continue
        cells = r["cells"]
        raw_name = cells.get(ni, "")
        has_vals = any(cells.get(i) for i in val_cols)
        if not has_vals:
            pending = raw_name if raw_name.strip() else None
            parent = pending if pending and not SECTION_RE.search(pending) and len(pending) < 60 else None
            if parent and last is not None and squash(parent) == squash(last["_raw"]):
                parent = pending = None
            continue
        if raw_name.strip() and parent and (SUBLABEL_RE.match(raw_name.strip()) or re.match(r"^[a-z]", raw_name.strip())):
            raw_name = parent + " - " + raw_name.strip()
        elif raw_name.strip() and parent:
            parent = None
        if not raw_name.strip():
            if pending:
                raw_name = pending
            elif last is None or (getattr(g, "kind", "") == "text" and not r.get("inherit")):
                continue
            else:
                raw_name = last["_raw"]
        pending = None
        # sub-row labelled as range of the row above
        if re.match(r"^\(?range\b", raw_name.strip(), re.I) and last is not None:
            for i in val_cols:
                if cells.get(i):
                    pr = parse_range(cells[i])
                    if pr:
                        last["range_low"], last["range_high"] = pr[0], pr[1]
                        break
            continue
        if hdr_parent and (SUBLABEL_RE.match(raw_name.strip()) or re.match(r"^[a-z%]", raw_name.strip())) and \
                not CONTAM_VOCAB.search(raw_name):
            raw_name = hdr_parent + " - " + raw_name.strip()
        name, ndate, nunit = clean_name(raw_name)
        place = None
        if place_param:
            place = name
            name, ndate2, nunit2 = clean_name(place_param)
            ndate, nunit = ndate or ndate2, nunit or nunit2
        if label_i is not None and cells.get(label_i) and not numericish(cells[label_i]):
            name = name + " - " + clean_name(cells[label_i])[0]
        if not name or not re.search(r"[A-Za-z]", name):
            continue
        if header_score(name) >= 3:
            continue
        for gname, idx in group_sets:
            recs = build_record(g, kinds, cells, idx, name, ndate, nunit, title_unit, system)
            for rec in recs:
                if gname:
                    rec["location"] = gname
                if place:
                    rec["location"] = place
                rec["_raw"] = raw_name
                out.append(rec)
                last = rec
    return out


def build_record(g, kinds, cells, idx, name, ndate, nunit, title_unit, system):
    rec = {"system": system, "contaminant": name, "sample_date": ndate, "level": None,
           "range_low": None, "range_high": None, "unit": None, "mcl": None, "mclg": None,
           "violation": None}
    other = {}
    level_i = None
    for i in idx:
        if kinds[i] == "value":
            level_i = i
            break
    count_promote = None
    if level_i is None:
        for i in idx:
            if kinds[i] == "count" and re.search(r"positive|detection|present|presence", g.cols[i].get("header", ""), re.I):
                count_promote = i
                level_i = i
                break
    units = {}
    lvl = None
    if level_i is not None and cells.get(level_i):
        lvl = parse_level_cell(cells[level_i])
        if lvl["locs"]:
            recs = []
            for (lab, v, lo, hi, u) in lvl["locs"]:
                sub = dict(cells)
                sub[level_i] = str(v) if v is not None else ""
                rr = build_record(g, kinds, sub, idx, name, ndate, nunit, title_unit, system)[0]
                rr["level"] = v
                if lo is not None or hi is not None:
                    rr["range_low"], rr["range_high"] = lo, hi
                if u and not rr["unit"]:
                    rr["unit"] = u
                rr["location"] = lab
                recs.append(rr)
            return recs
        rec["level"] = lvl["level"]
        if lvl["lo"] is not None or lvl["hi"] is not None:
            rec["range_low"], rec["range_high"] = lvl["lo"], lvl["hi"]
        units["level"] = lvl["unit"] or (unit_in_value(cells[level_i]) if lvl["text"] else None)
    lo_i = hi_i = None
    date_from_col = False
    for i in idx:
        k = kinds[i]
        txt = cells.get(i, "")
        hdr = g.cols[i].get("header", "") or g.cols[i].get("group", "")
        if k == "range":
            if txt:
                pr = parse_range(txt)
                if pr:
                    rec["range_low"], rec["range_high"] = pr[0], pr[1]
                    units.setdefault("range", pr[2])
                elif not is_null(txt):
                    other[hdr or "range"] = txt
        elif k == "range_or_level":
            if txt and not is_null(txt):
                pr = parse_range(txt)
                if pr and pr[0] != pr[1]:
                    rec["range_low"], rec["range_high"] = pr[0], pr[1]
                    units.setdefault("range", pr[2])
                elif rec["level"] is None and valueish(txt):
                    v, u = parse_scalar(txt)
                    rec["level"] = v
                    units["level"] = u
                else:
                    other[hdr or "range"] = txt
        elif k == "low":
            lo_i = i
            if txt:
                v, u = parse_scalar(txt)
                rec["range_low"] = v
                units.setdefault("range", u)
        elif k == "high":
            hi_i = i
            if txt:
                v, u = parse_scalar(txt)
                rec["range_high"] = v
                units.setdefault("range", u)
        elif k == "unit":
            if txt and not is_null(txt) and rec["unit"] is None:
                rec["unit"] = strip_marks(txt)
        elif k == "mcl":
            if txt and rec["mcl"] is None:
                v, u = parse_scalar(txt)
                if u and "unit" in kinds and not is_null(txt):
                    v = strip_marks(txt)
                rec["mcl"] = v
                units["mcl"] = u or (unit_in_value(txt) if isinstance(v, str) else None)
        elif k == "mclg":
            if txt and rec["mclg"] is None:
                v, u = parse_scalar(txt)
                rec["mclg"] = v
        elif k == "location":
            if txt and not is_null(txt):
                rec["location"] = strip_marks(txt)
        elif k == "date":
            if txt and not is_null(txt) and not date_from_col:
                rec["sample_date"] = strip_marks(txt)
                date_from_col = True
        elif k == "violation":
            if txt and rec["violation"] is None:
                rec["violation"] = parse_violation(txt)
        elif k == "compliance":
            if txt and rec["violation"] is None:
                rec["violation"] = parse_violation(txt, compliance=True)
        elif k in ("count", "advisory", "value"):
            if i != level_i and txt:
                v, u = parse_scalar(txt)
                if v is not None:
                    other[hdr or k] = v
    # unit precedence
    if rec["unit"] is None:
        hdr_unit = None
        if level_i is not None:
            hdr_unit = unit_in(g.cols[level_i].get("header", ""))
        for u in (nunit, units.get("level"), units.get("mcl"), hdr_unit, units.get("range"), title_unit):
            if u:
                rec["unit"] = u
                break
    if other:
        rec["other_values"] = other
    return [rec]


# ----------------------------------------------------------------------------
# system id
# ----------------------------------------------------------------------------

PWS_PAT = re.compile(
    r"(?:PWS\s*ID|PWSID|PWS\s*I\.?D\.?|P\.W\.S\.\s*I\.?D\.?|Public\s+Water\s+(?:Supply|System)\s+(?:ID|Identification|Number|No\.?|#)"
    r"(?:\s*(?:Number|No\.?|#))?|Water\s+System\s+(?:ID|No\.?|Number|#)|System\s+(?:ID|Number|No\.?)|PWS\s*(?:#|No\.?|Number)|"
    r"Federal\s+ID|ID\s*#|State\s+ID|WSSN|PWS)"
    r"\s*(?:\([A-Za-z]+\))?\s*(?:number|no\.?|#)?\s*[:#.\-]?\s*(?:is|=)?\s*[:#]?\s*"
    r"([A-Z]{0,2}[\s-]?\d[\d\s-]{3,12}\d|[A-Z]{2}\d{5,9})", re.I)


BARE_ID = re.compile(r"(?<![A-Za-z0-9])((?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|IA|ID|IL|IN|KS|KY|LA|MA|MD|ME|MI|MN|MO|MS|"
                     r"MT|NC|ND|NE|NH|NJ|NM|NV|NY|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VA|VT|WA|WI|WV|WY)\d{7})(?![A-Za-z0-9])")
PURCHASE = re.compile(r"purchas|supplied by|supplier|wholesale|buys?\b|bought|interconnect|provided by|"
                      r"comes from|receives? (?:its |our )?water|obtain|sells?\b|consecutive", re.I)


def find_systems(pages_text):
    found = []
    for pi, txt in enumerate(pages_text):
        for m in PWS_PAT.finditer(txt):
            sid = m.group(1).split("\n")[0].strip(" -")
            toks = sid.split()
            keep = []
            nd = 0
            for tk in toks:
                d_ = len(re.sub(r"\D", "", tk))
                if keep and (nd + d_ > 10 or (nd >= 7 and d_ > 0)):
                    break
                keep.append(tk)
                nd += d_
            sid = " ".join(keep).strip(" -")
            digits = re.sub(r"\D", "", sid)
            if len(digits) < 5 or len(digits) > 10:
                continue
            ls = txt.rfind("\n", 0, m.start())
            ls = txt.rfind("\n", 0, ls) if ls > 0 else 0
            le = txt.find("\n", m.end())
            ctx = txt[max(0, ls):le if le > 0 else len(txt)]
            a = m.start()
            for _ in range(4):
                a = txt.rfind("\n", 0, a) if a > 0 else 0
            b = m.end()
            for _ in range(4):
                nb = txt.find("\n", b + 1)
                b = nb if nb > 0 else len(txt)
            wide = txt[max(0, a):b]
            l0 = txt.rfind("\n", 0, m.start()) + 1
            l1 = txt.find("\n", m.end())
            own_line = txt[l0:l1 if l1 >= 0 else len(txt)]
            heading = m.start() <= 0.3 * max(1, len(txt)) and len(own_line.split()) <= 14 and \
                bool(re.search(r"report|consumer confidence|\bccr\b|annual|quality", wide, re.I))
            found.append((pi, m.start(), sid, bool(PURCHASE.search(ctx)), len(txt), heading))
    if not found:
        for pi, txt in enumerate(pages_text):
            for m in BARE_ID.finditer(txt):
                ls = max(0, txt.rfind("\n", 0, m.start()))
                le = txt.find("\n", m.end())
                ctx = txt[ls:le if le > 0 else len(txt)]
                found.append((pi, m.start(), m.group(1), bool(PURCHASE.search(ctx)), len(txt), False))
    return found


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------

class Timeout(Exception):
    pass


def _alarm(signum, frame):
    raise Timeout()


def page_texts(pdf_path, pdf):
    """Plain text per page (for the system ID); pdftotext is fast, pdfplumber is the fallback."""
    try:
        import subprocess
        out = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", pdf_path, "-"], capture_output=True,
                             timeout=30).stdout.decode("utf-8", "replace")
        pages = out.split("\f")
        if len(pages) >= len(pdf.pages):
            return [norm(t) for t in pages[:len(pdf.pages)]]
    except Exception:
        pass
    res = []
    for page in pdf.pages:
        try:
            res.append(norm(page.extract_text() or ""))
        except Exception:
            res.append("")
    return res


def extract(pdf_path, records=None):
    if records is None:
        records = []
    with pdfplumber.open(pdf_path) as pdf:
        systems = find_systems(page_texts(pdf_path, pdf))
        prev = None
        for pno, page in enumerate(pdf.pages):
            try:
                words = page_words(page)
            except Timeout:
                raise
            except Exception:
                words = []
            grids = []
            if not NOLATTICE:
                try:
                    grids = lattice_grids(page, words, pno)
                except Timeout:
                    raise
                except Exception:
                    grids = []
            try:
                grids += text_grids(words, [gr.bbox for gr in grids], pno, float(page.width), horizontal_rules(page))
            except Timeout:
                raise
            except Exception:
                pass
            for gr in grids:
                gr.title = squash((title_above(words, gr) + " " + gr.title))
            for gr in sorted(grids, key=lambda x: x.top):
                try:
                    sysid = system_for(systems, pno, gr.top / float(page.height or 792))
                    if not gr.has_header and prev is not None:
                        if len(prev.cols) == len(gr.cols):
                            for a, b in zip(gr.cols, prev.cols):
                                a["header"], a["group"] = b.get("header", ""), b.get("group", "")
                        elif abs(prev.cols[0]["x0"] - gr.cols[0]["x0"]) < 12 and \
                                abs(prev.cols[-1]["x1"] - gr.cols[-1]["x1"]) < 12:
                            for a in gr.cols:
                                best = max(prev.cols, key=lambda b: overlap(a["x0"], a["x1"], b["x0"], b["x1"]))
                                if overlap(a["x0"], a["x1"], best["x0"], best["x1"]) > 0.5 * (a["x1"] - a["x0"]):
                                    a["header"], a["group"] = best.get("header", ""), best.get("group", "")
                    recs = records_from_grid(gr, sysid, prev)
                except Timeout:
                    raise
                except Exception:
                    recs = []
                if DEBUG:
                    dump_grid(gr)
                if recs:
                    prev = gr
                records.extend(recs)
            try:
                page.flush_cache()
            except Exception:
                pass
    for r in records:
        r.pop("_raw", None)
    return records


DEBUG = False
NOLATTICE = False
RELAX = False


def dump_grid(g):
    kinds = classify_columns(g, None)
    print("=== page", g.page + 1, "bbox", [round(x) for x in g.bbox], "title:", g.title[:80])
    for c, k in zip(g.cols, kinds):
        print("   col %4.0f-%4.0f %-10s %r | group %r" % (c["x0"], c["x1"], k, c.get("header", "")[:60], c.get("group", "")[:40]))
    for r in g.rows:
        print("   ROW", {i: v[:40] for i, v in sorted(r["cells"].items())})


def title_above(words, g):
    bx0, btop, bx1, _ = g.bbox
    above = [w for w in words if w.bottom <= btop + 1 and w.bottom >= btop - 40 and w.x1 >= bx0 - 5 and w.x0 <= bx1 + 5]
    if not above:
        g.title_lines = []
        return ""
    lns = [ln for ln in lines_of(above) if sum(1 for w in ln if numtok(w.text)) < 2]
    g.title_lines = [" ".join(w.text for w in ln) for ln in lns[-2:]][::-1]
    return " ".join(" ".join(w.text for w in ln) for ln in lns[-2:])


def system_for(systems, pno, frac=0.0):
    if not systems:
        return None
    own = [s for s in systems if not s[3]] or systems
    ids = [s[2] for s in own]
    if len(set(ids)) == 1:
        return ids[0]
    counts = {}
    for i in ids:
        counts[i] = counts.get(i, 0) + 1
    heads = set(s_[2] for s_ in own if s_[5])
    if len(heads) < 2:
        best = max(counts.values())
        for s_ in own:
            if counts[s_[2]] == best:
                return s_[2]
    before = [s for s in own if s[0] < pno or (s[0] == pno and s[1] <= frac * s[4])]
    if before:
        return before[-1][2]
    return own[0][2]


def main(pdf_path, out_path):
    global RELAX
    records = []
    try:
        import signal
        signal.signal(signal.SIGALRM, _alarm)
        signal.alarm(100)
    except Exception:
        pass
    try:
        extract(pdf_path, records)
        if not records:
            RELAX = True
            extract(pdf_path, records)
    except (Exception, Timeout) as e:  # never fail without output
        sys.stderr.write("extract stopped: %r\n" % (e,))
    try:
        import signal
        signal.alarm(0)
    except Exception:
        pass
    for r in records:
        r.pop("_raw", None)
    clean = []
    for r in records:
        try:
            json.dumps(r)
            clean.append(r)
        except (TypeError, ValueError):
            pass
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({"records": clean}, fh, indent=1, ensure_ascii=True)


if __name__ == "__main__":
    if "debug" in sys.argv[3:]:
        DEBUG = True
    if "nolattice" in sys.argv[3:]:
        NOLATTICE = True
    main(sys.argv[1], sys.argv[2])
