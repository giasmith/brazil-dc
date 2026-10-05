import numpy as np, json, html
from locate_candidates import *
step=0.0009
lon=np.arange(-38.95,-38.68,step); lat=np.arange(-3.75,-3.45,step)
x,y=np.meshgrid(lon,lat)
cauc=feat('municipalities','feature_name','Caucaia'); apa=feat('protected_lands','nome_uc','ÁREA DE PROTEÇÃO AMBIENTAL DO LAGAMAR'); ee=feat('protected_lands','nome_uc','ESTAÇÃO ECOLÓGICA DO PÉCEM')
sga=feat('municipalities','feature_name','São Gonçalo'); tap=feat('indigenous_lands','feature_name','Tapeba'); dune=feat('protected_lands','nome_uc','ÁREA DE PROTEÇÃO AMBIENTAL DAS DUNAS')
inC=mask(cauc,x,y); inA=mask(apa,x,y); inE=mask(ee,x,y); dA=dist_km(apa,x,y); dA[inA]=0
ring=inC&~inA&~inE&(dA>=1.5)&(dA<=2.5)
W,H=900,int(900*(0.30/0.27)*KY/KX)
sx=lambda v:(v+38.95)/0.27*W; sy=lambda v:(-3.45-v)/0.30*H
def path(f):
    s=''
    for o,hs in rings(f):
        for r in [o]+hs: s+='M'+'L'.join(f'{sx(a):.1f},{sy(b):.1f}' for a,b in r)+'Z'
    return s
cells=''.join(f'<rect x="{sx(a):.1f}" y="{sy(b)-H*step/0.30:.1f}" width="{W*step/0.27+.5:.1f}" height="{H*step/0.30+.5:.1f}"/>' for a,b in zip(x[ring],y[ring]))
lay=[(cauc,'#f4efe6','#999',''),(sga,'#e9eef4','#999',''),(dune,'#cfe3c4','#5a8a4a',''),(ee,'#9fcf8f','#2d6a2d',''),(apa,'#6fb86f','#1b5e20',''),(tap,'#f5c28a','#b36b00','')]
body=''.join(f'<path d="{path(f)}" fill="{fc}" stroke="{sc}" stroke-width="1" fill-rule="evenodd"/>' for f,fc,sc,_ in lay)
lab=lambda lo,la,t,c='#222':f'<text x="{sx(lo):.0f}" y="{sy(la):.0f}" font-size="13" fill="{c}" text-anchor="middle">{t}</text>'
port=(-38.80,-3.54)
page=f'''<!doctype html><meta charset=utf-8><title>Pecém data center: relative location</title>
<style>body{{font:15px system-ui;margin:20px;max-width:960px;color:#222}}svg{{border:1px solid #ccc;max-width:100%;height:auto}}</style>
<h2>Pecém data center: inferred candidate zone (not a surveyed location)</h2>
<p>Red cells = Caucaia land 1.5–2.5 km from the APA do Lagamar do Cauípe boundary, excluding the APA and the Estação Ecológica do Pécem. The sources give no coordinates; this narrows the search, it does not locate the site. Port marker is approximate (author's knowledge, unverified).</p>
<svg viewBox="0 0 {W} {H}" width="{W}">{body}<g fill="#d32f2f" fill-opacity=".55">{cells}</g>
<circle cx="{sx(port[0]):.0f}" cy="{sy(port[1]):.0f}" r="5" fill="#0b3d91"/>{lab(port[0],port[1]-0.012,'Porto do Pecém (aprox.)','#0b3d91')}
{lab(-38.791,-3.622,'APA Lagamar do Cauípe','#fff')}{lab(-38.82,-3.575,'EE do Pécem','#1b5e20')}{lab(-38.74,-3.74,'TI Tapeba (Declarada)','#7a4500')}
</svg>
<p>Layers: IBGE municipalities, CNUC conservation units, FUNAI indigenous lands (project geobr extracts). The Anacé territory under Funai study (cited by MPF) is not in this dataset; "Taba dos Anacé" (reserve, in Caucaia east) is a different record.</p>'''
open('pecem_candidate_zone.html','w').write(page)
print('ring ha',round(ring.sum()*(step*KX)*(step*KY)*100))
