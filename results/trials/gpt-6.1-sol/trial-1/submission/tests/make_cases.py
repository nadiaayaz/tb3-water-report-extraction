from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle, Paragraph
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib import colors
from pathlib import Path
P=Path(__file__).parent
style=ParagraphStyle('s',fontName='Helvetica',fontSize=9,leading=11)
c=canvas.Canvas(str(P/'cases.pdf'),pagesize=(612,792))
def table(title,data,widths,spans=(),grid=True):
 c.setFont('Helvetica',11); c.drawString(35,750,'Public Water System ID: XX1234567')
 c.drawString(35,720,title)
 dd=[[Paragraph(str(v),style) if v else '' for v in row] for row in data]
 t=Table(dd,colWidths=widths)
 st=[('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]
 if grid: st += [('GRID',(0,0),(-1,-1),.5,colors.black)]
 for a,b in spans: st += [('SPAN',a,b)]
 t.setStyle(TableStyle(st)); w,h=t.wrapOn(c,550,600);t.drawOn(c,35,690-h)
 c.showPage()
table('Regulated contaminants',[
 ['Contaminant','MCL','MCLG','Average','Range','Unit','Test Date','Violation'],
 ['1. Barium (2023)','2','2','0.34','ND - 0.4','mg/L','2023','No'],
 ['Nitrate (mg/L as N)','10','10','Average: 0.89<br/>Range: 0.31-1.36','N/A','','','No'],
 ['Copper (90th percentile)','AL=1.3','1.3','90th percentile value = 0.21','0.02 to 0.4','ppm','2022','No'],
 ],[125,50,40,95,62,47,55,50])
table('Source water results',[
 ['Contaminant','MCL','MCLG','Plant A','','Plant B',''],
 ['','','','Average','Range','Average','Range'],
 ['Nitrate (ppm)','10','10','2.0','1-3','4.0','3-5'],
 ['Arsenic (ppb)','10','0','0.6','ND-0.8','0.9','ND-1.1'],
 ],[125,50,40,70,70,70,70],[((0,0),(0,1)),((1,0),(1,1)),((2,0),(2,1)),((3,0),(4,0)),((5,0),(6,0))])
table('Unregulated contaminants',[
 ['Sample Date','Chromium (ppb)','Strontium (µg/L)','Vanadium (µg/L)'],
 ['1/15/2024','0.6','380','1.7'],['7/16/2024','0.5','395','2.3'],
 ],[100,140,140,140])
table('TOC Removal Ratio',[
 ['Plant','Average','Minimum','Maximum'],
 ['East Plant','1.7','1.3','2.0'],['West Plant','1.9','1.6','2.2'],
 ],[170,110,110,110])
table('Hardness',[
 ['Area','mg/L','grains per gallon'],['North','120','7.0'],['South','140','8.2'],
 ],[170,170,170])
table('Detected contaminants',[
 ['Contaminant','MCLG','MCL','Detected<br/>Average','Range','Year','Violation'],
 ['Fluoride (ppm)','4','4','0.7','0.61-0.8','2024','No'],
 ['Total<br/>Trihalomethanes<br/>(ppb)','N/A','80','23.1','16.4-29.7','2024','No'],
 ['HAA5 (ppb)','N/A','60','9.2','ND-18.4','2024','No'],
 ],[135,50,50,90,80,55,55],grid=False)
table('Lead and copper',[
 ['Contaminant','Action Level','MCLG','90th Percentile','Test Year','Violation'],
 ['Lead (ppb)','15','0','1.5','2023','No'],['Range of Results','','','0 - 9.7','',''],
 ['Copper (ppm)','1.3','1.3','0.088','2023','No'],['Range of Results','','','0.03 - 0.194','',''],
 ],[150,80,60,100,60,60])
table('Microbiological results',[
 ['Contaminant','MCL','MCLG','Highest No. of Detections','Violation'],['Total Coliform','1','0','0','No'],['E. coli','TT','0','Absent','No'],
 ],[150,70,70,170,60])
table('Unregulated Monitoring Results (all values in µg/L)',[
 ['Compound','Average','Minimum','Maximum','Health Advisory'],['Lithium','4.8','2.1','8.9','10'],['Manganese','14','ND','40','300'],
 ],[140,90,90,90,110])
c.save()
print(P/'cases.pdf')
