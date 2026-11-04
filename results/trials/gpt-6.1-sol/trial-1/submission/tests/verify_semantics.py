"""Behavior checks for layouts and rules not covered by the visible reports.

Run from the workspace root with the supplied Python environment.
The PDF generators use reportlab; extraction itself does not need it.
"""
import json
import subprocess
import sys
from pathlib import Path

HERE=Path(__file__).resolve().parent
EXTRACTOR=HERE.parent/'extract.py'
def run(script,pdf):
    subprocess.run([sys.executable,str(HERE/script)],check=True)
    path=HERE/pdf
    out=path.with_suffix('.json')
    subprocess.run([sys.executable,str(EXTRACTOR),str(path),str(out)],check=True)
    return json.loads(out.read_text())['records']

records=run('make_cases.py','cases.pdf')
byname=lambda name:[r for r in records if r['contaminant']==name]
assert len(records)==26
assert [(r['location'],r['level'],r['range_low'],r['range_high']) for r in byname('Nitrate') if 'location' in r]==[('Plant A',2,1,3),('Plant B',4,3,5)]
assert [(r['sample_date'],r['level']) for r in byname('Chromium')]==[('1/15/2024',.6),('7/16/2024',.5)]
assert [(r['location'],r['level'],r['unit']) for r in byname('Hardness')]==[('North',120,'mg/L'),('South',140,'mg/L')]
assert byname('Nitrate')[0]['range_low']==.31
assert byname('Lead')[0]['range_high']==9.7
assert byname('Total Coliform')[0]['level']==0
assert byname('E. coli')[0]['level']=='Absent'
assert byname('Manganese')[0]['mcl'] is None

records=run('edge_cases.py','edges.pdf')
assert len(records)==18
byname=lambda name:[r for r in records if r['contaminant']==name]
assert [r['level'] for r in byname('TOC')]==[3.49,3.61]
assert [r['range_high'] for r in byname('TOC')]==[4.5,4.6]
assert byname('Turbidity - Highest single measurement')[0]['level']==.107
assert byname('Sodium')[0]['level'] is None
assert byname('Sodium')[0]['range_low']=='ND'
assert byname('Sulfate')[0]['range_low'] is None
assert byname('Nitrate')[0]['sample_date']=='2022'
assert byname('Barium')[0]['sample_date'] is None
assert byname('Manganese')[0]['range_high'] is None
assert byname('Copper (ppm)')==[]
assert byname('Lead')[0]['violation']=='Y'
assert len(byname('Nitrate'))==4
assert byname('Benzene')==[]
subprocess.run([sys.executable,str(HERE/'protocol_cases.py')],check=True)
print('Semantic and multi-system checks passed.')
