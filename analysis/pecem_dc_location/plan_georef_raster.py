import json, numpy as np, math
from PIL import Image, ImageDraw
res=json.load(open('/tmp/plan_polys_pt.json'))
# calibration from grid labels (300-dpi pixels)
ex=np.array([484.3,1075.2,1666.1,2257.0,2846.4,3438.9]); E=np.arange(510000,525001,3000)
ny=np.array([3409,2819,2230,1640,1051,463.]); N=np.arange(9594500,9609501,3000)
ae=np.polyfit(ex,E,1); an=np.polyfit(ny,N,1)
print('m/px E',ae[0],'N',an[0], 'resid E',np.abs(np.polyval(ae,ex)-E).max(),'N',np.abs(np.polyval(an,ny)-N).max())
def to_utm(p):
    p=np.array(p); px=p[:,0]*300/72; py=p[:,1]*300/72
    return np.column_stack([np.polyval(ae,px),np.polyval(an,py)])
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
print('check APA west edge E521458 N9599350 ->',utm_to_ll(521458,9599350))
# raster
e0,e1,n0,n1=510000,525500,9590000,9610000; cell=10
W=int((e1-e0)/cell); H=int((n1-n0)/cell)
def raster(polys):
    im=Image.new('1',(W,H),0); d=ImageDraw.Draw(im)
    for p in polys:
        u=to_utm(p); pts=[((x-e0)/cell,(n1-y)/cell) for x,y in u]
        d.polygon(pts,fill=1)
    return np.array(im)
R={k:raster(v) for k,v in res.items()}
for k,v in R.items(): print(k,'ha',v.sum()*cell*cell/1e4)
np.savez_compressed('/tmp/plan_rasters.npz',**R)
json.dump({'ae':ae.tolist(),'an':an.tolist(),'e0':e0,'n1':n1,'cell':cell},open('/tmp/plan_cal.json','w'))
