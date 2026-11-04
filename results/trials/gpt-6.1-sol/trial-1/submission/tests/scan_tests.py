from reportlab.pdfgen import canvas
from pathlib import Path
import subprocess,sys,json,re,time
P=Path(__file__).parent/'ocr'
P.mkdir(exist_ok=True)
fields='system contaminant sample_date level range_low range_high unit mcl mclg violation'.split()
for name,pages in [('mn-mankato',[4,5,7]),('ny-medina',[2,3,4]),('pa-reserve',[3,4,5]),('oh-toledo',[3,4])]:
 if len(sys.argv)>1 and name not in sys.argv[1:]:continue
 pdf=P/(name+'-scan.pdf');c=canvas.Canvas(str(pdf),pagesize=(612,792))
 for pg in pages:
  png=P/(name+'-'+str(pg))
  subprocess.run(['pdftoppm','-f',str(pg),'-l',str(pg),'-r','300','-png','-singlefile','app/visible/'+name+'.pdf',str(png)],check=True)
  c.drawImage(str(png)+'.png',0,0,width=612,height=792);c.showPage()
 c.save();start=time.monotonic()
 subprocess.run([sys.executable,'app/extractor/extract.py',str(pdf),str(pdf.with_suffix('.json'))],check=True)
 got=json.load(open(pdf.with_suffix('.json')))['records'];key=json.load(open('app/visible/'+name+'.json'))['records']
 scores={};usedk=set();usedg=set();tot=0
 for k,a in enumerate(key):
  a['system']=None
  for g,b in enumerate(got):
   if re.sub(r'\W','',a['contaminant']).lower()==re.sub(r'\W','',b['contaminant']).lower():scores[k,g]=sum(a.get(f)==b.get(f) for f in fields)
 for (k,g),v in sorted(scores.items(),key=lambda a:-a[1]):
  if k in usedk or g in usedg:continue
  usedk.add(k);usedg.add(g);tot+=v
  diffs={f:(key[k].get(f),got[g].get(f)) for f in fields if key[k].get(f)!=got[g].get(f)}
  if diffs:print(name,key[k]['contaminant'],diffs,flush=True)
 print(name,'scan score',tot/(max(len(key),len(got))*len(fields)),'records',len(key),len(got),'seconds',round(time.monotonic()-start,1),flush=True)
 for k,r in enumerate(key):
  if k not in usedk:print('MISSING',r['contaminant'],flush=True)
 for g,r in enumerate(got):
  if g not in usedg:print('EXTRA',r['contaminant'],flush=True)
