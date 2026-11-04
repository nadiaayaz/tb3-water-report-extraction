"""Check the CLI against visible keys; --no-lines also stress-tests borderless parsing."""
import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE=Path(__file__).resolve().parent
APP=HERE.parents[1]
FIELDS='system contaminant sample_date level range_low range_high unit mcl mclg violation'.split()
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--no-lines',action='store_true')
args=parser.parse_args()
def norm(s):return re.sub(r'\W','',str(s)).lower()
def same(a,b):
 if isinstance(a,(int,float)) and isinstance(b,(int,float)):return abs(a-b)<1e-8
 return a==b
with tempfile.TemporaryDirectory(prefix='.verify-',dir=HERE) as td:
 for pdf in sorted((APP/'visible').glob('*.pdf')):
  source=pdf
  if args.no_lines:
   from pypdf import PdfReader,PdfWriter
   from pypdf.generic import ContentStream,NameObject
   reader=PdfReader(pdf);writer=PdfWriter()
   for page in reader.pages:
    content=ContentStream(page.get_contents(),reader)
    content.operations=[(a,b'n' if b in (b'S',b's',b'f',b'F',b'f*',b'B',b'B*',b'b',b'b*') else b) for a,b in content.operations]
    page[NameObject('/Contents')]=content;writer.add_page(page)
   source=Path(td)/pdf.name;writer.write(source)
  out=Path(td)/(pdf.stem+'.json')
  subprocess.run([sys.executable,str(APP/'extractor'/'extract.py'),str(source),str(out)],check=True)
  key=json.loads(pdf.with_suffix('.json').read_text())['records']
  got=json.loads(out.read_text())['records']
  matches={}
  for i,a in enumerate(key):
   for j,b in enumerate(got):
    if norm(a['contaminant'])==norm(b['contaminant']):matches[i,j]=sum(same(a.get(f),b.get(f)) for f in FIELDS)
  used_key=set();used_got=set();total=0
  for (i,j),v in sorted(matches.items(),key=lambda item:-item[1]):
   if i in used_key or j in used_got:continue
   used_key.add(i);used_got.add(j);total+=v
   diff={f:(key[i].get(f),got[j].get(f)) for f in FIELDS if not same(key[i].get(f),got[j].get(f))}
   if diff:print(pdf.stem,key[i]['contaminant'],diff)
  score=total/(max(len(got),len(key))*len(FIELDS))
  print(pdf.stem,'records',len(key),len(got),'fact recovery',round(score,4))
  assert score>=.95,(str(pdf),score)
