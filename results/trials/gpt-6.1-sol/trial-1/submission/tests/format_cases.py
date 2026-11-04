"""Additional FORMAT.md cases, exercised through real PDFs and the CLI."""
from pathlib import Path
import json,subprocess,sys
from reportlab.pdfgen import canvas
from reportlab.platypus import Table,TableStyle,Paragraph
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib import colors
P=Path(__file__).resolve().parent
path=P/'format-cases.pdf';c=canvas.Canvas(str(path),pagesize=(612,792))
style=ParagraphStyle('s',fontName='Helvetica',fontSize=10)
def add(title,rows,widths,spans=(),subtitle=None):
 c.setFont('Helvetica',10);c.drawString(20,755,'PWSID: CA-1234567-01');c.drawString(20,725,title)
 if subtitle:c.drawString(20,710,subtitle)
 t=Table(rows,colWidths=widths)
 t.setStyle(TableStyle([('GRID',(0,0),(-1,-1),.5,colors.black)]+[('SPAN',a,b) for a,b in spans]))
 _,h=t.wrapOn(c,570,550);t.drawOn(c,20,690-h);c.showPage()
add('Results',[
 ['Detected Contaminants','','','','',''],['Contaminant','MCL','MCLG','Average','Range','Units'],
 ['Nitrate',10,10,1.2,'ND - 2','ppm'],['Fluoride',4,4,.7,'0.6-0.8','ppm'],
 ['Substance','Units','90th Percentile','Action Level','MCLG','Violation'],
 ['Lead','ppb',1.5,15,0,'No'],['Copper','ppm',.21,1.3,1.3,'No'],
 ],[130,75,90,90,80,75],[((0,0),(2,0))])
add('Compliance',[
 ['Contaminant','Level Detected','MCL','MCLG','Meets Standard?'],
 ['Nitrate',1.2,10,10,Paragraph('<font name="ZapfDingbats">✓</font>',style)],
 ['Fluoride',.7,4,4,Paragraph('<font name="ZapfDingbats">✗</font>',style)],
 ],[145,100,80,80,150])
add('Unregulated Contaminants (ppb)',[['Contaminant','Average','Range'],['Lithium',2.4,'ND - 3.1']],[200,120,150],subtitle='Downtown Distribution System')
add('Radiological results',[
 ['Contaminant','Range or Level Detected','MCL','PHG','Sample Date'],
 ['Gross Alpha (pCi/L)','-0.5',15,0,'Next Testing 2027'],
 ],[145,165,70,70,110])
add('Not detected',[
 ['Contaminant','MCL','MCLG','Units','Not Detected'],['Benzene',5,0,'ppb',Paragraph('<font name="ZapfDingbats">✓</font>',style)],
 ],[145,100,80,80,150])
add('Turbidity (NTU)',[['Date of Sample','Average','Minimum','Maximum'],['1/1/2024',.08,.02,.1]],[150,130,130,130])
add('Unregulated contaminants',[
 ['Sample Date','Lithium','Molybdenum'],['1/24',3.4,1.6],['Units','µg/L','µg/L'],['MCL','None','None'],
 ],[150,190,190])
add('Historical results (mg/L)',[['Contaminant','2023','2024'],['Nitrate',1.0,2.0]],[200,170,170])
c.save()
out=path.with_suffix('.json');subprocess.run([sys.executable,str(P.parent/'extract.py'),str(path),str(out)],check=True)
r=json.loads(out.read_text())['records']
assert len(r)==12,(len(r),r)
assert all(x['system']=='CA-1234567-01' for x in r)
assert [(x['contaminant'],x['level'],x['mcl']) for x in r[:4]]==[('Nitrate',1.2,10),('Fluoride',.7,4),('Lead',1.5,15),('Copper',.21,1.3)]
assert [x['violation'] for x in r[4:6]]==['N','Y']
assert r[6]['unit']=='ppb' and r[6]['sample_date'] is None
assert r[7]['level']==-.5 and r[7]['range_low'] is None and r[7]['sample_date'] is None
assert not any(x['contaminant']=='Benzene' for x in r)
assert r[8]['contaminant']=='Turbidity' and r[8]['sample_date']=='1/1/2024' and r[8]['range_low']==.02
assert [(x['contaminant'],x['unit'],x['level']) for x in r[9:11]]==[('Lithium','µg/L',3.4),('Molybdenum','µg/L',1.6)]
assert r[11]['level']==1 and r[11]['sample_date'] is None
print('Additional FORMAT cases passed.')
