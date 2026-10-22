/* sprites:begin */
/* ======================= sprite parts (body coords: 18 wide, row 0 = top of head) ======================= */
/* a soft mochi: wider than tall, flat base, one light outline, big low eyes */
const BODY = [
  "......oooooo......",
  "....oohhhbbboo....",
  "...ohhwwhbbbbbo...",
  "..ohhwwhbbbbbbbo..",
  ".ohhhhbbbbbbbbbBo.",
  ".ohhbbbbbbbbbbbBo.",
  "ohhbbbbbbbbbbbbbBo",
  "ohbbbbbbbbbbbbbbBo",
  "obbbbbbbbbbbbbbbBo",
  "obbbbbbbbbbbbbbbBo",
  "obbbbbbbbbbbbbbBBo",
  "oBbbbbbbbbbbbbbBBo",
  ".oBBbbbbbbbbbBBBo.",
  "..ooBBBBBBBBBBoo..",
  "....oooooooooo....",
];
const FEET = {
  stand:".....oo....oo.....", walkA:"....oo.....oo.....", walkB:".....oo.....oo....",
  kickA:"...oo........oo...", kickB:"......oo..oo......",
};
const px = (rows, top) => { const o=[]; rows.forEach((r,i)=>[...r].forEach((ch,c)=>{ if(ch!=='.') o.push([top+i,c,ch]) })); return o };
const LEAF = {
  up:   px(["..........ll......", ".....ll..lLl......", "......lLsl........", "........s........."], -4),
  up2:  px(["...........ll.....", "....ll....lLl.....", ".....lLLsl........", "........s........."], -4),
  perk: px(["....ll.....ll.....", "....lLl..lLl......", "......lLsl........", "........s........."], -4),
  droop:px(["........s.........", ".....lLLsLLl......", "....l...s...l....."], -3),
  wilt: px(["........sl........", "........s.lL......", "........s...l....."], -3),
};
const shift = l => l.concat(l.map(([r,c,k])=>[r,c+8,k]));      // same eye twice, sparkle on the same side
const sym   = l => l.concat(l.map(([r,c,k])=>[r,17-c,k]));     // mirrored shapes: ^ ^, > <, hearts
const EYES = {
  open:  shift([[8,4,'E'],[8,5,'e'],[9,4,'e'],[9,5,'e'],[10,4,'e'],[10,5,'e']]),
  blink: shift([[10,4,'e'],[10,5,'e']]),
  happy: sym([[9,3,'e'],[8,4,'e'],[8,5,'e'],[9,6,'e']]),
  sleep: sym([[9,3,'e'],[10,4,'e'],[10,5,'e'],[9,6,'e']]),
  half:  shift([[9,4,'e'],[9,5,'e'],[10,4,'E'],[10,5,'e']]),
  spent: sym([[8,4,'e'],[9,5,'e'],[10,4,'e']]),
  wide:  shift([[7,4,'E'],[7,5,'e'],[8,4,'e'],[8,5,'e'],[9,4,'e'],[9,5,'e'],[10,4,'e'],[10,5,'e']]),
  teary: shift([[8,4,'E'],[8,5,'e'],[9,4,'e'],[9,5,'e'],[10,4,'T'],[10,5,'T']]),
  heart: sym([[8,3,'C'],[8,5,'C'],[9,3,'C'],[9,4,'C'],[9,5,'C'],[10,4,'C']]),
  star:  sym([[8,4,'y'],[9,3,'y'],[9,4,'W'],[9,5,'y'],[10,4,'y']]),
  ring:  sym([[8,3,'e'],[8,4,'e'],[8,5,'e'],[9,3,'e'],[9,5,'e'],[10,3,'e'],[10,4,'e'],[10,5,'e']]),
};
const MOUTH = {
  smile:[[10,7,'m'],[11,8,'m'],[11,9,'m'],[10,10,'m']],
  open:[[10,7,'m'],[10,8,'m'],[10,9,'m'],[10,10,'m'],[11,7,'m'],[11,8,'t'],[11,9,'t'],[11,10,'m'],[12,8,'m'],[12,9,'m']],
  o:[[10,8,'m'],[10,9,'m'],[11,8,'m'],[11,9,'m']],
  neutral:[[11,8,'m'],[11,9,'m']],
  small:[[11,8,'m'],[11,9,'m']],
  cat:[[10,7,'m'],[11,8,'m'],[10,9,'m'],[11,10,'m'],[10,11,'m']],
  yawn:[[10,8,'m'],[10,9,'m'],[11,7,'m'],[11,8,'t'],[11,9,'t'],[11,10,'m'],[12,7,'m'],[12,8,'t'],[12,9,'t'],[12,10,'m'],[13,8,'m'],[13,9,'m']],
  frown:[[11,7,'m'],[10,8,'m'],[10,9,'m'],[11,10,'m']],
  wavy:[[11,7,'m'],[10,8,'m'],[11,9,'m'],[10,10,'m']],
  pout:[[10,8,'m'],[11,9,'m'],[12,8,'m']],
  none:[],
};
const CHEEKS = {
  c: sym([[11,2,'c'],[11,3,'C'],[11,4,'c']]),
  C: sym([[10,2,'c'],[11,2,'C'],[11,3,'C'],[11,4,'C'],[12,3,'c']]),
};
const ARM = {   // tiny nubs, only when he does something
  none:[],
  side:[[9,18,'o'],[10,18,'b'],[10,19,'o'],[11,18,'o']],
  up:  [[5,19,'o'],[6,18,'o'],[6,19,'b'],[6,20,'o'],[7,18,'b'],[7,19,'o'],[8,18,'o']],
  up2: [[7,19,'o'],[7,20,'o'],[8,18,'b'],[8,19,'b'],[8,20,'o'],[9,18,'o'],[9,19,'o']],
  point:[[8,18,'o'],[8,19,'o'],[8,20,'o'],[9,18,'b'],[9,19,'b'],[9,20,'b'],[9,21,'o'],[10,18,'o'],[10,19,'o'],[10,20,'o']],  // R-115: points at the notebook
};
const mirror = l => l.map(([r,c,k])=>[r,17-c,k]);
const THERMO = [...[10,11,12,13,14,15,16,17].map(c=>[11,c,'W']), [10,16,'g'],[10,17,'g'],[12,16,'g'],[12,17,'g'],
                [11,18,'r'],[11,19,'r'],[10,18,'R'],[10,19,'r'],[12,18,'R'],[12,19,'R']];
const ICE = [[-1,11,'T'],[-1,12,'T'],[-1,13,'T'],[-1,14,'T'],[0,10,'T'],[0,11,'W'],[0,12,'j'],[0,13,'j'],[0,14,'j'],[0,15,'T'],
             [1,10,'T'],[1,11,'j'],[1,12,'j'],[1,13,'j'],[1,14,'j'],[1,15,'T'],[2,11,'T'],[2,12,'T'],[2,13,'T'],[2,14,'T']];
const FLOWERS = [[-6,11,'p'],[-5,10,'p'],[-5,12,'p'],[-4,11,'p'],[-5,11,'y'],[1,4,'p'],[2,3,'p'],[2,5,'p'],[3,4,'p'],[2,4,'y']];
const CAP = px([
  "..........nn......",
  ".......nnnNnn.....",
  "......nNnnnnnnn...",
  ".....nnnnNnnnnnnWW",
  "...WWWWWWWWWWWW.WW",
], -3);
const BUBBLE = [
  [[11,11,'j']],
  [[11,11,'j'],[11,12,'W'],[12,11,'T'],[12,12,'j']],
  [[10,12,'T'],[11,11,'T'],[11,12,'W'],[11,13,'T'],[12,11,'T'],[12,12,'j'],[12,13,'T'],[13,12,'T']],
  [[10,12,'T'],[10,13,'T'],[11,11,'T'],[11,12,'W'],[11,13,'j'],[11,14,'T'],[12,11,'T'],[12,12,'j'],[12,13,'j'],[12,14,'T'],[13,12,'T'],[13,13,'T']],
];
const SPR = {
  spark:[".y.","yWy",".y."], dot:["y"], twink:["W"], note:["..o","..o","ooo","oo."], Z:["ZZZ",".Z.","ZZZ"], z:["ZZ","ZZ"],
  heart:[".C.C.","CCCCC","CCCCC",".CCC.","..C.."], puff:[".PP.","PPPP",".PP."], sweat:[".T.","TjT",".T."],
  moon:[".yy","yy.","yy.",".yy"], book:["oooo","oWWo","ogWo","oWgo","oWWo","oooo"], bang:["e","e",".","e"], fallen:[".lL","lL."], dotE:["o"], star:[".y.","yyy",".y."], drip:["T","T"],
};
/* palettes: soft pastel outline, never grey-brown; tired states only fade the colour */
const COMMON = {e:'#4b3a57',E:'#ffffff',G:'#b7a8e0',m:'#4b3a57',t:'#f29bb0',c:'#f8bccb',C:'#f292ab',W:'#fffaf2',g:'#c9c1d1',
  p:'#f7a8c0',y:'#f5d683',r:'#ec7f94',R:'#c95c74',j:'#e3f0fb',T:'#9cc4e6',n:'#8e9ee0',N:'#6b79bd',Z:'#8e9ee0',P:'#ddd5e6'};
const BODIES = {
  radiante:{o:'#8f74d4',B:'#cbb6f3',b:'#e9ddff',h:'#f6f0ff',w:'#ffffff',l:'#a7dc95',L:'#74b765',s:'#74b765'},
  contento:{o:'#9d8bcc',B:'#c6b6ea',b:'#e0d6f5',h:'#f2edfc',w:'#fdfbff',l:'#b0d6a6',L:'#86b57c',s:'#86b57c'},
  justo:   {o:'#8f92cf',B:'#c0c3ec',b:'#dcdef7',h:'#eff0fc',w:'#fbfbff',l:'#b6d3a0',L:'#89ad77',s:'#89ad77'},
  cansado: {o:'#a493c4',B:'#cfc3e6',b:'#e6dff3',h:'#f3effa',w:'#fbf9fe',l:'#cfd6a0',L:'#a5ad70',s:'#a5ad70'},
  agotado: {o:'#a89cbb',B:'#d3cbe0',b:'#e8e3ef',h:'#f4f1f8',w:'#fcfbfd',l:'#e8d39a',L:'#c6a96a',s:'#b9a477'},
  enfermo: {o:'#86a87c',B:'#c0d8b3',b:'#ddecd3',h:'#eff7ea',w:'#f9fcf7',l:'#e6b794',L:'#c28c69',s:'#b88a6b',e:'#4b5545',m:'#4b5545',G:'#b9d4ae',C:'#f07f97'},
  dormido: {o:'#7f89c8',B:'#b6bfea',b:'#d4dbf7',h:'#eaeefc',w:'#f8f9ff',l:'#9cc2a6',L:'#6f9a7c',s:'#6f9a7c',c:'#d6c4f0',C:'#c9b3ec'},
};
const PALS = Object.fromEntries(Object.entries(BODIES).map(([k,v])=>[k,{...COMMON,...v}]));
const ORDER = ['radiante','contento','justo','cansado','agotado','enfermo','dormido'];
const SHADOW = 'rgba(90,70,120,.14)', SHADOW2 = 'rgba(90,70,120,.06)';
function mix(a,b,k){ const A=parseInt(a.slice(1),16),B=parseInt(b.slice(1),16),ch=(x,s)=>(x>>s)&255;
  return '#'+[16,8,0].map(s=>Math.round(ch(A,s)+(ch(B,s)-ch(A,s))*k).toString(16).padStart(2,'0')).join('') }

/* ======================= compose one frame ======================= */
const W=34, H=28, OX=8, OY=10;
function compose(S, P){
  const g=Array.from({length:H},()=>Array(W).fill(null));
  const put=(y,x,c)=>{ if(c&&y>=0&&y<H&&x>=0&&x<W) g[y][x]=c };
  const dy=S.dy+(S.sit?1:0), ox=OX+S.dx, oy=OY+dy;
  const off=r=>S.stretch===1?(r<=11?-1:0):S.stretch===-1?(r<=4?1:0):0;
  const at=(r,c,k)=>put(oy+r+off(r), ox+c, P[k]);
  // ground: shadow and fallen leaf stay put while he hops
  const lift=Math.max(0,-S.dy), half=Math.max(4,8-Math.round(lift*1.2)), gy=OY+16;
  for(let c=9-half;c<9+half;c++) put(gy, OX+c, (c===9-half||c===8+half)?SHADOW2:SHADOW);
  if(S.fallen) SPR.fallen.forEach((row,i)=>[...row].forEach((ch,j)=>{ if(ch!=='.') put(OY+14+i, OX+21+j, P[ch]) }));
  BODY.forEach((row,r)=>{ if(S.stretch===-1&&r===5) return; [...row].forEach((ch,c)=>{ if(ch!=='.') at(r,c,ch) }) });
  if(S.stretch===1) [...BODY[11]].forEach((ch,c)=>{ if(ch!=='.') put(oy+11,ox+c,P[ch]) });
  if(!S.sit) [...FEET[S.feet]].forEach((ch,c)=>{ if(ch!=='.') put(oy+15,ox+c,P[ch]) });
  ARM[S.armR].forEach(([r,c,k])=>at(r,c,k));
  mirror(ARM[S.armL]).forEach(([r,c,k])=>at(r,c,k));
  if(S.cap) CAP.forEach(([r,c,k])=>at(r,c,k));
  else { LEAF[S.leaf].forEach(([r,c,k])=>at(r,c,k)); if(S.flowers) FLOWERS.forEach(([r,c,k])=>at(r,c,k)) }
  if(S.ice) ICE.forEach(([r,c,k])=>at(r,c,k));
  if(S.cheeks) CHEEKS[S.cheeks].forEach(([r,c,k])=>at(r,c,k));
  (EYES[S.eyes]||[]).forEach(([r,c,k])=>at(r+S.lookY,c+S.look,k));
  (MOUTH[S.mouth]||[]).forEach(([r,c,k])=>at(r,c,k));
  if(S.tear) at(10,2,'T'), at(11,2,'T');
  if(S.bubble>=0) BUBBLE[S.bubble].forEach(([r,c,k])=>at(r,c,k));
  if(S.thermo) THERMO.forEach(([r,c,k])=>at(r,c,k));
  S.ex.forEach(([name,r,c,ground])=>{
    const by=ground?OY+r:oy+r, bx=ground?OX+c:ox+c;
    SPR[name].forEach((row,i)=>[...row].forEach((ch,j)=>{ if(ch!=='.') put(by+i,bx+j,P[ch]) }));
  });
  return g;
}
/* ======================= personality ======================= */
const FIDGETS = {
  radiante:[['dance',28],['hum',22],['wave',16]],
  contento:[['look',26],['hum',22],['wiggle',12],['wave',16],['stretch',16]],
  justo:[['think',32],['look',26],['tap',20]],
  cansado:[['yawn',20],['nod',36],['sway',24]],
  agotado:[['sigh',16],['melt',30]],
  enfermo:[['sneeze',14],['sniff',10]],
  dormido:[],
};
const ACTLEN = {hello:16, poke:16, grumpy:30, celebrate:32, land:5, dizzy:26, wake:28};
class Pet {
  constructor(state){ Object.assign(this,{state,from:null,k:1,t:Math.floor(Math.random()*60),act:null,actT:0,
    held:false,hover:false,lookPin:null,walking:false,h5:0,stale:0,fid:null,nextFid:30+Math.floor(Math.random()*60)}) }
  setState(s){ if(s===this.state) return; this.from=this.pal(); this.state=s; this.k=0; this.fid=null }
  pal(){ const to=PALS[this.state]; if(!this.from||this.k>=1) return to; const o={};
    for(const key in to) o[key]=mix(this.from[key]||to[key],to[key],this.k); return o }
  do(a){ this.act=a; this.actT=0 }
  tick(){
    this.t++; if(this.k<1) this.k=Math.min(1,Math.round((this.k+0.1)*10)/10);     // exactly 10 frames to the new palette
    if(this.act){ this.actT++; if(this.actT>=ACTLEN[this.act]){ const a=this.act; this.act=null; if(a==='land'&&this.dizzyNext){ this.dizzyNext=false; this.do('dizzy') } } }
    if(this.fid&&this.t-this.fid.t0>=this.fid.len){ this.fid=null; this.nextFid=this.t+50+Math.floor(Math.random()*60) }
    if(!this.fid&&!this.act&&!this.held&&!this.hover&&!this.walking&&this.t>=this.nextFid){
      const list=FIDGETS[this.state]; if(list.length){ const [name,len]=list[Math.floor(Math.random()*list.length)]; this.fid={name,t0:this.t,len} } }
  }
  frame(){ return compose(this.spec(),this.pal()) }
  spec(){
    const t=this.t, s=this.state, f=this.fid, ft=f?t-f.t0:0, fn=f?f.name:null;
    const S={stretch:0,eyes:'open',mouth:'smile',cheeks:'c',leaf:'up',flowers:false,cap:false,ice:false,
      feet:'stand',armL:'none',armR:'none',thermo:false,dx:0,dy:0,look:0,lookY:0,sit:false,ex:[],fallen:false,tear:false,bubble:-1};
    const br=p=>((t/p|0)%2), blink=(t%53)<2;
    switch(s){
      case 'radiante':
        Object.assign(S,{eyes:(t%70)<46?'happy':'open',mouth:'open',cheeks:'C',flowers:true,leaf:br(5)?'up':'up2',stretch:br(4)});
        if(S.eyes==='open'&&blink) S.eyes='blink';
        [[-6,-3],[-3,20],[4,-5],[-8,6]].forEach(([r,c],i)=>{ const ph=(t+i*6)%20; if(ph<6) S.ex.push([ph<2||ph>3?'twink':'spark',r,c]) });
        if(fn==='dance'){ const b=ft%8; S.dy=[0,-1,-2,-1,0,-1,-2,-1][b]; S.armL=(ft>>2)%2?'up':'side'; S.armR=(ft>>2)%2?'side':'up'; S.dx=(ft>>3)%2?1:0 }
        if(fn==='hum'||fn==='dance') S.ex.push(['note',-4-(ft>>2)%6,19+((ft>>3)%2),false]);
        if(fn==='wave'){ S.armR=(ft>>1)%2?'up':'up2' }
        break;
      case 'contento':
        Object.assign(S,{leaf:br(10)?'up':'up2',stretch:br(7)}); if(blink) S.eyes='blink';
        if(fn==='look'){ S.look=ft<9?-1:ft<18?1:0 }
        if(fn==='hum'){ S.eyes='happy'; S.mouth='cat'; S.ex.push(['note',-4-(ft>>2)%6,19,false]); S.dx=(ft>>2)%2 }
        if(fn==='wiggle') S.leaf=ft%2?'up':'perk';
        if(fn==='wave'){ S.armR=(ft>>1)%2?'up':'up2'; S.eyes='happy'; S.mouth='open' }
        if(fn==='stretch'){ S.armL='up'; S.armR='up'; S.stretch=1; S.eyes='sleep'; S.mouth='o' }
        if(this.walking){ S.feet=(t>>1)%2?'walkA':'walkB'; S.stretch=(t>>1)%2; S.look=1 }
        break;
      case 'justo':
        Object.assign(S,{mouth:'neutral',stretch:br(8)}); if(blink) S.eyes='blink';
        if(fn==='think'){ S.look=1; S.lookY=-1; S.mouth='small'; S.armR='side'; for(let i=0;i<Math.min(3,(ft>>3)+1);i++) S.ex.push(['dotE',-2-i,16+i*2,false]) }
        if(fn==='look') S.look=ft<9?-1:ft<18?1:0;
        if(fn==='tap'){ S.feet=(ft>>1)%2?'walkA':'stand'; S.lookY=1 }
        break;
      case 'cansado':
        Object.assign(S,{leaf:'droop',eyes:'half',mouth:'small',stretch:br(12)});
        if(fn==='yawn'){ S.eyes='sleep'; S.mouth=ft<3?'small':'yawn'; S.stretch=ft>=3&&ft<13?1:0; S.tear=ft>=9; S.armL=ft>=3&&ft<13?'up':'none'; S.armR=S.armL }
        if(fn==='nod'){ if(ft<26){ S.eyes=ft<8?'half':'sleep'; S.dy=ft>8?1:0; S.stretch=ft>8?-1:0; if(ft>12) S.ex.push(['z',-2-((ft-12)>>2),15,false]) }
          else if(ft<32){ S.eyes='wide'; S.mouth='o'; S.dy=-1; S.ex.push(['bang',-6,15,false]) } }
        if(fn==='sway') S.dx=(ft>>3)%2;
        break;
      case 'agotado':
        Object.assign(S,{sit:true,leaf:'wilt',eyes:'spent',mouth:'wavy',stretch:-1});
        if(fn==='sigh'){ S.eyes='sleep'; S.mouth='o'; if(ft>2) S.ex.push(['puff',10+(ft>9?1:0),-3-((ft-3)>>1),false]) }
        if(fn==='melt'){ S.dy=ft>4&&ft<26?1:0; S.eyes=ft>4&&ft<26?'sleep':'spent' }
        break;
      case 'enfermo':
        Object.assign(S,{leaf:'wilt',ice:true,thermo:true,eyes:'teary',mouth:'o',cheeks:'C',dx:(t>>1)%2,fallen:true});
        if(fn==='sneeze'){ if(ft<6){ S.eyes='sleep'; S.stretch=1; S.dy=-1 } else { S.eyes='sleep'; S.mouth='o'; S.thermo=false; S.dx=2; S.stretch=-1; S.ex.push(['puff',9,19+(ft-6),false]) } }
        if(fn==='sniff'){ S.eyes='blink'; S.ex.push(['drip',12,7,false]) }
        break;
      case 'dormido':
        Object.assign(S,{cap:true,eyes:'sleep',mouth:'small',stretch:br(14)});
        { const b=t%44; S.bubble=b<40?Math.min(3,b/10|0):-1; if(b>=40&&b<43) S.ex.push(['twink',9,13,false],['twink',11,15,false],['twink',8,11,false]) }
        S.ex.push(['moon',-9,-6,true]);
        for(let i=0;i<3;i++){ const p=(t+i*13)%39; if(p<33) S.ex.push([i===1?'z':'Z',-3-(p/3|0),17+(p/7|0),false]) }
        break;
    }
    if(this.stale>0&&s!=='dormido'&&!this.held&&!this.act){   // R-115: stale doing items — he points at the notebook for 4 s every 15 s
      const cyc=t%150; if(cyc>=96&&cyc<136){ S.armR='point'; S.look=1; S.dx=0; S.ex.push(['book',(cyc>>2)%2?5:6,22,true]) } }
    if(this.h5>=70&&s!=='dormido'&&s!=='enfermo') S.ex.push(['sweat',2+((t>>2)%4),17,false]);
    if(this.h5>=90&&s!=='dormido'){ S.ex.push(['puff',-6-((t>>1)%2),2,false],['puff',-6-(((t>>1)+1)%2),13,false]); S.stretch=t%2 }
    const awake=!S.cap&&!['sleep','blink','happy'].includes(S.eyes);
    if(this.hover&&s!=='dormido'&&(S.leaf==='up'||S.leaf==='up2')) S.leaf='perk';
    if(this.lookPin!==null&&awake) S.look=this.lookPin;
    // what you do to him
    if(this.held){ Object.assign(S,{eyes:'wide',mouth:'o',cheeks:'C',armL:t%2?'up':'up2',armR:t%2?'up2':'up',feet:t%2?'kickA':'kickB',
      stretch:1,dy:0,dx:0,look:0,lookY:0,sit:false,tear:false,bubble:-1,thermo:false}); S.ex=[] }
    else if(this.act==='hello'){ const a=this.actT; Object.assign(S,{armR:(a>>1)%2?'up':'up2',eyes:'happy',mouth:'open',cheeks:'C',thermo:false}) }
    else if(this.act==='poke'){ const a=this.actT; Object.assign(S,{eyes:'heart',mouth:'open',cheeks:'C',dy:[0,-1,-2,-2,-1][a]??0,
      armL:a<5?'up':'none',armR:a<5?'up':'none',thermo:false}); S.ex.push(['heart',-7-(a>>1),7,false]) }
    else if(this.act==='grumpy'){ const a=this.actT; Object.assign(S,{eyes:'blink',mouth:'pout',cheeks:'C',look:0,dx:a%4<2?0:1,thermo:false,stretch:-1});
      if(a%12<7) S.ex.push(['puff',2,a%24<12?-5:19,false]) }
    else if(this.act==='celebrate'){ const a=this.actT; Object.assign(S,{eyes:'star',mouth:'open',cheeks:'C',sit:false,thermo:false,
      armL:(a>>1)%2?'up':'up2',armR:(a>>1)%2?'up2':'up',dy:[0,-2,-3,-3,-2,0,0,0,-2,-3,-3,-2,0][a%16]??0});
      for(let i=0;i<10;i++){ const y=((a*1.4+i*7)%28|0)-10, x=(i*11+2)%32-8; S.ex.push([['dot','twink','dotE'][i%3],y,x,true]) } }
    else if(this.act==='land'){ S.stretch=-1; S.eyes='sleep'; S.ex.push(['puff',14,-4,true],['puff',14,18,true]) }
    else if(this.act==='dizzy'){ const a=this.actT; S.eyes='ring'; S.mouth='wavy'; S.dx=(a>>2)%2;
      const pts=[[-3,1],[-6,8],[-3,15],[-1,8]]; [0,2].forEach(o=>{ const [r,c]=pts[((a>>1)+o)%4]; S.ex.push(['star',r,c,false]) }) }
    else if(this.act==='wake'){ const a=this.actT; S.cap=a<8; S.eyes=a<10?'sleep':'happy'; S.mouth=a<10?'yawn':'open'; S.stretch=a>=8&&a<18?1:0;
      S.armL=a>=8&&a<18?'up':'none'; S.armR=S.armL; S.bubble=-1; S.cheeks='C';
      if(a>=12) [[-6,-3],[-3,20],[-8,7]].forEach(([r,c],i)=>{ if((a+i)%4<2) S.ex.push(['spark',r,c,false]) }) }
    return S;
  }
}
/* sprites:end */
