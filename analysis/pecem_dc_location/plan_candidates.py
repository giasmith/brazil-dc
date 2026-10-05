import json, math, numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage as ndi
exec(open('/tmp/raster_plan.py').read().split('# raster')[0].split("res=json.load")[0])  # imports only
cal=json.load(open('/tmp/plan_cal.json')); e0,n1,cell=cal['e0'],cal['n1'],cal['cell']
Z=np.load('/tmp/plan_rasters.npz'); yel,ind=Z['yellow_cipp'],Z['orange_industry']; H,W=yel.shape
def ll_to_utm(lon,lat):
    a=6378137.0; f=1/298.257222101; k0=0.9996; e2=f*(2-f); ep2=e2/(1-e2)
    phi=math.radians(lat); lam=math.radians(lon-(-39.0))
    N=a/math.sqrt(1-e2*math.sin(phi)**2); T=math.tan(phi)**2; C=ep2*math.cos(phi)**2; A=lam*math.cos(phi)
    M=a*((1-e2/4-3*e2**2/64-5*e2**3/256)*phi-(3*e2/8+3*e2**2/32+45*e2**3/1024)*math.sin(2*phi)+(15*e2**2/256+45*e2**3/1024)*math.sin(4*phi)-(35*e2**3/3072)*math.sin(6*phi))
    x=k0*N*(A+(1-T+C)*A**3/6+(5-18*T+T**2+72*C-58*ep2)*A**5/120)+500000
    y=k0*(M+N*math.tan(phi)*(A**2/2+(5-T+9*C+4*C**2)*A**4/24+(61-58*T+T**2+600*C-330*ep2)*A**6/720))+10000000
    return x,y
def utm_to_ll(E,N,zone=24,south=True):
    a=6378137.0; f=1/298.257222101; k0=0.9996
    e2=f*(2-f); ep2=e2/(1-e2)
    x=E-500000.0; y=N-(10000000.0 if south else 0)
    M=y/k0; mu=M/(a*(1-e2/4-3*e2**2/64-5*e2**3/256))
    e1=(1-math.sqrt(1-e2))/(1+math.sqrt(1-e2))
    phi1=mu+(3*e1/2-27*e1**3/32)*math.sin(2*mu)+(21*e1**2/16-55*e1**4/32)*math.sin(4*mu)+(151*e1**3/96)*math.sin(6*mu)+(1097*e1**4/512)*math.sin(8*mu)
    C1=ep2*math.cos(phi1)**2; T1=math.tan(phi1)**2; N1=a/math.sqrt(1-e2*math.sin(phi1)**2); R1=a*(1-e2)/(1-e2*math.sin(phi1)**2)**1.5; D=x/(N1*k0)
    lat=phi1-(N1*math.tan(phi1)/R1)*(D**2/2-(5+3*T1+10*C1-4*C1**2-9*ep2)*D**4/24+(61+90*T1+298*C1+45*T1**2-252*ep2-3*C1**2)*D**6/720)
    lon0=math.radians(-39+ (0)) ; lon=lon0+(D-(1+2*T1+C1)*D**3/6+(5-2*C1+28*T1-3*C1**2+8*ep2+24*T1**2)*D**5/120)/math.cos(phi1)
    return math.degrees(lon),math.degrees(lat)

g=json.load(open('/mnt/user-data/uploads/Brazil/analysis/pecem_dc_location/pecem_area_layers.geojson'))
def get(name):
    for f in g['features']:
        if (f['properties'].get('nome_uc') or '').upper().startswith(name): return f
apa=get('ÁREA DE PROTEÇÃO AMBIENTAL DO LAGAMAR'); ee=get('ESTAÇÃO ECOLÓGICA DO PÉCEM')
def mask_of(f):
    im=Image.new('1',(W,H),0); d=ImageDraw.Draw(im)
    geo=f['geometry']; polys=[geo['coordinates']] if geo['type']=='Polygon' else geo['coordinates']
    for p in polys:
        for i,ring in enumerate(p):
            pts=[]
            for lon,lat in ring:
                x,y=ll_to_utm(lon,lat); pts.append(((x-e0)/cell,(n1-y)/cell))
            d.polygon(pts,fill=1 if i==0 else 0)
    return np.array(im)
A=mask_of(apa); EE=mask_of(ee)
print('APA ha on grid',A.sum()*cell*cell/1e4,'EE ha',EE.sum()*cell*cell/1e4)
dist=ndi.distance_transform_edt(~A)*cell/1000.0
land=yel&~ind&~A&~EE
print('CIPP yellow land ha (excl. industries/APA/EE):',land.sum()*cell*cell/1e4)
for lo,hi in [(0,1.0),(1.0,1.5),(1.5,2.5),(2.5,5),(5,50)]:
    m=land&(dist>=lo)&(dist<hi); print(f'  {lo}-{hi} km from APA: {m.sum()*cell*cell/1e4:.0f} ha')
ring=land&(dist>=1.5)&(dist<=2.5)
lab,n=ndi.label(ring); print('ring components',n)
rows=[]
for i in range(1,n+1):
    m=lab==i; ha=m.sum()*cell*cell/1e4
    if ha<5: continue
    ys,xs=np.nonzero(m); cx=e0+xs.mean()*cell; cy=n1-ys.mean()*cell
    lon,lat=utm_to_ll(cx,cy); rows.append((ha,lon,lat,cx,cy,m)); 
for ha,lon,lat,cx,cy,_ in sorted(rows,reverse=True)[:6]: print(f'  comp {ha:.0f} ha centroid lon {lon:.4f} lat {lat:.4f} (E{cx:.0f} N{cy:.0f})')
np.savez_compressed('/tmp/cand.npz',ring=ring,land=land,dist=dist.astype('f4'),A=A,EE=EE,yel=yel,ind=ind)
