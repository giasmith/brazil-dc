"""Relative-location screen for the Pecém (Caucaia) data center.
Uses only repo layers (CNUC protected areas, FUNAI indigenous lands, IBGE municipalities).
No site coordinates are published; this produces an INFERRED candidate zone, not a location."""
import json, numpy as np, os
HERE=os.path.dirname(os.path.abspath(__file__))
d=json.load(open(os.path.join(HERE,'pecem_area_layers.geojson')))
def feat(layer,key,val):
    for f in d['features']:
        p=f['properties']
        if p['source_layer']==layer and (p.get(key) or '').upper().startswith(val.upper()): return f
    raise KeyError(val)
def rings(f):
    g=f['geometry']; ps=[g['coordinates']] if g['type']=='Polygon' else g['coordinates']
    return [(np.array(p[0]),[np.array(h) for h in p[1:]]) for p in ps]
LAT0=-3.6; KX=111.32*np.cos(np.radians(-LAT0)); KY=110.57   # km per degree
def inpoly(x,y,ring):
    xs,ys=ring[:,0],ring[:,1]; inside=np.zeros(x.shape,bool)
    for i in range(len(xs)-1):
        x1,y1,x2,y2=xs[i],ys[i],xs[i+1],ys[i+1]
        c=((y1>y)!=(y2>y))&(x<(x2-x1)*(y-y1)/(y2-y1+1e-18)+x1); inside^=c
    return inside
def mask(f,x,y):
    m=np.zeros(x.shape,bool)
    for outer,holes in rings(f):
        mm=inpoly(x,y,outer)
        for h in holes: mm&=~inpoly(x,y,h)
        m|=mm
    return m
def dist_km(f,x,y):
    best=np.full(x.shape,1e9)
    for outer,holes in rings(f):
        for r in [outer]+holes:
            px=(r[:,0]-x.mean())*KX; py=(r[:,1]-y.mean())*KY
            X=(x-x.mean())*KX; Y=(y-y.mean())*KY
            for i in range(len(px)-1):
                ax,ay,bx,by=px[i],py[i],px[i+1],py[i+1]; dx,dy=bx-ax,by-ay; L=dx*dx+dy*dy+1e-18
                t=np.clip(((X-ax)*dx+(Y-ay)*dy)/L,0,1)
                best=np.minimum(best,np.hypot(X-(ax+t*dx),Y-(ay+t*dy)))
    return best
if __name__=='__main__':
    step=0.0009
    lon=np.arange(-38.95,-38.68,step); lat=np.arange(-3.75,-3.45,step)
    x,y=np.meshgrid(lon,lat)
    cell_ha=(step*KX)*(step*KY)*100
    cauc=feat('municipalities','feature_name','Caucaia'); sga=feat('municipalities','feature_name','São Gonçalo')
    apa=feat('protected_lands','nome_uc','ÁREA DE PROTEÇÃO AMBIENTAL DO LAGAMAR'); ee=feat('protected_lands','nome_uc','ESTAÇÃO ECOLÓGICA DO PÉCEM')
    inC=mask(cauc,x,y); inS=mask(sga,x,y); inA=mask(apa,x,y); inE=mask(ee,x,y)
    dA=dist_km(apa,x,y); dA[inA]=0
    print('APA Lagamar do Cauípe bbox',[round(v,4) for v in (min(r[:,0].min() for r,_ in rings(apa)),min(r[:,1].min() for r,_ in rings(apa)),max(r[:,0].max() for r,_ in rings(apa)),max(r[:,1].max() for r,_ in rings(apa)))])
    for lo,hi in [(1.5,2.5),(1.0,3.0)]:
        zone=inC&~inA&~inE&(dA>=lo)&(dA<=hi)
        print(f'\nCaucaia land {lo}-{hi} km from APA (excl. APA, EE): {zone.sum()*cell_ha:.0f} ha')
        zs=inS&~inA&~inE&(dA>=lo)&(dA<=hi); print(f'  same ring in São Gonçalo do Amarante: {zs.sum()*cell_ha:.0f} ha')
        for name,cond in [('north of APA (lat>-3.582)',y>-3.582),('south of APA',y<-3.662),('west of APA (lon<-38.808)',x<-38.808),('east of APA',x>-38.773)]:
            z=zone&cond; 
            if z.sum(): print(f'  {name}: {z.sum()*cell_ha:.0f} ha, lon {x[z].min():.3f}..{x[z].max():.3f}, lat {y[z].min():.3f}..{y[z].max():.3f}')
    # Indigenous lands distance
    for n in ['Tapeba','Taba dos','Pitaguary']:
        f=feat('indigenous_lands','feature_name',n); di=dist_km(f,np.array([-38.80]),np.array([-3.58]))
        print(n,'distance from (-38.80,-3.58) ref point km:',round(float(di[0]),1))
    # Caucaia / SGA boundary: SGA cells within 1 km of Caucaia
    print('EE Pecém ha (grid):',round(inE.sum()*cell_ha),' APA ha (grid):',round(inA.sum()*cell_ha))
