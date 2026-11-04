from reportlab.pdfgen import canvas
from reportlab.platypus import Table,TableStyle,Paragraph
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib import colors
import random,json,subprocess,sys,re
from pathlib import Path
P=Path(__file__).parent/'fuzz';P.mkdir(exist_ok=True)
rng=random.Random(421)
HEADS={
 'name':['Contaminant','Substance (units)','Regulated Contaminants','Constituent','Parameter'],
 'level':['Level Detected','Average','Highest Level Detected','90th Percentile','Your Water','Amount Found'],
 'range':['Range of Results','Range','Range of Detected Levels','Low - High'],
 'mcl':['MCL','MCL or MRDL','Action Level','Highest Level Allowed'],
 'mclg':['MCLG','PHG','MRDLG','EPA\'s Ideal Goal'],
 'date':['Sample Date','Year Sampled','Date of Collection','Test Dates'],
 'unit':['Units','Unit of Measurement'],
 'violation':['Violation Y/N','MCL Exceeded?','Meets Standard?','In Compliance?'],
 'source':['Typical Sources','Likely Sources of Contamination'],
 'count':['Number of Samples','# Sites Above AL','Sample Size'],
}
FIELDS='system contaminant sample_date level range_low range_high unit mcl mclg violation'.split()
minimum=1.0
for case in range(36):
 grid=['full','horizontal','vertical','none'][case%4]
 fs=[7,9,10][case%3]
 names=['Barium','Nitrate','Fluoride']
 units=['ppm','ppm','ppm']
 lev=[.4,1.25,'ND'];lo=['ND',.2,'ND'];hi=[.5,2.1,'ND']
 dates=['2023','2024','2024']
 mcls=[2,10,4];goals=[2,10,4]
 cols=list(HEADS)
 if grid=='full': rng.shuffle(cols)
 else: rest=cols[1:];rng.shuffle(rest);cols=['name']+rest
 headings={k:rng.choice(HEADS[k]) for k in cols}
 data=[[headings[k] for k in cols]]
 expected=[]
 for i,name in enumerate(names):
  row=dict(name=name+' (ppm)',level=str(lev[i]),range=str(lo[i])+' - '+str(hi[i]),mcl=str(mcls[i])+' ppm',mclg=str(goals[i]),date=dates[i],unit='ppm',violation='Yes' if headings['violation'] in ('Meets Standard?','In Compliance?') else 'No',source='Erosion of natural deposits; water additive',count='0')
  data.append([row[k] for k in cols])
  expected.append(dict(zip(FIELDS,['AZ1234567',name,dates[i],lev[i],lo[i],hi[i],'ppm',mcls[i],goals[i],'N'])))
 widths={'name':100,'level':75,'range':80,'mcl':65,'mclg':55,'date':75,'unit':55,'violation':75,'source':120,'count':65}
 ws=[widths[k] for k in cols];scale=735/sum(ws);ws=[x*scale for x in ws]
 sty=ParagraphStyle('s',fontName='Helvetica',fontSize=fs,leading=fs*1.15)
 t=Table([[Paragraph(v,sty) for v in row] for row in data],colWidths=ws)
 st=[('VALIGN',(0,0),(-1,-1),rng.choice(['TOP','MIDDLE'])),('TOPPADDING',(0,0),(-1,-1),rng.choice([3,5,8])),('BOTTOMPADDING',(0,0),(-1,-1),rng.choice([3,5,8]))]
 if grid=='full':st.append(('GRID',(0,0),(-1,-1),.5,colors.black))
 if grid=='horizontal':
  for r in range(len(data)): st.append(('LINEBELOW',(0,r),(-1,r),.5,colors.black))
 if grid=='vertical':
  for cc in range(len(cols)):st.append(('LINEAFTER',(cc,0),(cc,-1),.5,colors.black))
 t.setStyle(TableStyle(st))
 path=P/(str(case)+'.pdf');c=canvas.Canvas(str(path),pagesize=(792,612))
 c.setFont('Helvetica',11);c.drawString(30,580,'2024 Consumer Confidence Report')
 c.drawString(30,560,'PWSID: AZ1234567')
 w,h=t.wrapOn(c,740,450);t.drawOn(c,30,530-h);c.save()
 subprocess.run([sys.executable,'app/extractor/extract.py',str(path),str(path.with_suffix('.json'))],check=True)
 got=json.load(open(path.with_suffix('.json')))['records']
 score=0
 for key in expected:
  candidates=[r for r in got if re.sub(r'\W','',r['contaminant']).lower()==key['contaminant'].lower()]
  if candidates: score+=max(sum(r.get(k)==key[k] for k in FIELDS) for r in candidates)
 frac=score/(max(len(got),len(expected))*len(FIELDS))
 minimum=min(minimum,frac)
 if frac<1: print(case,grid,fs,round(frac,3),headings,got,flush=True)
 assert frac>=.95,(case,frac)
print('36 layouts passed; minimum fact recovery:',minimum,flush=True)
