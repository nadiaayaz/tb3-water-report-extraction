#!/usr/bin/env python3
"""Offline geometry-aware extraction of Consumer Confidence Report results."""
import collections
import json
import math
import os
import re
import statistics
import subprocess
import sys
import time
import pdfplumber
from pdfplumber.utils import extract_text
START=time.monotonic()
DEBUG=bool(os.environ.get('CCR_DEBUG'))
def log(*args):
    if DEBUG: print(*args,file=sys.stderr)
def tidy(s):
    if s is None: return ''
    s=str(s).replace('\uf06d','µ').replace('\uf0a3','≤').replace('\uf0b3','≥').replace('\ufb01','fi').replace('\ufb02','fl').replace('\u00ad','')
    s=re.sub(r'(?<=[a-z])-\n(?=[a-z])','',s)
    s=re.sub(r'(?<=[/\-–—])\s*\n\s*(?=\d)',lambda m:' ' if m.start()>0 and s[m.start()-2:m.start()-1]==' ' else '',s)
    s=re.sub(r'\s+',' ',s).strip()
    return re.sub(r'\s+([,;)])',r'\1',s)
def norm(s): return re.sub(r'[^a-z0-9]+',' ',tidy(s).lower()).strip()
def strip_notes(s):
    s=tidy(s)
    s=re.sub(r'\[\s*\d+(?:\s*,\s*\d+)*\s*\]','',s)
    s=re.sub(r'[⁰¹²³⁴⁵⁶⁷⁸⁹*†‡]+','',s)
    s=re.sub(r'\s*\((?:see\s+)?note\s*\d+\)\s*','',s,flags=re.I)
    return s.strip()
NULLS={'','na','n a','n/a','n.a.','n.a','none','no range','-','--','---','–','—','−','―','not applicable','not available'}
def is_null(s):
    s=strip_notes(s)
    return s.lower() in NULLS or bool(re.fullmatch(r'\(?see\s+(?:foot)?note\s*\d*\)?',s,re.I))
UNIT_PATTERN=r'(?<![A-Za-z])(?:pCi\s*/\s*[lL]|[munpµμ]g\s*/\s*[lL]|mg\s*/\s*kg|ppm|ppb|ppt|ppq|NTU|FTU|MFL|mrem\s*/\s*(?:yr|year)|(?:µ|μ|u)S\s*/\s*cm|(?:micro|[µμu])mho[s]?\s*/\s*cm|grains?\s*(?:/|per)\s*gallon|gpg|(?:CFU|MPN)\s*/\s*(?:100\s*)?m[lL]|parts per (?:million|billion|trillion|quadrillion)|(?:milli|micro|nano|pico)grams? per lit(?:er|re)|picocuries per lit(?:er|re)|standard units|S\.?U\.?|°[CF]|(?<=\()\s*%\s*(?=\)))(?:\s+as\s+(?:CaCO3|CaCO₃|N|Ca|P|Cl2|Cl₂)|\s*\(\s*as\s+(?:CaCO3|CaCO₃|N|Ca|P|Cl2|Cl₂)\s*\))?(?![A-Za-z])'
UNIT_RE=re.compile(UNIT_PATTERN,re.I)
def get_unit(s):
    m=UNIT_RE.search(tidy(s));return m.group(0).strip() if m else None
def unit_from_caption(context):
    lines=[tidy(s) for s in context.splitlines() if tidy(s)]
    for line in reversed(lines[-3:]):
        if re.search(r'(?:all|units?|expressed|reported|measured)\b.*?\b(?:in|as)\s*'+UNIT_PATTERN+r'|\bunits?\s*[:=]',line,re.I):return get_unit(line)
    if lines:
        line=lines[-1]
        remainder=re.sub(r'\b(?:19|20)\d{2}\b','',remove_unit(line))
        if len(line)<150 and not re.search(r'\d',remainder):return get_unit(line)
    return None
def remove_unit(s): return re.sub(r'\(\s*\)','',UNIT_RE.sub('',s)).strip()
def scalar(s,keep_unit=False):
    s=strip_notes(s)
    if is_null(s): return None
    s=re.sub(r'^[−﹣－–—]\s*(?=\d|\.)','-',s)
    s=re.sub(r'(?<=[eE])[−﹣－–—](?=\d)','-',s)
    if not keep_unit:
        trial=remove_unit(s)
        if re.fullmatch(r'[<>≤≥~]?\s*[+\-]?(?:\d[\d,]*(?:\.\d*)?|\.\d+)(?:[eE][+\-]?\d+)?(?:\s*%)?',trial): s=trial
        elif re.fullmatch(r'(?:MCLG?|MRDLG?|SMCL|AL|ALG|PHG)\s*[:=]?\s*[<>≤≥]?\s*[+\-]?(?:\d[\d,]*(?:\.\d*)?|\.\d+)',trial,re.I):s=trial
        elif get_unit(s) and re.fullmatch(r'N\.?D\.?|BDL|BQL|Absent|Present|Negative|Positive|Not detected|Non[- ]?detect(?:ed)?',trial,re.I):s=trial
        elif get_unit(s) and trial and is_null(trial):return None
    s=re.sub(r'^([<>≤≥])\s+(?=\d|\.)',r'\1',s)
    if re.fullmatch(r'[+\-]?(?:\d[\d,]*(?:\.\d*)?|\.\d+)(?:[eE][+\-]?\d+)?',s):
        n=float(s.replace(',',''))
        if not math.isfinite(n):return s
        return int(n) if n.is_integer() else n
    return s
def regulatory_value(s,keep_unit=False):
    s=strip_notes(s)
    if UNIT_RE.fullmatch(s):return None
    if re.match(r'^(?:N/A|NA)\s+(?:synthetic|radioa|inorg|organic|microbiological|unregulated|regulated)\b',s,re.I):return None
    s=re.sub(r'\s+(?:(?:inorganic|organic|synthetic|radioactive|microbiological)\s+contamin\w*|(?:synthetic|radioa))\s*$','',s,flags=re.I)
    s=re.sub(r'(?<=samples)\s+[A-Za-z]\s+(?=[<>≤≥])',' ',s)
    m=re.search(r'\b(?:MCL|SMCL|AL|MRDL)\s*=\s*[<>≤≥]?\s*\d+(?:\.\d+)?(?:\s*'+UNIT_PATTERN+r')?',s,re.I)
    if m:
        tail=s[m.end():]
        if not re.search(r'\b(?:or|of|per|samples?|average|annual|monthly|daily|percent|for|as|in|at|and|not|more|less|must|ppm|ppb)\b',tail,re.I):s=m.group(0)
    return scalar(s,keep_unit)
ATOM=r'(?:[<>≤≥]?\s*[+\-−–—]?(?:\d[\d,]*(?:\.\d*)?|\.\d+)(?:[eE][+\-−–—]?\d+)?\s*%?|N\.?D\.?|BDL|BQL|Absent|Non[- ]?detect(?:ed)?)'
RANGE_RE=re.compile(r'(?P<lo>'+ATOM+r')\s*(?:[-–—−]|\bto\b)\s*(?P<hi>'+ATOM+r')',re.I)
def parse_range(s):
    s=strip_notes(s)
    if is_null(s): return None,None
    s=re.sub(r'^range\s*(?:of\s*(?:results|detections))?\s*[:=]?','',s,flags=re.I).strip()
    trial=remove_unit(s).strip(' ()')
    m=RANGE_RE.search(trial)
    if m: return scalar(m.group('lo')),scalar(m.group('hi'))
    if re.fullmatch(ATOM,trial,re.I):
        v=scalar(trial);return v,v
    return None,None
def measured(s):
    s=strip_notes(s)
    if not s:return False
    if re.match(r'^[<>≤≥~(]?\s*[+\-]?(?:\d|\.\d)|^(?:nd\b|absent\b|present\b|negative\b|positive\b|not detected\b|non.detect\b|bdl\b|bql\b|n/?a\b|none\b|tt\b|al\s*=|mrdl\s*=)',s,re.I):return True
    if re.match(r'^(?:range|highest|maximum|max|average|avg\.?|mean|result|level)\b',s,re.I) and re.search(r'\d',s):return True
    return bool(re.search(r'\d\s*\([^()]*\d\s*[-–—]\s*\d[^()]*\)',s))
def level_parts(s):
    original=s;s=strip_notes(s)
    if is_null(s): return None,None,None
    if UNIT_RE.fullmatch(s):return None,None,None
    lo=hi=None
    rm=re.search(r'\(([^()]*)\)',s)
    if rm and RANGE_RE.search(remove_unit(rm.group(1))):
        lo,hi=parse_range(rm.group(1));s=(s[:rm.start()]+' '+s[rm.end():]).strip()
    rm=re.search(r'\bRange\b\s*(?:of\s*(?:results|detections))?\s*[:=]?\s*',s,re.I)
    if rm:
        tail=s[rm.end():];rval=RANGE_RE.search(remove_unit(tail))
        if rval:
            lo,hi=scalar(rval.group('lo')),scalar(rval.group('hi'))
            # Find the next labelled statistic so a range printed first does
            # not hide a following average or maximum.
            next_stat=re.search(r'\b(?:Average|Avg\.?|Mean|Max(?:imum)?|Highest)\b',tail,re.I)
            s=(s[:rm.start()]+' '+(tail[next_stat.start():] if next_stat else '')).strip(' /;,')
        else:
            s=s[:rm.start()].strip(' /;,')
    elif '\n' in original:
        pieces=[tidy(p) for p in original.split('\n') if tidy(p)]
        if len(pieces)>=2 and measured(pieces[0]) and RANGE_RE.fullmatch(remove_unit(' '.join(pieces[1:])).strip(' ()')):
            lo,hi=parse_range(' '.join(pieces[1:]));s=pieces[0]
    stripped=re.sub(r'^(?:(?:90th\s*percentile|highest\s*(?:single\s*)?(?:result|measurement|value|level|reading)?|maximum|max|average|avg\.?|mean|LRAA|RAA|MPA|running annual average|test\s+result|result|concentration|amount|detected|level(?:\s*(?:detected|found))?)\s*(?:value)?\s*[:=]?\s*)','',s,flags=re.I)
    if stripped:s=stripped
    s=re.sub(r'^\s*\((?:LRAA|RAA|MPA)\)\s*','',s,flags=re.I)
    s=re.sub(r'^(?:90%\s*(?:of\s*)?(?:results|samples)?\s*(?:were\s*)?less\s*than\s*[:=]?)','',s,flags=re.I).strip()
    s=re.split(r'\s*(?:/|;)\s*(?=(?:average|avg|max|highest|mean)\b)',s,maxsplit=1,flags=re.I)[0]
    s=re.split(r'\s+(?=(?:Average|Avg\.?|Maximum|Max|Highest|Mean)\s*[:=])',s,maxsplit=1,flags=re.I)[0]
    s=re.sub(r'\s*\((?:L?RAA|RA|MPA|90th\s*percentile|average|avg\.?|maximum|max)\)\s*$','',s,flags=re.I)
    return scalar(s),lo,hi
def violation(s,heading):
    s=strip_notes(s)
    if is_null(s): return None
    yes=bool(re.match(r'^(?:yes|y|true|pass(?:ed)?)(?:\b|$)|^[✓✔ü\uf0fc]',s,re.I))
    no=bool(re.match(r'^(?:no|n|false|fail(?:ed)?)(?:\b|$)|^[✗✘×]',s,re.I))
    if not (yes or no): return None
    compliance=bool(re.search(r'compliance|complian[tce]|meets?|met\s+(?:the\s*)?standards?|standards?\s+met|pass',heading,re.I))
    return ('N' if yes else 'Y') if compliance else ('Y' if yes else 'N')
NAME_HEADER=re.compile(r'\b(contaminant|parameter|substance|constituent|analyte|compound|chemical)(?:s)?\b',re.I)
def role(h):
    s=norm(h)
    if not s: return 'unknown'
    if tidy(h) in ['#','No.','No','ID'] or re.fullmatch(r'item(?: no| number)?|row(?: no| number)?|s no|sr no',s):return 'count'
    if NAME_HEADER.search(s) and re.match(r'^(?:contaminants?|parameters?|substances?|constituents?|analytes?|compounds?|chemicals?)\b',s): return 'name'
    if re.fullmatch(r'(?:result|measurement|value|test|statistic) (?:type|description|statistic)',s):return 'subname'
    if re.fullmatch(r'(?:(?:sampling|sample|water|distribution|service|treatment) )?(?:locations?|sites?|plants?|wells?|systems?|facilit(?:y|ies)|areas?|entry points?|water supplies)(?: (?:name|number|no|id))?',s): return 'location'
    location_words={'location','locations','plant','plants','well','wells','site','sites','system','systems','name','id','no','source','sources','treatment','water','sample','sampling','monitoring','facility','facilities','entry','point','points','area','areas','type','s'}
    if set(s.split())<=location_words and (re.search(r'\blocations?\b|\bplants?\b|\bwells?\b|\bsites?\b|\bfacilit(?:y|ies)\b|\bentry points?\b',s) or re.fullmatch(r'(?:water|source|sample) type|system (?:name|id)',s)):return 'location'
    if re.fullmatch(r'(?:raw|source|finished|treated|untreated)(?: water)?(?: results?| concentrations?| levels?)?',s):return 'level'
    if re.search(r'sources?|health effects?|origin|potential effects?',s) and not re.search(r'(?:source|raw).*?(?:level|result|average|mean|concentration|amount|detect)|(?:level|result|average|mean|concentration|amount|detect).*?(?:source|raw)',s): return 'description'
    if re.fullmatch(r'(?:no|number) (?:of )?(?:samples?|sites?|homes?|tests?|locations?|results?)|samples?|tests?|(?:detected|detection|positive|sample) counts?|sample size',s) or re.search(r'(?:sites?|homes?|samples?) (?:sampled|tested|collected|analy[sz]ed)',s):return 'count'
    if re.search(r'(?:number|no|sites?|samples?|homes?|locations?) .*?(?:above|exceed|over|collected|tested|taken)|(?:number|no) of (?:samples?|sites?|homes?|quarters)|individual results|total samples|results greater than al',s) and not re.search(r'yes no|y n',s): return 'count'
    if re.search(r'violat|exceed|compliance|complian[tce]|meets? .*standard|met standards|standards? met|^pass|yes no|y n',s): return 'violation'
    if re.search(r'comparison|advisory|health (?:based|reference|risk)|notification|response level|\bhrl\b|health risk limit|minimum report|report(?:ing)? limit|detection limit|quantitation limit|quantification limit|limit of (?:detection|quantitation|quantification)|\b(?:md l|mdl|lod|loq|rl|dl)\b|method detection|next (?:test|sample|sampling)|future',s): return 'other'
    if re.search(r'\bmclg\b|\bmrdlg\b|\bmldg\b|\bphg\b|\balg\b|ideal goal|level goal|public health goal|^goal$',s): return 'mclg'
    if re.search(r'\bal\b',s) and not re.search(r'90|percentile',s):return 'mcl'
    if re.search(r'\bmcl\b|\bsmcl\b|\bmrdl\b|\bminrdl\b|\btt\b|action (?:level|limit)|regulatory|epa.*limit|\ballowed\b|\brequired\b|highest.*allow|maximum.*allow|maximum contaminant level|maximum residual disinfectant level|removal required|minimum.*(?:residual|disinfectant)|(?:state|federal|epa|drinking water).*standard|^limit|^standard',s): return 'mcl'
    if re.search(r'range.*(?:or|and).*level|range level|level or range',s): return 'range_level'
    if re.search(r'\b(?:min|minimum|low|lowest)\b.*\b(?:max|maximum|high|highest)\b',s):return 'range'
    if re.search(r'\brange\b.*\b(?:low|lowest|min|minimum)\b',s): return 'low'
    if re.search(r'\brange\b.*\b(?:high|highest|max|maximum)\b',s): return 'high'
    if 'range' in s: return 'level' if re.match(r'(level|amount|average|highest)',s) else 'range'
    if re.search(r'\bunits?\b|unit of measure',s) and not re.search(r'level|detected|average|maximum|minimum|result|found',s): return 'unit'
    if re.search(r'\bdates?\b|\byear\b|\byears\b|\bsampled\b|when sampled|sampling period|monitoring period|^sample$|^collection$|collection period',s): return 'date'
    if NAME_HEADER.search(s): return 'name'
    if re.search(r'number.*(?:detections|positive)|highest no|positive samples',s): return 'micro_count'
    if re.match(r'^(?:low|lowest|min|minimum)\b',s) and not re.search(r'average|90|percentile',s): return 'low'
    if re.match(r'^(?:high|highest|max|maximum)\b',s) and not re.search(r'average|90|percentile',s): return 'high'
    if re.search(r'level|detect|found|result|average|\bavg\b|\b(?:l?raa|mpa)\b|mean|90|percentile|concentration|amount|\bvalue\b|measured|removal ratio|percent removal|our water|your water',s): return 'level'
    if get_unit(h): return 'level'
    return 'unknown'
DATE_CORE=r'(?:\b(?:19|20)\d{2}-\d{1,2}-\d{1,2}\b|\b\d{1,2}/\d{1,4}(?:/\d{2,4})?\b|\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+(?:\d{1,2}(?:st|nd|rd|th)?[, ]+)?(?:19|20)\d{2}\b|\b(?:19|20)\d{2}\b)'
DATE_RE=re.compile(DATE_CORE+r'(?:\s*(?:[-–—,;/]|\band\b|&)\s*'+DATE_CORE+r')*',re.I)
def clean_name(s):
    s=strip_notes(s)
    s=re.sub(r'^\s*\d+[.)]\s*(?=[A-Za-z])','',s)
    s=re.sub(r'^(?!(?:19|20)\d{2}\b)\d+\s+(?=[A-Za-z])','',s)
    sample_text=re.sub(r'\([^()]*(?:next|future)[^()]*\)','',s,flags=re.I)
    sample_text=re.split(r'\b(?:Next|Future)\s+(?:Test(?:ing)?|Sampling)\b',sample_text,maxsplit=1,flags=re.I)[0]
    dm=DATE_RE.search(sample_text);date=dm.group(0) if dm else None
    s=re.sub(r'\([^()]*(?:\btested\b|\bsampled\b|\btesting\b|\bnext\s+test\b|\bfuture\b)[^()]*\)','',s,flags=re.I)
    s=re.sub(r'\((?:\d{1,2}/\d{1,2}(?:/\d{2,4})?(?:\s*[-–]\s*\d{1,2}/\d{1,2}(?:/\d{2,4})?)?|(?:19|20)\d{2})\)','',s)
    s=re.sub(r'\b(?:Next\s+(?:Test(?:ing)?|Sampling)|Tested|Last\s+(?:Tested|Sampled))\b.*$','',s,flags=re.I)
    s=DATE_RE.sub('',s)
    s=re.sub(r'\([^()]*\)',lambda m:'' if get_unit(m.group()) and not re.search(r'[A-Za-z]',remove_unit(m.group().strip('()'))) else m.group(),s)
    s=re.sub(r'\s*[,;:]?\s*'+UNIT_PATTERN+r'\s*$','',s,flags=re.I)
    s=re.sub(r'\s*\(\s*\)\s*','',s)
    s=re.sub(r'[,;:]\s*\)',')',s)
    s=re.sub(r'\s+(?:Disinfection\s+Byproducts|(?:Inorganic|Organic|Radioactive|Synthetic|Microbiological|Regulated|Unregulated|Secondary)\s+Contaminants)\s*$','',s,flags=re.I)
    for count in [1,2]:
        if re.search(r'\d{'+str(count)+r'}$',s):
            base=s[:-count]
            known=known_parameter(base)
            if known and (norm(known)==norm(base) or re.search(r'\([A-Za-z0-9-]+\)$',base)):
                s=base;break
    return tidy(s).strip(' ,;:'),date
def cell_text(chars,bbox,footnotes=True):
    x0,y0,x1,y1=bbox
    cs=[c for c in chars if x0-.2<=(c['x0']+c['x1'])/2<x1-.1 and y0-.2<=(c['top']+c['bottom'])/2<y1-.1]
    if not cs: return ''
    if footnotes:
        sizes=collections.Counter(round(c.get('size',10),1) for c in cs if c['text'].strip())
        dominant=sizes.most_common(1)[0][0] if sizes else 10
        cs=[c for c in cs if c.get('fontname','')=='OCR' or 'glyphless' in c.get('fontname','').lower() or not(c.get('size',dominant)<dominant*.82 and re.fullmatch(r'[0-9a-z*†‡]+',c['text']))]
    try: return extract_text(cs,x_tolerance=1.3,y_tolerance=2).strip()
    except Exception: return ''.join(c['text'] for c in cs)
def coverage(intervals):
    end=-1e20;total=0
    for a,b in sorted(intervals):
        total+=max(0,b-max(a,end));end=max(end,b)
    return total
class Grid:
    def __init__(self,page,table):
        self.page=page;self.bbox=table.bbox;self.cells=table.cells;self.chars=page.chars;self.rows=[];self.row_boxes=[];self.groups=[]
        x0,y0,x1,y1=self.bbox
        xs=collections.defaultdict(list);ys=collections.defaultdict(list)
        for a,b,c,d in self.cells:
            xs[round(a,2)].append((b,d));xs[round(c,2)].append((b,d))
            ys[round(b,2)].append((a,c));ys[round(d,2)].append((a,c))
        self.xs=[x for x,sp in sorted(xs.items()) if coverage(sp)>=.55*(y1-y0)]
        self.ys=[y for y,sp in sorted(ys.items()) if coverage(sp)>=.53*(x1-x0)]
        if len(self.xs)<3 or len(self.ys)<2: return
        # Horizontal rules may divide only the result columns while the name
        # and limits remain merged. Retain those genuine result-cell divisions.
        value_headers=[]
        for c in self.cells:
            if c[1]>y0+(y1-y0)*.5:continue
            tx=cell_text(self.chars,c)
            if len(tx)<140 and re.search(r'\blevel\b|\bfound\b|\baverage\b|\bpercentile\b|90%.*(?:of|results)|highest.*detections',tx,re.I) and not re.search(r'\bmcl\b|\bmclg\b|\bmrdl\b|action level|allowed|required|number of|above',tx,re.I):
                for i,(a,b) in enumerate(zip(self.xs,self.xs[1:])):
                    if c[0]<=a+.5 and c[2]>=b-.5 and c[2]-c[0]<(x1-x0)*.4:value_headers.append((i,c[3]))
        extras=[]
        for i,header_bottom in value_headers:
            a,b=self.xs[i:i+2]
            for c in self.cells:
                if c[0]<=a+.5 and c[2]>=b-.5 and c[2]-c[0]<(b-a)*1.2 and c[1]>=header_bottom-.5:
                    extras.extend([round(c[1],2),round(c[3],2)])
        self.ys=sorted(set(self.ys+extras))
        for c in self.cells:
            covered=[i for i,(a,b) in enumerate(zip(self.xs,self.xs[1:])) if c[0]<=a+.5 and c[2]>=b-.5]
            if 2<=len(covered)<len(self.xs)-2:
                self.groups.append((c,covered,cell_text(self.chars,c)))
        for ya,yb in zip(self.ys,self.ys[1:]):
            if yb-ya<1: continue
            across=[c for c in self.cells if c[0]<=self.xs[0]+8 and c[2]>=self.xs[-1]-8 and c[1]<=ya+.5 and c[3]>=yb-.5]
            if across:
                self.rows.append([cell_text(self.chars,(self.xs[0],ya,self.xs[-1],yb))]+['']*(len(self.xs)-2))
                self.row_boxes.append((self.xs[0],ya,self.xs[-1],yb))
                continue
            row=[]
            for xa,xb in zip(self.xs,self.xs[1:]):
                bounds=(xa,ya,xb,yb);midx,midy=(xa+xb)/2,(ya+yb)/2
                spanning=[c for c in self.cells if c[0]-.2<=midx<=c[2]+.2 and c[1]-.2<=midy<=c[3]+.2 and c[2]-c[0]<(x1-x0)*.9]
                if spanning:
                    c=min(spanning,key=lambda c:(c[2]-c[0])*(c[3]-c[1]))
                    if c[0]<=xa+.5 and c[2]>=xb-.5 and c[3]-c[1]>(yb-ya)+2:
                        full=cell_text(self.chars,(xa,c[1],xb,c[3]))
                        if len(re.findall(r'(?im)^(?:Highest|Lowest|Monthly|Percent|Average|90th|Minimum|Maximum)\b',full))<2:bounds=(xa,c[1],xb,c[3])
                row.append(cell_text(self.chars,bounds))
            self.rows.append(row);self.row_boxes.append((self.xs[0],ya,self.xs[-1],yb))

class SimpleGrid:
    def __init__(self,page,rows,bbox,xs=None,row_boxes=None):
        self.page=page;self.rows=rows;self.bbox=bbox;self.xs=xs or [];self.row_boxes=row_boxes or [];self.groups=[]
def system_ids(text):
    text=re.sub(r'(\bPWS\s*ID\b)\s*(?:Number|No\.?)\s*',r'\1 ',text,flags=re.I)
    patterns=[r'(?:\bPWS\s*I\s*D\b|\bPWSID\b|\bPWS\s*(?:Identification|ID|Number|No\.?|#)|Public\s+Water\s+(?:Supply|System)\s+(?:Identification|I\.?D\.?|Number|No\.?))[^\n\w]{0,8}(?:\(PWSID\))?\s*[:#\-]?\s*([A-Z]{0,2}\s*\d{5,10}[A-Z]?)',r'\b(?:System|Water\s+Supply)\s+(?:ID|Identification)\s*(?:No\.?|Number|#)?\s*[:#]?\s*([A-Z]{0,2}\d{6,10})',r'\b(?:PWSID|PWS)\s*[:#]?\s*([A-Z]{2}\d{5,9})']
    found=[]
    for pat in patterns:
        for m in re.finditer(pat,text,re.I):
            value=re.sub(r'\s+','',m.group(1))
            if value not in [v for _,v in found]: found.append((m.start(),value))
    return sorted(found)
def choose_system(ids,counts,text):
    if not ids:return None
    def score(pair):
        pos,sid=pair
        full_label=bool(re.match(r'Public\s+Water',text[pos:],re.I))
        edge=pos<600 or pos>len(text)-450
        return (counts.get(sid,0)*4+full_label+edge,-pos)
    return max(ids,key=score)[1]
def report_front(text):
    front=tidy(text[:1100])
    m=re.search(r'(?:annual.{0,80}(?:water|quality).{0,50}report|(?:19|20)\d{2}.{0,80}(?:water|consumer).{0,50}report|(?:water|consumer).{0,70}report.{0,40}(?:19|20)\d{2})',front,re.I)
    return bool(m and m.start()<500)
def row_is_data(row,ni):
    name=tidy(row[ni])
    if not name or len(name)>180 or not re.search(r'[a-zA-Z]',name): return False
    if role(name)=='name' or re.match(r'^(?:definitions?|notes?\b|\d+\s*during|range of|range$)',name,re.I): return False
    if re.match(r'^(?:inorganic|organic|synthetic|volatile|radioactive|microbiological|regulated|unregulated|secondary|disinfection byproducts)\s*(?:chemical\s*)?contaminants?\b',name,re.I): return False
    if any(measured(c) for i,c in enumerate(row) if i!=ni):return True
    if any(get_unit(c) for i,c in enumerate(row) if i!=ni):return True
    return False
def labelled_levels(raw):
    s=strip_notes(raw)
    if is_null(s) or RANGE_RE.fullmatch(remove_unit(s).strip(' ()')):return [(None,raw)]
    pat=re.compile(r'(?P<place>[A-Za-z][A-Za-z0-9 .#\-/]*?)\s*[:=]?\s*(?P<value>'+ATOM+r')\s*(?:'+UNIT_PATTERN+r')?\s*(?:\(\s*(?P<range>'+ATOM+r'\s*[-–—]\s*'+ATOM+r')\s*\))?(?:\s*\((?:L?RAA|MPA)\))?(?=\s*(?:$|[;,\n/]|[A-Za-z]))',re.I)
    matches=list(pat.finditer(s))
    statistics_label=r'\b(?:average|avg|maximum|max|highest|high|minimum|min|lowest|low|range|90th|lraa|raa|mpa|result|value|level|mean|concentration|amount|detected|number|count|samples|removal)\b'
    if matches and (len(matches)>1 or (matches[0].start()==0 and matches[0].end()==len(s))) and not any(re.search(statistics_label,m.group('place'),re.I) for m in matches):
        return [(m.group('place').strip(' ;,'),s[m.start('value'):m.end()].strip()) for m in matches]
    return [(None,raw)]
class Extractor:
    def __init__(self):
        self.records=[];self.last_header=None;self.table_debug=[]
    def parse(self,grid,system,context=''):
        rows=grid.rows
        if len(rows)<2: return False
        nc=len(rows[0])
        if nc<2 or nc>35: return False
        ni=0;header_start=None
        for ri,row in enumerate(rows[:15]):
            candidates=[i for i,c in enumerate(row) if role(c)=='name' and len(tidy(c))<110 and (sum(bool(tidy(v)) for v in row)>2 or re.fullmatch(r'contaminants?|parameters?|substances?|constituents?|analytes?|compounds?|chemicals?',tidy(c),re.I))]
            if candidates: ni=candidates[0];header_start=ri;break
        if header_start is None: return self.parse_special(grid,system,context)
        first=None
        for ri in range(header_start+1,len(rows)):
            if row_is_data(rows[ri],ni): first=ri;break
        if first is None: return False
        # A single drawn grid may contain several sections with different columns.
        for ri in range(first+1,len(rows)):
            if role(rows[ri][ni])=='name' and sum(role(c) in ['mcl','mclg','level','range','date','unit','violation'] for c in rows[ri])>=2:
                a=SimpleGrid(grid.page,rows[:ri],grid.bbox,grid.xs,grid.row_boxes[:ri])
                b=SimpleGrid(grid.page,rows[ri:],grid.bbox,grid.xs,grid.row_boxes[ri:])
                return self.parse(a,system,context) | self.parse(b,system,context)
        headers=[]
        for ci in range(nc):
            pieces=[]
            for row in rows[header_start:first]:
                v=tidy(row[ci])
                if v and v not in pieces: pieces.append(v)
            headers.append(' '.join(pieces))
        roles=[role(h) for h in headers];roles[ni]='name';data=rows[first:]
        if re.search(r'violations?\s+(?:summary|history)|summary\s+of\s+violations?',context,re.I) and re.search(r'explanation|corrective action|duration|violation type|date.*violation',' '.join(headers),re.I):return False
        microbial=bool(re.search(r'coliform|\bcoli\b|bacteri|microbiolog|microbial|microorganism|\bHPC\b|heterotrophic|plate count|cryptosporidium|giardia|enterococc',context+' '+ ' '.join(tidy(r[ni]) for r in data),re.I))
        if not microbial:roles=['count' if r=='micro_count' else r for r in roles]
        # Date annotations in the name header do not make that a date column.
        for ci in range(nc):
            vals=[strip_notes(r[ci]).lower() for r in data if tidy(r[ci])]
            if roles[ci]=='unknown' and vals and all(v in ['yes','no','y','n','✓','✔','✗','✘'] for v in vals): roles[ci]='violation'
            if roles[ci]=='unknown' and vals and all(is_null(v) or UNIT_RE.fullmatch(v) for v in vals): roles[ci]='unit'
            if roles[ci]=='unknown' and re.search(r'plant|well|source|raw|finished|treated|system|area|location|entry point',headers[ci],re.I) and vals and sum(measured(v) for v in vals)>=.6*len(vals):roles[ci]='level'
            if roles[ci]=='description' and re.match(r'^sources?\b',tidy(headers[ci]),re.I) and not re.search(r'contamin|likely|typical|origin|health',headers[ci],re.I) and vals:
                strong=[v for v in vals if not is_null(v)]
                if strong and sum(measured(v) for v in strong)>=.6*len(strong):roles[ci]='level'
        if not ('low' in roles and 'high' in roles): roles=['level' if r in ['low','high'] else r for r in roles]
        if not any(r in ['level','range_level','micro_count'] for r in roles):
            for ci in range(nc):
                vals=[r[ci] for r in data if tidy(r[ci])]
                if roles[ci]=='unknown' and vals and sum(measured(v) for v in vals)>=.6*len(vals): roles[ci]='level'
        if microbial and not any(r in ['level','range_level','micro_count'] for r in roles):
            # Positive-sample counts are levels when they are the only measurement.
            for ci,h in enumerate(headers):
                if re.search(r'positive|detections|samples? with.*coliform',h,re.I) and roles[ci]=='count': roles[ci]='micro_count'
        if not any(r in ['level','range','range_level','micro_count'] for r in roles): return False
        self.table_debug.append({'page':grid.page.page_number,'bbox':grid.bbox,'headers':headers,'roles':roles,'rows':data})
        log('TABLE',grid.page.page_number,headers,roles)
        # Associate repeated column groups with their printed source/plant headings.
        groups={}
        for box,cols,label in getattr(grid,'groups',[]):
            if grid.row_boxes and box[3]>grid.row_boxes[first][1]+1: continue
            label=strip_notes(label)
            if label and len(label)<90 and not re.search(r'contaminant|range|detected|highest|average|result|mcl|mrdl|violation|percentile',label,re.I):
                for ci in cols: groups[ci]=label
        levels=[i for i,r in enumerate(roles) if r=='level']
        split_groups=len({groups.get(i) for i in levels if groups.get(i)})>1
        if len(levels)>1 and not split_groups:
            places=[i for i in levels if re.search(r'plant|well|entry point|\bw[st]p\b|\bwtp\b|source|raw|finished|treated|system|area|location|water district|north|south|east|west',headers[i],re.I) and not re.search(r'highest|average|maximum|90',headers[i],re.I)]
            if len(places)==len(levels):
                split_groups=True;groups.update({i:headers[i] for i in places})
            else:
                guesses={}
                for i in levels:
                    if re.search(r'plant|well|source|raw|finished|treated|system|area|location|entry point',headers[i],re.I):
                        label=re.sub(r'\b(?:average|highest|lowest|maximum|minimum|level|detected|result|results|concentration|found)\b','',headers[i],flags=re.I)
                        guesses[i]=tidy(remove_unit(label)).strip(' -:()')
                if len(guesses)==len(levels) and len({norm(v) for v in guesses.values()})>1:split_groups=True;groups.update(guesses)
            if split_groups:
                for r in set(roles)-{'name','level','description','count','unknown','subname'}:
                    indices=[i for i,v in enumerate(roles) if v==r]
                    if len(indices)==len(levels):
                        for ci,li in zip(indices,levels):groups[ci]=groups[li]
        merged=[]
        for row in data:
            if not any(tidy(c) for c in row): continue
            name=tidy(row[ni])
            if not name and merged:
                lv=next((i for i,r in enumerate(roles) if r=='level'),None)
                if lv is not None and measured(row[lv]) and tidy(merged[-1][lv]) and any(tidy(row[i]) and roles[i] in ['mcl','date','violation'] for i in range(nc)):
                    row=list(row);row[ni]=merged[-1][ni]
                    for i,r in enumerate(roles):
                        if r in ['mclg','unit'] and not tidy(row[i]):row[i]=merged[-1][i]
                    merged.append(row);continue
                for ci,c in enumerate(row):
                    if tidy(c) and tidy(c)!=tidy(merged[-1][ci]): merged[-1][ci]=tidy(merged[-1][ci]+' '+c)
            else: merged.append(list(row))
        location=None;last_created=[]
        for row in merged:
            name=tidy(row[ni])
            continuation=re.match(r'^\(([A-Za-z][A-Za-z0-9-]{1,14})\)\s+(.+)',name)
            if continuation and self.records and known_parameter(continuation.group(2)):
                previous=self.records[-1]
                previous['contaminant']+=' ('+continuation.group(1)+')'
                row=list(row);row[ni]=continuation.group(2);name=row[ni]
            if not row_is_data(row,ni):
                if name and sum(bool(tidy(c)) for c in row)<=2 and len(name)<100 and not re.search(r'contaminants|notes|sources',name,re.I): location=name
                if re.match(r'^range\b',name,re.I) and last_created:
                    for ci,r in enumerate(roles):
                        if r in ['level','range','range_level']:
                            lo,hi=parse_range(row[ci])
                            if lo is not None or hi is not None:
                                loc=groups.get(ci)
                                targets=[v for v in last_created if v.get('location')==loc] if loc else last_created
                                for value in targets:value['range_low']=lo;value['range_high']=hi
                continue
            record_start=len(self.records)
            if split_groups:
                seen_places=set()
                for li in levels:
                    loc=groups.get(li)
                    if not loc or loc in seen_places: continue
                    seen_places.add(loc)
                    local_roles=[r if i not in groups or groups[i]==loc else 'ignore' for i,r in enumerate(roles)]
                    self.records.extend(self.make_records(row,headers,local_roles,ni,system,loc,context))
            else:
                self.records.extend(self.make_records(row,headers,roles,ni,system,location,context))
            last_created=self.records[record_start:]
        self.last_header=(headers,roles,ni,nc,list(grid.xs))
        return True
    def make_records(self,row,headers,roles,ni,system,location,context):
        name,date=clean_name(row[ni])
        if not name or len(name)>150: return []
        def fv(r): return next((row[i] for i,rr in enumerate(roles) if rr==r),'')
        inferred_violation=None
        if 'violation' not in roles:
            for i,r in enumerate(roles):
                if r not in ['mcl','mclg','unit','level','range']:continue
                m=re.fullmatch(r'(.+?)\s+(Yes|No|Y|N)',tidy(row[i]),re.I)
                if m and measured(m.group(1)):
                    row=list(row);row[i]=m.group(1);inferred_violation=violation(m.group(2),'Violation')
        dc=fv('date')
        if tidy(dc) and not is_null(dc) and not re.search(r'next|future',dc,re.I):date=strip_notes(dc)
        unit_text=re.sub(r'\s*\((?:L?RAA|MPA|average|avg\.?|mean|90th percentile)\)\s*','',strip_notes(fv('unit')),flags=re.I)
        unit=scalar(unit_text) if unit_text else None
        if not isinstance(unit,str):unit=None
        if unit and get_unit(unit) and re.search(r'\d|[|_]',remove_unit(unit)):unit=get_unit(unit)
        if unit and not get_unit(unit) and norm(unit) in ['percent removal','removal ratio','ratio','see note','see']: unit=None
        unit=unit or get_unit(row[ni]);mcl=regulatory_value(fv('mcl'));mclg=regulatory_value(fv('mclg'))
        v=next((violation(row[i],headers[i]) for i,r in enumerate(roles) if r=='violation'),inferred_violation)
        level_cols=[i for i,r in enumerate(roles) if r=='level'] or [i for i,r in enumerate(roles) if r=='range_level'] or [i for i,r in enumerate(roles) if r=='micro_count']
        li=level_cols[0] if level_cols else None
        # Parallel lines in a value cell are individual observations.
        if li is not None:
            lines=[s.strip() for s in row[li].split('\n') if s.strip()]
            if len(lines)>1 and all(measured(s) and not s.startswith('(') and not RANGE_RE.search(remove_unit(s)) and not re.fullmatch(UNIT_PATTERN,s,re.I) for s in lines):
                recs=[]
                for n,line in enumerate(lines):
                    rr=list(row)
                    for i,c in enumerate(row):
                        cc=[s.strip() for s in c.split('\n') if s.strip()]
                        if len(cc)==len(lines) and i!=ni: rr[i]=cc[n]
                        elif roles[i]=='mcl':
                            ttparts=[p.strip() for p in re.split(r'(?=\bTT\s*=)',tidy(c)) if p.strip()]
                            if len(ttparts)==len(lines):rr[i]=ttparts[n]
                    rr[li]=line
                    recs.extend(self.make_records(rr,headers,roles,ni,system,location,context))
                return recs
        for ci in range(ni+1,li if li is not None else ni+1):
            if roles[ci] in ['unknown','subname'] and tidy(row[ci]) and not measured(row[ci]) and len(tidy(row[ci]))<90:
                name+=' - '+strip_notes(row[ci])
        location=scalar(fv('location')) or location
        ranges=[i for i,r in enumerate(roles) if r=='range'];lo=hi=None
        if ranges: lo,hi=parse_range(row[ranges[0]])
        if 'low' in roles: lo=scalar(fv('low'));hi=scalar(fv('high'))
        base={'system':system,'contaminant':name,'sample_date':date,'level':None,'range_low':lo,'range_high':hi,'unit':unit,'mcl':mcl,'mclg':mclg,'violation':v}
        if location: base['location']=location
        other={headers[i]:scalar(row[i],keep_unit=True) for i,r in enumerate(roles) if (r in ['other','count'] or (r=='level' and i!=li)) and tidy(row[i])}
        if other: base['other_values']=other
        raw=row[li] if li is not None else '';records=[]
        for place,raw_value in labelled_levels(raw):
            rec=dict(base);value,low,high=level_parts(raw_value)
            if li is not None and roles[li]=='range_level':
                low,high=parse_range(raw_value)
                if low is not None: value=None
                if not RANGE_RE.search(remove_unit(strip_notes(raw_value))): low=high=None;value=level_parts(raw_value)[0]
            rec['level']=value
            if low is not None or high is not None: rec['range_low']=low;rec['range_high']=high
            caption_unit=unit_from_caption(context)
            rec['unit']=unit or get_unit(raw_value) or get_unit(fv('mcl')) or (get_unit(headers[li]) if li is not None else None) or (get_unit(row[ranges[0]]) if ranges else None) or caption_unit
            if rec['unit']: rec['unit']=rec['unit'].replace('μ','µ')
            if place: rec['location']=place
            records.append(rec)
        return records
    def parse_special(self,grid,system,context):
        rows=grid.rows;nc=len(rows[0])
        if re.search(r'\b(?:definitions?|abbreviations?|acronyms?)\b',' '.join(rows[0]),re.I):return False
        # Tables transposed so each contaminant is a column.
        for hi,row in enumerate(rows[:6]):
            if role(row[0]) not in ['date','location'] and norm(row[0]) not in ['month','sampling date','sample','site']: continue
            cs=[i for i in range(1,nc) if (get_unit(row[i]) or known_parameter(row[i])) and role(row[i]) not in ['date','mcl','mclg','unit','violation']]
            for i in range(1,nc):
                h=tidy(row[i]);vals=[rr[i] for rr in rows[hi+1:] if tidy(rr[i])]
                if i not in cs and h and role(h)=='unknown' and not re.search(r'\bid\b|number|samples?|site|location|plant|well|notes?|comments?',h,re.I) and vals and sum(measured(v) for v in vals)>=.6*len(vals):cs.append(i)
            if not cs: continue
            metadata={role(rr[0]):rr for rr in rows[hi+1:] if role(rr[0]) in ['mcl','mclg','unit','violation','other','count']}
            count=0
            for rr in rows[hi+1:]:
                rrole=role(rr[0])
                if rrole in ['mcl','mclg','unit','violation','other','count']:continue
                if not tidy(rr[0]): continue
                for ci in cs:
                    if not measured(rr[ci]): continue
                    name,date=clean_name(row[ci]);v,lo,high=level_parts(rr[ci])
                    rec={'system':system,'contaminant':name,'sample_date':strip_notes(rr[0]) if role(row[0])=='date' or re.search(r'\b(?:19|20)\d\d\b|\d/\d',rr[0]) else date,'level':v,'range_low':lo,'range_high':high,'unit':(scalar(metadata['unit'][ci]) if 'unit' in metadata else None) or get_unit(row[ci]) or get_unit(rr[ci]) or (get_unit(metadata['mcl'][ci]) if 'mcl' in metadata else None) or unit_from_caption(context),'mcl':scalar(metadata['mcl'][ci]) if 'mcl' in metadata else None,'mclg':scalar(metadata['mclg'][ci]) if 'mclg' in metadata else None,'violation':violation(metadata['violation'][ci],metadata['violation'][0]) if 'violation' in metadata else None}
                    if rec['sample_date'] is None: rec['location']=strip_notes(rr[0])
                    self.records.append(rec);count+=1
            if count: return True
        # Plants or areas are rows for a single parameter named by the title.
        title=' '.join(tidy(r[0]) for r in rows[:2] if sum(bool(tidy(c)) for c in r)<=1)
        title=title or next((tidy(s) for s in reversed(context.splitlines()) if known_parameter(s)),'')
        parameter=known_parameter(title)
        for hi,row in enumerate(rows[:5]):
            if (role(row[0])!='location' and norm(row[0]) not in ['source','water source','sample source','sample point','']) or not parameter: continue
            hs=['Contaminant']+row[1:];rs=['name']+[role(h) for h in row[1:]]
            if not any(r=='level' for r in rs): continue
            for rr in rows[hi+1:]:
                if not any(measured(c) for c in rr[1:]):continue
                loc=strip_notes(rr[0]);rr=[parameter]+rr[1:]
                self.records.extend(self.make_records(rr,hs,rs,0,system,loc,context))
            return True
        # A page can continue the preceding table without repeating its heading.
        if self.last_header:
            hs,rs,ni,oldnc,xs=self.last_header
            compatible=nc==oldnc and len(grid.xs)==len(xs) and all(abs(a-b)<8 for a,b in zip(xs,grid.xs))
            if compatible and sum(row_is_data(r,ni) for r in rows)>=1:
                return self.parse(SimpleGrid(grid.page,[hs]+rows,grid.bbox,grid.xs),system,context)
        return False

PARAMETER_RE=re.compile(r'\b(?:TOC\s+Removal\s+Ratio|Total\s+Organic\s+Carbon(?:\s*\(TOC\))?(?:\s+Removal\s+Ratio)?|(?:Total\s+)?Hardness|(?:Free\s+|Total\s+)?Chlorine(?:\s+Residual)?|(?:Total\s+)?Alkalinity|Arsenic|Barium|Beryllium|Cadmium|Chromium|Copper|Cyanide|Fluoride|Lead|Mercury|Nickel|Nitrate(?:\s*\([^)]*\))?|Nitrite|Selenium|Thallium|Sodium|Sulfate|Sulphate|Chloride|Iron|Manganese|Aluminum|Zinc|Turbidity|Gross\s+Alpha(?:\s+Particles)?|(?:Combined\s+)?Radium(?:\s*226\s*(?:and|&)\s*228)?|Uranium|pH|TTHM[s]?|HAA[569]s?|PFOS|PFOA|PFBA|PFBS|PFHxS|PFHxA|Total\s+Trihalomethanes|(?:Total\s+)?Haloacetic\s+Acids|Total\s+Coliform|E\.?\s*coli|TOC|Bromate|Chlorite|Total\s+Dissolved\s+Solids)\b',re.I)
def known_parameter(s):
    m=PARAMETER_RE.search(tidy(s));return m.group(0) if m else None

def word_lines(page):
    words=page.extract_words(x_tolerance=2,y_tolerance=3,keep_blank_chars=False)
    lines=[]
    for w in sorted(words,key=lambda w:(round(w['top']/2)*2,w['x0'])):
        match=None
        for line in lines[-4:]:
            if abs(statistics.median(v['top'] for v in line)-w['top'])<3:
                match=line;break
        if match is None: lines.append([w])
        else: match.append(w)
    return [sorted(line,key=lambda w:w['x0']) for line in sorted(lines,key=lambda l:min(w['top'] for w in l))]

def segments(words):
    out=[];part=[]
    for w in words:
        if part and w['x0']-part[-1]['x1']>7:
            out.append(part);part=[]
        # Short atomic column names must not be fused with a different heading.
        atomic=role(w['text'])
        prev=role(' '.join(x['text'] for x in part))
        if part and atomic in ['name','mcl','mclg','unit','date'] and prev in ['name','mcl','mclg','unit','date'] and atomic!=prev:
            out.append(part);part=[]
        part.append(w)
    if part: out.append(part)
    return [{'text':' '.join(w['text'] for w in p),'x0':p[0]['x0'],'x1':p[-1]['x1'],'top':min(w['top'] for w in p),'bottom':max(w['bottom'] for w in p)} for p in out]

def in_box(x,y,b): return b[0]-2<=x<=b[2]+2 and b[1]-2<=y<=b[3]+2
def overlaps(a,b):
    inter=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    return inter/max(1,min((a[2]-a[0])*(a[3]-a[1]),(b[2]-b[0])*(b[3]-b[1])))>.55

def borderless_grids(page,occupied):
    lines=word_lines(page);result=[];seen=[]
    font=statistics.median(c['size'] for c in page.chars if c['text'].isalpha()) if page.chars else 10
    for ln,line in enumerate(lines):
        names=[w for w in line if re.fullmatch(r'Contaminant|Parameter|Substance|Constituent|Analyte|Compound|Chemical',w['text'],re.I)]
        if not names:continue
        name=names[0];ny=name['top'];nx=(name['x0']+name['x1'])/2
        if any(in_box(nx,ny,b) for b in occupied+seen):continue
        # Locate the first numeric body line, including vertically centered cells.
        first_body=None
        for ll in lines[ln+1:]:
            yy=min(w['top'] for w in ll)
            if yy>ny+100:break
            nums=sum(bool(re.match(r'^[<>≤≥]?(?:\d+(?:\.\d+)?|\.\d+)(?:%|[-–/]\d.*)?$',w['text'])) for w in ll)
            units=sum(bool(get_unit(w['text'])) or w['text'].lower() in ['yes','no','y','n'] for w in ll)
            if ((nums>=2 and units) or (nums>=1 and units>=2) or (units>=2 and any(w['text'].lower() in ['yes','no'] for w in ll)) or any(w['text']=='100%' for w in ll)) and yy>ny+8:
                first_body=yy;break
        head_end=first_body-3 if first_body else ny+45
        head_top=max(0,ny-max(16,font*1.8))
        prev_y=ny
        for ll in reversed(lines[:ln]):
            yy=min(w['top'] for w in ll)
            text=' '.join(w['text'] for w in ll)
            if prev_y-yy>font*1.8 or len(ll)>18 or re.search(r'\b(?:has|have|contains|containing|people|could|during|there)\b',text,re.I):break
            if NAME_HEADER.search(text) and len(text)>45:break
            head_top=min(head_top,yy-1);prev_y=yy
        candidates=[]
        primary={
            'mclg':'mclg','mrdlg':'mclg','phg':'mclg','alg':'mclg','goal':'mclg','mldg':'mclg',
            'mcl':'mcl','smcl':'mcl','mrdl':'mcl','minrdl':'mcl','tt':'mcl','action':'mcl','regulatory':'mcl','allowed':'mcl','required':'mcl','minimum':'mcl',
            'unit':'unit','units':'unit','uom':'unit','date':'date','year':'date','sampled':'date','sample':'date',
            'level':'level','average':'level','avg':'level','mean':'level','highest':'level','lowest':'level','90%':'level','90th':'level','concentration':'level',
            'range':'range','violation':'violation','exceeds':'violation','compliance':'violation',
            'number':'count','#':'count','individual':'count','comparison':'other','sources':'description','source':'description','health':'description',
        }
        nearwords=[name]
        k=line.index(name)+1
        while k<len(line) and line[k]['x0']-nearwords[-1]['x1']<8:
            nearwords.append(line[k]);k+=1
        name_segment={'text':' '.join(w['text'] for w in nearwords),'x0':name['x0'],'x1':nearwords[-1]['x1'],'top':name['top'],'bottom':max(w['bottom'] for w in nearwords)}
        candidates.append(dict(name_segment,r='name'))
        for ll in lines:
            yy=min(w['top'] for w in ll)
            if not head_top<=yy<head_end or len(ll)>24:continue
            if re.search(r'\b(?:has|have|contains|containing|people|could|during|there)\b',' '.join(w['text'] for w in ll),re.I):continue
            if len(ll)>6 and len({primary.get(w['text'].lower().strip('(),.:?')) for w in ll if primary.get(w['text'].lower().strip('(),.:?'))})<2:continue
            ss=segments(ll)
            for w in ll:
                token=w['text'].lower().strip('(),.:?')
                r=primary.get(token)
                if not r:continue
                if r=='description' and token=='health' and not any(w2['text'].lower().startswith('effect') for w2 in ll):continue
                cx=(w['x0']+w['x1'])/2
                if r=='date' and cx<name_segment['x1']+15:continue
                seg=next((s for s in ss if s['x0']<=cx<=s['x1']),w)
                # Expand a primary word to its phrase only if the phrase does
                # not contain a second, different column heading.
                ps=[primary.get(t.lower().strip('(),.:?')) for t in seg['text'].split()]
                if len(set(v for v in ps if v))<=1 and len(seg['text'])<65:
                    w=seg
                candidates.append(dict(w,r=r))
        # Cluster vertically stacked fragments by their horizontal centers.
        clusters=[]
        for s in sorted(candidates,key=lambda s:({'name':0,'mclg':1,'mcl':2,'range':3,'violation':4,'unit':5,'count':6,'level':7,'date':8,'description':9}.get(s['r'],10),s['top'])):
            cx=(s['x0']+s['x1'])/2
            near=next((c for c in clusters if abs(c['cx']-cx)<max(11,font*2.1)),None)
            if near:
                near['items'].append(s)
            else:clusters.append({'cx':cx,'items':[s],'r':s['r']})
        clusters=sorted(clusters,key=lambda c:c['cx'])
        for c in clusters:
            if c['r']=='range' and any(re.fullmatch(r'level|average|avg|mean',s['text'].strip('().: '),re.I) for s in c['items']):c['r']='level'
            if any(re.fullmatch(r'number|#|individual',s['text'].strip('().: '),re.I) for s in c['items']):c['r']='count'
            if any(re.search(r'health\s+effects',s['text'],re.I) for s in c['items']):c['r']='description'
        # Captions and surrounding prose cannot supply extra columns on the left.
        ni=next((i for i,c in enumerate(clusters) if c['r']=='name'),None)
        if ni is None:continue
        clusters=clusters[ni:];ni=0
        if len(clusters)<2 or len(clusters)>18:continue
        if sum(c['r'] in ['mcl','mclg','level','range','date','unit','violation'] for c in clusters)<2:continue
        bounds=[]
        for c in clusters:
            # The first primary phrase defines the column center and extent.
            s=c['items'][0];bounds.append((s['x0'],s['x1']))
        left=min((w['x0'] for ll in lines for w in ll if w['x0']<name_segment['x0']+5),default=name_segment['x0'])-3
        xs=[max(0,left)]+[(a[1]+b[0])/2 for a,b in zip(bounds,bounds[1:])]
        typical=statistics.median(b['cx']-a['cx'] for a,b in zip(clusters,clusters[1:]))
        right=bounds[-1][1]+max(15,typical*.55)
        if clusters[-1]['r']=='description':right=max(right,page.width-20)
        xs.append(min(page.width,right))
        xs=refine_columns(page,xs,first_body or ny+35,min(page.height-20,(first_body or ny+35)+350),[c['cx'] for c in clusters])
        rs=[c['r'] for c in clusters]
        li=next((i for i,r in enumerate(rs) if r in ['level','range']),None)
        if li is None:continue
        anchors=[];tail=None
        for j in range(ln+1,len(lines)):
            ll=lines[j];yt=min(w['top'] for w in ll)
            if yt<ny+6:continue
            if anchors and yt-anchors[-1][0]>300:break
            if any(re.fullmatch(r'Contaminant|Parameter|Substance',w['text'],re.I) for w in ll):tail=yt;break
            text=' '.join(w['text'] for w in ll)
            if anchors and (re.match(r'^\d*\s*During\b|^Notes?\b',text,re.I) or re.search(r'\bstands for\b|\bsites tested\b|\bcollected and analyzed\b|\bvalues (?:were|are) (?:below|above)\b',text,re.I)):
                tail=yt;break
            nearby=[w for l2 in lines[max(ln+1,j-2):j+3] for w in l2 if abs(w['top']-yt)<=font*.8]
            rr=[' '.join(w['text'] for w in sorted(nearby,key=lambda w:(w['top'],w['x0'])) if a<=(w['x0']+w['x1'])/2<b) for a,b in zip(xs,xs[1:])]
            physical=[' '.join(w['text'] for w in ll if a<=(w['x0']+w['x1'])/2<b) for a,b in zip(xs,xs[1:])]
            physical_meta=sum(bool(tidy(c)) and (measured(c) or bool(get_unit(c)) or norm(c) in ['yes','no','y','n']) for i,c in enumerate(physical) if i!=ni and i!=li and rs[i] not in ['range','description'])
            nam=rr[ni]
            meta=sum(bool(tidy(c)) and (measured(c) or bool(get_unit(c)) or norm(c) in ['yes','no','y','n']) for i,c in enumerate(rr) if i!=ni and i!=li and rs[i] not in ['range','description'])
            data=(measured(rr[li]) and ((row_is_data(rr,ni) and len(nam)<90 and (meta>=1 or len(rr)<=3)) or (not nam and meta>=3))) or (meta>=3 and bool(tidy(rr[li])) and len(nam)<70)
            if len(rr)>3 and physical_meta<min(2,len(rr)-3):data=False
            if data:
                candidate=(yt,max(w['bottom'] for w in ll),j,nam,meta)
                if anchors and yt-anchors[-1][0]<font*1.15:
                    if meta>anchors[-1][4]:anchors[-1]=candidate
                else:anchors.append(candidate)
                continue
            if anchors and len(nam)>70 and len(' '.join(rr))>150:tail=yt;break
        if not anchors:continue
        last=anchors[-1];guess_end=last[1]+2;prev_y=last[0]
        for ll in lines[last[2]+1:]:
            yy=min(w['top'] for w in ll)
            if yy-prev_y>font*1.8 or yy>last[0]+100 or (tail and yy>=tail) or len(ll)>24:break
            if re.match(r'^\d*\s*During\b|^\d+[.)]?\s*(?:TTHM|HAA|The|This|Turbidity|Results)\b',' '.join(w['text'] for w in ll),re.I):break
            if any(re.fullmatch(r'Contaminant|Parameter|Substance',w['text'],re.I) for w in ll):break
            guess_end=max(guess_end,max(w['bottom'] for w in ll)+2);prev_y=yy
        xs=refine_columns(page,xs,max(ny+8,anchors[0][0]-font*2),guess_end,[c['cx'] for c in clusters])
        header_bottom=anchors[0][0]-3
        hs=[cell_text(page.chars,(a,head_top,b,header_bottom)) for a,b in zip(xs,xs[1:])]
        # Anchor roles take precedence over title fragments crossing columns.
        canonical={'name':'Contaminant','mcl':'MCL','mclg':'MCLG','level':'Level Detected','range':'Range','date':'Sample Date','unit':'Units','count':'Number of Samples','other':'Comparison Value','violation':'Violation','description':'Likely Source'}
        for i,c in enumerate(clusters):
            actual_role=role(hs[i])
            if actual_role!=c['r'] and not(c['r']=='level' and actual_role in ['low','high']):hs[i]=canonical[c['r']]
        hs[ni]='Contaminant'
        blocks=[];value_blocks=[];last_name_y=last_value_y=-1000
        for ll in lines:
            yt=min(w['top'] for w in ll)
            if yt<ny+10 or yt>anchors[-1][1]+font*2:continue
            nn=[w for w in ll if xs[0]<=(w['x0']+w['x1'])/2<xs[1]]
            if nn and re.search(r'[A-Za-z]',' '.join(w['text'] for w in nn)):
                bot=max(w['bottom'] for w in nn)
                if blocks and yt-last_name_y<font*1.5:blocks[-1][1]=bot
                else:blocks.append([yt,bot])
                last_name_y=yt
            vv=[w for w in ll if xs[li]<=(w['x0']+w['x1'])/2<xs[li+1]]
            if vv:
                bot=max(w['bottom'] for w in vv)
                if value_blocks and yt-last_value_y<font*1.5:value_blocks[-1][1]=bot
                else:value_blocks.append([yt,bot])
                last_value_y=yt
        row_tops=[];last_block=None
        header_end=max((c['bottom'] for c in candidates if c['top']<head_end),default=ny+font)
        for an,anchor in enumerate(anchors):
            block=min(blocks,key=lambda b:min(abs(anchor[0]-b[0]),abs(anchor[0]-b[1]))) if blocks else None
            start=anchor[0]-font*2
            shared=block is not None and sum(block[0]-font*.5<=a[0]<=block[1]+font*.5 for a in anchors)>1
            if block is not None and not shared and block!=last_block and min(abs(anchor[0]-block[0]),abs(anchor[0]-block[1]))<font*4:
                start=min(start,block[0]-1);last_block=block
            vb=next((b for b in value_blocks if b[0]-font*.5<=anchor[0]<=b[1]+font*.5),None)
            if vb is not None and sum(vb[0]-font*.5<=a[0]<=vb[1]+font*.5 for a in anchors)==1:start=min(start,vb[0]-1)
            if an>0 and anchor[0]-anchors[an-1][0]<font*3:start=(anchors[an-1][1]+anchor[0])/2
            if an==0:start=max(start,min(header_end+1,anchor[0]-1))
            if row_tops:start=max(start,row_tops[-1]+font)
            row_tops.append(max(ny+8,start))
        last=anchors[-1];end=last[1]+2;prev_y=last[0]
        for ll in lines[last[2]+1:]:
            yy=min(w['top'] for w in ll)
            if yy-prev_y>font*1.8 or yy>last[0]+100 or (tail and yy>=tail) or len(ll)>24:break
            if re.match(r'^\d*\s*During\b|^\d+[.)]?\s*(?:TTHM|HAA|The|This|Turbidity|Results)\b',' '.join(w['text'] for w in ll),re.I):break
            if any(re.fullmatch(r'Contaminant|Parameter|Substance',w['text'],re.I) for w in ll):break
            end=max(end,max(w['bottom'] for w in ll)+2);prev_y=yy
        row_tops.append(max(last[1]+1,end))
        rows=[hs];boxes=[(xs[0],head_top,xs[-1],header_bottom)]
        for ya,yb in zip(row_tops,row_tops[1:]):
            rows.append([cell_text(page.chars,(a,ya,b,yb)) for a,b in zip(xs,xs[1:])]);boxes.append((xs[0],ya,xs[-1],yb))
        bbox=(xs[0],head_top,xs[-1],row_tops[-1])
        result.append(SimpleGrid(page,rows,bbox,xs,boxes));seen.append(bbox)
    return result

def refine_columns(page,xs,y0,y1,centers):
    # Seek vertical whitespace in the body so centered headings cannot clip
    # a long contaminant name or include the beginning of its neighboring cell.
    counts=[0]*(int(page.width)+2)
    for c in page.chars:
        if not y0<=c['top']<=y1 or not c['text'].strip():continue
        for x in range(max(0,int(c['x0'])),min(len(counts),int(c['x1'])+1)):counts[x]+=1
    limit=max(1,max(counts,default=1)*.035)
    out=[xs[0]]
    for i in range(1,len(xs)-1):
        lo=max(0,int(centers[i-1]+5));hi=min(len(counts)-1,int(centers[i]-5))
        gaps=[];start=None
        for x in range(lo,hi+1):
            if counts[x]<=limit:
                if start is None:start=x
            elif start is not None:
                if x-start>=3:gaps.append((start,x))
                start=None
        if start is not None and hi-start>=3:gaps.append((start,hi))
        if gaps:
            a,b=max(gaps,key=lambda g:g[1]-g[0]-.30*abs((g[0]+g[1])/2-xs[i]))
            out.append((a+b)/2)
        else:out.append(xs[i])
    return out+[xs[-1]]


def parse_page(ext,page,system,text,settings=None):
    p=page.dedupe_chars(tolerance=.6)
    try:tables=p.find_tables(settings)
    except Exception as exc:log('table detection',exc);tables=[]
    occupied=[]
    def consume(grid):
        x0,y0,x1,y1=grid.bbox
        context=cell_text(p.chars,(max(0,x0-5),max(0,y0-45),min(p.width,x1+5),y0),footnotes=False)
        before=len(ext.records)
        try:ext.parse(grid,system,context)
        except Exception as exc:log('table parse',p.page_number,exc)
        if len(ext.records)>before:occupied.append(grid.bbox)
    for table in tables:
        if len(table.columns)>=2:consume(Grid(p,table))
    if NAME_HEADER.search(text) and time.monotonic()-START<100:
        try:alternatives=p.find_tables({'vertical_strategy':'lines_strict','horizontal_strategy':'lines_strict'}) if settings is None else []
        except Exception:alternatives=[]
        for table in alternatives:
            if len(table.columns)>=2 and not any(overlaps(table.bbox,b) for b in occupied):consume(Grid(p,table))
        try:
            for grid in borderless_grids(p,occupied):consume(grid)
        except Exception as exc:log('whitespace tables',p.page_number,exc)
    p.flush_cache()

def raster_rules(path,pdfwidth,pdfheight,clean_path=None):
    from PIL import Image,ImageDraw
    original=Image.open(path).convert('RGB')
    im=original.convert('L').point(lambda v:0 if v<235 else 255)
    draw=ImageDraw.Draw(original)
    iw,ih=im.size;groups=[]
    for vertical,img in [(False,im),(True,im.transpose(Image.Transpose.TRANSPOSE))]:
        w,h=img.size;raw=img.tobytes();runs=[]
        for y in range(h):
            for match in re.finditer(b'\x00{45,}',raw[y*w:(y+1)*w]):
                a,b=match.span()
                near=next((g for g in reversed(runs[-50:]) if y-g[3]<3 and min(b,g[2])-max(a,g[1])>=min(b-a,g[2]-g[1])*.85),None)
                if near:near[1]=min(a,near[1]);near[2]=max(b,near[2]);near[3]=y
                else:runs.append([y,a,b,y])
        for first,a,b,last in runs:
            if last-first>8:continue
            mid=(first+last)/2
            if vertical:
                x0=x1=mid*pdfwidth/iw;top=a*pdfheight/ih;bottom=b*pdfheight/ih
            else:
                x0=a*pdfwidth/iw;x1=b*pdfwidth/iw;top=bottom=mid*pdfheight/ih
            groups.append({'object_type':'line','x0':x0,'x1':x1,'top':top,'bottom':bottom,'width':x1-x0,'height':bottom-top,'erase':(first-1,a,last+1,b) if vertical else (a,first-1,b,last+1)})
    vs=[r for r in groups if r['height']>0];hs=[r for r in groups if r['width']>0]
    log('raster candidate rules',len(vs),len(hs))
    def intersects(v,h):return h['x0']-1<=v['x0']<=h['x1']+1 and v['top']-1<=h['top']<=v['bottom']+1
    vkeep=[v for v in vs if sum(intersects(v,h) for h in hs)>=2]
    hkeep=[h for h in hs if sum(intersects(v,h) for v in vs)>=2]
    vertical_only=len(hs)<2 and len(vs)>=3
    if vertical_only:
        vkeep=[]
        for v in vs:
            peers=[w for w in vs if abs(w['x0']-v['x0'])<1.2]
            if v['height']>=20 or len(peers)>=2:vkeep.append(v)
    vs,hs=vkeep,hkeep
    log('raster grid rules',len(vs),len(hs))
    for edge in vs+hs:
        box=edge.pop('erase',None)
        if clean_path and box:draw.rectangle(box,fill='white')
    if clean_path:original.save(clean_path,dpi=(300,300))
    if vertical_only and len(vs)>=2:
        return {'vertical_strategy':'explicit','horizontal_strategy':'text','explicit_vertical_lines':vs,'min_words_horizontal':1,'snap_tolerance':3,'join_tolerance':3,'intersection_tolerance':4}
    if len(vs)<2 or len(hs)<2:return None
    return {'vertical_strategy':'explicit','horizontal_strategy':'explicit','explicit_vertical_lines':vs,'explicit_horizontal_lines':hs,'snap_tolerance':3,'join_tolerance':3,'intersection_tolerance':4}

def missing_ocr_punctuation(items,image,wordbox):
    """Recover small, unrecognized ink components between numeric glyphs."""
    text=''.join(c[0] for c in items)
    digits=[c for c in items if c[0].isdigit()]
    if len(digits)<2 or sum(ch.isdigit() for ch in text)<len(text)*.35:return items
    from PIL import Image
    heights=[b[3]-b[1] for ch,b in digits if b[3]>b[1]]
    if not heights:return items
    height=statistics.median(heights);baseline=statistics.median(b[3] for ch,b in digits)
    a,t,b,d=wordbox;margin=int(height*.35)+1
    left=max(0,a-margin);top=max(0,t-1);right=min(image.width,b+margin);bottom=min(image.height,d+int(height*.15)+2)
    if right-left<=0 or bottom-top<=0 or (right-left)*(bottom-top)>80000:return items
    patch=image.crop((left,top,right,bottom)).convert('L');w,h=patch.size
    pixels=bytearray(1 if v<225 else 0 for v in patch.tobytes())
    additions=[]
    for start in range(len(pixels)):
        if pixels[start]!=1:continue
        pixels[start]=0;stack=[start];xx=start%w;yy=start//w;xa=xb=xx;ya=yb=yy
        while stack:
            pos=stack.pop();x=pos%w;y=pos//w;xa=min(xa,x);xb=max(xb,x);ya=min(ya,y);yb=max(yb,y)
            for dx,dy in [(-1,-1),(0,-1),(1,-1),(-1,0),(1,0),(-1,1),(0,1),(1,1)]:
                nx=x+dx;ny=y+dy
                if 0<=nx<w and 0<=ny<h:
                    ni=ny*w+nx
                    if pixels[ni]:pixels[ni]=0;stack.append(ni)
        xa+=left;xb+=left+1;ya+=top;yb+=top+1
        ww,hh=xb-xa,yb-ya;cx,cy=(xa+xb)/2,(ya+yb)/2
        punctuation=None
        if ww<=height*.3 and hh<=height*.38 and baseline-height*.27<=cy<=baseline+height*.15:
            punctuation=',' if yb>baseline+height*.1 and hh>height*.2 else '.'
        elif height*.2<=ww<=height*.9 and hh<=height*.22 and baseline-height*.7<=cy<=baseline-height*.25:punctuation='-'
        if not punctuation:continue
        if any(not ch.isdigit() and bb[0]-1<=cx<=bb[2]+1 for ch,bb in items):continue
        before=[(ch,bb) for ch,bb in items if (bb[0]+bb[2])/2<cx-height*.07]
        after=[(ch,bb) for ch,bb in items if (bb[0]+bb[2])/2>cx+height*.07]
        leftchar=max(before,key=lambda q:q[1][0]) if before else None
        rightchar=min(after,key=lambda q:q[1][0]) if after else None
        between=leftchar and rightchar and leftchar[0].isdigit() and rightchar[0].isdigit()
        at_end=punctuation=='-' and leftchar and leftchar[0].isdigit() and rightchar is None and cx<=wordbox[2]+height*.4
        if between or at_end:additions.append((punctuation,(xa,ya,xb,yb)))
    return sorted(items+additions,key=lambda c:(c[1][0],c[1][1]))

def hocr_characters(path,image,px0,py0,sx,sy,pageheight):
    import xml.etree.ElementTree as ET
    root=ET.parse(path).getroot();chars=[]
    for word in root.iter():
        if word.attrib.get('class')!='ocrx_word':continue
        match=re.search(r'\bbbox (\d+) (\d+) (\d+) (\d+)',word.attrib.get('title',''))
        if not match:continue
        wb=tuple(map(int,match.groups()));items=[]
        for node in word.iter():
            if node.attrib.get('class')!='ocrx_cinfo':continue
            mm=re.search(r'x_bboxes (\d+) (\d+) (\d+) (\d+)',node.attrib.get('title',''))
            if mm and node.text:items.append((node.text,tuple(map(int,mm.groups()))))
        if not items:continue
        normal=[bb[3]-bb[1] for ch,bb in items if ch.isalnum() and bb[3]>bb[1]]
        size=statistics.median(normal) if normal else max(1,wb[3]-wb[1])
        items=[(ch,bb) for ch,bb in items if not(re.fullmatch(r'[0-9*†‡]',ch) and bb[3]-bb[1]<size*.65 and bb[3]<wb[3]-size*.18)]
        items=missing_ocr_punctuation(items,image,wb)
        for n,(text,bb) in enumerate(items):
            x0,x1=bb[0],bb[2]
            if n+1<len(items) and 0<=items[n+1][1][0]-x1<=size*.22:x1=items[n+1][1][0]
            top=(py0+wb[1])/sy;bottom=(py0+wb[3])/sy
            for ci,ch in enumerate(text):
                a=(px0+x0+(x1-x0)*ci/len(text))/sx;b=(px0+x0+(x1-x0)*(ci+1)/len(text))/sx
                chars.append({'text':ch,'x0':a,'x1':b,'top':top,'bottom':bottom,'doctop':top,'width':b-a,'height':bottom-top,'size':bottom-top,'fontname':'OCR','upright':True,'adv':b-a,'y0':pageheight-bottom,'y1':pageheight-top})
    return chars

def ocr_columns(page,image_path,settings,engine,prefix,env):
    """Recognize narrow table columns independently of neighboring prose."""
    from PIL import Image
    import csv
    regions=[]
    if settings:
        for table in page.find_tables(settings):
            if len(table.columns)<2:continue
            x0,y0,x1,y1=table.bbox;spans=collections.defaultdict(list)
            for a,b,c,d in table.cells:
                spans[round(a,2)].append((b,d));spans[round(c,2)].append((b,d))
            xs=[x for x,iv in sorted(spans.items()) if coverage(iv)>=(y1-y0)*.55]
            regions.append((xs,y0,y1))
    if not regions:
        for grid in borderless_grids(page,[]):regions.append((grid.xs,grid.bbox[1],grid.bbox[3]))
    with Image.open(image_path) as im:
        sx,sy=im.width/page.width,im.height/page.height
        new=[];replaced=[];serial=0
        for xs,y0,y1 in regions:
            for a,b in zip(xs,xs[1:]):
                if time.monotonic()-START>100:break
                if b-a<8:continue
                px0,py0=int(a*sx),int(y0*sy)
                crop=im.crop((px0,py0,int(b*sx)+1,int(y1*sy)+1))
                target=prefix+'-column';crop.save(target+'.png',dpi=(300,300));serial+=1
                try:
                    cmd=[engine,target+'.png',target,'-l','eng','--psm','6','--user-words',os.path.join(os.path.dirname(__file__),'ocr-words.txt'),'--tessdata-dir',os.path.join(os.path.dirname(__file__),'tessdata'),'-c','tessedit_create_tsv=1','-c','tessedit_create_hocr=1','-c','hocr_char_boxes=1']
                    subprocess.run(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=env,timeout=max(1,min(12,104-(time.monotonic()-START))),check=True)
                    chars=[]
                    try:chars=hocr_characters(target+'.hocr',crop,px0,py0,sx,sy,page.height)
                    except Exception as exc:log('hOCR',exc)
                    hocr_ok=bool(chars)
                    with open(target+'.tsv',newline='',encoding='utf-8') as fh:
                        for word in csv.DictReader(fh,delimiter='\t'):
                            if hocr_ok:break
                            text=word.get('text','').strip()
                            if not text or float(word.get('conf','-1'))<0:continue
                            left=(px0+float(word['left']))/sx;top=(py0+float(word['top']))/sy
                            width=float(word['width'])/sx;height=float(word['height'])/sy
                            for ci,ch in enumerate(text):
                                xx=left+width*ci/len(text);ww=width/len(text)
                                chars.append({'text':ch,'x0':xx,'x1':xx+ww,'top':top,'bottom':top+height,'doctop':top,'width':ww,'height':height,'size':height,'fontname':'OCR','upright':True,'adv':ww,'y0':page.height-top-height,'y1':page.height-top})
                    new.extend(chars);replaced.append((a,y0,b,y1))
                except Exception as exc:log('OCR column',serial,exc)
        if replaced:
            old=[c for c in page.chars if not any(in_box((c['x0']+c['x1'])/2,(c['top']+c['bottom'])/2,box) for box in replaced)]
            page._objects=dict(page.objects);page._objects['char']=old+new
            if hasattr(page.get_textmap,'cache_clear'):page.get_textmap.cache_clear()

def ocr_cells(page,image_path,settings,engine,prefix,env):
    if not settings or settings.get('horizontal_strategy')!='explicit':return
    from PIL import Image,ImageFilter
    with Image.open(image_path) as im:
        sx,sy=im.width/page.width,im.height/page.height;new=[];regions=[]
        for table in page.find_tables(settings):
            if len(table.columns)<2:continue
            spans=collections.defaultdict(list);x0,y0,x1,y1=table.bbox
            for a,b,c,d in table.cells:
                spans[round(a,2)].append((b,d));spans[round(c,2)].append((b,d))
            xs=[x for x,iv in sorted(spans.items()) if coverage(iv)>=(y1-y0)*.55]
            for a,b in zip(xs,xs[1:]):
                header=cell_text(page.chars,(a,y0,b,min(y0+65,y1)))
                if role(header) in ['description','count','other']:continue
                cells=sorted({(a,c[1],b,c[3]) for c in table.cells if c[0]<=a+.5 and c[2]>=b-.5 and c[2]-c[0]<(b-a)*1.25 and c[3]-c[1]>=5},key=lambda c:c[1])
                for box in cells:
                    if time.monotonic()-START>100:break
                    px0,py0=int(box[0]*sx),int(box[1]*sy)
                    crop=im.crop((px0,py0,int(box[2]*sx)+1,int(box[3]*sy)+1)).convert('L')
                    scale=2;crop=crop.resize((crop.width*scale,crop.height*scale),Image.Resampling.LANCZOS).filter(ImageFilter.UnsharpMask(radius=1,percent=150,threshold=2))
                    target=prefix+'-cell';crop.save(target+'.png',dpi=(600,600))
                    try:
                        cmd=[engine,target+'.png',target,'-l','eng','--psm','6','--user-words',os.path.join(os.path.dirname(__file__),'ocr-words.txt'),'--tessdata-dir',os.path.join(os.path.dirname(__file__),'tessdata'),'-c','tessedit_create_hocr=1','-c','hocr_char_boxes=1']
                        subprocess.run(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=env,timeout=max(1,min(8,104-(time.monotonic()-START))),check=True)
                        chars=hocr_characters(target+'.hocr',crop,px0*scale,py0*scale,sx*scale,sy*scale,page.height)
                        new.extend(chars);regions.append(box)
                    except Exception as exc:log('OCR cell',exc)
        if regions:
            old=[c for c in page.chars if not any(in_box((c['x0']+c['x1'])/2,(c['top']+c['bottom'])/2,box) for box in regions)]
            page._objects=dict(page.objects);page._objects['char']=old+new
            if hasattr(page.get_textmap,'cache_clear'):page.get_textmap.cache_clear()

def ocr_page(ext,pdf_path,pi,system):
    import shutil,tempfile
    engine=shutil.which('tesseract') or next((p for p in ['/usr/bin/tesseract','/usr/local/bin/tesseract','/opt/homebrew/bin/tesseract'] if os.access(p,os.X_OK)),None)
    if not engine:return
    remaining=106-(time.monotonic()-START)
    if remaining<10:return
    with tempfile.TemporaryDirectory(prefix='ccr-') as temp:
        prefix=os.path.join(temp,'page');env=dict(os.environ,OMP_THREAD_LIMIT='1')
        try:
            subprocess.run(['pdftoppm','-f',str(pi+1),'-l',str(pi+1),'-r','300','-singlefile','-png',pdf_path,prefix],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=min(15,remaining),check=True)
            from PIL import Image
            with Image.open(prefix+'.png') as im:iw,ih=im.size
            settings=raster_rules(prefix+'.png',iw*72/300,ih*72/300,prefix+'-clean.png')
            command=[engine,prefix+'-clean.png',prefix,'-l','eng','--psm','3','--user-words',os.path.join(os.path.dirname(__file__),'ocr-words.txt'),'--tessdata-dir',os.path.join(os.path.dirname(__file__),'tessdata'),'-c','textonly_pdf=1','-c','tessedit_create_pdf=1']
            subprocess.run(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=env,timeout=max(1,min(25,106-(time.monotonic()-START))),check=True)
            with pdfplumber.open(prefix+'.pdf') as doc:
                p=doc.pages[0]
                p._objects=dict(p.objects);p._objects['char']=[c for c in p.chars if c['text'] not in ['|','_']]
                text=p.extract_text() or ''
                ocr_columns(p,prefix+'-clean.png',settings,engine,prefix,env)
                ocr_cells(p,prefix+'-clean.png',settings,engine,prefix,env)
                text=p.extract_text() or text
                if not NAME_HEADER.search(text) and not any(known_parameter(t) for t in text.splitlines()):return
                parse_page(ext,p,system,text,settings)
        except Exception as exc:log('OCR',pi+1,exc)

def parse_rotated(ext,pdf_path,pi,system):
    import io
    from pypdf import PdfReader,PdfWriter
    reader=PdfReader(pdf_path);rp=reader.pages[pi];original=rp.rotation
    for turn in [90,270]:
        rp.rotation=(original+turn)%360
        writer=PdfWriter();writer.add_page(rp);stream=io.BytesIO();writer.write(stream);stream.seek(0)
        with pdfplumber.open(stream) as doc:
            p=doc.pages[0];text=p.extract_text() or ''
            if not NAME_HEADER.search(text):continue
            if system is None:
                ids=system_ids(text)
                if ids:system=ids[0][1]
            parse_page(ext,p,system,text)
            return True
    return False

def main(pdf_path,out_path):
    ext=Extractor()
    with pdfplumber.open(pdf_path) as pdf:
        try:
            cp=subprocess.run(['pdftotext','-layout',pdf_path,'-'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=15,check=True)
            page_texts=cp.stdout.decode('utf8','replace').split('\f')
        except Exception:
            page_texts=[]
        if len(page_texts)<len(pdf.pages):
            page_texts=[]
            for p in pdf.pages:
                try:page_texts.append(p.extract_text() or '')
                except Exception:page_texts.append('')
        page_ids=[system_ids(t) for t in page_texts]
        counts=collections.Counter(sid for ids in page_ids for _,sid in ids)
        first_page=next((i for i,ids in enumerate(page_ids) if ids),None)
        default_system=choose_system(page_ids[first_page],counts,page_texts[first_page]) if first_page is not None else None
        for pi,p in enumerate(pdf.pages):
            if time.monotonic()-START>107:break
            local_ids=page_ids[pi]
            is_front=report_front(page_texts[pi])
            if local_ids and is_front:default_system=choose_system(local_ids,counts,page_texts[pi])
            elif is_front and len(tidy(page_texts[pi]))<500 and not local_ids:
                # An illustrated cover may start a new report without an ID.
                # Look forward within that part; never borrow a preceding ID.
                default_system=None
                for j in range(pi+1,len(pdf.pages)):
                    if j>pi+1 and report_front(page_texts[j]):break
                    if page_ids[j]:
                        default_system=choose_system(page_ids[j],counts,page_texts[j]);break
            try:
                if len(p.chars)<20 and p.images:
                    ocr_page(ext,pdf_path,pi,default_system)
                elif p.chars and sum(not c.get('upright',True) for c in p.chars)>.65*len(p.chars):
                    if not parse_rotated(ext,pdf_path,pi,default_system):parse_page(ext,p,default_system,page_texts[pi])
                else:parse_page(ext,p,default_system,page_texts[pi])
            except Exception as exc:log('page',pi+1,exc)
            p.flush_cache()
    with open(out_path,'w',encoding='utf-8') as fh:json.dump({'records':ext.records},fh,indent=2,ensure_ascii=False,allow_nan=False)
    if DEBUG:
        with open(out_path+'.tables.json','w',encoding='utf-8') as fh:json.dump(ext.table_debug,fh,indent=2,ensure_ascii=False)
if __name__=='__main__':main(sys.argv[1],sys.argv[2])
