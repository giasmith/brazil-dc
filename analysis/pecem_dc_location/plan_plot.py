import json, numpy as np, math
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
from scipy import ndimage as ndi
src=open('/tmp/candidates2.py').read().split("np.savez_compressed")[0]
exec(src.split("print('APA ha")[0])   # defs + masks
Zc=np.load('/tmp/cand.npz'); ring=Zc['ring']; land=Zc['land']
ext=[e0,e0+W*cell,n1-H*cell,n1]
fig,ax=plt.subplots(figsize=(11,11))
ax.imshow(np.where(Zc['yel'],1,np.nan),extent=ext,cmap='YlOrBr',alpha=.45,vmin=0,vmax=3)
ax.imshow(np.where(Zc['ind'],1,np.nan),extent=ext,cmap='Oranges_r',alpha=.9)
ax.imshow(np.where(Zc['A'],1,np.nan),extent=ext,cmap='Greens_r',alpha=.6)
ax.imshow(np.where(Zc['EE'],1,np.nan),extent=ext,cmap='Greens',alpha=.5)
ax.imshow(np.where(ring,1,np.nan),extent=ext,cmap='Reds_r',alpha=.85)
ax.contour(np.flipud(Zc['dist']),levels=[1.5,2.5],extent=ext,colors=['k'],linewidths=.7,linestyles='--')
ax.set_xlim(514000,525500); ax.set_ylim(9594500,9609500); ax.set_aspect('equal')
ax.set_title('Pecém CIPP land (Plano Diretor 2022) 1.5–2.5 km from APA Lagamar do Cauípe\nred = candidate parcels (inferred); dashed = 1.5 and 2.5 km from APA')
for lab_,x,y in [('Porto',522300,9606800),('APA Lagamar\ndo Cauípe',523500,9598500),('EE Pécem',520600,9604200)]: ax.text(x,y,lab_,fontsize=9)
ax.set_xlabel('UTM 24S easting (m)'); ax.set_ylabel('northing (m)')
fig.savefig('/mnt/user-data/outputs/pecem/pecem_candidate_parcels.png',dpi=110,bbox_inches='tight')
# GeoJSON of ring components >5 ha via contour
lab,n=ndi.label(ring); feats=[]
for i in range(1,n+1):
    m=lab==i; ha=m.sum()*cell*cell/1e4
    if ha<5: continue
    cs=plt.figure().add_subplot().contour(np.flipud(np.pad(m,1).astype(float)),levels=[.5])
    seg=max(cs.allsegs[0],key=len)
    pts=[]
    for x,y in seg:
        E_=e0+(x-1)*cell; N_=n1-H*cell+(y-1)*cell+0   # flipud: y from bottom
        pts.append(list(utm_to_ll(E_,N_)))
    ys,xs=np.nonzero(m); cx=e0+xs.mean()*cell; cy=n1-ys.mean()*cell
    feats.append({'type':'Feature','properties':{'id':f'cand_{i}','area_ha':round(ha,1),'basis':'CIPP-owned land (Plano Diretor 2022) 1.5-2.5 km from APA Lagamar do Cauipe, excl. installed industries','status':'INFERRED - not a surveyed site'},'geometry':{'type':'Polygon','coordinates':[pts]}})
json.dump({'type':'FeatureCollection','features':feats},open('/mnt/user-data/outputs/pecem/pecem_candidate_parcels.geojson','w'))
print([(f['properties']['id'],f['properties']['area_ha'],len(f['geometry']['coordinates'][0])) for f in feats])
