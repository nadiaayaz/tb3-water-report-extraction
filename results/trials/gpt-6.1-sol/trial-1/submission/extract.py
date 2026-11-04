#!/usr/bin/env python3
"""Offline extraction of Consumer Confidence Report result tables.

The extractor combines PDF ruling geometry with the semantics of column headings.
It deliberately does not substitute regulatory defaults or infer sample years.
"""
import collections
import io
import json
import math
import os
import re
import statistics
import subprocess
import sys
import shutil
import signal
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import pdfplumber
from pdfplumber.utils import extract_text

DEBUG = bool(os.environ.get('CCR_DEBUG'))
START = time.monotonic()
OCR_DPI = 300
_PROGRESS_RECORDS=[]


class ExtractionDeadline(BaseException):
    pass


def deadline_handler(signum,frame):
    global START
    START=min(START,time.monotonic()-111)
    raise ExtractionDeadline()


def log(*args):
    if DEBUG:
        print(*args, file=sys.stderr, flush=True)


def clean(s):
    if s is None:
        return ''
    s = str(s).replace('\uf06d', 'µ').replace('μ', 'µ').replace('\uf0a3', '≤').replace('\uf0b3', '≥')
    s = s.replace('\ufb01', 'fi').replace('\ufb02', 'fl').replace('\u00ad', '')
    s = re.sub(r'([A-Za-z])-\s*\n\s*([a-z])', r'\1\2', s)
    s = re.sub(r'(?<=\S)-[ \t]*\n\s*(?=\d)', '-', s)
    return re.sub(r'\s+', ' ', s).strip()


def unfoot(s):
    s = re.sub(r'[¹²³⁴⁵⁶⁷⁸⁹⁰*†‡§]+', '', s)
    s = re.sub(r'\[\s*\d+(?:\s*[,–-]\s*\d+)*\s*\]', '', s)
    return clean(s)


NULL_RE = re.compile(r'^(?:n\s*[/.]?\s*a\.?|none|not applicable|no range|[-–—_]+|see\s*(?:foot)?note\s*\(?\d*\)?\.?|not available)?$', re.I)
UNIT_PATTERN = (r'(?:p\s*Ci\s*/\s*[lL]|[µμunmcp]g\s*/\s*[lL]|mg\s*/\s*kg|'
                r'mrem\s*/\s*(?:yr|year)|mrem(?:s)?(?:\s+per\s+year)?|'
                r'ppt|ppb|ppm|ppq|parts\s+per\s+(?:million|billion|trillion|quadrillion)|NTU|[µμu]S\s*/\s*cm|'
                r'[µμu]mhos?\s*/\s*cm|micromhos?\s*/\s*cm|microsiemens\s*(?:/|per)\s*(?:cm|centimeter)|CFU\s*/\s*100\s*m[lL]|'
                r'MPN\s*/\s*100\s*m[lL]|(?:oo)?cysts\s*/\s*[lL]|MFL|TON|PCU|CU|S\.?U\.?|grains(?:\s*(?:/|per)\s*gal(?:lon)?)?|gpg|'
                r'units|standard units|pH units|[°º]\s*[CF])')
UNIT_RE = re.compile(r'(?<![A-Za-z])(' + UNIT_PATTERN + r')(?![A-Za-z])(?:\s+\(?as\s+(?:CaCO3|CaCO₃|NO3|NO₃|Cl2|Cl₂|N|P)\)?)?', re.I)
NUM = r'(?:[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d*)?|[+-]?\.\d+)(?:[eE][+-]?\d+)?'
ATOM = r'(?:[<>≤≥]=?\s*)?(?:' + NUM + r')\s*%?|N\.?D\.?|[Nn]on[- ]?[Dd]etect(?:ed|s)?|[Aa]bsent'
PAIR_RE = re.compile(r'^\s*\(?\s*(' + ATOM + r')\s*(?:[-–—−]|\bto\b|\bthrough\b)\s*(' + ATOM + r')\s*\)?\s*$', re.I)
ATOM_RE = re.compile(r'^\s*(?:' + ATOM + r')\s*$', re.I)
MONTH = r'(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?'
DATE_TOKEN = r'(?:(?:19|20)\d{2}-\d{1,2}-\d{1,2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{1,2}/\d{2,4}|'+MONTH+r'\s+(?:\d{1,2},?\s+)?(?:19|20)\d{2}|(?:19|20)\d{2})'
DATE_FIND = re.compile(r'(?<![\d/])'+DATE_TOKEN+r'(?![\d/])',re.I)


def get_unit(s):
    s = unfoot(s or '')
    m = UNIT_RE.search(s)
    if not m:
        return None
    u = m.group(0)
    if u.lower() == 'units':
        return None
    return re.sub(r'\s*/\s*', '/', u).strip()


def remove_unit(s):
    return UNIT_RE.sub('', s).strip(' ()')


def scalar(s, kind='value'):
    s = unfoot(s or '')
    s = re.sub(r'(?<=\d)\s*\([a-d]\)\s*$','',s,flags=re.I)
    if NULL_RE.fullmatch(s):
        return None
    # A unit is removed only from a pure scalar, never from a sentence or AL=15.
    stripped = remove_unit(s)
    if re.fullmatch(NUM, stripped):
        if kind == 'mcl' and re.search(r'\bNTU\b', s, re.I):
            return s
        try:
            n = float(stripped.replace(',', ''))
            if not math.isfinite(n):
                return s
            return int(n) if n.is_integer() else n
        except ValueError:
            pass
    if re.fullmatch(r'[<>≤≥]=?\s*' + NUM + r'\s*%?', stripped):
        return stripped
    if re.fullmatch(NUM + r'\s*%', stripped):
        return stripped.replace(' %', '%')
    return s


def parse_range(s):
    s = unfoot(s or '')
    if NULL_RE.fullmatch(s):
        return None, None
    s = remove_unit(s).strip()
    labelled=re.search(r'(?:lowest|low|minimum|min)\s*[:=]?\s*('+ATOM+r').*?(?:highest|high|maximum|max)\s*[:=]?\s*('+ATOM+r')',s,re.I)
    if labelled:
        return scalar(labelled.group(1)),scalar(labelled.group(2))
    # Labelled maxima/averages in range cells are extra values, not ranges.
    s = re.split(r'\b(?:Highest|Maximum|Max\.?|Average|Avg\.?|LRAA|RAA)\s*(?:result|value|level|LRAA|RAA)?\s*[:=]', s, flags=re.I)[0].strip(' ;/,')
    s = re.sub(r'^(?:range(?:\s+of\s+(?:results|detections|values|samples))?\s*[:=]?\s*)', '', s, flags=re.I)
    m = PAIR_RE.fullmatch(s)
    if m:
        return scalar(m.group(1)), scalar(m.group(2))
    if ATOM_RE.fullmatch(s.strip('()')):
        v = scalar(s.strip('()'))
        return v, v
    # A pair may be followed by a labelled additional measurement.
    m = re.match(r'^\(?\s*(' + ATOM + r')\s*[-–—−]\s*(' + ATOM + r')\s*\)?(?:\s*[/;(]|\s+[A-Za-z])', s, re.I)
    if m:
        return scalar(m.group(1)), scalar(m.group(2))
    return None, None


def parse_level(s, heading=''):
    s = unfoot(s or '')
    if NULL_RE.fullmatch(s):
        return None, None, None
    lo = hi = None
    rm = re.search(r'\brange(?:\s+of\s+(?:results|detections|values))?\s*[:=]?\s*(.*)', s, re.I)
    if rm:
        lo, hi = parse_range(rm.group(1))
        s = s[:rm.start()].strip(' /;,')
    # In a level/range cell the parenthesis is a range, not a second level.
    for m in re.finditer(r'\(([^()]*)\)', s):
        a, b = parse_range(m.group(1))
        if a is not None and b is not None and PAIR_RE.fullmatch(remove_unit(m.group(1))):
            lo, hi = a, b
            s = s[:m.start()].strip()
            break
    if re.search(r'range\s*(?:or|/)\s*(?:level|result)|(?:level|result)\s*(?:or|/)\s*range', heading, re.I):
        a, b = parse_range(s)
        pair=PAIR_RE.fullmatch(remove_unit(s)) or re.match(r'^\(?\s*(?:'+ATOM+r')\s*(?:[-–—−]|\bto\b)\s*(?:'+ATOM+r')\s*\)?\s*[/;(]',remove_unit(s),re.I)
        if a is not None and pair:
            return None, a, b
    # Strip prose labels that identify the statistic; retain real textual values.
    s = re.sub(r'^(?:(?:90(?:th|%)?\s*percentile(?:\s+value)?)|(?:LRAA|RAA|MRAA|MPA)|(?:highest|maximum|max\.?|average|avg\.?|mean|median|minimum|min\.?)(?:\s+(?:annual|monthly|quarterly|running|single|test|sample|detected|result|value|level|average|measurement|measure|LRAA|RAA|MRAA))*)\s*[:=]?\s*', '', s, flags=re.I)
    # A slash separated list of labelled statistics uses the first one.
    s = re.split(r'\s*[/;]\s*(?=(?:average|avg|max|min|highest|lowest|LRAA|RAA)\b)', s, flags=re.I)[0]
    s = re.sub(r'\s*\((?:LRAA|RAA|MRA|MPA|90th percentile|average|avg\.?|maximum|max\.?)\)\s*$', '', s, flags=re.I)
    return scalar(s), lo, hi


def date_value(s):
    s = unfoot(s or '')
    if NULL_RE.fullmatch(s):
        return None
    if re.search(r'\bnext\b|\bfuture\b|scheduled',s,re.I):
        before=re.split(r'\bnext\b|\bfuture\b|scheduled',s,flags=re.I)[0].strip(' ,;()-')
        return before if re.search(r'\d',before) else None
    return s


def contaminant_name(s):
    s = unfoot(s or '')
    sample = None
    # Preserve ordinary chemical parentheses, extract only dates, units and test notes.
    for m in list(re.finditer(r'\(([^()]*)\)', s))[::-1]:
        inn = m.group(1)
        u = get_unit(inn)
        # Parentheses may contain both a unit and the year of the test.
        if u and not re.search(r'next|future',inn,re.I):
            yd=re.search(r'\b(?:19|20)\d{2}\b',inn)
            if yd:
                sample=yd.group(0)
        datepart=re.sub(r'^(?:(?:last\s+|most recent\s+)?(?:tested|sampled|collected|analyzed)(?:\s+in|\s+on)?\s*[:=-]?\s*)','',inn,flags=re.I)
        datepart=UNIT_RE.sub('',datepart).strip(' ,;')
        isdate=bool(re.fullmatch(DATE_TOKEN+r'(?:\s*(?:[,;&/–-]|\bto\b|\band\b)\s*'+DATE_TOKEN+r')*',datepart,re.I))
        testnote = bool(re.search(r'test(?:ed|ing)?|sampl(?:ed|ing)|at risk homes', inn, re.I) or (DATE_FIND.search(inn) and re.search(r'result|analy[sz]|\byear\b|\bdate\b|\bsamples?\b',inn,re.I)))
        if isdate or testnote:
            if not re.search(r'next|future', inn, re.I):
                dm=DATE_FIND.search(inn)
                if isdate:
                    sample=datepart
                elif dm:
                    sample = dm.group(0)
            s = s[:m.start()] + s[m.end():]
        elif u and (clean(UNIT_RE.sub('', inn)).strip(' ,;') == '' or re.match(r'^(?:pp[mbtq]|[µumnp]g/|pCi/|NTU)', inn, re.I)):
            s = s[:m.start()] + s[m.end():]
    # A few templates print test years without parentheses.
    dm = re.search(r'\b(?:tested|sampled|collection date|last test)(?:\s+in|\s+on)?\s*[:=-]?\s*(\d{1,2}/\d{1,2}/\d{2,4}|(?:19|20)\d{2})', s, re.I)
    if dm and not re.search(r'next\s*$', s[max(0, dm.start()-8):dm.start()], re.I):
        sample = dm.group(1)
        s = s[:dm.start()]
    tm=re.search(r'\b(?:tested|sampled|last test|collection date)\b',s,re.I)
    if tm and s[:tm.start()].strip():
        tail=s[tm.start():]
        if not re.search(r'next|future',tail,re.I):
            dt=DATE_FIND.search(tail)
            if dt:
                sample=dt.group(0)
        s=s[:tm.start()]
    s = re.split(r'\bnext\s+(?:test|testing|sample|sampling)', s, flags=re.I)[0]
    if sample is None:
        dm = DATE_FIND.search(s)
        if dm:
            sample=dm.group(0)
            s=s[:dm.start()]+s[dm.end():]
    s = UNIT_RE.sub('',s)
    s = re.sub(r'^\s*\d+[.)](?:\s+|(?=[A-Za-z]))', '', s)
    return clean(s).strip(' ,;:-–'), sample


def header_text(s):
    h=clean(s)
    h=re.sub(r'(?<!\w)([A-Za-z](?:\s+[A-Za-z]){1,})(?!\w)',lambda m:re.sub(r'\s+','',m.group(1)),h)
    if h.lower()[::-1] in ('contaminant','contaminants','substance','parameter','mcl','mclg','mrdl','mrdlg','violation','range','units','average','date','year'):
        h=h[::-1]
    return h


def heading_role(s):
    h = header_text(s).lower().replace('‐', '-')
    h = re.sub(r'\([^)]*\)', lambda m: ' '+m.group(0)[1:-1]+' ', h)
    if not h:
        return 'unknown'
    if re.search(r'next\s*(?:test|sampl|monitor)|future|sampling frequency|testing frequency|scheduled',h):
        return 'schedule'
    if re.search(r'not detected|not found',h) and not re.search(r'level|result|average|amount',h):
        return 'non_detect_list'
    if re.search(r'likely|typical|major|possible|common|potential|source[s]?\s+of|origin|health effect|purpose', h) and not re.search(r'level|detected|result|average', h):
        return 'source'
    if re.search(r'violation|exceed(?:s|ed)?\s*(?:the\s*)?(?:mcl|al|action|standard|limit)|(?:mcl|\bal\b).*(?:exceed|yes\s*/\s*no)|over\s+(?:mcl|al)\??$', h) and not re.search(r'number|#|no\. of|sites|samples', h):
        return 'violation'
    if re.search(r'compliance|meets?\s+(?:the\s*)?(?:standard|requirement)|met\s+(?:the\s*)?standard|^pass\b', h) and not re.search(r'number|quarters|#', h):
        return 'compliance'
    if re.search(r'mclg|mrdlg|\bml?dg\b|\bphg\b|\balg\b|\bgoal\b|maximum contaminant level goal|maximum residual disinfectant level goal', h):
        return 'mclg'
    if re.search(r'comparison|health advisory|health.based|health reference|health standard|\bhrl\b|notification|reference|screening|advisory|guideline|(?:method )?detection limit|reporting limit|\b(?:mdl|mrl|rl)\b', h):
        return 'advisory'
    if re.search(r'number|#|\bno\.?\s+(?:of|positive)|sample size|sample count|positive samples|total samples|samples collected|(?:samples|sites|homes|tests)\s+(?:tested|collected|analyzed|performed|conducted)|times tested|(?:sites|samples|homes).*(?:above|over|exceed)|^count$',h):
        return 'count'
    if re.search(r'\bmcl\b|\bsmcl\b|\bmrdl\b|action level|regulatory limit|highest level allowed|maximum allowed|allowable|minimum (?:disinfectant|residual)|removal required|removal.*required|required.*removal|\blimit\b|^tt$|treatment technique|(?:federal|state|epa) standard|epa.*?\ba level', h):
        return 'mcl'
    if re.search(r'(?:sample|sampling|collection|test|monitoring)?\s*(?:date|year)|when tested|last sampled|last tested|last analyzed|(?:sampl\w*|test).*period|period.*(?:sampl|test)', h) and not re.search(r'result|level|range|average', h):
        return 'date'
    if re.search(r'\bunits?\b|unitsof|unitof', h) and not re.search(r'level|range|result|average|highest|lowest|maximum|minimum', h):
        return 'unit'
    if re.search(r'contaminant|substance|constituent|parameter|analyte|chemical name|compound|organism|microbe|\belements?\b|\bmetals?\b|characteristic|indicator', h) and not re.search(r'level|amount|detected|range|goal|limit|source', h):
        return 'name'
    if re.search(r'number|#|no\.\s*(?:of|positive)|sites above|samples above|homes with|individual results.*(?:greater|above)|total sites', h):
        return 'count'
    if re.search(r'range\s*(?:or|/)\s*(?:level|result)|(?:level|result)\s*(?:or|/)\s*range', h):
        return 'combined'
    if re.search(r'\b(?:low|lowest|min|minimum)\b\s*[-–/,]\s*\b(?:high|highest|max|maximum)\b',h):
        return 'range'
    if 'range' in h and not (re.search(r'level detected|average|avg\.|90th', h) and re.search(r'\brange\b', h)):
        return 'range'
    if re.fullmatch(r'(?:low|high|lowest|highest|min(?:imum)?|max(?:imum)?)(?:\s+(?:level|detected|result|value))?', h):
        return 'low' if re.search(r'low|min', h) else 'high'
    if re.search(r'level|found|detect|result|amount|average|\bavg\b|\bmean\b|median|percentile|90%|maximum|minimum|highest|lowest|removal|ratio|concentration|value|measure|(?:your|our) water', h):
        return 'level'
    if re.search(r'location|plant|entry point|sampling point|site|well|area|station|water system|source', h):
        return 'location'
    return 'unknown'


def is_name_header(s):
    h = header_text(s)
    return bool(re.match(r'^(?:(?:regulated|unregulated|detected|primary|secondary|inorganic|organic|radioactive|radiological|microbiological|microbial|water quality|drinking water|other|epa|name of)\b\s*[&/–-]?\s*)*(contaminant|substance|constituent|parameter|analyte|chemical|compound|organism|microbe|element|metal|characteristic|indicator)s?\b', h, re.I)) and len(h) < 180 and not re.search(r'\b(?:of|for) (?:the )?(?:contaminants|substances)\b|source|health effects|not detected', h, re.I)


def plain_name_header(s):
    base=header_text(s).split('(')[0].strip()
    return bool(re.fullmatch(r'(?:(?:regulated|unregulated|detected|primary|secondary|inorganic|organic|microbiological|radiological|water quality|chemical)\s+)*(?:contaminant|substance|constituent|parameter|analyte|chemical|compound|organism|microbe|element|metal|characteristic|indicator)s?(?:\s+(?:and|&|name|units?|of|measure(?:ment)?))*',base,re.I))


def value_like(s):
    s = unfoot(s)
    return bool(re.search(r'\d', s) or re.fullmatch(r'(?:ND|N/?A|Absent|Present|Negative|Positive|TT|No|Yes|N|Y|None|BDL|BD)', s, re.I)) and len(s) < 180


@dataclass
class Cell:
    box: tuple
    text: str


@dataclass
class TablePart:
    bbox: tuple


def table_sections(cells,bbox):
    candidates=[]
    functional={'mcl','mclg','unit','date','level','range','combined','violation','compliance','count'}
    for c in cells:
        if not is_name_header(c.text) or c.box[2]-c.box[0]>(bbox[2]-bbox[0])*.6:
            continue
        if not plain_name_header(c.text):
            continue
        strength=0
        for d in cells:
            if d is c or heading_role(d.text) not in functional:
                continue
            height=min(c.box[3],d.box[3])-max(c.box[1],d.box[1])
            if height>min(c.box[3]-c.box[1],d.box[3]-d.box[1])*.4:
                strength+=1
        if strength:
            candidates.append((c,strength))
    candidates.sort(key=lambda z:z[0].box[1])
    heads=[]
    for c,strength in candidates:
        if heads and (c.box[1]<heads[-1][0].box[3]-1 or c.box[1]-heads[-1][0].box[1]<18):
            old,oscore=heads[-1]
            if (strength,-(c.box[2]-c.box[0]))>(oscore,-(old.box[2]-old.box[0])):
                heads[-1]=(c,strength)
        else:
            heads.append((c,strength))
    if len(heads)<2:
        return [(bbox,cells)]
    boundaries=[bbox[1]]+[c.box[1] for c,_ in heads[1:]]+[bbox[3]]
    out=[]
    for y0,y1 in zip(boundaries,boundaries[1:]):
        cc=[]
        for c in cells:
            if c.box[1]<y1-.5 and c.box[3]>y0+.5:
                cc.append(Cell((c.box[0],max(y0,c.box[1]),c.box[2],min(y1,c.box[3])),c.text))
        out.append(((bbox[0],y0,bbox[2],y1),cc))
    return out


class PageData:
    def __init__(self, page, num):
        self.page = page
        self.num = num
        self.chars = page.chars
        self._textcache = {}
        self._filtered = None
        self._sections = None
        self._words = None
        self.ocr_image=None
        self._ocr_cell_cache={}

    def text(self, box, superscripts=True, skip_sections=False, whole_words=False):
        key = tuple(round(x, 2) for x in box), superscripts, skip_sections,whole_words
        if key in self._textcache:
            return self._textcache[key]
        x0, y0, x1, y1 = box
        if whole_words:
            if self._words is None:
                self._words=self.page.extract_words(x_tolerance=1.5,y_tolerance=2)
            ws=[w for w in self._words if x0<=(w['x0']+w['x1'])/2<x1 and y0<=(w['top']+w['bottom'])/2<y1]
            chars=[c for c in self.chars if (c['text'].isspace() and x0<=(c['x0']+c['x1'])/2<x1 and y0<=(c['top']+c['bottom'])/2<y1) or any(w['x0']-.01<=(c['x0']+c['x1'])/2<=w['x1']+.01 and w['top']-.01<=(c['top']+c['bottom'])/2<=w['bottom']+.01 for w in ws)]
        else:
            chars = [c for c in self.chars if x0 <= (c['x0']+c['x1'])/2 < x1 and y0 <= (c['top']+c['bottom'])/2 < y1]
        if skip_sections:
            if self._sections is None:
                self._sections=[]
                for line in word_lines(self.page.extract_words(x_tolerance=1.5,y_tolerance=2)):
                    txt=clean(' '.join(v['text'] for v in line))
                    if re.fullmatch(r'(?:inorganic|organic|microbiological|radioactive|synthetic organic|unregulated|regulated|secondary|disinfection).*?(?:contaminants|byproducts|parameters)[\d*]*',txt,re.I):
                        self._sections.append((min(v['top'] for v in line)-.5,max(v['bottom'] for v in line)+.5))
            chars=[c for c in chars if not any(a<=(c['top']+c['bottom'])/2<b for a,b in self._sections)]
        if superscripts and chars:
            # Footnote numbers are both smaller and raised relative to their text.
            kept = []
            for c in chars:
                if c['text'] in '0123456789abcdefghijklmnopqrstuvwxyz' and c.get('size', 0):
                    neighbours = [d for d in chars if d is not c and abs(d['x1']-c['x0']) < max(3, c['size']*.65) and d.get('size', 0) >= c['size']*.95 and d['bottom'] > c['bottom'] + c['size']*.22 and abs(d['top']-c['top']) < d['size']*.8]
                    if neighbours:
                        continue
                kept.append(c)
            chars = kept
        mapped=[]
        for c in chars:
            font=c.get('fontname','').lower()
            value=c['text']
            if 'zapfdingbats' in font:
                value={'3':'✓','4':'✓','5':'✗','6':'✗','7':'✗','8':'✗','n':'■'}.get(value,value)
            elif 'wingdings2' in font or 'wingdings 2' in font:
                value={'P':'✓','R':'✓','O':'✗','Q':'✗'}.get(value,value)
            elif 'wingdings' in font:
                value={'ü':'✓','þ':'✓','û':'✗','ý':'✗','\uf0fc':'✓','\uf0fe':'✓','\uf0fb':'✗','\uf0fd':'✗'}.get(value,value)
            mapped.append(dict(c,text=value) if value!=c['text'] else c)
        chars=mapped
        out = extract_text(chars, x_tolerance=1.5, y_tolerance=2) if chars else ''
        self._textcache[key] = out
        return out


def native_cells(pd, table):
    boxes=[tuple(b) for b in table.cells]
    if pd.ocr_image is not None and time.monotonic()-START<98:
        missing=[b for b in boxes if b not in pd._ocr_cell_cache]
        with ThreadPoolExecutor(max_workers=4) as pool:
            for b,txt in zip(missing,pool.map(lambda bb:ocr_cell(pd,bb),missing)):
                pd._ocr_cell_cache[b]=txt
        result=[]
        for b in boxes:
            txt=pd._ocr_cell_cache.get(b) or pd.text(b)
            old=clean(pd.text(b));new=clean(txt)
            if re.fullmatch(r'\d+',old) and re.fullmatch(r'\d+',new) and len(old)==len(new)+1 and old.endswith(new):
                txt=old
            result.append(Cell(b,txt))
        return result
    return [Cell(b,pd.text(b)) for b in boxes]


def ocr_cell(pd,box):
    if time.monotonic()-START>102:
        return ''
    im=pd.ocr_image
    sx=im.width/pd.page.width;sy=im.height/pd.page.height
    a,b,c,d=box
    if c-a<4 or d-b<4:
        return ''
    crop=im.crop((max(0,int(a*sx)+2),max(0,int(b*sy)+2),min(im.width,int(c*sx)-1),min(im.height,int(d*sy)-1)))
    if crop.width<4 or crop.height<4:
        return ''
    from PIL import ImageOps,Image
    if crop.height<100 or crop.width<160:
        crop=crop.resize((crop.width*2,crop.height*2),getattr(Image,'Resampling',Image).LANCZOS)
    crop=ImageOps.expand(crop,border=12,fill='white')
    buf=io.BytesIO();crop.save(buf,format='PNG',dpi=(OCR_DPI,OCR_DPI))
    try:
        result=subprocess.run([shutil.which('tesseract'),'stdin','stdout','-l','eng','--psm','6'],input=buf.getvalue(),stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=dict(os.environ,OMP_THREAD_LIMIT='1'),timeout=7,check=True)
        txt=result.stdout.decode('utf-8','replace').strip()
        return unit_glyphs(crop,txt)
    except Exception:
        return ''


def unit_glyphs(image,text):
    """Resolve the micro sign from its descender, rather than guessing a unit."""
    compact=re.sub(r'\s+','',text)
    match=re.fullmatch(r'([A-Za-zµμ`\'"]?)g/([lLI1|])',compact)
    if not match:
        return text
    try:
        im=image.convert('L');w,h=im.size;buf=im.tobytes()
        dark={i for i,v in enumerate(buf) if v<140}
        components=[]
        while dark:
            start=dark.pop();todo=[start];points=[start]
            while todo:
                p=todo.pop();x=p%w;y=p//w
                for dx,dy in ((-1,0),(1,0),(0,-1),(0,1),(-1,-1),(1,-1),(-1,1),(1,1)):
                    xx=x+dx;yy=y+dy
                    if 0<=xx<w and 0<=yy<h:
                        q=yy*w+xx
                        if q in dark:
                            dark.remove(q);todo.append(q);points.append(q)
            xs=[p%w for p in points];ys=[p//w for p in points]
            if len(points)>4:
                components.append((min(xs),min(ys),max(xs),max(ys),set(points)))
        if not components:
            return text
        tallest=max(z[3]-z[1]+1 for z in components)
        components=[z for z in components if z[3]-z[1]+1>=tallest*.45]
        components.sort(key=lambda z:z[0])
        if len(components)!=4:
            return text
        first,last=components[0],components[-1]
        if abs((first[1]+first[3]-last[1]-last[3])/2)>tallest*.6:
            return text
        # A p has a closed counter; a micro sign has an open top like a u.
        a,b,c,d,ink=first
        white={(x,y) for y in range(b,d+1) for x in range(a,c+1) if y*w+x not in ink}
        flood=[p for p in white if p[0] in (a,c) or p[1] in (b,d)]
        for p in flood:
            white.discard(p)
        while flood:
            x,y=flood.pop()
            for p in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
                if p in white:
                    white.remove(p);flood.append(p)
        prefix=match.group(1)
        if first[3]-last[3]>max(2,tallest*.08) and not white:
            prefix='µ'
        end=match.group(2)
        if end not in 'lL':
            end='L' if (last[2]-last[0]+1)/(last[3]-last[1]+1)>.4 else 'l'
        return prefix+'g/'+end
    except Exception:
        return text


def overlap(a, b):
    return max(0, min(a[2], b[2])-max(a[0], b[0])) * max(0, min(a[3], b[3])-max(a[1], b[1]))


def header_candidate(c):
    return is_name_header(c.text)


def find_schema(pd, cells, bbox, parameter=None):
    name_headers = [c for c in cells if header_candidate(c) and c.box[2]-c.box[0] < (bbox[2]-bbox[0])*.60]
    if not name_headers and parameter:
        name_headers = [c for c in cells if re.fullmatch(r'(?:treatment\s+|sampling\s+)?(?:plant|well|site|area|location|station|source|water system|point|entry point)s?(?:\s+(?:name|id|number))?',clean(c.text),re.I)]
    if not name_headers:
        return None
    def header_rank(c):
        base=header_text(c.text).split('(')[0].strip()
        plain=bool(re.fullmatch(r'(?:(?:regulated|unregulated|detected|primary|secondary|water quality)\s+)?(?:contaminant|substance|constituent|parameter|analyte|chemical|compound)s?(?:\s+(?:and|&|name|units?|of|measure(?:ment)?))*',base,re.I))
        return (0 if plain else 1,c.box[2]-c.box[0],c.box[1],c.box[0])
    nameh = min(name_headers, key=header_rank)
    nx0, hy0, nx1, hy1 = nameh.box
    names = []
    for c in cells:
        if c.box[1] < hy1-1 or not c.text.strip():
            continue
        if c.box[0] < nx0-4 or c.box[2] > nx1+5 or c.box[2]-c.box[0] < (nx1-nx0)*.4:
            continue
        s = clean(c.text)
        if plain_name_header(s) or len(s) > 220 or (not re.search('[A-Za-z]', s) and not (parameter and value_like(s))):
            continue
        names.append(c)
    if not names:
        return None
    # A populated result row supplies the true columns, with the Word fragments removed.
    candidates = []
    names.sort(key=lambda c:c.box[1])
    for nameidx,nc in enumerate(names):
        end = names[nameidx+1].box[1] if nameidx+1 < len(names) else bbox[3]
        for yy in sorted(set(c.box[1] for c in cells if nc.box[1]-.5 <= c.box[1] < end-.5)):
            row = [c for c in cells if c.box[1] <= yy+.5 and c.box[3] > yy+1 and c.text.strip() and c.box[2]-c.box[0] > 5]
            if nc not in row:
                row.append(nc)
            row.sort(key=lambda c: c.box[0])
            if len(row) < 2:
                continue
            nv = sum(value_like(c.text) for c in row if c is not nc)
            if nv < 1:
                continue
            candidates.append((len(row) + min(nv, 6)*.3, nc, row))
    if not candidates:
        return None
    _, best_name, row = max(candidates, key=lambda r: r[0])
    first_data = min(nc.box[1] for _, nc, rr in candidates)
    # Drop spanning titles from the header rectangle.
    htop = bbox[1]
    for c in cells:
        if c.box[3] <= hy0+1 and c.box[2]-c.box[0] > (bbox[2]-bbox[0])*.7 and c.text.strip():
            htop = max(htop, c.box[3])
    spans = [(c.box[0], c.box[2]) for c in row]
    # Add empty data columns when there is a genuine heading in the gap.
    spans.sort()
    for c in cells:
        if c.box[1] < first_data and c.box[3] > htop and c.text.strip() and c.box[2]-c.box[0] > 12:
            if not any(max(0, min(b, c.box[2])-max(a, c.box[0])) > .5*min(b-a, c.box[2]-c.box[0]) for a,b in spans):
                spans.append((c.box[0], c.box[2]))
    spans.sort()
    cols = []
    for a,b in spans:
        if cols and a < cols[-1][1]-2:
            continue
        cols.append([a,b])
    for i in range(len(cols)-1):
        boundary = (cols[i][1]+cols[i+1][0])/2
        cols[i][1] = boundary
        cols[i+1][0] = boundary
    headers = []
    for a,b in cols:
        hc = [c for c in cells if c.text.strip() and c.box[1] >= htop-.5 and c.box[3] <= first_data+.5
              and c.box[0] >= a-4 and c.box[2] <= b+4 and c.box[2]-c.box[0] <= (b-a)*1.35]
        hc.sort(key=lambda c:(c.box[1],c.box[0]))
        text=clean('\n'.join(c.text for c in hc))
        if not text:
            text=clean(ocr_cell(pd,(a,htop,b,first_data))) if pd.ocr_image is not None else clean(pd.text((a,htop,b,first_data)))
        headers.append(text)
    roles = [heading_role(h) for h in headers]
    # Locate the name column geometrically even when its header includes date/units.
    ni = min(range(len(cols)), key=lambda i: abs(cols[i][0]-nx0)+abs(cols[i][1]-nx1))
    roles[ni] = 'name'
    if parameter:
        for i,h in enumerate(headers):
            if i != ni and get_unit(h) and not remove_unit(h).strip(' ()'):
                roles[i] = 'level'
    groups = []
    for c in cells:
        if not c.text.strip() or c.box[1] < htop-.5 or c.box[3] > first_data+.5:
            continue
        covered = [i for i,(a,b) in enumerate(cols) if a >= c.box[0]-4 and b <= c.box[2]+4]
        label = clean(c.text)
        if len(covered) >= 2 and ni not in covered and not re.search(r'contaminant|chemical|parameter|regulated|inorganic|organic|microbial|radiolog|mcl|goal|regulat|limit|detect|result|^level|^range|unit|year|date|violation', label, re.I):
            groups.append((label,covered))
    # Columns named for a source are themselves measurements when their cells hold values.
    for i,r in enumerate(roles):
        if r not in ('unknown','location') or i == ni:
            continue
        vals = [clean(c.text) for c in cells if c.box[1] >= first_data-1 and c.box[0] >= cols[i][0]-3 and c.box[2] <= cols[i][1]+3 and c.text.strip()]
        if vals and sum(strict_measure(v) and not re.fullmatch(r'yes|no|y|n',v,re.I) for v in vals) >= len(vals)*.7:
            if r=='unknown' or re.search(r'plant|well|water|entry|ep\s*\d|site|station|distribution|source|system|^wtp|^wtf',headers[i],re.I):
                roles[i]='level'
            if roles[i]=='level' and re.search('[A-Za-z]',headers[i]):
                groups.append((headers[i],[i]))
        elif vals and (not headers[i] or re.search(r'statistic|description|measurement type',headers[i],re.I)) and all(re.search(r'highest|lowest|single|monthly|percent|measurement|average',v,re.I) for v in vals):
            roles[i] = 'sublabel'
    # Blank Yes/No columns occur in some state templates.
    for i,r in enumerate(roles):
        if r == 'unknown' and not headers[i]:
            vals = [unfoot(c.text).lower() for c in cells if c.box[1] >= first_data-1 and abs((c.box[0]+c.box[2])/2-(cols[i][0]+cols[i][1])/2) < (cols[i][1]-cols[i][0])*.35 and c.text.strip()]
            if vals and all(v in ('yes','no','y','n','✓','✔','✗','✘') for v in vals):
                roles[i] = 'violation'
    # Min/max are ranges only when paired; an unpaired highest is a measured level.
    if not ('low' in roles and 'high' in roles):
        roles = ['level' if r in ('low','high') else r for r in roles]
    if not any(r in ('level','combined') for r in roles):
        # Microbiological tables may report only counts of positive samples.
        for i,r in enumerate(roles):
            if r == 'count' and (re.search(r'detect|positive', headers[i], re.I) or any(re.search(r'coliform|e\.?\s*coli|bacteria',c.text,re.I) for c in names)) and not re.search(r'above|over|sites|total sites', headers[i], re.I):
                roles[i] = 'level'
                break
    if not any(r in ('level','combined','range','low','high') for r in roles):
        return None
    log('SCHEMA', pd.num, tuple(round(x,1) for x in bbox), list(zip(headers, roles)), 'data', first_data)
    return {'cols': cols, 'headers': headers, 'roles': roles, 'ni':ni, 'start':first_data, 'htop': htop, 'bbox':bbox, 'names':names, 'groups':groups, 'parameter':parameter}


def logical_rows(pd, cells, sc):
    cols, roles, ni = sc['cols'], sc['roles'], sc['ni']
    a,b = cols[ni]
    names = []
    for c in cells:
        if c.box[1] < sc['start']-1 or not c.text.strip():
            continue
        if c.box[0] >= a-3 and c.box[2] <= b+3 and c.box[2]-c.box[0] >= (b-a)*.45:
            names.append(c)
    names.sort(key=lambda c:(c.box[1],c.box[0]))
    # Primary measurements determine splits inside merged contaminant cells.
    mids = [i for i,r in enumerate(roles) if r in ('level','combined')]
    if not mids:
        mids = [i for i,r in enumerate(roles) if r in ('range','low','high')]
    pi = mids[0]
    ca,cb = cols[pi]
    for nameidx,nc in enumerate(names):
        y0 = nc.box[1]
        y1 = names[nameidx+1].box[1] if nameidx+1 < len(names) else sc['bbox'][3]
        # A full-width section heading terminates the preceding row.
        for cc in cells:
            if cc.text.strip() and cc.box[1] >= nc.box[3]-.5 and cc.box[1] > y0+1 and cc.box[2]-cc.box[0] > (sc['bbox'][2]-sc['bbox'][0])*.7:
                y1 = min(y1,cc.box[1])
        if y1-y0 < 3:
            continue
        splits = []
        for c in cells:
            if c.box[0] >= ca-3 and c.box[2] <= cb+3 and c.box[2]-c.box[0] > (cb-ca)*.45 and c.box[1] >= y0-1 and c.box[3] <= y1+1 and c.text.strip():
                splits.append(c)
        splits.sort(key=lambda c:c.box[1])
        # Only split if each separate value is a result, not wrapped labels.
        meaningful = [c for c in splits if value_like(c.text) or NULL_RE.fullmatch(clean(c.text))]
        if len(meaningful) > 1 and len(meaningful) == len(splits):
            intervals = [(c.box[1], c.box[3]) for c in meaningful]
        else:
            intervals = [(y0,y1)]
        for ry0,ry1 in intervals:
            vals = []
            for i,(x0,x1) in enumerate(cols):
                if i == ni:
                    vals.append(nc.text)
                    continue
                # Reuse true merged cells, so inherited MCLG values are not blank.
                containing = [c for c in cells if c.text.strip() and c.box[0] >= x0-3 and c.box[2] <= x1+3 and c.box[2]-c.box[0] > (x1-x0)*.4 and c.box[1] <= ry0+1 and c.box[3] >= ry1-1]
                if containing:
                    cc = min(containing, key=lambda c:(c.box[3]-c.box[1])*(c.box[2]-c.box[0]))
                    vals.append(cc.text)
                else:
                    vals.append(pd.text((x0,ry0,x1,ry1)))
            yield vals, (ry0,ry1)


def violation(s, inverse=False):
    s = unfoot(s or '').lower().strip(' .')
    if NULL_RE.fullmatch(s):
        return None
    if s in ('yes','y','✓','✔','☑','true','violation','exceeded') or s.startswith('yes '):
        return 'N' if inverse else 'Y'
    if s in ('no','n','✗','✘','×','false','none','no violation') or s.startswith('no '):
        return 'Y' if inverse else 'N'
    if inverse:
        if re.search(r'not\s+(?:met|meet|compl)|fail|non[- ]?compliant|out of compliance',s):
            return 'Y'
        if re.search(r'pass|met|compliant|meets|in compliance', s):
            return 'N'
    return None


def split_locations(s):
    # Each place followed by a result and optional parenthesized range.
    raw = clean(s or '')
    atom = r'(?:[<>≤≥]=?\s*)?(?:' + NUM + r')|ND|Absent'
    pat = re.compile(r'(?:^|[;\n]|(?<=\)))\s*([A-Za-z][A-Za-z0-9 .,#/&\-]*?)\s*(?::\s*|\s+(?=[<>≤≥\d]))\s*(' + atom + r')\s*(?:\(([^)]*)\))?', re.I)
    found = []
    for m in pat.finditer(raw):
        label = clean(m.group(1))
        if re.search(r'average|maximum|highest|lowest|percentile|result|range|value|samples|less than|^max$|^avg$|^mean$', label, re.I):
            continue
        found.append((label, m.group(2)+((' ('+m.group(3)+')') if m.group(3) else '')))
    return found if len(found) >= 2 else []


def row_record(vals, sc, system, table_unit=None):
    if sc.get('ocr'):
        vals=[re.sub(r'\bp\s*c+\s*i\s*/\s*([I1|lL])',lambda m:'pCi/'+(m.group(1) if m.group(1) in 'lL' else 'l'),v,flags=re.I) for v in vals]
        vals=[re.sub(r'\b([umnµ]g)\s*/\s*[I1|]',r'\1/L',v,flags=re.I) for v in vals]
        vals=[re.sub(r'(?<!\w)[Oo](?=\s*(?:ppm|ppb|pCi/|$))','0',v) for v in vals]
        vals=[re.sub(r'(?<=\d),(?=\d{1,2}(?:\D|$))','.',v) for v in vals]
    # Source column groups share the name and any regulatory columns outside the group.
    if sc.get('groups'):
        out = []
        grouped = set(i for _,indices in sc['groups'] for i in indices)
        for label,indices in sc['groups']:
            ss = dict(sc, groups=[], location=label)
            ss['roles'] = [r if i not in grouped or i in indices else 'ignore' for i,r in enumerate(sc['roles'])]
            if any(ss['roles'][i] in ('level','combined','range','low','high') for i in indices):
                out.extend(row_record(vals,ss,system,table_unit))
        if out:
            return out
    headers, roles = sc['headers'], sc['roles']
    ni = sc['ni']
    name_raw = vals[ni]
    if sc.get('ocr'):
        name_raw=re.sub(r'\bHAAS\b','HAA5',name_raw,flags=re.I)
        name_raw=re.sub(r'\blron\b','Iron',name_raw)
        name_raw=re.sub(r'\b(Lead|Copper|Barium|Fluoride|Sodium|Nitrate|Turbidity|TTHM|HAA5|TOC|PFOS|PFBA|PFOA)[\d?!*]+(?=\s|\(|$)',r'\1',name_raw,flags=re.I)
        name_raw=re.sub(r'(?<=[A-Za-z)])\?+(?=\s|$)','',name_raw)
        name_raw=re.sub(r'^\s*\d+\s+(?=[A-Za-z])','',name_raw)
    name, nd = contaminant_name(name_raw)
    if sc.get('parameter'):
        name,nd = contaminant_name(sc['parameter'])
    if not name or plain_name_header(name) or len(name) > 180:
        return []
    if re.search(r'^(?:note|definition|source[s]?\b|\*|range of|range$|next test)|not detected|^total number|^number of', name, re.I):
        return []
    fieldvals = collections.defaultdict(list)
    for i,(h,r,v) in enumerate(zip(headers,roles,vals)):
        fieldvals[r].append((i,h,v))
    def first(role):
        return fieldvals[role][0][2] if fieldvals[role] else ''
    if fieldvals['sublabel'] and first('sublabel'):
        name += ' - ' + unfoot(first('sublabel'))
    sample = (date_value(first('date')) if fieldvals['date'] else None) or nd
    mcl = scalar(first('mcl'), 'mcl')
    mclg = scalar(first('mclg'), 'mclg')
    v = next((violation(x[2]) for x in fieldvals['violation'] if violation(x[2]) is not None),None) if fieldvals['violation'] else violation(first('compliance'), True)
    ls = fieldvals['level']+fieldvals['combined']
    ls.sort(key=lambda z:z[0])
    level_raw = ls[0][2] if ls else ''
    lh = ls[0][1] if ls else ''
    if not sc.get('_no_split') and ls:
        lines = [x.strip() for x in level_raw.split('\n') if x.strip()]
        parsed = [parse_level(x,lh) for x in lines]
        if len(lines)>1 and all(a is not None and b is None and c is None and (isinstance(a,(int,float)) or ATOM_RE.fullmatch(str(a))) for a,b,c in parsed):
            out = []
            for lineno,line in enumerate(lines):
                vv = list(vals)
                vv[ls[0][0]] = line
                for i,r in enumerate(roles):
                    vl = [x.strip() for x in vals[i].split('\n') if x.strip()]
                    if r in ('mcl','mclg') and len(vl)!=len(lines):
                        chunks=re.split(r'(?m)(?=^(?:TT|MCLG?|MRDLG?|AL)\s*[:=])',vals[i])
                        grouped=[x.strip() for x in chunks if x.strip()]
                        if len(grouped)==len(lines):
                            vl=grouped
                    if i != ni and i != ls[0][0] and len(vl)==len(lines) and (r in ('location','date','unit','range','low','high','violation','compliance') or all(value_like(x) for x in vl)):
                        vv[i] = vl[lineno]
                out.extend(row_record(vv,dict(sc,_no_split=True),system,table_unit))
            return out
    ranges = fieldvals['range']
    range_raw = ranges[0][2] if ranges else ''
    unitcol = scalar(first('unit'))
    unit = clean(unitcol) if unitcol is not None else None
    if unit and get_unit(unit) and remove_unit(unit).strip(' ()/,;'):
        unit=get_unit(unit)
    if unit and re.search(r'percent removal|\bratio\b|^see\s*(?:note\s*)?\d*\s*$|see note|^unitless$|^dimensionless$', unit, re.I):
        unit = None
    unit = unit or get_unit(name_raw) or get_unit(level_raw) or get_unit(first('mcl')) or get_unit(lh) or get_unit(range_raw) or table_unit
    location = sc.get('location') or clean(first('location')) or None
    if sc.get('parameter'):
        location = clean(name_raw)
    variants = split_locations(level_raw)
    if not variants:
        variants = [(location, level_raw)]
    records = []
    for place,lr in variants:
        lev,lo,hi = parse_level(lr,lh)
        if ranges:
            ra,rb = parse_range(range_raw)
            if ra is not None or rb is not None:
                lo,hi = ra,rb
        if fieldvals['low']:
            lo = scalar(first('low'))
        if fieldvals['high']:
            hi = scalar(first('high'))
        rec = dict(system=system,contaminant=name,sample_date=sample,level=lev,range_low=lo,range_high=hi,unit=unit,mcl=mcl,mclg=mclg,violation=v)
        if place:
            rec['location'] = place
        other = {}
        for i,h,ov in ls[1:]+fieldvals['count']+fieldvals['advisory']:
            sv = scalar(ov)
            if sv is not None:
                other[h or 'Additional value'] = sv
        if other:
            rec['other_values'] = other
        records.append(rec)
    return records


ID_RE = re.compile(r'(?:\bP\s*W\s*S\s*(?:I\s*D|identification)?|public\s+water\s+(?:supply|system)\s*(?:identification|number|no\.?|id)?)(?:\s*(?:identification|number|no\.?|id))?\s*[#:=.()\- ]{0,15}\s*([A-Z]{0,3}[- ]*\d{5,10}(?:-\d{1,4})?)\b', re.I)


def find_system(text):
    matches = []
    for m in ID_RE.finditer(text):
        sid = re.sub(r'\s+', '', m.group(1))
        if sid:
            matches.append(sid)
    return matches


def own_id_context(context):
    supplier=list(re.finditer(r'purchas|wholesale|supplied by|supplier',context,re.I))
    if not supplier:
        return True
    reports=list(re.finditer(r'annual.{0,50}report|consumer confidence|drinking water quality report|water quality report',context,re.I|re.S))
    return bool(reports and reports[-1].start()>supplier[-1].start())


def primary_page_id(text):
    for m in ID_RE.finditer(text):
        end=text.find('\n',m.end())
        if end<0:
            end=m.end()+50
        context=text[max(0,m.start()-250):min(end,m.end()+70)]
        if own_id_context(context):
            return re.sub(r'\s+','',m.group(1))
    return None


def table_caption(pd,cells,bbox):
    titles = [c.text for c in cells if c.box[1] <= bbox[1]+2 and c.box[2]-c.box[0] > (bbox[2]-bbox[0])*.7 and c.text.strip()]
    if titles:
        return clean(' '.join(titles))
    above = pd.text((bbox[0],max(0,bbox[1]-45),bbox[2],bbox[1]-1)).splitlines()
    if not above:
        return ''
    caption=clean(above[-1])
    for line in reversed(above[-3:-1]):
        line=clean(line)
        introductory=bool(re.match(r'^(?:all\b|units?\b|values?\b|results?\b|(?:20\d\d\s+)?(?:regulated|unregulated|secondary|primary|inorganic|organic|microbiological|radiological|ucmr\d*|other|water quality|detected)\b)',line,re.I))
        param=parameter_title(line)
        parameter_caption=param and re.sub(r'[^A-Za-z]','',remove_unit(line)).lower()==re.sub(r'[^A-Za-z]','',param).lower()
        if (introductory or parameter_caption) and get_unit(line):
            caption=line+' '+caption
        else:
            break
    return caption


def system_mentions(pd):
    out=[]
    for line in word_lines(pd.page.extract_words(x_tolerance=1.5,y_tolerance=2)):
        txt=clean(' '.join(w['text'] for w in line))
        ids=find_system(txt)
        if ids:
            yy=min(w['top'] for w in line)
            context=clean(pd.text((0,max(0,yy-100),pd.page.width,min(pd.page.height,yy+15))))
            out.append((yy,ids[0],own_id_context(context)))
    return out


def table_system(mentions,y,default):
    before=[(yy,sid) for yy,sid,allowed in mentions if yy<y and allowed]
    return max(before)[1] if before else default


def continue_schema(pd,cells,bbox,previous):
    if not previous:
        return None
    sc,caption,width=previous
    if abs(width-pd.page.width)>5 or abs(bbox[0]-sc['bbox'][0])>10 or abs(bbox[2]-sc['bbox'][2])>10:
        return None
    na,nb=sc['cols'][sc['ni']]
    names=[c for c in cells if c.box[0]>=na-3 and c.box[2]<=nb+3 and c.text.strip()]
    if not names or not NAME_START.match(clean(min(names,key=lambda c:c.box[1]).text)):
        return None
    levels=[j for j,r in enumerate(sc['roles']) if r in ('level','combined','range')]
    if not levels:
        return None
    aa,bb=sc['cols'][levels[0]]
    vv=[c for c in cells if c.box[0]>=aa-3 and c.box[2]<=bb+3 and c.text.strip()]
    if not vv or sum(strict_measure(c.text) for c in vv)<len(vv)*.7:
        return None
    return dict(sc,start=bbox[1],htop=bbox[1],bbox=bbox,groups=sc.get('groups',[]))


def parameter_title(caption):
    m = re.search(r'(?:Total Organic Carbon\s*\(TOC\)|TOC)\s+(?:Removal\s+)?Ratio|(?:Total Organic Carbon\s*\(TOC\)|TOC)\s+Removal|Hardness|Alkalinity|Conductivity|Turbidity|Chlorine Residual|pH',caption,re.I)
    return m.group(0) if m else None


def transpose_records(pd,cells,bbox,system,caption):
    firsts = [c for c in cells if (heading_role(c.text)=='date' or re.fullmatch(r'(?:sample|sampling)?\s*(?:location|site|station|point)|sample',clean(c.text),re.I)) and c.box[0] < bbox[0]+(bbox[2]-bbox[0])*.15]
    for fc in sorted(firsts,key=lambda c:c.box[1]):
        hh = [c for c in cells if c.box[0] >= fc.box[2]-1 and c.box[1] <= fc.box[1]+1 and c.box[3] >= fc.box[3]-1 and c.text.strip()]
        hh.sort(key=lambda c:c.box[0])
        param=parameter_title(caption)
        param_table=param and all(heading_role(c.text) in ('level','range','low','high') for c in hh)
        if not hh or (not param_table and any(heading_role(c.text) in ('mcl','mclg','range','violation','level','source') for c in hh)):
            continue
        if any(not re.search(r'[A-Za-z]',c.text) for c in hh):
            continue
        rr = [c for c in cells if abs(c.box[0]-fc.box[0])<4 and c.box[2] <= fc.box[2]+4 and c.box[1] >= fc.box[3]-1 and c.text.strip()]
        rr.sort(key=lambda c:c.box[1])
        limits = {}
        units=None
        for rc in rr:
            lr=heading_role(rc.text)
            if lr in ('mcl','mclg','unit'):
                vv=[pd.text((hc.box[0],rc.box[1],hc.box[2],rc.box[3])) for hc in hh]
                if lr=='unit':
                    units=vv
                else:
                    limits[lr]=vv
        out = []
        for rc in rr:
            rowlabel = clean(rc.text)
            values = [pd.text((hc.box[0],rc.box[1],hc.box[2],rc.box[3])) for hc in hh]
            lr = heading_role(rowlabel)
            if lr in ('mcl','mclg','unit'):
                continue
            isdate = heading_role(fc.text)=='date'
            if isdate and not re.search(r'\d',rowlabel):
                continue
            if not any(value_like(v) for v in values):
                continue
            if param_table:
                roles=['date' if isdate else 'location']+[heading_role(hc.text) for hc in hh]
                if not ('low' in roles and 'high' in roles):
                    roles=['level' if r in ('low','high') else r for r in roles]
                ss=dict(headers=[fc.text]+[hc.text for hc in hh],roles=roles,ni=0,parameter=param,groups=[])
                out.extend(row_record([rc.text]+values,ss,system,get_unit(caption)))
                continue
            for i,(hc,vv) in enumerate(zip(hh,values)):
                name,sd = contaminant_name(hc.text)
                lev,lo,hi = parse_level(vv)
                rec = dict(system=system,contaminant=name,sample_date=rowlabel if isdate else sd,level=lev,range_low=lo,range_high=hi,
                           unit=(clean(units[i]) if units and scalar(units[i]) is not None else None) or get_unit(hc.text) or get_unit(vv) or get_unit(caption),
                           mcl=scalar(limits.get('mcl',['']*len(hh))[i],'mcl'),mclg=scalar(limits.get('mclg',['']*len(hh))[i]),violation=None)
                if not isdate:
                    rec['location']=rowlabel
                out.append(rec)
        if out:
            return out
    return []


def records_from_table(pd,cells,sc,system,caption):
    out = []
    last = []
    table_unit = get_unit(caption)
    rows=list(logical_rows(pd,cells,sc))
    parent=None
    for rowidx,(vals,yr) in enumerate(rows):
        log('ROW',pd.num,[clean(s) for s in vals])
        rowname=clean(vals[sc['ni']])
        statrow=bool(re.match(r'^(?:highest|lowest|maximum|minimum|average|single|monthly|percent|90th)',rowname,re.I))
        if rowidx+1<len(rows) and NAME_START.match(rowname) and not any(clean(v) for v,r in zip(vals,sc['roles']) if r in ('level','combined','range','low','high')) and re.match(r'^(?:highest|lowest|maximum|minimum|average|single|monthly|percent)',clean(rows[rowidx+1][0][sc['ni']]),re.I):
            parent=vals[sc['ni']]
            continue
        if statrow:
            fullnames=[c for c in cells if c.text.strip() and c.box[2]-c.box[0]>(sc['bbox'][2]-sc['bbox'][0])*.7 and c.box[3]<=yr[0]+1 and NAME_START.match(clean(c.text))]
            prefix=parent or (max(fullnames,key=lambda c:c.box[1]).text if fullnames else None)
            if prefix:
                vals=list(vals);vals[sc['ni']]=prefix+' - '+vals[sc['ni']]
        elif not re.match(r'^range',rowname,re.I):
            parent=None
        labels = [vals[sc['ni']]]+[v for v,r in zip(vals,sc['roles']) if r=='sublabel']
        named_range=any(re.match(r'^\s*range(?:\s+of|\s+for|\s*$)',clean(v),re.I) for v in labels)
        value_range=any(re.match(r'^\s*range\b',clean(v),re.I) for v,r in zip(vals,sc['roles']) if r in ('level','combined'))
        same_parent=last and contaminant_name(vals[sc['ni']])[0]==last[0]['contaminant']
        if named_range or (value_range and same_parent):
            ranges=[(i,v) for i,(v,r) in enumerate(zip(vals,sc['roles'])) if r in ('level','combined','range') and parse_range(v)!=(None,None)]
            for rec in last:
                possible=ranges
                for group,indices in sc.get('groups',[]):
                    if rec.get('location')==group:
                        possible=[(i,v) for i,v in ranges if i in indices]
                        break
                if possible:
                    lo,hi=parse_range(possible[0][1])
                    rec['range_low'],rec['range_high']=lo,hi
            continue
        ss = sc
        section = [c for c in cells if c.box[1] >= sc['start']-1 and c.box[3] <= yr[0]+1 and c.box[2]-c.box[0] > (sc['bbox'][2]-sc['bbox'][0])*.7 and c.text.strip()]
        if section:
            label=clean(max(section,key=lambda c:c.box[1]).text)
            if not re.search(r'contaminant|chemical|substance|parameter|regulated|microbiol|inorganic|organic|radiolog|disinfect|lead and copper|results|note',label,re.I):
                ss=dict(sc,location=label)
        rr = row_record(vals,ss,system,table_unit)
        if rr:
            out.extend(rr)
            last=rr
    return out


def scan_rules(path,fine=False):
    """Find thin table rules in a scan, keeping geometry for table recovery."""
    from PIL import Image,ImageDraw
    im=Image.open(path).convert('L')
    w,h=im.size
    buf=im.tobytes();horizontal=[];vertical=[]
    for yy in range(h):
        base=yy*w;start=None
        for xx in range(w+1):
            black=xx<w and buf[base+xx]<128
            if black and start is None:
                start=xx
            elif not black and start is not None:
                if xx-start>(max(50,w*.025) if fine else max(100,w*.12)):
                    horizontal.append((start,yy,xx,yy))
                start=None
    for xx in range(w):
        start=None
        for yy in range(h+1):
            black=yy<h and buf[yy*w+xx]<128
            if black and start is None:
                start=yy
            elif not black and start is not None:
                if yy-start>(max(50,h*.020) if fine else max(100,h*.045)):
                    vertical.append((xx,start,xx,yy))
                start=None
    draw=ImageDraw.Draw(im)
    for x0,y0,x1,y1 in horizontal:
        draw.rectangle((x0-1,y0-1,x1+1,y1+1),fill=255)
    for x0,y0,x1,y1 in vertical:
        draw.rectangle((x0-1,y0-1,x1+1,y1+1),fill=255)
    im.save(path,dpi=(OCR_DPI,OCR_DPI))
    if len(horizontal)+len(vertical)>1500:
        # Dark photographs can produce long pixel runs as well. Broad runs are
        # not table rules, and must not create a quadratic intersection graph.
        def thin(lines,horizontal_axis):
            buckets=collections.defaultdict(list)
            for z in lines:
                a,b,c,d=z
                key=(round(a/10),round(c/10)) if horizontal_axis else (round(b/10),round(d/10))
                buckets[key].append(z)
            out=[]
            for zz in buckets.values():
                zz.sort(key=lambda z:z[1] if horizontal_axis else z[0])
                cluster=[]
                for z in zz+[None]:
                    coord=(z[1] if horizontal_axis else z[0]) if z is not None else 10**9
                    prev=(cluster[-1][1] if horizontal_axis else cluster[-1][0]) if cluster else coord
                    if cluster and coord-prev>1:
                        if len(cluster)<=max(8,OCR_DPI//30):
                            out.append(cluster[len(cluster)//2])
                        cluster=[]
                    if z is not None:
                        cluster.append(z)
            return out
        horizontal=thin(horizontal,True);vertical=thin(vertical,False)
        if len(horizontal)+len(vertical)>1500:
            horizontal=[];vertical=[]
    return horizontal,vertical,w,h


def ocr_page(pdf_path,pageno,out_dir):
    """Optional local OCR for image-only pages; original text alone supplies IDs."""
    binary=shutil.which('tesseract')
    remaining=110-(time.monotonic()-START)
    if not binary or remaining<12:
        return None
    try:
        with tempfile.TemporaryDirectory(prefix='.ccr-',dir=out_dir) as td:
            raster=os.path.join(td,'page')
            subprocess.run(['pdftoppm','-f',str(pageno),'-l',str(pageno),'-r',str(OCR_DPI),'-png','-singlefile',pdf_path,raster],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=min(20,remaining-5),check=True)
            env=dict(os.environ,OMP_THREAD_LIMIT='1')
            with open(raster+'.png','rb') as fh:
                original=fh.read()
            for fine in (False,True):
                if fine:
                    with open(raster+'.png','wb') as fh:
                        fh.write(original)
                hh,vv,w,h=scan_rules(raster+'.png',fine)
                subprocess.run([binary,raster+'.png',os.path.join(td,'ocr'),'-l','eng','--dpi',str(OCR_DPI),'--psm','3','-c','textonly_pdf=1','pdf'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=env,timeout=min(35,110-(time.monotonic()-START)),check=True)
                with open(os.path.join(td,'ocr.pdf'),'rb') as fh:
                    doc=pdfplumber.open(io.BytesIO(fh.read()))
                sizes=[c['size'] for c in doc.pages[0].chars if c['text'].strip()]
                if fine or not sizes or statistics.median(sizes)>=8:
                    break
                doc.close()
            from PIL import Image
            doc._scan_image=Image.open(raster+'.png').copy()
            page=doc.pages[0]
            xscale=page.width/w;yscale=page.height/h
            def edge(z):
                a,b,c,d=z
                return dict(object_type='line',x0=a*xscale,x1=c*xscale,top=b*yscale,bottom=d*yscale,width=(c-a)*xscale,height=(d-b)*yscale)
            doc._scan_rules=([edge(z) for z in hh],[edge(z) for z in vv])
            return doc
    except Exception as exc:
        log('OCR ERROR',pageno,str(exc))
        return None


HEADER_WORDS = re.compile(r'^(?:MCLG?|SMCL|MRDLG?|PHG|ALG|AL|TT|units?|years?|dates?|sampled?|sampling|levels?|detected|detection[s]?|found|range|average|avg\.?|mean|median|maximum|minimum|highest|lowest|result[s]?|percentile|90th|90%|violation|compliance|meets?|met|standards?|pass|goal|limit|source[s]?|allowed|value[s]?|collection|test|EPA.?s?|ideal|regulatory|amount|measure(?:ment)?|low|high)$',re.I)


def word_lines(words,tolerance=2.5):
    lines=[]
    for w in sorted(words,key=lambda w:(w['top'],w['x0'])):
        if lines and abs(w['top']-statistics.median(x['top'] for x in lines[-1])) <= tolerance:
            lines[-1].append(w)
        else:
            lines.append([w])
    for line in lines:
        line.sort(key=lambda w:w['x0'])
    return lines


def strict_measure(s):
    if clean(s)=='nd.':
        return False
    v,lo,hi=parse_level(s)
    if isinstance(v,(int,float)) or lo is not None or hi is not None or parse_range(s)!=(None,None):
        return True
    ss=remove_unit(unfoot(s)).strip(' ()')
    if ss=='nd.':
        return False
    return bool(ATOM_RE.fullmatch(ss) or re.match(r'^\d+\s+(?:positive|negative|detection|sample)',ss,re.I) or re.match(r'^\d+%\s+(?:of\s+\S+|less\s+than\s+\S+|below\s+\S+)',ss,re.I) or NULL_RE.fullmatch(ss))


NAME_START = re.compile(r'^(?:\d+[.)]\s*)?(?:total|combined|gross|adjusted|free|entry point|radium|uranium|lead|copper|barium|fluoride|nitrate|nitrite|arsenic|selenium|antimony|asbestos|bromate|chlorite|chlorine|chloramine|chloride|sulfate|sulphate|sodium|hardness|calcium|magnesium|manganese|iron|zinc|aluminum|silver|cadmium|chromium|mercury|thallium|beryllium|nickel|lithium|strontium|vanadium|molybdenum|boron|silica|alkalinity|conductivity|color|colour|odor|odour|pH|TOC|TTHMs?|HAA[59]?|PFOA|PFOS|PFBA|PFHx\w*|PFBS|PFNA|PFDA|perfluoro\w*|benzene|toluene|ethylbenzene|xylene|styrene|atrazine|simazine|tetrachloro\w*|trichloro\w*|tetrahydro\w*|bromo\w*|chloro\w*|dibromo\w*|dichloro\w*|E\.?\s*coli|turbidity|alpha|beta)\b',re.I)
BAD_NAME = re.compile(r'\b(?:we|our|your|please|information|following|defined|means|should|because|report contains|some people|learn more|may be|water quality report)\b|^(?:note|definition|public water|page|sources of|\*note|require that|\d*results for|detection is shown|drinking water sources?|groundwater|surface of the land)|^\d+\s*[A-Za-z].*?\b(?:is|are|refers|require|reported)\b',re.I)


def text_rows(pd,cells,sc,header_end):
    roles,cols=sc['roles'],sc['cols']
    mids=[i for i,r in enumerate(roles) if r in ('level','combined')]
    if not mids:
        mids=[i for i,r in enumerate(roles) if r in ('range','low','high')]
    if not mids:
        return
    i=mids[0];a,b=cols[i]
    anchors=[]
    pw=pd.page.extract_words(x_tolerance=1.5,y_tolerance=2)
    for line in word_lines([v for v in pw if a<=(v['x0']+v['x1'])/2<b and header_end<=v['top']<sc['bbox'][3]]):
        top=min(v['top'] for v in line);bottom=max(v['bottom'] for v in line)
        txt=pd.text((a,top-.25,b,bottom+.25),skip_sections=True)
        if txt.strip() and strict_measure(txt):
            anchors.append(Cell((a,top,b,bottom),txt))
    anchors.sort(key=lambda c:c.box[1])
    if not anchors:
        return
    na,nb=cols[sc['ni']]
    ws=[v for v in pw if na<=(v['x0']+v['x1'])/2<nb and header_end<=v['top']<min(sc['bbox'][3],anchors[-1].box[3]+25)]
    lines=word_lines(ws)
    blocks=[]
    acenters=[(c.box[1]+c.box[3])/2 for c in anchors]
    for line in lines:
        top=min(v['top'] for v in line);bottom=max(v['bottom'] for v in line)
        rawtxt=pd.text((na,top-.5,nb,bottom+.5),skip_sections=True)
        txt=clean(rawtxt)
        if not txt or re.fullmatch(r'contaminants?|substances?|constituents?|parameters?|analytes?',txt,re.I) or re.search(r'contaminants?$|^disinfection byproducts|^microbiological|^inorganic|^radioactive|^synthetic organic',txt,re.I):
            continue
        if BAD_NAME.search(txt):
            continue
        ai=min(range(len(anchors)),key=lambda j:abs(acenters[j]-(top+bottom)/2))
        standalone_paren=bool(re.fullmatch(r'\([^)]*\)',txt))
        prefix=bool(blocks and re.fullmatch(r'(?:Total|Gross|Combined|Adjusted|Free|Entry Point)',clean(' '.join(blocks[-1]['lines'])),re.I))
        new=not blocks
        if blocks and not standalone_paren and not prefix:
            gap=top-blocks[-1]['bottom']
            prevtxt=clean(' '.join(blocks[-1]['lines']))
            continuation=bool(re.match(r'^(?:in\b|distribution\b|system\b|combined$|Source\b|Treated\b|Residual\b|Carbon\b|Acids?\b|Particles\b|noic\b|nesulfonic\b)',txt,re.I) or prevtxt.endswith('-') or (prevtxt.count('(')>prevtxt.count(')')))
            if gap>(bottom-top)*.7 or (NAME_START.match(txt) and NAME_START.match(clean(' '.join(blocks[-1]['lines'])))):
                new=True
            elif ai!=blocks[-1]['anchor'] and gap>-.5 and not continuation:
                new=True
            if continuation:
                new=False
        if new:
            blocks.append(dict(top=top,bottom=bottom,lines=[rawtxt],anchor=ai))
        else:
            blocks[-1]['lines'].append(rawtxt);blocks[-1]['bottom']=bottom
    if not blocks:
        return
    assigned=collections.defaultdict(list)
    for c in anchors:
        cy=(c.box[1]+c.box[3])/2
        bi=min(range(len(blocks)),key=lambda j:max(blocks[j]['top']-cy,cy-blocks[j]['bottom'],0)+abs(cy-(blocks[j]['top']+blocks[j]['bottom'])/2)*.15)
        block=blocks[bi]
        if max(block['top']-cy,cy-block['bottom'],0)>85:
            continue
        assigned[bi].append(c)
    active=[j for j in range(len(blocks)) if assigned[j]]
    starts=[]
    for j in active:
        g=assigned[j];block=blocks[j]
        yy=min(block['top'],g[0].box[1])-max(2,(g[0].box[3]-g[0].box[1])*1.05)
        # A location label may precede the numeric part of a value cell.
        lvlines=word_lines([v for v in pd.page.extract_words(x_tolerance=1.5,y_tolerance=2) if a<=(v['x0']+v['x1'])/2<b and yy-28<=v['top']<g[0].box[1]])
        for line in reversed(lvlines):
            ltop=min(v['top'] for v in line);lbot=max(v['bottom'] for v in line)
            txt=clean(' '.join(v['text'] for v in line))
            if yy-lbot>12 or not re.search('[A-Za-z]',txt) or strict_measure(txt) or re.search(r'range|average|highest|lowest',txt,re.I):
                break
            yy=min(yy,ltop-1)
        starts.append(max(header_end,yy))
    for ii in range(1,len(starts)):
        prev=assigned[active[ii-1]][-1]
        cur=assigned[active[ii]][0]
        middle=(prev.box[1]+prev.box[3]+cur.box[1]+cur.box[3])/4
        starts[ii]=max(starts[ii],middle)
    log('TEXT BLOCKS',pd.num,[(clean(' '.join(blocks[j]['lines'])),round(starts[ii],1),[clean(c.text) for c in assigned[j]]) for ii,j in enumerate(active)])
    for jj,j in enumerate(active):
        g=assigned[j];block=blocks[j]
        y0=starts[jj]
        if jj+1<len(active):
            y1=starts[jj+1]
        else:
            y1=max(block['bottom'],g[-1].box[3])
            functional=[(aa,bb) for (aa,bb),rr in zip(cols,roles) if rr not in ('name','source','ignore','unknown')]
            tail=word_lines([ww for ww in pd.page.extract_words(x_tolerance=1.5,y_tolerance=2) if y0<=ww['top']<sc['bbox'][3] and any(aa<=(ww['x0']+ww['x1'])/2<bb for aa,bb in functional)])
            prev=y0
            for line in tail:
                ltop=min(ww['top'] for ww in line);lbot=max(ww['bottom'] for ww in line)
                ht=statistics.median(ww['bottom']-ww['top'] for ww in line)
                if ltop-prev>max(10,ht*1.1) and ltop>y1:
                    break
                prev=max(prev,lbot);y1=max(y1,lbot)
            y1=min(sc['bbox'][3],y1+1)
            for line in word_lines(pw):
                ltop=min(ww['top'] for ww in line)
                if ltop>=g[-1].box[3] and BAD_NAME.search(clean(' '.join(ww['text'] for ww in line))):
                    y1=min(y1,ltop-.5)
                    break
        vals=[pd.text((a,y0,b,y1),skip_sections=True,whole_words=True) for a,b in cols]
        vals[sc['ni']]='\n'.join(block['lines'])
        name=clean(vals[sc['ni']])
        if not name or len(name)>160 or not re.search('[A-Za-z]',name):
            continue
        if BAD_NAME.search(name):
            continue
        # Do not turn prose that happens to contain a number into a table row.
        other=0;bad=0
        for k,(r,vv) in enumerate(zip(roles,vals)):
            vv=clean(vv)
            if k==sc['ni'] or r in ('source','ignore','unknown','location','sublabel') or not vv:
                continue
            other+=1
            if r in ('mcl','mclg','range','low','high','level','combined') and len(vv)>110:
                bad+=1
        if other<1 or bad:
            continue
        yield vals,(y0,y1)


def borderless_tables(pd,excluded):
    words=pd.page.extract_words(x_tolerance=1.5,y_tolerance=2)
    anchors=[w for w in words if re.fullmatch(r'contaminants?|substances?|constituents?|parameters?|analytes?|compounds?|organisms?|microbes?|elements?|metals?|characteristics?|indicators?',w['text'],re.I)]
    accepted=[]
    for w in anchors:
        if any(b[0]-4<=w['x0']<=b[2]+4 and b[1]-5<=w['top']<=b[3]+5 for b in excluded):
            continue
        near=[v for v in words if abs(v['top']-w['top'])<60 and v['x0']>w['x1']+5 and HEADER_WORDS.fullmatch(v['text'].strip('():,?'))]
        if len(near)<2:
            continue
        sameline=[v for v in words if abs(v['top']-w['top'])<2 and v['x1']<w['x0'] and w['x0']-v['x1']<12 and not re.fullmatch(r'regulated|unregulated|detected|primary|secondary|chemical',v['text'],re.I)]
        if sameline:
            continue
        accepted.append(w)
    distinct=[]
    for w in accepted:
        duplicate=None
        for ii,v in enumerate(distinct):
            if abs(v['x0']-w['x0'])<100 and abs(v['top']-w['top'])<45:
                between=[z for z in words if min(w['top'],v['top'])+3<z['top']<max(w['top'],v['top'])-2 and (re.fullmatch(NUM,z['text']) or re.fullmatch(r'ND|Absent',z['text'],re.I))]
                if not between:
                    duplicate=ii
                    break
        if duplicate is None:
            distinct.append(w)
        else:
            def strength(z):
                return sum(bool(HEADER_WORDS.fullmatch(q['text'].strip('():,?'))) for q in words if abs(q['top']-z['top'])<4 and q['x0']>z['x1'])
            if strength(w)>strength(distinct[duplicate]):
                distinct[duplicate]=w
    accepted=distinct
    for w in accepted:
        x0=max(0,w['x0']-50)
        x1=pd.page.width
        parallel=[v for v in accepted if abs(v['top']-w['top'])<25 and v['x0']>w['x0']+100]
        if parallel:
            x1=min(v['x0'] for v in parallel)-12
        later=[v for v in accepted if abs(v['x0']-w['x0'])<60 and v['top']>w['top']+35]
        yend=min((v['top']-12 for v in later),default=pd.page.height-12)
        near=[v for v in words if x0<=v['x0']<x1 and w['top']-65<=v['top']<min(yend,w['top']+150)]
        lines=word_lines(near)
        firstdata=yend
        for line in lines:
            yy=min(v['top'] for v in line)
            if yy<=w['top']+4:
                continue
            nums=[v for v in line if (re.fullmatch(NUM+r'%?',v['text']) or re.fullmatch(r'ND|Absent|Present|BDL',v['text'],re.I)) and v['x0']>w['x0']+65]
            left=[v for v in line if v['x0']<w['x0']+100 and re.search('[A-Za-z]',v['text']) and not HEADER_WORDS.fullmatch(v['text'].strip('():,?')) and not is_name_header(v['text'])]
            if nums and left:
                firstdata=yy
                break
        headerlines=[]
        for line in lines:
            y=min(v['top'] for v in line)
            if y>=firstdata-1 or y>w['top']+85:
                continue
            hc=sum(bool(HEADER_WORDS.fullmatch(v['text'].strip('():,?'))) for v in line)
            scalars=sum(bool(re.fullmatch(NUM,v['text'])) for v in line)
            line_text=clean(' '.join(v['text'] for v in line))
            title=bool(re.search(r'detected sample results|water quality|results\s*:?$|contaminants\s*[–-]',line_text,re.I))
            if (hc or w in line) and not scalars and (hc>=len(line)*.25 or w in line) and not title:
                headerlines.append(line)
        if not headerlines:
            continue
        htop=min(v['top'] for line in headerlines for v in line)-1
        hend=max(v['bottom'] for line in headerlines for v in line)+1
        log('HEADER SPAN',pd.num,w['text'],round(htop,1),round(hend,1),round(firstdata,1),[(round(min(z['top'] for z in ln),1),' '.join(z['text'] for z in ln)) for ln in headerlines])
        # The union of printed header extents supplies column gaps. Text-only
        # table finders otherwise split centered numbers into spurious columns.
        hw=[v for line in headerlines for v in line]
        intervals=[]
        gap=max(3,min(6,statistics.median(v['bottom']-v['top'] for v in hw)*.55))
        for v in sorted(hw,key=lambda v:v['x0']):
            if intervals and v['x0']<=intervals[-1][1]+gap:
                intervals[-1][1]=max(intervals[-1][1],v['x1'])
            else:
                intervals.append([v['x0'],v['x1']])
        for vv in words:
            if hend<vv['top']<yend and x0<vv['x0']<x1 and re.fullmatch(r'Yes|No|Y|N',vv['text'],re.I):
                if not any(a-4<=(vv['x0']+vv['x1'])/2<=b+4 for a,b in intervals):
                    intervals.append([vv['x0'],vv['x1']])
        intervals.sort()
        merged=[]
        for a,b in intervals:
            if merged and a<=merged[-1][1]+3:
                merged[-1][1]=max(merged[-1][1],b)
            else:
                merged.append([a,b])
        if len(merged)<2:
            continue
        bounds=[x0]+[(merged[j][1]+merged[j+1][0])/2 for j in range(len(merged)-1)]+[x1]
        datawords=[]
        hintheaders=[clean(pd.text((a-.5,htop,b+.5,hend),whole_words=True)) for a,b in merged]
        hintroles=[heading_role(h) for h in hintheaders]
        measureindex=next((j for j,r in enumerate(hintroles) if r in ('level','combined','range','low','high')),None)
        acceptedlines=0
        for line in word_lines([v for v in words if x0<=v['x0']<x1 and firstdata-1<=v['top']<yend]):
            measured=measureindex is not None and any(bounds[measureindex]<=(v['x0']+v['x1'])/2<bounds[measureindex+1] and strict_measure(v['text']) for v in line)
            if measured or sum(bool(ATOM_RE.fullmatch(v['text'].strip('()'))) for v in line)>=min(2,max(1,len(merged)-2)):
                datawords.extend(line)
                acceptedlines+=1
                if acceptedlines>=10:
                    break
        for j in range(1,len(bounds)-1):
            left=(merged[j-1][0]+merged[j-1][1])/2
            right=(merged[j][0]+merged[j][1])/2
            occupied=[]
            for vv in sorted(datawords,key=lambda v:v['x0']):
                a=max(left,vv['x0']);b=min(right,vv['x1'])
                if b<=a:
                    continue
                if occupied and a<=occupied[-1][1]+1:
                    occupied[-1][1]=max(occupied[-1][1],b)
                else:
                    occupied.append([a,b])
            spaces=[(occupied[k+1][0]-occupied[k][1],(occupied[k+1][0]+occupied[k][1])/2) for k in range(len(occupied)-1)]
            if spaces:
                width,center=max(spaces)
                if width>=5:
                    bounds[j]=center
        for j,r in enumerate(hintroles):
            if r!='source' or j==0:
                continue
            lo=merged[j-1][1]-3;hi=merged[j][0]+8
            starts=collections.Counter(round(vv['x0']*2)/2 for vv in words if firstdata<=vv['top']<yend and lo<=vv['x0']<=hi and len(re.sub('[^A-Za-z]','',vv['text']))>=2)
            if starts:
                xx,count=starts.most_common(1)[0]
                if count>=3 and bounds[j-1]+5<xx<bounds[j+1]-5:
                    bounds[j]=xx-.5
        cols=list(zip(bounds,bounds[1:]))
        headers=[clean(pd.text((a-.5,htop,b+.5,hend),whole_words=True)) for a,b in merged]
        roles=[heading_role(h) for h in headers]
        ni=next((j for j,(a,b) in enumerate(cols) if a<=w['x0']<=b),0)
        roles[ni]='name'
        for j,(h,r) in enumerate(zip(headers,roles)):
            if not h:
                roles[j]='violation'
        if not ('low' in roles and 'high' in roles):
            roles=['level' if r in ('low','high') else r for r in roles]
        if not any(r in ('level','combined','range','low','high') for r in roles):
            log('REJECT TEXT SCHEMA',pd.num,htop,hend,list(zip(headers,roles)))
            continue
        log('TEXT SCHEMA',pd.num,list(zip(headers,roles)),cols)
        try:
            crop=pd.page.crop((x0,htop,x1,yend))
            tabs=crop.find_tables({'vertical_strategy':'explicit','explicit_vertical_lines':bounds,'horizontal_strategy':'text','min_words_horizontal':1,'text_x_tolerance':1.5,'text_y_tolerance':2,'intersection_tolerance':4})
        except Exception as exc:
            log('TEXT TABLE ERROR',exc)
            continue
        for t in tabs:
            if len(t.columns)<2:
                continue
            cells=native_cells(pd,t)
            sc=dict(cols=cols,headers=headers,roles=roles,ni=ni,start=hend,htop=htop,bbox=(x0,htop,x1,yend),groups=[])
            yield cells,sc,hend,table_caption(pd,cells,(x0,htop,x1,yend))


def main(pdf_path, out_path):
    global _PROGRESS_RECORDS
    pdf_path=os.path.abspath(pdf_path)
    out_path=os.path.abspath(out_path)
    try:
        result = subprocess.run(['pdftotext','-layout',pdf_path,'-'], capture_output=True, text=True, encoding='utf-8',errors='replace',timeout=20)
        pagetexts = result.stdout.split('\f') if result.returncode==0 and result.stdout.strip() else []
    except Exception:
        pagetexts = []
    default_system = primary_page_id('\n'.join(pagetexts))
    records = []
    _PROGRESS_RECORDS=records
    current_system = default_system
    previous = None
    with pdfplumber.open(pdf_path) as pdf:
        for pn,page in enumerate(pdf.pages):
            if time.monotonic()-START > 105:
                break
            page_text = pagetexts[pn] if pn < len(pagetexts) else page.extract_text() or ''
            page_ids = find_system(page_text)
            page_id=primary_page_id(page_text)
            if page_id:
                if current_system!=page_id:
                    previous=None
                current_system = page_id
            ocr_doc=None
            is_ocr=False
            dominant_image=max((max(0,min(page.width,im.get('x1',0))-max(0,im.get('x0',0)))*max(0,min(page.height,im.get('bottom',0))-max(0,im.get('top',0)))/(page.width*page.height) for im in page.images),default=0)
            if page.images and (len(page.chars)<30 or (len(page.chars)<350 and dominant_image>.55)):
                ocr_doc=ocr_page(pdf_path,pn+1,os.path.dirname(os.path.abspath(out_path)))
                if ocr_doc:
                    page=ocr_doc.pages[0]
                    page_text=page.extract_text() or ''
                    is_ocr=True
            # Narrative-only pages have no dense column headings.
            if not previous and not re.search(r'\bMCLG?\b|\bMRDLG?\b|\bPHG\b|level\s+(?:found|detected)|range\s+of|90th|percentile|UCMR|comparison value|hardness|removal ratio|contaminant|parameter|substance|constituent|compound|analyte|average|organism|microbe|element|metal|characteristic|indicator|concentration|measurement|\bmean\b', page_text, re.I):
                if ocr_doc:
                    ocr_doc.close()
                continue
            pd = PageData(page,pn+1)
            if ocr_doc and hasattr(ocr_doc,'_scan_image'):
                pd.ocr_image=ocr_doc._scan_image
            mentions=[] if is_ocr else system_mentions(pd)
            settings={'snap_tolerance':3,'join_tolerance':3,'intersection_tolerance':4}
            if ocr_doc and hasattr(ocr_doc,'_scan_rules'):
                hh,vv=ocr_doc._scan_rules
                if len(hh)>=2 and len(vv)>=2:
                    settings.update(vertical_strategy='explicit',horizontal_strategy='explicit',explicit_vertical_lines=vv,explicit_horizontal_lines=hh)
            tables = page.find_tables(table_settings=settings)
            selected = []
            excluded = []
            table_inputs=[]
            for t in tables:
                one_continuation=previous and len(t.rows)==1 and t.bbox[3]-t.bbox[1]<page.height*.5
                if len(t.columns) < 2 or (len(t.rows) < 2 and not one_continuation) or t.bbox[2]-t.bbox[0] < page.width*.20:
                    continue
                cells = native_cells(pd,t)
                for bb,cc in table_sections(cells,t.bbox):
                    table_inputs.append((TablePart(bb),cc))
            for t,cells in table_inputs:
                caption = table_caption(pd,cells,t.bbox)
                if re.search(r'violation summary|summary of violations|monitoring violations|sampling schedule|service.line inventory|health.effects table',caption,re.I):
                    excluded.append(t.bbox)
                    continue
                sc = find_schema(pd,cells,t.bbox)
                if not sc:
                    transposed=transpose_records(pd,cells,t.bbox,table_system(mentions,t.bbox[1],current_system),caption)
                    if transposed:
                        records.extend(transposed)
                        excluded.append(t.bbox)
                        continue
                    param=parameter_title(caption)
                    if param:
                        sc=find_schema(pd,cells,t.bbox,param)
                    if not sc:
                        sc=continue_schema(pd,cells,t.bbox,previous)
                if sc:
                    if is_ocr:
                        sc['ocr']=True
                        for j,r in enumerate(sc['roles']):
                            if r!='level':
                                continue
                            a,b=sc['cols'][j]
                            vv=[c.text for c in cells if c.box[1]>=sc['start']-1 and c.box[0]>=a-3 and c.box[2]<=b+3 and c.text.strip()]
                            if vv and all(PAIR_RE.fullmatch(remove_unit(unfoot(v)).strip()) for v in vv):
                                sc['roles'][j]='range'
                    selected.append((t,cells,sc,caption))
                    excluded.append(t.bbox)
                    previous=(sc,caption,page.width)
            for t,cells,sc,caption in selected:
                records.extend(records_from_table(pd,cells,sc,table_system(mentions,t.bbox[1],current_system),caption))
            for cells,sc,hend,caption in borderless_tables(pd,excluded):
                if is_ocr:
                    sc['ocr']=True
                last=[]
                for vals,yr in text_rows(pd,cells,sc,hend):
                    mid=(yr[0]+yr[1])/2
                    if any(bb[1]-1<=mid<=bb[3]+1 and max(0,min(sc['bbox'][2],bb[2])-max(sc['bbox'][0],bb[0]))>min(sc['bbox'][2]-sc['bbox'][0],bb[2]-bb[0])*.6 for bb in excluded):
                        continue
                    log('TEXT ROW',pn+1,[clean(v) for v in vals])
                    rr=row_record(vals,sc,table_system(mentions,sc['bbox'][1],current_system),get_unit(caption))
                    if rr:
                        records.extend(rr)
                        last=rr
            page.close()
            if ocr_doc:
                ocr_doc.close()
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({'records':records}, f, ensure_ascii=False, indent=2)
        f.write('\n')


if __name__ == '__main__':
    if len(sys.argv)!=3:
        raise SystemExit('Usage: extract.py report.pdf output.json')
    if hasattr(signal,'SIGALRM'):
        signal.signal(signal.SIGALRM,deadline_handler)
        signal.alarm(110)
    try:
        main(sys.argv[1],sys.argv[2])
    except ExtractionDeadline:
        with open(sys.argv[2],'w',encoding='utf-8') as fh:
            json.dump({'records':_PROGRESS_RECORDS},fh,ensure_ascii=False,indent=2)
            fh.write('\n')
    finally:
        if hasattr(signal,'SIGALRM'):
            signal.alarm(0)
