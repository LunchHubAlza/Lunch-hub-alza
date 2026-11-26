#!/usr/bin/env python3
"""Fetch and parse lunch menus from the four configured restaurant websites."""
import json, re, sys, time
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import requests
from bs4 import BeautifulSoup

TZ = ZoneInfo('Europe/Prague')
OUT = Path('data/menu.json')
HEADERS = {'User-Agent': 'LunchHub/1.0 (daily menu aggregation; contact: repository owner)'}
SOURCES = [
    ('V Domově', 'https://restauracevdomove.cz/jidelni-listek-2/', 'domove'),
    ('Pod Párou', 'https://podparou.eu/denni-menu/', 'podparou'),
    ('Hamburg', 'http://www.restauracehamburg.cz/', 'hamburg'),
    ('U Holiše', 'https://www.restauraceuholise.cz/denni-menu', 'holise'),
]

def fetch(url):
    r=requests.get(url,headers=HEADERS,timeout=25)
    r.raise_for_status()
    r.encoding=r.apparent_encoding
    return BeautifulSoup(r.text,'html.parser'), r.url

def clean(s):
    return re.sub(r'\s+',' ',s).strip()

def price_from(text):
    m=re.search(r'(\d{2,4})\s*(?:,-|Kč|CZK)', text, re.I)
    return (m.group(1)+' Kč') if m else ''

def split_line_price(line):
    line=clean(line)
    # Prefer prices near the end of the line; tolerate dot/comma decimal and ,- notation.
    m=re.search(r'(?<!\d)(\d{2,4})\s*(?:,-|Kč|CZK)?\s*$',line,re.I)
    if m:
        return clean(line[:m.start()]), m.group(1)+' Kč'
    return line, ''

def text_lines(soup):
    for node in soup(['script','style','noscript','svg']): node.decompose()
    return [clean(x) for x in soup.get_text('\n').splitlines() if clean(x)]

def parse_podparou(soup):
    lines=text_lines(soup)
    # The page presents today's date, followed by the menu items as list lines.
    start=next((i for i,l in enumerate(lines) if re.search(r'Denní menu',l,re.I)),None)
    if start is None: raise ValueError('Menu section not found')
    end=min([i for i in range(start+1,len(lines)) if re.search(r'Vítejte|Informace o alergenech',lines[i],re.I)] or [len(lines)])
    block=lines[start:end]
    sections=[{'title':'Denní nabídka','items':[]}]
    for l in block:
        # Avoid headings, date and boilerplate; extract food rows with trailing prices.
        if re.search(r'^(Denní menu|Denní nabídku|Pokud se|Restaurace Pod Párou|Jankovcova|Pondělí|Sobota|Neděle)',l,re.I): continue
        food,price=split_line_price(l)
        if price and food and len(food)>3 and not re.search(r'\b\d{1,2}[-./]\d{1,2}',food):
            sections[0]['items'].append({'name':food,'price':price})
    if not sections[0]['items']: raise ValueError('Could not extract menu items')
    # Separate soup when it appears to be the first short item.
    sections[0]['items'][0]['category']='Soup/first item'
    return sections

def parse_domove(soup):
    lines=text_lines(soup)
    start=next((i for i,l in enumerate(lines) if re.search(r'Denní nabídka',l,re.I)),None)
    if start is None: raise ValueError('Daily offer section not found')
    block=lines[start:start+60]
    items=[]
    # Extract rows from the daily offer area, stop at the permanent menu.
    for l in block[1:]:
        if re.search(r'^Stálá nabídka$',l,re.I): break
        # Date may be merged with the next text; ignore date-only lines.
        if re.search(r'^(Pondělí|Úterý|Středa|Čtvrtek|Pátek|Sobota|Neděle)',l,re.I) and len(l)<40: continue
        food,price=split_line_price(l)
        if price and food and len(food)>3:
            food=re.sub(r'^\d+(?:[.,]\d+)?\s*(?:g|ml|l)\s+','',food)
            items.append({'name':food,'price':price})
    if not items: raise ValueError('Could not extract V Domově items')
    return [{'title':'Denní nabídka','items':items}]

def parse_hamburg(soup):
    lines=text_lines(soup)
    start=next((i for i,l in enumerate(lines) if re.search(r'Dnešní menu',l,re.I)),None)
    if start is None: raise ValueError('Today menu block not found on homepage')
    block=lines[start:start+40]
    items=[]
    for l in block[1:]:
        if re.search(r'^Týdenní nabídka$',l,re.I): break
        food,price=split_line_price(l)
        if price and food and len(food)>4:
            food=re.sub(r'^\d+(?:[.,]\d+)?\s*(?:g|ml|l)\s+','',food)
            items.append({'name':food,'price':price})
    if not items: raise ValueError('Could not extract Hamburg items')
    return [{'title':'Dnešní menu','items':items}]

def parse_holise(soup):
    lines=text_lines(soup)
    # Build items by locating the menu date and parsing price-terminated rows.
    start=next((i for i,l in enumerate(lines) if re.search(r'Denní menu\s*[-–].*\d',l,re.I)),None)
    if start is None: start=next((i for i,l in enumerate(lines) if re.search(r'^POLÉVKA$',l,re.I)),None)
    if start is None: raise ValueError('Menu section not found')
    block=lines[start:start+160]
    sections=[]; current={'title':'Denní menu','items':[]}; sections.append(current)
    for l in block:
        if re.search(r'^(POLÉVKA|ZVÝHODNĚNÉ MENU|HLAVNÍ CHODY|Z NAŠEHO GRILU|DEZERT|NÁPOJ|NÁPOJE)$',l,re.I):
            title=l.title()
            current={'title':title,'items':[]}; sections.append(current); continue
        food,price=split_line_price(l)
        if price and food and len(food)>3:
            food=re.sub(r'^\d+(?:[.,]\d+)?\s*(?:g|ml|l)\s+','',food)
            # Strip likely allergen/image text and avoid adding rows that are just allergens.
            if not re.search(r'^(Image|Obiloviny|Mléko|Vejce|Celer|Hořčice|Oxid|Ryby)',food,re.I):
                current['items'].append({'name':food,'price':price})
    sections=[s for s in sections if s['items']]
    if not sections: raise ValueError('Could not extract U Holiše items')
    return sections

PARSERS={'domove':parse_domove,'podparou':parse_podparou,'hamburg':parse_hamburg,'holise':parse_holise}

def main():
    now=datetime.now(TZ)
    result={'updated_at':now.isoformat(),'updated_label':now.strftime('%d.%m.%Y %H:%M'),'date':now.strftime('%d.%m.%Y'),'restaurants':[]}
    for name,url,key in SOURCES:
        record={'name':name,'url':url,'sections':[],'ok':False,'status':'Menu se nepodařilo načíst','error':''}
        try:
            soup,finalurl=fetch(url)
            sections=PARSERS[key](soup)
            record.update({'sections':sections,'ok':True,'status':'Menu načteno','url':url})
        except Exception as e:
            record['error']=str(e)[:240]
            record['status']='Načtení menu se nezdařilo'
        result['restaurants'].append(record)
        print(name, 'OK' if record['ok'] else 'ERROR', record['error'])
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    # Fail only if all sites failed; allow partial updates.
    if not any(r['ok'] for r in result['restaurants']): sys.exit(1)
if __name__=='__main__': main()
