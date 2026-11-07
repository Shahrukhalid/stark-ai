#!/usr/bin/env python3
# Rebuild apps/web/lib/presence-data.ts from a MakeHuman checkout (CC0 assets):
#   python scripts/bake-presence.py <makehuman>/makehuman/data out.json
"""Bake CC0 MakeHuman base heads into presence point clouds (positions, normals, brightness)."""
import base64, json, math, sys
import numpy as np
D = sys.argv[1]
rng = np.random.default_rng(5)

def load_obj(p):
    global QUADS
    V=[]; F={}; g=None; QUADS=[]
    for line in open(p):
        if line.startswith('v '): V.append([float(x) for x in line.split()[1:4]])
        elif line.startswith('g '): g=line.split()[1]
        elif line.startswith('f '):
            idx=[int(t.split('/')[0])-1 for t in line.split()[1:]]
            if g=='body': QUADS.append(idx)
            for k in range(1,len(idx)-1): F.setdefault(g,[]).append((idx[0],idx[k],idx[k+1]))
    return np.array(V), {k:np.array(v) for k,v in F.items()}

def target(p, n):
    d=np.zeros((n,3))
    for line in open(p):
        if line[0] in '#\n': continue
        a=line.split(); d[int(a[0])]=[float(a[1]),float(a[2]),float(a[3])]
    return d

V,F=load_obj(f'{D}/3dobjs/base.obj')
T=f'{D}/targets/macrodetails'
def human(sex):
    v=V.copy()
    v+=target(f'{T}/universal-{sex}-young-averagemuscle-averageweight.target',len(V))
    for eth in ('african','asian','caucasian'):
        v+=target(f'{T}/{eth}-{sex}-young.target',len(V))/3
    return v

def sample(v, faces, n, region):
    a=v[faces[:,0]]; b=v[faces[:,1]]; c=v[faces[:,2]]
    cen=(a+b+c)/3
    keep=region(cen)
    a,b,c=a[keep],b[keep],c[keep]
    nrm=np.cross(b-a,c-a); area=np.linalg.norm(nrm,axis=1)
    nrm/= (area[:,None]+1e-9)
    p=area/area.sum()
    fi=rng.choice(len(a), size=n, p=p)
    r1=np.sqrt(rng.random(n)); r2=rng.random(n)
    pts=a[fi]*(1-r1)[:,None]+b[fi]*(r1*(1-r2))[:,None]+c[fi]*(r1*r2)[:,None]
    return pts, nrm[fi]

L=np.array([-0.3,0.35,0.9]); L/=np.linalg.norm(L)  # obj y is up
def bake(sex):
    v=human(sex)
    body=F['body']
    # head frame: top of head and chin height from the body
    head=v[np.unique(body)]
    top=head[:,1].max()
    # take head, neck and upper shoulders
    cut=top-3.55
    neck=top-2.55
    hp,hn=sample(v,body,11000,lambda c: (c[:,1]>neck))
    sp_,sn=sample(v,body,2200,lambda c: (c[:,1]>cut)&(c[:,1]<=neck)&(np.abs(c[:,0])<2.1)&(c[:,2]>-1.2))
    pts=np.vstack([hp,sp_]); n=np.vstack([hn,sn]); N=len(pts)
    eyes=[]
    for g in ('helper-l-eye','helper-r-eye'):
        e,en=sample(v,F[g],350,lambda c: np.ones(len(c),bool)); eyes.append((e,en))
    hair_pts,hair_n=sample(v,F['helper-hair'],2400 if sex=='female' else 1400,lambda c: np.ones(len(c),bool))
    if sex=='female':
        # long hair: strands from the sides and back of the cap falling to the shoulders
        cap=hair_pts[(hair_pts[:,1]<top-0.6)&(hair_pts[:,2]<0.6)]
        k=4200; base=cap[rng.integers(0,len(cap),k)]
        t=rng.random(k)**0.8
        side=np.sign(base[:,0]+1e-6)
        sx=base[:,0]+side*(t*0.35+t*t*0.25)+np.sin(t*6+base[:,2]*3)*0.05
        sy=base[:,1]-t*(1.9+rng.random(k)*0.6)
        sz=base[:,2]-t*0.25
        strand=np.stack([sx,sy,sz],1)
        sn_=np.stack([side*0.7,np.zeros(k),np.full(k,0.7)],1)
        hair_pts=np.vstack([hair_pts,strand]); hair_n=np.vstack([hair_n,sn_])
    pts=np.vstack([pts]+[e for e,_ in eyes]+[hair_pts]); n=np.vstack([n]+[en for _,en in eyes]+[hair_n])
    kind=np.array([0]*N+[1]*700+[2]*len(hair_pts))
    lit=0.06+0.94*np.clip(n@L,0,1)**1.9
    rim=(1-np.abs(n[:,2]))**4*0.22
    b=np.clip(lit+rim,0,1)
    b[kind==1]*=0.55
    b[kind==2]=b[kind==2]*0.6+0.1
    # fade the shoulders out towards the cut
    fade=np.clip((pts[:,1]-cut)/0.9,0,1)**1.5; b*=0.1+0.9*fade
    b[(kind==2)&(pts[:,1]<neck)]=0.35+0.4*rng.random(((kind==2)&(pts[:,1]<neck)).sum())
    # importance-sample: keep points where light falls
    keep=rng.random(len(b)) < 0.1+0.9*b
    pts,n,b,kind=pts[keep],n[keep],b[keep],kind[keep]
    # normalise into the design space: head centre at 0, ~ 100 units tall for the head
    cx=0; cz=np.median(pts[kind==0][:,2])
    chin_y=top-2.3
    s=104/ (top-chin_y+0.35)
    X=(pts[:,0]-cx)*s; Y=-(pts[:,1]-(top-1.15))*s; Z=(pts[:,2]-cz)*s
    m=(Y<=118)&(np.abs(X)<=126)&(np.abs(Z)<=126)
    X,Y,Z,n,b,kind=X[m],Y[m],Z[m],n[m],b[m],kind[m]
    # jaw weight: below the mouth (rough), for speaking
    tr=lambda P: ((P[:,0]-cx)*s, -(P[:,1]-(top-1.15))*s, (P[:,2]-cz)*s)
    return dict(x=X,y=Y,z=Z,nx=n[:,0],ny=-n[:,1],nz=n[:,2],b=b,kind=kind), tr

def lattice(v, sex, cell):
    top=v[np.unique(F['body'])][:,1].max()
    Q=np.array(QUADS); c=v[Q].mean(1)
    m=(c[:,1]>top-3.3)&(np.abs(c[:,0])<2.0)&(c[:,2]>-1.0)
    Q=Q[m]
    used=np.unique(Q)
    key={}
    cl=np.floor(v[used]/cell).astype(int)
    ids={}
    lab=np.empty(len(v),int); lab[:]=-1
    for vi,k in zip(used,map(tuple,cl)):
        lab[vi]=ids.setdefault(k,len(ids))
    pos=np.zeros((len(ids),3)); cnt=np.zeros(len(ids))
    np.add.at(pos,lab[used],v[used]); np.add.at(cnt,lab[used],1); pos/=cnt[:,None]
    # vertex normals from quads
    nrm=np.zeros((len(v),3))
    a,b,cc,d=(v[Q[:,i]] for i in range(4))
    fn=np.cross(cc-a,d-b); np.add.at(nrm,Q.ravel(),np.repeat(fn,4,0))
    cn=np.zeros((len(ids),3)); np.add.at(cn,lab[used],nrm[used]); cn/=np.linalg.norm(cn,axis=1)[:,None]+1e-9
    E=set()
    for q in Q:
        for i in range(4):
            x,y=lab[q[i]],lab[q[(i+1)%4]]
            if x!=y: E.add((min(x,y),max(x,y)))
    return pos,cn,np.array(sorted(E)),top

out={}
for sex in ('female','male'):
    vv=human(sex)
    pos,cn,E,top=lattice(vv,sex,0.17 if sex=='male' else 0.16)
    d,tr=bake(sex)
    LX,LY,LZ=tr(pos)
    lb=np.clip(0.06+0.94*np.clip(cn@L,0,1)**1.9+(1-np.abs(cn[:,2]))**4*0.22,0,1)
    lb*=0.1+0.9*np.clip((LY.max()-LY)/40,0,1)**1.5  # shoulders fade out
    for k_,val in (('x',LX),('y',LY),('z',LZ),('nx',cn[:,0]),('ny',-cn[:,1]),('nz',cn[:,2]),('b',lb),('kind',np.full(len(LX),3))):
        d[k_]=np.concatenate([val,d[k_]])
    out[sex+'_edges']=base64.b64encode(E.astype('<u2').tobytes()).decode()
    out[sex+'_nodes']=int(len(LX))
    q=lambda a,s: np.clip(np.round(a*s),-127,127).astype(np.int8)
    arr=np.stack([q(d['x'],1),q(d['y'],1),q(d['z'],1),q(d['nx'],127),q(d['ny'],127),q(d['nz'],127),q(d['b'],127),d['kind'].astype(np.int8)],1)
    out[sex]=base64.b64encode(arr.tobytes()).decode()
json.dump(out,open(sys.argv[2],'w'))
