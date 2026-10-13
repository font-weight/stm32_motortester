from pathlib import Path
import hashlib, json, math, html
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont as RLFont
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen

BASE=Path(__file__).parent
OUT=BASE/'output'
FONT=BASE/'assets/OpenGOSTtypeB-Regular.ttf'
pdfmetrics.registerFont(RLFont('OpenGOST',str(FONT)))
font=TTFont(FONT); glyphs=font.getGlyphSet(); cmap=font.getBestCmap(); upm=font['head'].unitsPerEm
MM=72/25.4; W,H=420,297
pdf=canvas.Canvas(str(OUT/'Мотортестер_структурная_схема_Э1.pdf'),pagesize=(W*MM,H*MM),pageCompression=1)
pdf.setTitle('Мотортестер — схема электрическая структурная Э1')
pdf.setAuthor('Проект stm32_motortester')
svg=[]; used=set(); checks=[]

def line(points,thick=.35,dash=None):
    pdf.setLineWidth(thick*MM);pdf.setDash([v*MM for v in dash] if dash else [])
    p=pdf.beginPath();p.moveTo(points[0][0]*MM,(H-points[0][1])*MM)
    for x,y in points[1:]:p.lineTo(x*MM,(H-y)*MM)
    pdf.drawPath(p)
    da=f' stroke-dasharray="{",".join(map(str,dash))}"' if dash else ''
    svg.append(f'<polyline points="{" ".join(f"{x},{y}" for x,y in points)}" fill="none" stroke="black" stroke-width="{thick}"{da}/>')

def rect(x,y,w,h,thick=.35,dash=None):line([(x,y),(x+w,y),(x+w,y+h),(x,y+h),(x,y)],thick,dash)

def text(x,y,s,h=3.5,align='left',maxwidth=None,angle=0):
    size=h*1.7; width=pdfmetrics.stringWidth(s,'OpenGOST',size*MM)/MM
    if maxwidth is not None and width>maxwidth:raise ValueError((s,width,maxwidth))
    start=-width/2 if align=='center' else -width if align=='right' else 0
    x0=x+start
    pdf.saveState();pdf.translate(x*MM,(H-y)*MM);pdf.rotate(angle)
    pdf.setFont('OpenGOST',size*MM);pdf.drawString(start*MM,0,s);pdf.restoreState()
    svg.append(f'<g transform="translate({x:.6f},{y:.6f}) rotate({-angle})">')
    cursor=start
    for ch in s:
        if ord(ch) not in cmap:raise ValueError('Missing glyph '+repr(ch))
        name=cmap[ord(ch)];used.add(name)
        svg.append(f'<use href="#g_{html.escape(name,quote=True)}" transform="translate({cursor:.6f},0) scale({size/upm:.9f},{-size/upm:.9f})"/>')
        cursor+=font['hmtx'][name][0]*size/upm
    svg.append('</g>')
    checks.append({'text':s,'x':x0,'anchor_x':x,'baseline_y':y,'width':width,'cap_mm':h,'angle':angle})

def label(x,y,w,h,rows,cap=3.5,pitch=6):
    first=y+(h-(cap+(len(rows)-1)*pitch))/2+cap
    for i,row in enumerate(rows):text(x+w/2,first+i*pitch,row,cap,'center',w-4)

def box(x,y,w,h,rows,num=None,cap=3.5,external=False):
    rect(x,y,w,h,dash=[7,2,1,2] if external else None);label(x,y,w,h,rows,cap)
    if num:text(x+w,y-3,str(num),3.5,'right')

def arrow(points,both=False,external=False):
    line(points,dash=[7,2,1,2] if external else None)
    pairs=[(points[-2],points[-1])]
    if both:pairs.append((points[1],points[0]))
    for a,b in pairs:
        dx,dy=b[0]-a[0],b[1]-a[1];l=math.hypot(dx,dy);ux,uy=dx/l,dy/l
        bx,by=b[0]-3*ux,b[1]-3*uy
        line([(bx-1.2*uy,by+1.2*ux),b,(bx+1.2*uy,by-1.2*ux)])

# A3 landscape, normative drawing frame, s=0.7 mm; thin lines s/2.
rect(20,5,395,287,.7)
rect(20,5,70,14,.7)
text(55,10.25,'МТ.00.00.000 Э1',3.5,'center',66,angle=180)
rect(8,147,12,145,.7)
line([(13,147),(13,292)],.7)
for y in [182,207,232,267]:line([(8,y),(20,y)],.7)
for cy,title,maximum in [(279.5,'Инв. № подл.',23),(249.5,'Подп. и дата',33),(219.5,'Взам. инв. №',23),(194.5,'Инв. № дубл.',23),(164.5,'Подп. и дата',33)]:
    text(11.75,cy,title,2.5,'center',maximum,angle=90)
text(30,32,'Одноканальный мотортестер',5,maxwidth=375)
text(30,44,'Проектная структура конечного изделия',3.5,maxwidth=375)
box(30,80,44,36,['Система','зажигания','(внешний объект)'],cap=2.5,external=True)
box(86,80,48,36,['Ёмкостный','датчик'],1)
box(146,80,66,36,['Входной','аналоговый узел'],2)
box(226,80,67,36,['Узел регистрации','STM32F103C8T6','АЦП, память, управление'],3,3.5)
box(307,80,43,36,['Интерфейс','native USB FS'],4,3.5)
box(364,80,41,36,['ПК','Windows 10'],5,external=True)
arrow([(74,98),(86,98)],external=True)
for a,b in [(134,146),(212,226)]:arrow([(a,98),(b,98)])
arrow([(293,98),(307,98)],True);arrow([(350,98),(364,98)],True,external=True)
text(80,67,'Поле',2.5,'center')
text(140,67,'Сигнал',2.5,'center')
text(219,67,'В АЦП',2.5,'center')
text(300,61,'Данные',2.5,'center');text(300,67,'и команды',2.5,'center')
text(357,61,'Данные',2.5,'center');text(357,67,'и команды',2.5,'center')

box(57,164,77,25,['Низковольтный','источник питания*'],external=True)
box(183,161,88,29,['Питание и','опорные цепи*'],6)
arrow([(134,176),(183,176)],external=True)
text(159,168,'Вход питания',2.5,'center')
line([(227,161),(227,137),(179,137)])
arrow([(179,137),(179,116)]);arrow([(227,137),(259,137),(259,116)])
pdf.circle(227*MM,(H-137)*MM,.7*MM,fill=1,stroke=0)
svg.append('<circle cx="227" cy="137" r="0.7"/>')
text(216,150,'Питание и опорный уровень',2.5,'right')

text(30,211,'Примечания',3.5)
notes=[
 '1. Показаны функции изделия, а не монтажные соединения.',
 '2. Датчик и аналоговый узел требуют расчёта и проверки.',
 '3. * Источник, топология питания и развязка уточняются.',
 '   Вход питания не задаёт напряжение или распиновку.',
 '   Подача 19 В непосредственно на плату не допускается.',
 '4. Запись управляется командами приложения через USB.',
 '   Внешняя синхронизация и стенд в прибор не входят.',
 '5. Бесконтактный съём не доказывает развязку всего прибора.',
 '6. Подключение к зажиганию - после проверки безопасности.',
 '7. Обозначение документа временное, для согласования.',
]
for i,s in enumerate(notes):text(30,220+i*6,s,2.5,maxwidth=193)

# ГОСТ 2.104-2006, first-sheet Form 1. Coordinates here are local bottom-up.
X,Y=230,292
def gl(x1,y1,x2,y2,thick=.35):line([(X+x1,Y-y1),(X+x2,Y-y2)],thick)
def gt(x,y,s,h=2.5,align='left',maxwidth=None):text(X+x,Y-y,s,h,align,maxwidth)
rect(X,Y-55,185,55,.7)
for y in range(5,55,5):gl(0,y,65,y,.7 if y in (30,35) else .35)
for x in [17,40,55,65]:gl(x,0,x,55,.7)
gl(7,30,7,55)
gl(65,40,185,40,.7);gl(65,15,185,15,.7);gl(135,0,135,40,.7)
gl(135,35,185,35,.7);gl(135,20,185,20,.7)
for x in [150,167]:gl(x,20,x,40,.7)
for x in [140,145]:gl(x,20,x,35)
gl(155,15,155,20,.7)
for y,s in [(26,'Разраб.'),(21,'Пров.'),(16,'Т.контр.'),(6,'Н.контр.'),(1,'Утв.')]:gt(1,y,s,2.5,maxwidth=15)
for x,s,w in [(3.5,'Изм.',7),(12,'Лист',10),(28.5,'№ докум.',23),(47.5,'Подп.',15),(60,'Дата',10)]:gt(x,31.5,s,2.5,'center',w-.8)
gt(125,45,'МТ.00.00.000 Э1',5,'center',116)
for y,s in [(32,'Мотортестер'),(25,'Схема электрическая'),(18,'структурная')]:gt(100,y,s,3.5,'center',66)
for x,s,w in [(142.5,'Лит.',15),(158.5,'Масса',17),(176,'Масштаб',18)]:gt(x,36,s,2.5,'center',w-1)
gt(136,16,'Лист',2.5);gt(156,16,'Листов 1',2.5)
text(200,296,'Копировал',2.5,'left')
text(405,296,'Формат А3',2.5,'right')

pdf.showPage();pdf.save()
defs=[]
for name in sorted(used):
    pen=SVGPathPen(glyphs);glyphs[name].draw(pen)
    defs.append(f'<path id="g_{html.escape(name,quote=True)}" d="{pen.getCommands()}"/>')
svgtext='<?xml version="1.0" encoding="UTF-8"?>\n'+f'<svg xmlns="http://www.w3.org/2000/svg" width="420mm" height="297mm" viewBox="0 0 420 297"><title>Мотортестер. Схема электрическая структурная Э1</title><desc>Проектная схема. Шрифт OpenGOST type B, OFL-1.1. Текст представлен векторными контурами. Напряжения, компоненты и развязка не утверждены.</desc><defs>'+''.join(defs)+'</defs>'+''.join(svg)+'</svg>\n'
(OUT/'Мотортестер_структурная_схема_Э1.svg').write_text(svgtext,encoding='utf-8')
(BASE/'qa/text_geometry.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2)+'\n')
print('Created scheme PDF and vector SVG; glyphs',len(used),'text spans',len(checks))
for p in OUT.iterdir():print(p.name,p.stat().st_size,hashlib.sha256(p.read_bytes()).hexdigest())
