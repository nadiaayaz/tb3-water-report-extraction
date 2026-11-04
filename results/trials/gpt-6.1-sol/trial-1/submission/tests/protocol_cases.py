from reportlab.pdfgen import canvas
from reportlab.platypus import Table,TableStyle
from reportlab.lib import colors
from pathlib import Path
import subprocess,sys,json
p=Path(__file__).parent/'protocol.pdf';c=canvas.Canvas(str(p),pagesize=(612,792))
heads=['Contaminant','MCL','MCLG','Average','Range','Units']
widths=[130,65,65,70,110,70]
def title(sid,y=750,supplier=False):
 c.setFont('Helvetica',11)
 if not supplier:c.drawString(20,y,'2024 Annual Drinking Water Quality Report')
 c.drawString(20,y-20,('Water purchased from supplier. ' if supplier else '')+'PWSID: '+sid)
def tab(rows,y=680,header=True):
 t=Table(([heads] if header else [])+rows,colWidths=widths)
 t.setStyle(TableStyle([('GRID',(0,0),(-1,-1),.5,colors.black),('FONTSIZE',(0,0),(-1,-1),9)]))
 _,h=t.wrapOn(c,600,500);t.drawOn(c,20,y-h)
title('ZZ1111111');tab([['Barium',2,2,.4,'ND - 0.5','ppm']]);c.showPage()
tab([['Nitrate',10,10,1.25,'0.2 - 2.1','ppm'],['Fluoride',4,4,.7,'0.65 - 0.8','ppm']],y=740,header=False);c.showPage()
title('ZZ2222222');tab([['Barium',2,2,.5,'ND - 0.6','ppm']]);c.showPage()
title('ZZ3333333',supplier=True);tab([['Barium',2,2,.6,'ND - 0.7','ppm']]);c.showPage()
title('ZZ4444444');tab([['Barium',2,2,.7,'ND - 0.8','ppm']]);title('ZZ5555555',y=500);tab([['Barium',2,2,.8,'ND - 0.9','ppm']],y=430);c.showPage()
c.save();out=p.with_suffix('.json')
subprocess.run([sys.executable,'app/extractor/extract.py',str(p),str(out)],check=True)
r=json.load(open(out))['records'];print([(x['system'],x['contaminant'],x['level']) for x in r])
assert [x['system'] for x in r]==['ZZ1111111']*3+['ZZ2222222']*2+['ZZ4444444','ZZ5555555']
print('protocol cases passed')
