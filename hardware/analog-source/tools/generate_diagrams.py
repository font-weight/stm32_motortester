#!/usr/bin/env python3
"""Editable original SVGs: conceptual boundaries, not additional schematics."""
from pathlib import Path
from html import escape
OUT=Path(__file__).resolve().parents[1]/'exports'
def svg(title,subtitle,body):
 return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1400" height="800" viewBox="0 0 1400 800"><defs><marker id="a" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto"><path d="M0,0 L12,6 L0,12" fill="#34445d"/></marker></defs><rect width="1400" height="800" fill="#f8fafc"/><g font-family="DejaVu Sans,Arial,sans-serif" fill="#172539"><text x="50" y="62" font-size="30" font-weight="bold">{escape(title)}</text><text x="50" y="103" font-size="18">{escape(subtitle)}</text>{body}</g></svg>'''
def box(x,y,w,h,lines,color='#e1ebf6'):
 return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{color}" stroke="#667b96" stroke-width="2"/>'+''.join(f'<text x="{x+w/2}" y="{y+32+i*26}" text-anchor="middle" font-size="18">{escape(t)}</text>' for i,t in enumerate(lines))
def arr(x1,y1,x2,y2,dash=False):return f'<path d="M{x1},{y1} L{x2},{y2}" fill="none" stroke="#34445d" stroke-width="3" {"stroke-dasharray=\"8,6\"" if dash else ""} marker-end="url(#a)"/>'
body=box(50,160,240,115,['Источник A: общий DC','или B: известный','малый AC-сигнал'])+arr(295,217,355,217)+box(365,160,250,115,['Кандидат A или B','один выход ADC_IN','схема / ERC / расчёт'])+arr(620,217,680,217)+box(690,160,280,115,['Blue Pill / ADC1','физическая карта выводов','и питание: TBD'])+arr(975,217,1040,217)+box(1050,160,290,115,['Native USB → Windows','захват и исходные коды','проверяются отдельно'])
body+=box(50,340,600,120,['Граница питания и опорного потенциала','USB, ST-Link, генератор, осциллограф, ЛБП','Проверить все пути; развязка НЕ заявлена'],'#fff0d6')+box(710,340,630,120,['Будущий датчик зажигания: отдельная ветвь','Ёмкостный: форма отклика; индукционный: запуск','Не подключён к A/B. Защита, масштаб и изоляция TBD'],'#ffe3e1')
body+=box(50,540,1290,135,['Сейчас подтверждены только цифровые артефакты и номинальные расчёты.','Нет измерений платы, входного допуска к зажиганию, калибровки кВ или проверки защиты.','Полная схема прибора с USB/SWD и реальной платой остаётся открытой задачей.'],'#e8f1e9')
(OUT/'structural.svg').write_text(svg('Структура низковольтного кандидата','DRAFT-0.1 · Один канал STM32F103C8T6 · Схема границ, не инструкция соединения',body))
body=box(50,160,270,115,['A: 4 × 1 кОм','один отвод ¼ / ½ / ¾','измеренной общей шины'])+arr(325,217,395,217)+box(405,160,260,115,['1 кОм + 10 нФ','BAS70: кандидат','защита не проверена'])+arr(670,217,750,217)+box(760,160,570,115,['ADC1: 12 МГц, 71,5 такта выборки','50 kS/s: 20 мкс период, 7 мкс преобразование','Первые выборки и внешнее RC требуют проверки'])
body+=box(50,345,240,125,['B: −0,5…+0,5 В','1 мкФ + 10 кОм','100 кОм к VBIAS','2,2 нФ к AGND'])+arr(295,405,355,405)+box(365,345,250,125,['TLV9062 U1B','повторитель','смещение около','половины шины'])+arr(620,405,680,405)+box(690,345,290,125,['1 кОм + 10 нФ','ADC_IN','передача при 25 кГц','лишь −16,59 дБ'])+arr(985,405,1045,405)+box(1055,345,275,125,['Один канал ADC1','DMA / окно / USB','конфигурация сохраняется','вместе с отсчётами'])
body+=box(50,545,600,125,['VBIAS: 10 кОм + 10 кОм, 1 мкФ, U1A','Источник выключен при снятом питании','Лабораторные земли и реальные уровни проверить'],'#fff0d6')+box(710,545,620,125,['Не соединять выходы A и B между собой','Не подавать 24 В или первичное/вторичное HV','R/C здесь не задают итоговую полосу мотортестера'],'#ffe3e1')
(OUT/'functional.svg').write_text(svg('Функциональные пути A и B','DRAFT-0.1 · Альтернативные лабораторные модули · Все численные параметры расчётные',body))
print('Generated editable structural.svg and functional.svg')
