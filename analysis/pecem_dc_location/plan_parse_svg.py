import re, json, numpy as np
s=open('/tmp/pdg.svg').read()
M=0.12
def paths_with_fill(fill):
    out=[]
    for m in re.finditer(r'<path[^>]*fill="%s"[^>]*?d="([^"]+)"[^>]*transform="matrix\(0.12, 0, 0, -0.12, 0, 1191\)"'%re.escape(fill),s):
        d=m.group(1)
        for sub in re.split(r'(?=M )',d):
            nums=re.findall(r'(-?\d+\.?\d*) (-?\d+\.?\d*)',sub)
            if len(nums)>=3:
                a=np.array(nums,float); out.append(np.column_stack([a[:,0]*M, 1191-a[:,1]*M]))
    return out
fills={'yellow_cipp':'rgb(100%, 87.449646%, 49.803162%)','orange_industry':'rgb(100%, 49.803162%, 0%)','grey_third':'rgb(85.096741%, 85.096741%, 85.096741%)','green_future':'rgb(68.626404%, 85.096741%, 66.665649%)'}
res={}
for k,v in fills.items():
    p=paths_with_fill(v); res[k]=[x.tolist() for x in p]; print(k,len(p),sum(len(x) for x in p))
json.dump(res,open('/tmp/plan_polys_pt.json','w'))
