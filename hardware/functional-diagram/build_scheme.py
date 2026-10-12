from pathlib import Path
import hashlib, json, math, html
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont as RLFont
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen

BASE=Path(__file__).parent
OUT=BASE/'output'
OUT.mkdir(parents=True,exist_ok=True)
(BASE/'qa').mkdir(exist_ok=True)
FONT=BASE/'assets/OpenGOSTtypeB-Regular.ttf'
pdfmetrics.registerFont(RLFont('OpenGOST',str(FONT)))
font=TTFont(FONT); glyphs=font.getGlyphSet(); cmap=font.getBestCmap(); upm=font['head'].unitsPerEm
MM=72/25.4; W,H=420,297
pdf=canvas.Canvas(str(OUT/'Мотортестер_функциональная_схема_Э2.pdf'),pagesize=(W*MM,H*MM),pageCompression=1)
pdf.setTitle('Мотортестер — схема электрическая функциональная Э2')
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
rect(20,5,70,14,.35)
text(55,10.25,'МТ.00.00.000 Э2',3.5,'center',66,angle=180)
rect(8,147,12,145,.35)
line([(13,147),(13,292)])
for y in [182,207,232,267]:line([(8,y),(20,y)])
for cy,title,maximum in [(279.5,'Инв. № подл.',23),(249.5,'Подп. и дата',33),(219.5,'Взам. инв. №',23),(194.5,'Инв. № дубл.',23),(164.5,'Подп. и дата',33)]:
    text(11.75,cy,title,2.5,'center',maximum,angle=90)
text(30,32,'Одноканальный мотортестер',5,maxwidth=375)
text(30,44,'Проектное представление функций конечного изделия',3.5,maxwidth=375)
# Functional groups are named, without invented principal-schematic identifiers.
box(30,62,38,25,['Система','зажигания'],cap=3.5,external=True)
box(82,62,40,25,['Ёмкостный','съём'],cap=3.5)
box(136,62,45,25,['Входная','защита*'],cap=3.5)
box(195,62,48,25,['Высокоомное','согласование*'],cap=3.5)
box(257,62,45,25,['Масштаб и','смещение*'],cap=3.5)
box(316,62,37,25,['Аналоговый','фильтр*'],cap=3.5)
box(367,62,37,25,['Драйвер','АЦП*'],cap=3.5)
arrow([(68,74.5),(82,74.5)],external=True)
text(75,56,'Поле',2.5,'center')
for left,right in [(122,136),(181,195),(243,257),(302,316),(353,367)]:arrow([(left,74.5),(right,74.5)])
arrow([(404,74.5),(410,74.5),(410,96),(65,96),(65,118)])
text(233,94,'Обработанный аналоговый сигнал',2.5,'center')

# A group, not a second physical MCU or a pin-level drawing.
rect(30,104,374,79,dash=[7,2,1,2])
text(230,112,'STM32F103C8T6: аппаратные и программные функции',3.5,'center',326)
box(40,118,50,25,['АЦП ADC1','12 бит'],cap=3.5)
box(105,118,45,25,['Перенос','DMA'],cap=3.5)
box(165,118,53,25,['Буферы','в ОЗУ'],cap=3.5)
box(233,118,67,25,['Управление записью**','и выбор окна'],cap=3.5)
box(315,118,79,25,['Пакеты и состояния','native USB FS'],cap=3.5)
for left,right in [(90,105),(150,165),(218,233)]:arrow([(left,130.5),(right,130.5)])
arrow([(300,124),(315,124)])
arrow([(315,138),(300,138)])
text(307.5,117,'Данные',2.5,'center')
text(307.5,152,'Команды',2.5,'center')
box(40,157,50,20,['Таймер TIM3'],cap=3.5)
arrow([(52,157),(52,143)])
text(46,152,'TRGO',2.5,'right')
arrow([(254,143),(254,150),(78,150),(78,157)])
text(178,148,'Пуск / останов выборок',2.5,'center')
box(150,157,67,20,['Счётчик времени','TIM2'],cap=3.5)
arrow([(217,167),(280,167),(280,143)])
text(245,164,'Время',2.5,'center')
text(245,177,'Контроль сроков',2.5,'center')

# The host is external; the USB link carries both commands and captured data.
box(270,198,134,31,['ПК: приложение Windows 10','Управление записью','Просмотр, масштаб, сохранение'],cap=3.5,external=True)
arrow([(354.5,143),(354.5,198)],both=True,external=True)
text(383,175,'USB: данные',2.5,'center')
text(383,181,'и команды',2.5,'center')

box(30,204,53,25,['Низковольтный','источник*'],cap=2.5,external=True)
box(100,204,64,25,['Питание и','опорные цепи*'],cap=3.5)
arrow([(83,216.5),(100,216.5)],external=True)
arrow([(164,216.5),(184,216.5)])
text(188,213,'К аналоговому',2.5,maxwidth=76)
text(188,219,'тракту и МК',2.5,maxwidth=76)
text(188,225,'(связи условно)',2.5,maxwidth=76)

text(30,241,'Примечания',3.5)
notes=[
 '1. * Реализация, питание и развязка требуют расчёта и проверки.',
 '2. ** «Запись сейчас» или один разряд с горением и затуханием.',
 '   Параметры окна и полнота разряда требуют проверки.',
 '3. Внешней синхронизации и управления стендом нет.',
 '4. Целевая схема не означает завершённую интеграцию ПО.',
 '5. Сигнал и команды — сплошные линии; внешние связи',
 '   и контуры групп — штрихпунктир. Стрелки задают направление.',
 '6. Не схема монтажа. Обозначение временное.',
]
for i,t in enumerate(notes):text(30,248+i*5.4,t,2.5,maxwidth=194)

# ГОСТ 2.104-2006, first-sheet Form 1. Coordinates here are local bottom-up.
X,Y=230,292
def gl(x1,y1,x2,y2,thick=.35):line([(X+x1,Y-y1),(X+x2,Y-y2)],thick)
def gt(x,y,s,h=2.5,align='left',maxwidth=None):text(X+x,Y-y,s,h,align,maxwidth)
rect(X,Y-55,185,55,.7)
for y in range(5,55,5):gl(0,y,65,y)
for x in [17,40,55,65]:gl(x,0,x,55)
gl(7,30,7,55)
gl(65,40,185,40);gl(65,15,185,15);gl(135,0,135,40)
gl(135,35,185,35);gl(135,20,185,20)
for x in [150,167]:gl(x,20,x,40)
for x in [140,145]:gl(x,20,x,35)
gl(155,15,155,20)
for y,s in [(26,'Разраб.'),(21,'Пров.'),(16,'Т.контр.'),(6,'Н.контр.'),(1,'Утв.')]:gt(1,y,s,2.5,maxwidth=15)
for x,s,w in [(3.5,'Изм.',7),(12,'Лист',10),(28.5,'№ докум.',23),(47.5,'Подп.',15),(60,'Дата',10)]:gt(x,31,s,2.5,'center',w-.8)
gt(125,45,'МТ.00.00.000 Э2',5,'center',116)
for y,s in [(32,'Мотортестер'),(25,'Схема электрическая'),(18,'функциональная')]:gt(100,y,s,3.5,'center',66)
for x,s,w in [(142.5,'Лит.',15),(158.5,'Масса',17),(176,'Масштаб',18)]:gt(x,36,s,2.5,'center',w-1)
gt(136,16,'Лист',2.5);gt(156,16,'Листов 1',2.5)
text(200,296,'Копировал',2.5,'left')
text(405,296,'Формат А3',2.5,'right')

pdf.showPage();pdf.save()
defs=[]
for name in sorted(used):
    pen=SVGPathPen(glyphs);glyphs[name].draw(pen)
    defs.append(f'<path id="g_{html.escape(name,quote=True)}" d="{pen.getCommands()}"/>')
svgtext='<?xml version="1.0" encoding="UTF-8"?>\n'+f'<svg xmlns="http://www.w3.org/2000/svg" width="420mm" height="297mm" viewBox="0 0 420 297"><title>Мотортестер. Схема электрическая функциональная Э2</title><desc>Проектная схема. Шрифт OpenGOST type B, OFL-1.1. Текст представлен векторными контурами. Напряжения, компоненты и развязка не утверждены.</desc><defs>'+''.join(defs)+'</defs>'+''.join(svg)+'</svg>\n'
(OUT/'Мотортестер_функциональная_схема_Э2.svg').write_text(svgtext,encoding='utf-8')
(BASE/'qa/text_geometry.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2)+'\n')
print('Created scheme PDF and vector SVG; glyphs',len(used),'text spans',len(checks))
for p in OUT.iterdir():print(p.name,p.stat().st_size,hashlib.sha256(p.read_bytes()).hexdigest())
