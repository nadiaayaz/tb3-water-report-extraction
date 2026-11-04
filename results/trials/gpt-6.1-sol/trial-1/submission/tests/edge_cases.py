from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle, Paragraph
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib import colors
from pathlib import Path
P=Path(__file__).parent
style=ParagraphStyle('s',fontName='Helvetica',fontSize=9,leading=11)
c=canvas.Canvas(str(P/'edges.pdf'),pagesize=(612,792))
def table(title,data,widths,spans=(),grid=True):
 c.setFont('Helvetica',11); c.drawString(35,750,'Public Water System ID: XY1234567')
 c.drawString(35,720,title)
 dd=[[Paragraph(str(v),style) if v else '' for v in row] for row in data]
 t=Table(dd,colWidths=widths)
 st=[('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]
 if grid: st += [('GRID',(0,0),(-1,-1),.5,colors.black)]
 for a,b in spans: st += [('SPAN',a,b)]
 t.setStyle(TableStyle(st)); w,h=t.wrapOn(c,550,600);t.drawOn(c,35,690-h)
 c.showPage()
table('Detected results',[
 ['Contaminant','Level Detected','Range','Plant','MCL','MCLG','Meets Standard?'],
 ['TOC (ppm)','3.49 ppm (RAA)<br/>3.61 ppm (MPA)','1.5 - 4.5<br/>1.6 - 4.6','LWTP<br/>LWTP','TT','N/A','Yes'],
 ['Chlorine (ppm)','1.2','0.8 - 1.4','Citywide','MRDL=4','MRDLG=4','Yes'],
 ],[120,115,90,60,55,55,60])
table('Turbidity results',[
 ['Contaminant','','Level Detected','MCL','MCLG','Violation'],
 ['Turbidity (NTU)','Highest single measurement','0.107','TT=1 NTU','0','No'],
 ['','Lowest monthly percentage below limit','100%','TT=95%','0','No'],
 ],[100,135,80,80,55,65],[((0,1),(0,2))])
table('Other parameters (all values in mg/L)',[
 ['Contaminant','Range or Level Detected','Health-Based Value','Year Sampled'],
 ['Sodium','ND - 35','20','2023'],['Sulfate','12.3','500','2024'],
 ['Nitrate (Tested 30 at risk homes in 2022)','1.2','10',''],
 ['Barium Next Testing 2027','0.4','2',''],
 ],[190,130,100,100])
table('Other measurements',[
 ['Chemical','Mean','Range','SMCL','MCLG','Units'],
 ['Iron','0.08','0.0 - 0.11 / Highest LRAA: 0.1','0.3','0.3','mg/L'],
 ['Manganese','0.02','Highest result: 0.038','0.05','0.05','mg/L'],
 ['Color','4','one measure','15','15','CU'],
 ['Aluminum','0','0','N/A','N/A','ppm'],
 ],[150,70,150,50,50,50])
table('Primary results',[
 ['Substance','Date','MCL','PHG','Lowest','Highest','Average','Exceeds AL'],
 ['Copper (ppm)','2023','AL=1.3','0.3','ND','0.95','0.14','No'],
 ['Lead (ppb)','2023','AL=15','0.2','ND','1.3','0.7','Yes'],
 ],[130,60,70,50,50,50,60,60])
table('Locations',[
 ['Constituent','MCL','MCLG','Well 1','Well 2','Well 3','Unit'],
 ['Nitrate','10','10','1.2','2.3','3.4','ppm'],
 ],[140,60,60,65,65,65,60])
table('Not detected list',[
 ['Contaminant','MCL','MCLG','Units','Typical Sources'],
 ['Benzene','5','0','ppb','Industrial discharge'],
 ['Carbon Tetrachloride','5','0','ppb','Industrial discharge'],
 ],[140,60,60,65,190])
c.save()
print(P/'edges.pdf')
