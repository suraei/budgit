/* Generated from Brote's personality data. Do not edit by hand. */
(function(root){

const grid=(w,h)=>Array.from({length:h},()=>Array(w).fill('.'));
const stamp=(g,rows,r0,c0)=>{ rows.forEach((row,i)=>[...row].forEach((ch,j)=>{ if(ch!=='.'&&g[r0+i]) g[r0+i][c0+j]=ch })) };
const out=g=>g.map(r=>r.join(''));
const fromRows=rows=>rows.map(r=>[...r]);
function badge(glyph){
  const g=fromRows(['..HHHHHHH..','.HAAAAAAAH.','HAAAAAAAAAH','HAAAAAAAAAH','HAAAAAAAAAH','HAAAAAAAAAH','HAAAAAAAAAH','HAAAAAAAAAH','HAAAAAAAAAH','.HAAAAAAAH.','..HHHHHHH..']);
  stamp(g,glyph,1,2); return out(g) }
function cloud(glyph){
  const g=fromRows(['...ooooo...','.ooWWWWWoo.','oWWWWWWWWWo','oWWWWWWWWWo','oWWWWWWWWWo','oWWWWWWWWWo','oWWWWWWWWWo','.ooWWWWWoo.','...ooooo...','..oo.......','.oo........']);
  stamp(g,glyph,1,1); return out(g) }
const padTo=(rows,w)=>rows.map(r=>r.padEnd(w,'.'));
Object.assign(SPR,{
  bub_call_check: badge([".......","......e",".....ee","e...ee.","ee.ee..",".eee...","..e...."]),
  bub_call_ques: badge([".eeeee.","ee...ee",".....ee","...eee.","..ee...",".......","..ee..."]),
  bub_call_lock: badge(["..eee..",".e...e.",".e...e.","eeeeeee","eeeAeee","eeeAeee","eeeeeee"]),
  calltick:["H..",".H.","...",".H.","H.."],
  bub_think: cloud(["..nnnnn..",".nn...nn.",".....nn..","....nn...","...nn....",".........","...nn...."]),
  bub_done: padTo(["........uu.",".......uLLu","......uLLu.","uu...uLLu..","uLLuuLLu...",".uLLLLu....","..uLLu.....","...uu......"],11),
  bub_plus:["....uuu....","....uLu....",".uuuuLuuuu.",".uLLLLLLLu.",".uuuuLuuuu.","....uLu....","....uuu...."],
  bub_idea:["...DDDDD...","..DyyyyyD..",".DyyWWyyyD.",".DyWyyyyyD.",".DyyyyyyyD.","..DyyyyyD..","...DyyyD...","...DgDgD...","....DDD....","....DgD....","....DDD...."],
  bub_clock:["...ooooo...","..oWWWWWo..",".oWWWeWWWo.",".oWWWeWWWo.",".oWWWeeeWo.",".oWWWWWWWo.",".oWWWWWWWo.","..oWWWWWo..","...ooooo..."],
  bub_clock2:["...ooooo...","..oWWWWWo..",".oWWWWWWWo.",".oWWWWWWWo.",".oWWeeWWWo.",".oWWeWWWWo.",".oWWeWWWWo.","..oWWWWWo..","...ooooo..."],
  bub_sun:[".....y.....",".y...y...y.","...yyyyy...","..yyyyyyy..",".yyyyyyyyy.","yyyyyyyyyyy",".yyyyyyyyy.","..yyyyyyy..","...yyyyy...",".y...y...y.",".....y....."],
  bub_moon:["...yyyy....","..yyyy.....",".yyyy......",".yyyy......",".yyyy......","..yyyy.....","...yyyy....","...........",".......y..."],
  bub_battery:["eeeeeeeeee.","eRRWWWWWWe.","eRRWWWWWWee","eRRWWWWWWee","eRRWWWWWWe.","eeeeeeeeee."],
  bub_calendar:["..e.....e..",".rrrrrrrrr.",".rrrrrrrrr.","oWWWWWWWWWo","oWgWgWgWgWo","oWWWWWWWWWo","oWgWgRgWgWo","oWWWWWWWWWo","oWgWgWgWgWo","ooooooooooo"],
  bub_book:[".UUUUUUUUU.","UWWWWPWWWWU","UWgggPgggWU","UWWWWPWWWWU","UWgggPgggWU","UWWWWPWWWWU","UfFfFPFfFfU","UaaaaaRaaaU",".UUUUURUUU."],
});
SPR.chk=SPR.bub_call_check;

function ladSpr(p,n){ return Array.from({length:n},(_,i)=>{ const k=(i-p+50)%5; return k===0?'DdddddD':(k===1?'DDDDDDD':'Dd...dD') }) }
const NEWPAL={d:'#e3c096',D:'#b68a62',A:'#f7b59b',H:'#d9805f',K:'#2a2236',u:'#4f9a5e',v:'#2f6b3f',q:'#e0a15b',Q:'#b9783a',z:'#f4cd8f',X:'#b86f3a',a:'#a8683f',U:'#5f3b27',I:'#f0c862',f:'#fff3d6',F:'#e6cf9e'};
Object.values(PALS).forEach(p=>{ for(const k in NEWPAL) if(!p[k]) p[k]=NEWPAL[k] });
Object.assign(SPR,{
  lid:[".GGGGGGGGGG.","GPPPPlLPPPPG","GPPPPPsPPPPG","GGGGGGGGGGGG"],
  base:["nnnnnnnnnnnnnn"],
  hand:["bb","oo"],
  wall:["GG","Gg","Gg","Gg","Gg","Gg","Gg","Gg","Gg","Gg","Gg","Gg","Gg","GG"],
  tap:["g.g",".g."],
  speed:["PPP",".PP","PPP"],
  fan:fanSpr(),
  cup:["..oooooo...",".oXXXXXXo..",".oWWWWWWooo",".oWWnnWWo.o",".oWWWWWWo.o",".oWWWWWWooo","..oooooo..."].map(r=>r.padEnd(11,".")),
  steam1:["..n.",".n..","n...",".n..","..n."],
  steam2:["n...",".n..","..n.",".n..","n..."],
  food:["..W..",".WWW.","WWeWW","WWWWW",".ggg."],
  clasp:["bbbb","oooo"],
  pebble:["gg","gg"],
  keys:["l.y.l",".y.l."],
  dash:["P.P"],
  sunray:["y...y",".y.y.","..y.."],
  watch:["GG","GW","GG"],
  glow:["j","j"],
  ladder:ladSpr(0,24),
  ropeline:["R","y","R","y","R","y","R","y","R","y","R","y"],
  flatA:["BBo","BBo","BBo"],
  flatB:["BBo","BBo"],
  wallv:Array.from({length:22},(_,i)=>i%4===0?"ogG":"oGG"),
  vline:["P","P","P"],
  edge:["G".repeat(30),"g".repeat(30)],
  side:["G","G","G","G","G","G","G","G","G","G","G","G","G","G","G","G","G","G"],
  lens:["..nnnn..",".n....n.","n......n","n......n",".n....n.","..nnnnN.","......NN",".......N"],
  harm:["b","b","b","b","b","b","b","b"],
  track:["GGGGGGGG"],
  fill1:["e"],fill2:["ee"],fill3:["eee"],fill4:["eeee"],fill5:["eeeee"],fill6:["eeeeee"],fill7:["eeeeeee"],fill8:["eeeeeeee"],
});

const LAP=rows=>{ const f=['.oooooooooooo.']; rows.forEach(r=>f.push('.o'+r+'o.')); f.push('.oooooooooooo.','PgPgPgPgPgPgPP','nnnnnnnnnnnnnn'); return f };
const CODEL=['yyy.WWWW..','..rr.TTTT.','..WWWWWWW.','..lll.yy..','....WWWW..','..rr.TT.WW','yyy.......','..WWW.lll.','....TTTTT.','..rrrr.WW.','......lll.','yyy.WWWW..'];
const scrCode=ph=>Array.from({length:6},(_,i)=>CODEL[(ph+i)%12].replace(/\./g,'N'));
const scrRead=ph=>{ const t=['eeeeeee.W','NNNNNNNNN','NNNNNNNN.','NNNNNNN..','NNNNNNNNN','NNNNN....'], h=1+(ph%5);
  return t.map((r,i)=>r.replace(/\./g,i===h?'y':'W')+(i===h?'N':'g')) };
const scrSearch=ph=>{ const cs=['r','l','n','R'], rows=['TNTTTTTTTT','WWWWWWWWWW'];
  cs.forEach((c,k)=>{ const bg=k===ph?'y':'W'; rows.push(c+c+bg+'NNNNN'+bg+bg) }); return rows };
const scrBash=ph=>{ const t=['l.WWWWWW..','.llll.lll.','.ll.llll..','.lllll....','l.........'];
  const rows=t.map((r,i)=>(ph>=i?r:'..........').replace(/\./g,'K')), f=Math.min(8,ph); rows.push('g'+'l'.repeat(f)+'e'.repeat(8-f)+'g'); return rows };
const scrThink=ph=>{ const rows=Array(6).fill('KKKKKKKKKK'); const r=[...'KKKKKKKKKK']; [2,4,6].slice(0,ph+1).forEach(c=>{ r[c]='W' }); rows[3]=r.join(''); rows[5]='KWKKKKKKKK'; return rows };
const scrPic=ph=>['TTTTTTyyTT','TWWTTT'+(ph?'Wy':'yy')+'TT','WWWWTTTTTT','TTTTTTTTTT','LlllLLllLL','LLLLLlLLLL'];
const scrOff=()=>Array(6).fill('KKKKKKKKKK');
for(let i=0;i<12;i++) SPR['lp_code'+i]=LAP(scrCode(i));
for(let i=0;i<5;i++) SPR['lp_read'+i]=LAP(scrRead(i));
for(let i=0;i<4;i++) SPR['lp_search'+i]=LAP(scrSearch(i));
for(let i=0;i<9;i++) SPR['lp_bash'+i]=LAP(scrBash(i));
for(let i=0;i<3;i++) SPR['lp_think'+i]=LAP(scrThink(i));
for(let i=0;i<2;i++) SPR['lp_pic'+i]=LAP(scrPic(i));
SPR.lp_off=LAP(scrOff());
SPR.lp_mid=['.oooooooooooo.','.oKKKKKKKKKKo.','.oKKKKKKKKKKo.','.oooooooooooo.','PgPgPgPgPgPgPP','nnnnnnnnnnnnnn'];
SPR.lp_half=['.oooooooooooo.','.oKKKKKKKKKKo.','.oooooooooooo.','PgPgPgPgPgPgPP','nnnnnnnnnnnnnn'];
SPR.lp_closed=['GGGGGGGGGGGGGG','nnnnnnnnnnnnnn'];
function pufSpr(){ const w=24,h=6,g=grid(w,h);
  const top=[3,2,1,0,0,0,1,2,3,3,3,3,3,3,3,3,2,1,0,0,0,1,2,3], bot=[4,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,4];
  for(let c=0;c<w;c++) for(let r=top[c];r<=bot[c];r++) g[r][c]=(r===top[c]||c===0||c===w-1)?'r':(r===bot[c]?'R':(r>=4?'t':'p'));
  for(let c=3;c<=5;c++) g[1][c]='c'; g[2][2]='c'; for(let c=18;c<=19;c++) g[1][c]='c';
  for(let c=9;c<=14;c++) g[4][c]=(c%2?'r':'t');
  g[2][5]='r'; g[3][6]='r'; g[2][18]='r'; g[3][17]='r';
  return out(g) }
function fanSpr(){ const w=11,h=7,g=grid(w,h), px=5, py=6;
  for(let r=0;r<h;r++) for(let c=0;c<w;c++){ const dx=c-px, dy=py-r; const d=Math.hypot(dx,dy); if(dy<0||d>5.4) continue;
    const a=Math.atan2(dy,dx)*180/Math.PI, k=Math.floor((a+0.01)/22.5);
    g[r][c]=d>4.4?'o':(Math.abs(a/22.5-Math.round(a/22.5))<0.2&&d>1.5?'D':(k%2?'W':'p')) }
  g[6][px]='D'; g[5][px]='D'; return out(g) }
function bedSpr(){ const w=27,h=13,g=grid(w,h);
  for(let r=0;r<h;r++){ g[r][0]='D'; g[r][1]='d' } g[0][0]='d';
  for(let r=6;r<h;r++){ g[r][25]='d'; g[r][26]='D' }
  for(let c=2;c<=24;c++){ g[9][c]=c%4<2?'p':'c'; g[10][c]=c%4<2?'t':'p'; g[11][c]='d'; g[12][c]='D' }
  return out(g) }
const fanE=(ph)=>{ const w=11,h=13,g=grid(w,h), cx=5,cy=5;
  for(let r=0;r<11;r++) for(let c=0;c<11;c++){ const d=Math.hypot(c-cx,r-cy); if(d<=5.3&&d>4.2) g[r][c]='N'; else if(d<=4.2) g[r][c]='j' }
  for(const bl of [0,120,240]){ const a=(bl+ph*40)*Math.PI/180; for(let t=0;t<=4;t+=0.5){ const c=Math.round(cx+t*Math.cos(a)), r=Math.round(cy+t*Math.sin(a)); g[r][c]=t>2.2?'n':'N'; if(t>1.6&&t<=3.8){ const c2=Math.round(cx+t*Math.cos(a+0.35)), r2=Math.round(cy+t*Math.sin(a+0.35)); if(g[r2][c2]==='j') g[r2][c2]='T' } } }
  g[cy][cx]='y'; for(let r=11;r<h;r++){ g[r][cx]='N'; g[r][cx+1]='N' } for(let c=2;c<=8;c++) g[12][c]='N';
  return out(g) };
function arc(w,h,cx,cy,rx,ry,up){ const g=grid(w,h); for(let a=0;a<=100;a++){ const th=Math.PI*a/100, c=Math.round(cx-rx*Math.cos(th)), r=Math.round(cy+(up?-1:1)*ry*Math.sin(th)); for(const d of [0,1]) if(g[r]&&g[r][c+d]!==undefined) g[r][c+d]=a%12<6?'R':'y' }
  for(const [r,c] of [[up?cy:0,0],[up?cy:0,w-1]]) for(const d of [0,1]){ if(g[r+d]) g[r+d][c]='D' } return out(g) }
Object.assign(SPR,{
  puf:pufSpr(),
  efan0:fanE(0), efan1:fanE(1), efan2:fanE(2),
  airA:["TT.T"],airB:[".TTT"],airC:["T..T"],
  paw:["ooo","obo","ooo"],armH:["bbb","ooo"],
  vein:["rr.rr","r...r",".....","r...r","rr.rr"],
  lad0:ladSpr(0,27),lad1:ladSpr(1,27),lad2:ladSpr(2,27),lad3:ladSpr(3,27),lad4:ladSpr(4,27),
  desk:["dddddddddddddddd","DDDDDDDDDDDDDDDD",".DD..........DD.",".DD..........DD."],
  bed:bedSpr(),
  plate:[".ddd.ddd.","dedddded","dddDdddD","WWWWWWWWWW",".gggggggg."],
  milk:["T...T","TWWWT","TWWWT","TWWWT","TWWWT",".TTT."],
  cookie1:[".ddd.","deddd","ddded","dedDd",".ddd."],
  cookie2:[".dd..","ded..","ddd..","dedd.",".dd.."],
  cookie3:[".d.","de.","d.."],
  crumb:["d"],
  glasses:[".eee......eee.","e...e....e...e","e...eeeeee...e","e...e....e...e",".eee......eee."],
  gear1:["...D...",".D.D.D.","..DDD..","DDDeDDD","..DDD..",".D.D.D.","...D..."],
  gear2:["D.....D",".D.D.D.","..DDD..",".DDeDD.","..DDD..",".D.D.D.","D.....D"],
  jrU:arc(20,16,9.5,15,9.5,15,true),
  jrM:arc(20,4,9.5,0,9.5,3,false),
  jrD:arc(20,9,9.5,0,9.5,8,false),
});
Object.assign(LEAF,{
  prop1:px(["....lLl..lLl......","......lLsl........","........s.........","........s........."],-4),
  prop2:px(["......ll.ll........","......lLslL.......","........s.........","........s........."],-4),
});
Object.assign(EYES,{
  flat:sym([[9,3,'e'],[9,4,'e'],[9,5,'e']]),
  x:sym([[8,3,'e'],[8,5,'e'],[9,4,'e'],[10,3,'e'],[10,5,'e']]),
  dots:shift([[9,4,'e'],[9,5,'e']]),
  doubt:[[8,4,'E'],[8,5,'e'],[9,4,'e'],[9,5,'e'],[10,4,'e'],[10,5,'e'],[9,12,'e'],[9,13,'e'],[10,12,'E'],[10,13,'e']],
  mad:sym([[8,3,'e'],[9,4,'e'],[9,5,'e'],[10,5,'e']]),
});
Object.assign(MOUTH,{
  grit:[[10,7,'W'],[10,8,'W'],[10,9,'W'],[10,10,'W'],[11,7,'m'],[11,8,'m'],[11,9,'m'],[11,10,'m']],
  grin:[[10,6,'m'],[11,7,'m'],[11,8,'m'],[11,9,'m'],[11,10,'m'],[10,11,'m']],
});

const inP=(ft,a,b)=>ft>=a&&ft<b, alt=(ft,n)=>((ft/n)|0)%2;
const zs=(S,ft,c)=>{ c=c||15; for(let i=0;i<2;i++){ const p=(ft+i*13)%26; if(p<20) S.ex.push([i?'z':'Z',-3-(p/3|0),c+(p/7|0),false]) } };
const bub=(S,k,r,c)=>S.ex.push(['bub_'+k,r===undefined?-10:r,c===undefined?15:c,true]);
const call=(S,ft,k)=>{ bub(S,'call_'+k); if(alt(ft,3)) S.ex.push(['calltick',-8,12,true]) };
const snot=(S,ft,from)=>{ const b=ft-(from||0); if(b>=0){ const m=b%44; S.bubble=m<40?Math.min(3,(m/10)|0):-1; if(m>=40&&m<43) S.ex.push(['twink',9,13,false]) } };
const sparks=(S,ft,n)=>{ const P=[[-6,-3],[-3,20],[4,-5],[-8,6],[-5,16]]; for(let i=0;i<(n||4);i++){ const ph=(ft+i*5)%16; if(ph<6) S.ex.push([ph<2||ph>3?'twink':'spark',P[i][0],P[i][1],false]) } };
const hop=[0,-1,-2,-2,-1,0];
const lapPut=(S,name)=>S.ex.push([name,13-SPR[name].length,12,true]);
const deskPut=S=>S.ex.push(['desk',13,10,true]);
const work=(S,screen,dx)=>{ S.sit=true; S.dx=dx===undefined?-7:dx; S.look=1; deskPut(S); lapPut(S,screen) };
const typing=(S,ft,n)=>{ const a=alt(ft,n||1); S.ex.push(['hand',a?10:11,11,true],['hand',a?11:10,14,true]) };
const walkFeet=(S,ft,n)=>{ S.feet=alt(ft,n||2)?'walkA':'walkB' };
const lerp=(a,b,k)=>a+(b-a)*k;

const ladPh=ft=>(ft>>1)%5;
const ladOn=(S,ft,col,side)=>{ const p=ladPh(ft), m=(a,b)=>((a%b)+b)%b, aA=2+m(p-2,5), aB=5+m(p-5,5), aF=13+m(p-13,5), near=side>0?col-1:col+4;
  S.ex.push(['lad'+p,-10,col,true],['paw',aA,col+2,true],['paw',aB,col+2,true],['armH',aA+1,near,true],['armH',aB+1,near,true]); if(aF<=15) S.ex.push(['hand',aF,side>0?col:col+5,true]) };

const POSE={
  fall(S,ft){ Object.assign(S,{stretch:1,eyes:'wide',mouth:'o',armL:alt(ft,1)?'up':'up2',armR:alt(ft,1)?'up2':'up',feet:alt(ft,1)?'kickA':'kickB',leaf:'up2',thermo:false}); S.ex.push(['vline',0,-2,false],['vline',2,20,false]) },
  land(S,ft){ Object.assign(S,{stretch:-1,eyes:'sleep',mouth:'small'}); if(ft<4) S.ex.push(['puff',14,-4,true],['puff',14,18,true]) },
  fly(S,ft){ Object.assign(S,{eyes:'happy',mouth:'open',leaf:alt(ft,1)?'prop1':'prop2',armL:'up',armR:'up',feet:alt(ft,2)?'kickA':'kickB',stretch:alt(ft,3)}); S.ex.push(['vline',13,3,false],['vline',14,13,false]) },
  glide(S,ft){ Object.assign(S,{eyes:'half',mouth:'cat',leaf:alt(ft,1)?'prop1':'prop2',armL:'side',armR:'side',feet:'stand'}) },
  climb(S,ft){ Object.assign(S,{eyes:'open',mouth:'small',look:1,armL:alt(ft,3)?'up':'up2',armR:alt(ft,3)?'up2':'up',feet:alt(ft,3)?'walkA':'walkB',stretch:alt(ft,3)}) },
  hang(S,ft){ Object.assign(S,{eyes:alt(ft,6)?'wide':'half',mouth:'wavy',armL:'up',armR:'up',feet:alt(ft,4)?'kickA':'kickB',stretch:1}); S.ex.push(['sweat',2,17,false]) },
  walk(S,ft,n){ Object.assign(S,{feet:alt(ft,n||2)?'walkA':'walkB',stretch:alt(ft,n||2),look:1}) },
};

const CAT=[];
const A=(id,grp,st,ev,an,dur,cd,pri,att,src,plumb,f,x)=>CAT.push(Object.assign({id,grp,st,ev,an,dur,cd,pri,att,src,plumb,f},x||{}));

A('ag_walk',0,'agotado',0,0,9,60,2,0,0,0,
 (S,ft)=>{ Object.assign(S,{sit:false,stretch:-1,leaf:'wilt',mouth:'wavy',eyes:ft<60?(alt(ft,25)?'sleep':'spent'):'spent'}); POSE.walk(S,ft,4); S.stretch=-1; S.look=0; S.dy=(ft%8<2)?1:0; S.dx=Math.round(lerp(-6,5,ft/90));
   if(ft<68) snot(S,ft,8); else if(ft<76){ S.eyes='sleep'; S.dy=1; S.feet='stand'; S.dx=5; snot(S,ft,8) } else if(ft<82){ S.eyes='wide'; S.mouth='o'; S.dy=-2; S.feet='stand'; S.dx=5; S.bubble=-1; S.ex.push(['bang',-6,15+5,false]) } else { S.feet='stand'; S.dx=5; S.eyes='blink' } },{walk:1});
A('ag_bubbles',0,'agotado',0,0,6,30,1,0,0,0,
 (S,ft)=>{ S.eyes='sleep'; S.mouth='small'; snot(S,ft,0); if(ft>38) zs(S,ft) });
A('ag_nod_off',0,'agotado',0,0,5,45,2,0,0,0,
 (S,ft)=>{ S.sit=false; S.stretch=-1; S.leaf='wilt'; S.mouth='small'; if(ft<20){ S.eyes=ft<8?'half':'sleep'; S.dy=ft>10?1:0; if(ft>14) snot(S,ft,14) } else if(ft<28){ S.eyes='sleep'; S.dy=1; snot(S,ft,14) } else if(ft<36){ S.eyes='wide'; S.mouth='o'; S.dy=-2; S.bubble=-1; S.ex.push(['bang',-6,15,false]) } else { S.eyes=ft<42?'blink':'spent'; S.dx=alt(ft,2) } });
A('ag_catch',0,'agotado',0,0,3,60,2,0,0,0,
 (S,ft)=>{ S.sit=false; S.leaf='wilt'; if(ft<8){ S.dx=ft>>1; S.eyes='half'; POSE.walk(S,ft,3); S.stretch=-1; S.look=0 } else if(ft<18){ S.stretch=0; S.eyes='wide'; S.mouth='o'; S.armL=alt(ft,2)?'up':'up2'; S.armR=alt(ft,2)?'up2':'up'; S.feet=alt(ft,2)?'kickA':'kickB'; S.dx=[4,3,4,2,3,2,1,2,1,1][ft-8]; S.ex.push(['sweat',2,17,false]) } else { S.stretch=-1; S.eyes='sleep'; S.mouth='o'; S.ex.push(['puff',11,-3-((ft-18)>>2),false]) } });
A('ag_lean',0,'agotado',0,0,7,60,1,0,0,0,
 (S,ft)=>{ S.sit=false; S.leaf='wilt'; S.eyes='sleep'; S.mouth='small'; S.lookY=1; S.stretch=-1; S.dx=ft<12?Math.round(lerp(2,5,ft/12)):(ft<16?6:5); if(ft>=12){ S.ex.push(['flatA',3,15,false],['flatB',12,15,false]) } S.ex.push(['wallv',-6,23,true]); snot(S,ft,0); if(ft>20) zs(S,ft,14) });
A('ag_dust',0,'agotado',0,0,6,50,1,0,0,0,
 (S,ft)=>{ Object.assign(S,{sit:false,stretch:-1,eyes:'spent',mouth:'wavy',leaf:'wilt'}); POSE.walk(S,ft,4); S.stretch=-1; S.look=0; S.dx=Math.round(lerp(5,-5,ft/60)); if(ft%8<3&&ft<46) S.ex.push(['puff',14,S.dx+(ft%16<8?16:-2),true]); if(ft>=46){ S.eyes='sleep'; S.mouth='o'; S.feet='stand'; S.ex.push(['puff',10,-1-Math.min(1,(ft-46)>>3),false]) } },{walk:-1});

const FLEE_LIM=d=>d<0?-8:5;
A('ag_grumble',0,'agotado',0,0,3,0,4,0,0,0,
 (S,ft)=>{ S.leaf='wilt'; S.look=0; if(ft<6){ S.eyes='half'; S.mouth='small' } else if(ft<20){ S.eyes=(ft>=12&&ft<14)?'blink':'open'; S.mouth='neutral' } else { S.eyes='sleep'; S.mouth='small'; S.ex.push(['Z',-3,15,false]) } });
A('ag_annoyed',0,'agotado',0,0,3.5,0,4,0,0,0,
 (S,ft)=>{ S.leaf='wilt'; S.look=0; S.cheeks='c'; if(ft<6){ S.eyes='half'; S.mouth='small' } else { S.eyes=ft%24<20?'flat':'blink'; S.mouth='pout'; S.dx=(ft>>3)%2; if(ft%12<5) S.ex.push(['puff',9,19+((ft%12)>>1),false]); if(ft>=24&&ft<30){ S.armL='side'; S.armR='side'; S.dy=-1 } } });
A('ag_angry',0,'agotado',0,0,2.5,0,5,0,0,0,
 (S,ft)=>{ S.eyes='mad'; S.mouth='grit'; S.cheeks='C'; S.leaf='perk'; S.look=0; S.dx=alt(ft,1)?-1:1; S.armL='up'; S.armR='up'; S.dy=ft%6<3?0:-1; if(alt(ft,3)) S.ex.push(['vein',-4,13,false]); const k=(ft>>1)%3; S.ex.push(['puff',3-k,-3,false],['puff',3-k,20,false]) });
A('ag_flee',0,'agotado',0,0,2.8,0,5,0,0,0,
 (S,ft)=>{ const d=S.dir||-1, lim=FLEE_LIM(d), run=Math.min(1,Math.max(0,(ft-3)/13)); S.eyes='mad'; S.mouth='grit'; S.cheeks='C'; S.look=d;
  if(ft<3){ S.eyes='wide'; S.mouth='o'; S.dy=-2+ft; S.armL='up'; S.armR='up'; S.ex.push(['bang',-5,d<0?-1:15,false]); return }
  S.dx=Math.round(lim*run);
  if(ft<16){ S.feet=alt(ft,1)?'kickA':'kickB'; S.stretch=alt(ft,2); S.leaf='up2'; S.dy=alt(ft,2)?-1:0; const tr=d<0?S.dx+16:S.dx-3; if(ft%2===0) S.ex.push(['puff',14,tr,true]); S.ex.push(['vline',5,d<0?S.dx+19:S.dx-4,false],['vline',9,d<0?S.dx+18:S.dx-3,false]) }
  else { S.feet='stand'; S.stretch=-1; S.eyes=ft<22?'mad':'spent'; S.mouth=ft<22?'grit':'o'; if(ft<20) S.ex.push(['puff',14,d<0?S.dx+16:S.dx-3,true]) } });
A('ag_calm',0,'agotado',0,0,8,0,3,0,0,0,
 (S,ft)=>{ const d=S.dir||-1; S.sit=true; S.dx=FLEE_LIM(d); S.look=d; S.leaf='wilt';
  if(ft<30){ S.eyes='flat'; S.mouth='pout'; S.cheeks='C'; if(ft%10<4&&ft<24) S.ex.push(['puff',3-((ft%10)>>1),d<0?S.dx+19:S.dx-3,false]) }
  else if(ft<60){ S.eyes='half'; S.mouth='small'; S.cheeks='c'; S.ex.push(['sweat',2+((ft>>3)%3),d<0?S.dx+18:S.dx-3,false]) }
  else { S.eyes=ft%30<26?'half':'blink'; S.mouth=ft<68?'o':'small'; S.cheeks='c'; if(ft>=60&&ft<68) S.ex.push(['puff',9+((ft-60)>>2),d<0?S.dx+19:S.dx-3,false]) } });
A('ag_return',0,'agotado',0,0,6,0,2,0,0,0,
 (S,ft)=>{ const d=S.dir||-1, lim=FLEE_LIM(d), run=Math.min(1,ft/46); S.dx=Math.round(lim*(1-run)); S.leaf='wilt';
  if(ft<46){ S.feet=alt(ft,4)?'walkA':'walkB'; S.stretch=-1; S.look=-d; S.eyes=ft<30?'half':'open'; S.mouth='small'; S.dy=(ft%8<2)?1:0; if(ft>=30) S.ex.push(['sweat',2,d<0?S.dx+18:S.dx-3,false]) }
  else { S.sit=true; S.look=0; S.eyes=ft<54?'open':'half'; S.mouth=ft<54?'wavy':'small'; S.dx=0 } });

A('tr_up',0,'contento',0,0,3,0,3,0,0,0,
 (S,ft)=>{ if(ft<10){ S.eyes='sleep'; S.armL='up'; S.armR='up'; S.stretch=1; S.mouth='o' } else { S.eyes='happy'; S.mouth='open'; S.dy=hop[(ft-10)%6]||0; sparks(S,ft,4) } });
A('tr_down',0,'cansado',0,0,3,0,3,0,0,0,
 (S,ft)=>{ S.leaf=ft<6?'up':'droop'; if(ft>=6&&ft<22){ S.eyes='sleep'; S.mouth=ft<9?'small':'yawn'; S.armL=ft>=9?'up':'none'; S.armR=S.armL; S.stretch=ft>=9?1:0; S.tear=ft>=16 } });
A('tr_agotado',0,'agotado',0,0,3,0,3,0,0,0,
 (S,ft)=>{ if(ft<5){ S.sit=false; S.dy=-1-(ft>>1); S.eyes='wide'; S.mouth='o' } else { S.stretch=-1; if(ft<12) S.ex.push(['puff',14,-4,true],['puff',14,18,true]); else S.ex.push(['sweat',2+((ft-12)>>2),17,false]) } });
A('tr_enfermo',0,'enfermo',0,0,3,0,3,0,0,0,
 (S,ft)=>{ S.thermo=ft>10; S.dx=ft%2; if(ft<10){ S.eyes='wide'; S.mouth='o' } });
A('tr_dormido',0,'dormido',0,0,4,0,3,0,0,0,
 (S,ft)=>{ if(ft<8){ S.cap=false; S.eyes=ft<4?'half':'sleep'; S.leaf='droop' } else { S.cap=ft>10; if(ft>14) zs(S,ft) } });
A('tr_week',0,'radiante',0,0,5,0,4,0,0,0,
 (S,ft)=>{ S.eyes='happy'; S.mouth='open'; S.armL=alt(ft,2)?'up':'up2'; S.armR=alt(ft,2)?'up2':'up'; S.dy=hop[ft%6]; bub(S,'sun'); for(let i=0;i<8;i++){ const y=((ft*1.3+i*7)%26|0)-8, xx=(i*11+2)%32-7; S.ex.push([['dot','twink','dotE'][i%3],y,xx,true]) } });
A('tr_5h',0,'contento',0,0,3,0,2,0,0,0,
 (S,ft)=>{ if(ft<10){ S.armR='up'; S.eyes='sleep'; S.ex.push(['sweat',2+(ft>>2),17,false]) } else { S.eyes='happy'; S.mouth='o'; S.ex.push(['puff',9,19+Math.min(3,(ft-10)>>2),false]) } });

A('q_h5_wipe',0,'cansado',0,0,3,30,2,0,0,0,
 (S,ft)=>{ S.ex.push(['sweat',2+((ft>>2)%4),17,false]); if(inP(ft,6,18)){ S.armR=alt(ft,2)?'up':'up2'; S.eyes='sleep' } });
A('q_h5_fan',0,'cansado',0,0,4,20,3,0,0,0,
 (S,ft)=>{ S.dx=-7; S.eyes=ft%30<26?'happy':'blink'; S.mouth=alt(ft,2)?'o':'small'; S.cheeks='C'; S.leaf=alt(ft,2)?'perk':'droop'; S.look=0;
  S.ex.push(['efan'+(ft%3),1,22,false],['sweat',2+((ft>>2)%4),-1,false]);
  const k=ft%3, ln=['airA','airB','airC']; S.ex.push([ln[(k)%3],3,18+((ft>>1)%2),false],[ln[(k+1)%3],6,18+((ft>>1)%2?0:1),false],[ln[(k+2)%3],9,18+((ft>>1)%2),false]) });
A('q_watch',0,'justo',0,0,3,45,1,0,0,0,
 (S,ft)=>{ S.armR='side'; S.look=1; S.lookY=1; S.ex.push(['watch',9,19,false]); if(ft>12) S.eyes=alt(ft,3)?'blink':'open'; if(ft>16) bub(S,'clock') });
A('q_runout',0,'cansado',0,0,4,90,2,0,0,0,
 (S,ft)=>{ S.eyes='teary'; S.mouth='frown'; bub(S,'calendar'); if(ft>10) S.ex.push(['sweat',2,17,false]) });

A('t_new',0,'contento',0,0,2.5,10,2,0,0,0,
 (S,ft)=>{ S.leaf='perk'; S.look=1; S.eyes=ft<12?'open':'happy'; bub(S,'plus'); if(inP(ft,12,22)) S.dy=ft%4<2?-1:0 });
A('t_start',0,'contento',0,0,2.5,10,2,0,0,0,
 (S,ft)=>{ S.eyes='flat'; S.mouth='grit'; S.armL=alt(ft,3)?'up':'up2'; S.armR=alt(ft,3)?'up2':'up'; S.stretch=alt(ft,3); S.ex.push(['speed',1,-4,false]) });
A('t_done',0,'contento',0,0,3,0,4,0,0,0,
 (S,ft)=>{ S.eyes='happy'; S.mouth='open'; S.armL=alt(ft,2)?'up':'up2'; S.armR=alt(ft,2)?'up2':'up'; S.dy=hop[ft%6]; bub(S,'done'); sparks(S,ft,3) });
A('t_streak',0,'radiante',0,0,4,600,3,0,0,0,
 (S,ft)=>{ S.dy=[0,-1,-2,-1,0,-1,-2,-1][ft%8]; S.armL=alt(ft,4)?'up':'side'; S.armR=alt(ft,4)?'side':'up'; S.dx=(ft>>3)%2; S.ex.push(['note',-3-(ft>>2)%5,19,false]); if(ft>20) S.ex.push(['note',-6-(ft>>2)%3,-3,false]) });
A('t_all',0,'radiante',0,0,6,300,2,0,0,0,
 (S,ft)=>{ S.sit=true; S.ex.push(['puf',12,-3,true]); S.eyes=ft%40<34?'happy':'blink'; S.mouth='cat'; S.armR='side'; S.ex.push(['cup',alt(ft,12)?7:8,14,false],[alt(ft,5)?'steam1':'steam2',alt(ft,12)?2:3,17,false],[alt(ft,5)?'steam2':'steam1',alt(ft,12)?0:1,20,false]) });
A('t_stale',0,'contento',0,0,4,150,3,1,0,0,
 (S,ft)=>{ S.armR='point'; S.look=1; S.ex.push(['book',alt(ft,4)?5:6,22,true]); if(ft>20){ S.eyes='half'; bub(S,'clock') } });
A('t_unstale',0,'contento',0,0,2.5,0,2,0,0,0,
 (S,ft)=>{ S.eyes=ft<8?'sleep':'happy'; S.armL=alt(ft,2)?'side':'none'; S.armR=S.armL; S.mouth=ft<8?'o':'smile'; if(ft<8) S.ex.push(['puff',9,19,false]) });

A('c_needs',0,'contento',0,0,4,0,5,1,0,0,
 (S,ft)=>{ const q=ft%12; S.look=0; S.dx=-3-[0,1,2,1,0,-1,0,1,2,1,0,-1][q]; S.feet=alt(ft,2)?'walkA':'walkB';
  if(ft<24){ S.eyes='wide'; S.mouth='o'; S.armR=alt(ft,3)?'up':'up2'; S.dy=hop[ft%6]; S.leaf=ft%12<6?'perk':'up' }
  else { S.eyes='doubt'; S.mouth='wavy'; S.armR='side'; S.leaf='droop'; S.dx=-3+((ft>>3)%2) }
  call(S,ft,'ques') });
A('c_perm',0,'contento',0,0,4,0,5,1,0,0,
 (S,ft)=>{ const bow=(ft%24)>=16&&(ft%24)<22; S.look=0; S.dx=-3; S.eyes='teary'; S.mouth='small'; S.cheeks='C'; S.leaf='droop'; S.armL='none'; S.armR='none'; if(bow){ S.stretch=-1; S.dy=1 }
  S.ex.push(['clasp',12,7,false]); if(ft%20>=12&&ft%20<16) S.ex.push(['sweat',2,17,false]); call(S,ft,'lock') });
A('c_error',0,'contento',0,0,2.5,20,2,0,0,0,
 (S,ft)=>{ if(ft<4){ S.eyes='wide'; S.mouth='o'; S.dy=-2; S.ex.push(['bang',-6,15,false]) } else { S.eyes='half'; S.look=-1; S.mouth='wavy'; S.ex.push(['sweat',2,17,false]) } });
A('c_wait',0,'contento',0,0,6,300,1,0,0,0,
 (S,ft)=>{ work(S,'lp_think'+((ft>>3)%3)); S.lookY=1; if(ft<20){ S.eyes='sleep'; S.mouth=ft<4?'small':'yawn'; S.armL='up'; S.armR='up' } else { S.eyes='half'; S.mouth='small'; typing(S,ft,3) } if(ft>=8) bub(S,alt(ft,5)?'clock':'clock2') },{v:'pc_think'});
A('c_answered',0,'contento',0,0,2.5,0,5,0,0,0,
 (S,ft)=>{ S.eyes='happy'; S.mouth='open'; S.armR=alt(ft,2)?'up':'up2'; if(ft<14) S.ex.push(['heart',-7-(ft>>2),7,false]); S.leaf=ft%6<3?'up':'perk' });

A('att_1',0,'contento',0,0,3,0,5,1,0,0,
 (S,ft)=>{ S.look=0; S.dx=-Math.min(3,ft>>1); if(ft<24){ S.eyes='happy'; S.mouth='open'; S.armR=alt(ft,3)?'up':'up2'; S.dy=hop[ft%6]; S.feet=ft<6?(alt(ft,2)?'walkA':'walkB'):'stand' } else { S.eyes='open'; S.mouth='smile'; S.armR='up' } call(S,ft,'check') });
A('att_2',0,'contento',0,0,2,0,4,1,0,0,
 (S,ft)=>{ S.look=0; S.dx=-3; S.eyes='open'; S.mouth='smile'; S.armR=(ft%10<5)?'up':'up2'; S.dy=(ft%10<3)?-1:0; call(S,ft,'check') });
A('att_3',0,'contento',0,0,3,0,4,1,0,0,
 (S,ft)=>{ S.look=0; S.dx=-3; S.eyes=ft<22?'open':'half'; S.leaf=alt(ft,3)?'up':'perk'; S.mouth=ft<22?'small':'wavy'; if(ft>=14&&ft<24){ S.armL='side'; S.armR='side'; S.eyes='half'; S.mouth='frown' } call(S,ft,'check') });
A('att_cap',0,'contento',0,0,5,0,2,0,0,0,
 (S,ft)=>{ S.sit=true; S.look=0; S.dx=-3; S.eyes='open'; S.mouth='small'; bub(S,'call_check') });
A('att_rm',0,'contento',0,0,3,0,4,1,0,0,
 (S,ft)=>{ S.look=0; S.dx=-3; S.eyes='open'; S.mouth='smile'; S.armR=ft<15?'up':'none'; bub(S,'call_check') });

A('pc_out',0,'contento',0,0,4,0,3,0,0,0,
 (S,ft)=>{ if(ft<6){ S.look=-1; S.armR='up'; S.dx=Math.round(lerp(0,-7,ft/6)); S.eyes='open' }
  else { S.sit=true; S.dx=-7; S.look=1;
   if(ft<10){ S.armR='up'; S.ex.push(['puff',10,13,true],['puff',12,19,true]) }
   else if(ft<14) deskPut(S);
   else if(ft<18){ deskPut(S); lapPut(S,'lp_closed') }
   else if(ft<22){ deskPut(S); lapPut(S,'lp_half') }
   else if(ft<26){ deskPut(S); lapPut(S,'lp_mid') }
   else { deskPut(S); lapPut(S,'lp_think0'); S.eyes='half'; S.lookY=1; typing(S,ft,2) } } });
A('pc_type',0,'contento',0,0,6,0,3,0,0,0,
 (S,ft)=>{ work(S,'lp_code'+((ft>>2)%12)); S.eyes=ft%40<2?'blink':'open'; S.lookY=1; S.mouth=ft%16<12?'small':'open'; S.leaf=alt(ft,6)?'up':'up2'; typing(S,ft,1) });
A('pc_read',0,'contento',0,0,5,0,3,0,0,0,
 (S,ft)=>{ work(S,'lp_read'+((ft>>3)%5)); S.mouth='small'; S.look=[0,1,1,0][(ft>>1)%4]; S.lookY=((ft>>3)%2)?1:0; S.ex.push(['glasses',7,2,false],['hand',ft%16<8?10:9,22,true]) });
A('pc_search',0,'contento',0,0,5,0,3,0,0,0,
 (S,ft)=>{ const k=Math.min(3,ft>>4); work(S,'lp_search'+k); S.eyes='wide'; S.mouth='o'; S.lookY=0; S.armR='up'; S.ex.push(['lens',3+k,13+((ft>>2)%3),true]) });
A('pc_run',0,'contento',0,0,5.5,0,3,0,0,0,
 (S,ft)=>{ const ph=Math.max(0,Math.min(8,(ft-10)>>2)); work(S,'lp_bash'+ph); S.lookY=1;
  if(ft<8){ S.mouth='small'; S.eyes='open'; typing(S,ft,1) }
  else if(ft<11){ S.mouth='open'; S.eyes='wide'; S.ex.push(['hand',9,22,true],['twink',8,24,true],['twink',11,21,true]) }
  else if(ft<42){ S.mouth=alt(ft,6)?'small':'o'; S.eyes='open'; S.armR=alt(ft,3)?'up':'up2'; S.armL=alt(ft,3)?'up2':'up'; S.ex.push([alt(ft,2)?'gear1':'gear2',-10,5,true]) }
  else { S.eyes='happy'; S.mouth='open'; S.armR='up'; S.armL='up' } });
A('pc_think',0,'contento',0,0,4,0,3,0,0,0,
 (S,ft)=>{ work(S,'lp_think'+((ft>>3)%3)); S.eyes='open'; S.lookY=-1; S.look=0; S.mouth='small'; S.armR=alt(ft,4)?'up':'up2'; for(let i=0;i<Math.min(3,(ft>>3)+1);i++) S.ex.push(['dotE',-2-i,16+i*2,false]) });
A('pc_away',0,'contento',0,0,3,0,3,0,0,0,
 (S,ft)=>{ if(ft<20){ S.sit=true; S.dx=-7; S.look=1; deskPut(S);
   if(ft<4){ lapPut(S,'lp_think0'); typing(S,ft,2) } else if(ft<8) lapPut(S,'lp_mid'); else if(ft<12) lapPut(S,'lp_half'); else if(ft<16) lapPut(S,'lp_closed'); else S.ex.push(['puff',10,13,true],['puff',12,19,true]) }
  else { S.dx=Math.round(lerp(-7,0,Math.min(1,(ft-20)/8))); S.eyes='happy'; S.mouth='open' } });
A('pc_sleep',0,'agotado',0,0,6,0,2,0,0,0,
 (S,ft)=>{ S.leaf='wilt'; if(ft<24){ work(S,'lp_code'+((ft>>3)%12)); S.eyes='half'; S.lookY=1; S.ex.push(['hand',alt(ft,3)?9:10,13,true]) } else { work(S,'lp_off'); S.eyes='sleep'; S.dy=2; S.stretch=-1; snot(S,ft,24); S.ex.push(['Z',-3,3,true]) } });

A('w_hello',0,'contento',0,0,1.6,0,3,0,0,0,(S,ft)=>{ S.armR=(ft>>1)%2?'up':'up2'; S.eyes='happy'; S.mouth='open'; S.cheeks='C' });
A('w_poke',0,'contento',0,0,1.6,0,3,0,0,0,(S,ft)=>{ Object.assign(S,{eyes:'heart',mouth:'open',cheeks:'C',dy:[0,-1,-2,-2,-1][ft]||0,armL:ft<5?'up':'none',armR:ft<5?'up':'none'}); S.ex.push(['heart',-7-(ft>>1),7,false]) });
A('w_grumpy',0,'contento',0,0,3,0,3,0,0,0,(S,ft)=>{ Object.assign(S,{eyes:'blink',mouth:'pout',cheeks:'C',dx:ft%4<2?0:1,stretch:-1}); if(ft%12<7) S.ex.push(['puff',2,ft%24<12?-5:19,false]) });
A('w_held',0,'contento',0,0,1.6,0,4,0,0,0,(S,ft)=>{ Object.assign(S,{eyes:'wide',mouth:'o',cheeks:'C',armL:ft%2?'up':'up2',armR:ft%2?'up2':'up',feet:ft%2?'kickA':'kickB',stretch:1}) });
A('w_land',0,'contento',0,0,0.8,0,3,0,0,0,(S,ft)=>{ S.stretch=-1; S.eyes='sleep'; S.ex.push(['puff',14,-4,true],['puff',14,18,true]) });
A('w_dizzy',0,'contento',0,0,2.6,0,3,0,0,0,(S,ft)=>{ S.eyes='ring'; S.mouth='wavy'; S.dx=(ft>>2)%2; const pts=[[-3,1],[-6,8],[-3,15],[-1,8]]; [0,2].forEach(o=>{ const p=pts[((ft>>1)+o)%4]; S.ex.push(['star',p[0],p[1],false]) }) });
A('w_wheee',0,'contento',0,0,1.5,0,3,0,0,0,(S,ft)=>{ S.eyes='happy'; S.mouth='open'; S.armL='up'; S.armR='up'; S.feet='kickA'; S.ex.push(['speed',2,-6,false],['speed',8,-7,false],['speed',13,-5,false]) });
A('w_follow',0,'contento',0,0,4,0,1,0,0,0,(S,ft)=>{ S.look=ft<12?-1:ft<24?0:1; S.lookY=ft<8?-1:ft<30?0:1; S.leaf=ft%10<5?'up':'perk' });
A('w_longhover',0,'contento',0,0,4,0,2,0,0,0,(S,ft)=>{ S.eyes='happy'; S.mouth='cat'; S.cheeks='C'; S.dx=1; S.leaf=alt(ft,4)?'up':'perk'; if(ft>10) S.ex.push(['heart',-5-Math.min(2,(ft-10)>>2),7,false]) });
A('w_tickle',0,'contento',0,0,3,60,3,0,0,0,(S,ft)=>{ if(ft<20){ S.eyes='happy'; S.mouth='open'; S.dx=alt(ft,1); S.armL='up'; S.armR='up' } else { S.eyes='sleep'; S.mouth='open'; S.stretch=-1; S.dy=1; S.ex.push(['heart',-4,7,false]) } });
A('w_chase',0,'contento',0,0,2.5,0,2,0,0,0,(S,ft)=>{ POSE.walk(S,ft,1); S.dx=Math.round(lerp(-5,5,ft/25)); S.ex.push(['speed',6,-3,false]) },{walk:1});
A('w_minimise',0,'contento',0,0,4,0,3,0,0,0,(S,ft)=>{ if(ft<14){ S.look=-1; S.lookY=-1; S.eyes='wide' } else if(ft<26){ S.eyes='half'; S.armL='side'; S.armR='side'; S.dy=alt(ft,3)?-1:0 } else { S.eyes='open'; S.look=ft%20<10?-1:1 } });
A('w_restore',0,'contento',0,0,2,0,3,0,0,0,(S,ft)=>{ S.eyes='happy'; S.mouth='open'; S.armL=alt(ft,2)?'up':'up2'; S.armR=alt(ft,2)?'up2':'up'; S.dy=hop[ft%6]; S.ex.push(['heart',-5-Math.min(2,ft>>2),7,false]) });
A('w_book_open',0,'contento',0,0,3,0,2,0,0,0,(S,ft)=>{ S.look=1; S.eyes=ft%16<12?'open':'blink'; S.dy=(ft%12<2)?1:0; S.mouth='small'; bub(S,'book') });
A('w_book_close',0,'contento',0,0,1.6,0,1,0,0,0,(S,ft)=>{ S.armR=ft<8?'side':'none'; S.look=1; S.eyes=ft<10?'open':'happy'; S.mouth='smile' });
A('w_ignored',0,'contento',0,0,5,600,1,0,0,0,(S,ft)=>{ S.eyes='half'; S.mouth='small'; S.look=1; S.lookY=1; S.feet=(ft%20<4)?'kickA':'stand'; S.ex.push(['pebble',14,ft%20<4?20:22,true]) });

A('ti_morning',0,'contento',0,0,5,86400,3,0,0,0,(S,ft)=>{ if(ft<20){ S.eyes='sleep'; S.mouth=ft<4?'small':'yawn'; S.armL='up'; S.armR='up'; S.stretch=ft>4?1:0 } else { S.eyes='happy'; S.mouth='open'; bub(S,'sun') } });
A('ti_lunch',0,'contento',0,0,5,86400,1,0,0,0,(S,ft)=>{ S.sit=true; S.eyes='happy'; S.mouth=alt(ft,3)?'o':'small'; S.armR='up'; S.ex.push(['food',alt(ft,6)?6:7,19,false]); if(ft>34) S.mouth='cat' });
A('ti_evening',0,'cansado',0,0,5,86400,1,0,0,0,(S,ft)=>{ S.eyes=alt(ft,10)?'half':'sleep'; bub(S,'moon') });
A('ti_night',0,'cansado',0,0,6,3600,1,0,0,0,(S,ft)=>{ S.eyes='half'; S.look=0; S.armR='side'; S.ex.push(['cup',alt(ft,14)?8:7,14,false],[alt(ft,5)?'steam1':'steam2',alt(ft,14)?3:2,17,false]); if(ft>30) bub(S,'moon') });
A('ti_idle5',0,'contento',0,0,6,300,1,0,0,0,(S,ft)=>{ S.sit=true; S.look=ft<20?-1:ft<40?1:0; S.mouth='cat'; S.ex.push(['note',-4-(ft>>2)%6,19,false]) });
A('ti_idle20',0,'contento',0,0,6,1200,2,0,0,0,(S,ft)=>{ S.sit=true; if(ft<20){ S.eyes='half'; if(ft>=12) S.ex.push(['puff',12,-4,true],['puff',12,20,true]) } else if(ft<30){ S.eyes='sleep'; S.cap=ft>24; S.ex.push(['bed',4,-4,true]) } else { S.eyes='sleep'; S.cap=true; S.mouth='small'; S.ex.push(['bed',4,-4,true]); zs(S,ft,15); S.stretch=alt(ft,12) } });
A('ti_return',0,'dormido',0,0,3.5,0,3,0,0,0,(S,ft)=>{ S.cap=ft<8; S.eyes=ft<10?'sleep':'happy'; S.mouth=ft<10?'yawn':'open'; S.stretch=ft>=8&&ft<16?1:0; S.armL=ft>=8?'up':'none'; S.armR=ft>=8?(alt(ft,2)?'up':'up2'):'none'; S.bubble=-1; S.cheeks='C'; if(ft>=12) sparks(S,ft,3) });
A('ti_weekend',0,'radiante',0,0,6,3600,1,0,0,0,(S,ft)=>{ S.sit=true; S.eyes='happy'; S.mouth='cat'; S.armR='up'; S.ex.push(['fan',alt(ft,3)?5:6,14,false],['sunray',-9,-5,true]) });
A('sys_battery',0,'cansado',0,0,5,1800,2,0,0,0,(S,ft)=>{ S.eyes='half'; S.mouth='wavy'; S.stretch=-1; bub(S,'battery') });

A('fl_walk',0,'contento',0,0,7,0,1,0,0,0,(S,ft)=>{ if(ft<50){ POSE.walk(S,ft,2); S.dx=Math.round(lerp(-6,6,ft/50)) } else { S.dx=6; S.look=ft<60?-1:1 } },{walk:1});
A('fl_up',0,'contento',0,0,4,120,1,0,0,0,(S,ft)=>{ S.lookY=-1; S.eyes='open'; S.stretch=alt(ft,6); S.mouth='o'; if(ft>10) bub(S,'think') },{});

A('sf_top',0,'contento',0,0,8,0,1,0,0,0,(S,ft)=>{ POSE.walk(S,ft,2); S.dx=Math.round(lerp(-6,6,ft/80)); if(ft%40>30){ S.lookY=1; S.feet='stand' } S.ex.push(['edge',16,-4,true]) },{walk:1});
A('sf_other',0,'contento',0,0,8,0,1,0,0,0,(S,ft)=>{ POSE.walk(S,ft,2); S.dx=Math.round(lerp(6,-6,ft/80)); S.ex.push(['edge',16,-4,true]) },{walk:-1});
A('sf_edge',0,'contento',0,0,5,0,2,0,0,0,(S,ft)=>{ if(ft<20){ POSE.walk(S,ft,2); S.dx=ft>>2 } else if(ft<36){ S.dx=6; S.feet='stand'; S.eyes='wide'; S.mouth='o'; S.lookY=1; S.armL=alt(ft,1)?'up':'up2'; S.armR=alt(ft,1)?'up2':'up'; S.ex.push(['sweat',2,17,false]) } else { S.dx=6-((ft-36)>>2); S.eyes='happy'; S.stretch=-1 } S.ex.push(['edge',16,-4,true]) },{});
A('sf_sit',0,'contento',0,0,6,0,1,0,0,0,(S,ft)=>{ S.sit=true; S.feet='stand'; S.eyes=ft%36<32?'open':'blink'; S.look=ft<30?-1:1; S.mouth='cat'; S.leaf=alt(ft,8)?'up':'up2'; S.ex.push(['edge',16,-4,true]) },{});
A('sf_ride',0,'contento',0,0,3,0,3,0,0,0,(S,ft)=>{ S.eyes='wide'; S.mouth='small'; S.armL=alt(ft,3)?'side':'up'; S.armR=alt(ft,3)?'up':'side'; S.dx=alt(ft,2); S.ex.push(['edge',16,-4,true]) },{});
A('sf_fall_move',0,'contento',0,0,2.8,0,4,0,0,0,(S,ft)=>{ if(ft<5){ S.eyes='wide'; S.mouth='o'; S.ex.push(['bang',-6,15,false]) } else if(ft<20){ POSE.fall(S,ft); S.dy=-Math.min(5,Math.max(0,6-((ft-5)>>1))) } else { POSE.land(S,ft-20) } },{});
A('sf_fall_cover',0,'contento',0,0,2.8,0,4,0,0,0,(S,ft)=>{ if(ft<4){ S.eyes='wide'; S.mouth='o' } else if(ft<18){ POSE.fall(S,ft); S.dy=-Math.min(5,Math.max(0,6-((ft-4)>>1))) } else if(ft<24){ POSE.land(S,ft-18) } else { S.eyes='half'; S.mouth='pout'; S.look=-1 } },{});
A('sf_fall_close',0,'contento',0,0,2.8,0,4,0,0,0,(S,ft)=>{ if(ft<6){ S.eyes='wide'; S.mouth='o'; S.armL='up'; S.armR='up'; S.ex.push(['sweat',2,17,false]) } else if(ft<20){ POSE.fall(S,ft); S.dy=-Math.min(5,Math.max(0,6-((ft-6)>>1))) } else { POSE.land(S,ft-20) } },{});
A('sf_glide',0,'contento',0,0,3,0,3,0,0,0,(S,ft)=>{ POSE.glide(S,ft); S.dy=-Math.min(5,Math.max(0,9-(ft>>2))); S.dx=Math.round(Math.sin(ft/6)*2) },{});
A('sf_fly',0,'radiante',0,0,3,0,3,0,0,0,(S,ft)=>{ POSE.fly(S,ft); S.dy=-Math.min(5,ft>>1) },{});
A('sf_ladder',0,'contento',0,0,5,0,2,0,0,0,(S,ft)=>{ if(ft<6){ S.eyes='open'; S.armR='up'; S.look=-1; if(ft<4) S.ex.push(['puff',14,-6,true]) } else { S.look=-1; S.lookY=-1; S.eyes='open'; S.mouth=alt(ft,5)?'small':'o'; S.feet=alt(ft,2)?'walkA':'walkB'; S.stretch=alt(ft,3); ladOn(S,ft-6,-5,-1) } },{});
A('sf_climb',0,'contento',0,0,4,0,2,0,0,0,(S,ft)=>{ S.look=1; S.lookY=-1; S.eyes='open'; S.mouth=alt(ft,5)?'small':'o'; S.feet=alt(ft,2)?'walkA':'walkB'; S.stretch=alt(ft,3); S.ex.push(['wallv',-6,23,true]); ladOn(S,ft,16,1) },{});
A('sf_hang',0,'contento',0,0,4,0,2,0,0,0,(S,ft)=>{ if(ft<26){ POSE.hang(S,ft); S.dy=0; S.armL='none'; S.armR='none'; S.ex.push(['edge',-7,-4,true],['ladder',-7,-7,true],['hand',-7,1,false],['hand',-7,15,false],['harm',-5,2,false],['harm',-5,15,false]) } else { S.eyes='happy'; S.mouth='open'; S.dy=hop[(ft-26)%6]; S.ex.push(['edge',16,-4,true]) } },{});
A('sf_peek',0,'contento',0,0,4,0,1,0,0,0,(S,ft)=>{ S.lookY=1; S.dx=ft<28?(ft>>2):(7-((ft-28)>>1)); S.eyes='open'; S.mouth='o'; S.ex.push(['edge',16,-4,true]) },{});

const rot180=rows=>rows.slice().reverse().map(r=>[...r].reverse().join(''));
const BKO=[".UUUUUUUUUUU.","aWrrrWgWgggWa","aWLlLWgWWWWWa","aWWWWWgWgggWa","aFfFfFfRFfFfa",".UaaaaaaaaaU."];
Object.assign(SPR,{
  bkC:["UUUUF","UaaFf","IaIfF","UaIFf","IaafF","UffFf"],
  bkF:["UUUUF","UaaFf","IaIfF","UaIFf","UaaFf","IaafF","UaaFf","UffFf"],
  bkO:BKO,
  bkU:rot180(BKO),
  pg:["WWW","WgW"],
});
const lap=(S,up)=>{ S.sit=true; S.ex.push([up===false?'bkU':'bkO',11,2,false],['hand',13,0,false],['hand',13,16,false]) };
const scan=(S,ft,n)=>{ S.look=((ft/(n||3)|0)%3)-1; S.lookY=1 };
const flip=(S,ft,per)=>{ const q=ft%per; if(q>=per-4) S.ex.push(['pg',9-(q-(per-4))*2,6+(q-(per-4))*2,false]) };
const floorBook=(S,dx)=>S.ex.push(['bkF',8,21+(dx||0),true]);
A('bk_look',0,'contento',0,0,2,20,2,0,0,0,
 (S,ft)=>{ S.look=1; S.lookY=ft>=4?1:0; S.armR=ft>=4?'point':'none'; S.eyes=ft<14?'wide':'open'; S.mouth='small'; S.leaf=ft%12<6?'perk':'up'; if(ft>=4){ S.stretch=-1; bub(S,'book') } if(ft%10===6) S.ex.push(['twink',7,20,false]); floorBook(S) });
A('bk_present',0,'contento',0,0,2.4,0,3,0,0,0,
 (S,ft)=>{ S.look=1; S.armR='point'; S.eyes=ft<16?'happy':'open'; S.mouth='open'; S.cheeks='C'; S.leaf=ft%10<5?'up':'perk'; if(ft%12===6) S.ex.push(['twink',6,20,false]); floorBook(S) });
A('bk_pickup',0,'contento',0,0,4.5,600,2,0,0,0,
 (S,ft)=>{ if(ft<12){ POSE.walk(S,ft,2); S.look=1; S.mouth='smile'; floorBook(S) }
  else if(ft<18){ S.look=1; S.stretch=-1; S.armR=ft<15?'point':'up'; S.eyes='wide'; floorBook(S) }
  else if(ft<30){ const p=(ft-18)/11; S.armL='up'; S.armR='up'; S.look=1; S.eyes='happy'; S.ex.push(['bkC',Math.round(lerp(10,7,p)),Math.round(lerp(21,6,p)),false]) }
  else if(ft<38){ S.sit=true; S.armL='up'; S.armR='up'; S.lookY=1; S.eyes='open'; S.mouth='small'; S.ex.push(['bkC',8,6,false]) }
  else { lap(S,true); S.eyes='open'; S.mouth='small'; S.lookY=1 } },
 {amb:1,cd:600});
A('bk_read_up',0,'contento',0,0,6,0,2,0,0,0,
 (S,ft)=>{ lap(S,true); scan(S,ft,3); S.eyes=ft%40>37?'blink':'open'; S.mouth='small'; S.leaf=alt(ft,10)?'up':'up2'; flip(S,ft,24) },
 {});
A('bk_read_upside',0,'contento',0,0,6,0,2,0,0,0,
 (S,ft)=>{ if(ft<26){ lap(S,false); S.eyes='doubt'; S.lookY=1; S.mouth='wavy'; S.dx=(ft>>3)%2; if(ft>=6) bub(S,'think') }
  else if(ft<38){ S.sit=true; S.eyes='wide'; S.mouth='o'; const k=ft<30?'bkU':ft<34?'bkC':'bkO', edge=k==='bkC'; S.ex.push([k,9,edge?6:2,false],['hand',11,edge?4:0,false],['hand',11,edge?11:16,false]) }
  else { lap(S,true); S.eyes='happy'; S.mouth='smile'; S.lookY=1; if(ft<46) S.ex.push(['twink',3,13,false]) } },
 {v:'bk_read_up'});
A('bk_read_squint',0,'contento',0,0,5,0,2,0,0,0,
 (S,ft)=>{ S.sit=true; S.dx=ft<10?0:1; S.lookY=1; S.eyes=ft<40?'flat':'half'; S.mouth=ft<40?'small':'pout'; const near=ft%20<10&&ft<40; S.ex.push([ 'bkO',near?9:11,2,false],['hand',near?11:13,0,false],['hand',near?11:13,16,false]); if(ft>=40) S.ex.push(['sweat',2,17,false]) },
 {v:'bk_read_up'});
A('bk_read_flip',0,'contento',0,0,4,0,2,0,0,0,
 (S,ft)=>{ lap(S,true); scan(S,ft,2); S.eyes='open'; S.mouth='open'; S.leaf=alt(ft,2)?'up2':'up'; flip(S,ft,8); S.cheeks='C' },
 {v:'bk_read_up'});
A('bk_read_aha',0,'contento',0,0,3.5,0,2,0,0,0,
 (S,ft)=>{ lap(S,true); if(ft<16){ scan(S,ft,3); S.eyes='open'; S.mouth='small' } else { S.lookY=0; S.eyes='wide'; S.mouth='o'; S.leaf='perk'; S.dy=alt(ft,3)?-1:0; bub(S,'idea') } },
 {v:'bk_read_up'});
A('bk_read_snooze',0,'agotado',0,0,7,0,2,0,0,0,
 (S,ft)=>{ lap(S,true); S.leaf='wilt'; if(ft<26){ S.eyes='half'; S.lookY=1; S.mouth='small'; S.look=((ft/6|0)%3)-1 } else if(ft<52){ S.eyes='sleep'; S.mouth='small'; S.stretch=-1; snot(S,ft,26); zs(S,ft,15) } else { S.eyes='wide'; S.mouth='o'; S.dy=-1; S.ex.push(['bang',-6,15,false]) } },
 {v:'bk_read_up'});
A('bk_drop',0,'contento',0,0,2.8,0,5,0,0,0,
 (S,ft)=>{ if(ft<10){ S.dy=ft<6?-2:-1; S.eyes='wide'; S.mouth='o'; S.armL='up'; S.armR='up'; S.cheeks='C'; if(ft<8) S.ex.push(['bang',-6,15,false]);
    if(ft<5) S.ex.push(['bkC',Math.round(lerp(7,-3,ft/4)),Math.round(lerp(6,12,ft/4)),false]);
    else S.ex.push(['bkC',Math.round(lerp(-3,10,(ft-5)/4)),Math.round(lerp(12,21,(ft-5)/4)),true]) }
  else { S.look=1; S.cheeks='C'; S.dx=ft<22?-alt(ft,1):0; S.eyes=ft%16>13?'blink':'wide'; S.mouth=ft<22?'wavy':'small'; S.armL=ft<22?(alt(ft,2)?'up':'up2'):'none'; S.armR=ft<22?(alt(ft,2)?'up2':'up'):'none'; S.ex.push(['sweat',2,17,false]); floorBook(S) } },
 {});
A('bk_putback',0,'contento',0,0,3.2,0,3,0,0,0,
 (S,ft)=>{ if(ft<4){ lap(S,true); S.lookY=1; S.eyes='open'; S.mouth='small' }
  else if(ft<6){ S.sit=true; S.armL='up'; S.armR='up'; S.ex.push(['bkC',8,6,false]); S.eyes='happy' }
  else if(ft<16){ const p=(ft-6)/9; S.stretch=-1; S.armL='up'; S.armR='up'; S.look=1; S.eyes='open'; S.ex.push(['bkC',Math.round(lerp(8,10,p)),Math.round(lerp(6,21,p)),false]) }
  else if(ft<24){ S.look=1; S.armR=ft%4<2?'point':'up'; S.eyes='happy'; S.mouth='smile'; if(ft%4===2) S.ex.push(['twink',7,19,false]); floorBook(S) }
  else { S.dx=-Math.min(3,ft-24); S.feet=alt(ft,2)?'walkA':'walkB'; S.look=1; S.eyes='happy'; S.mouth='smile'; floorBook(S,-Math.min(3,ft-24)) } });
A('bk_dodge',0,'contento',0,0,1.6,4,4,0,0,0,
 (S,ft)=>{ const dx=-Math.min(5,Math.round(ft/2.5)); S.dx=dx; S.look=1; S.eyes='wide'; S.mouth='o'; S.feet=alt(ft,1)?'walkA':'walkB'; S.cheeks='C'; if(ft<3) S.ex.push(['bang',-6,15,false]); floorBook(S,dx) },
 {});
A('bk_shoved',0,'contento',0,0,2.4,0,5,0,0,0,
 (S,ft)=>{ if(ft<3){ S.stretch=-1; S.eyes='sleep'; S.mouth='o'; S.cheeks='C'; S.ex.push(['puff',14,-4,true],['puff',14,18,true]) }
  else if(ft<9){ const h=[-2,-3,-3,-2,-1,0]; S.dy=h[ft-3]; S.dx=-Math.round((ft-3)*8/5); S.eyes='wide'; S.mouth='o'; S.armL='up'; S.armR='up'; S.look=1 }
  else { S.dx=-8; S.look=1; S.eyes=ft<15?'half':'open'; S.mouth='pout'; S.cheeks='C'; S.leaf=ft<18?'droop':'up'; if(ft<11) S.stretch=-1; floorBook(S,-8) } },
 {});
A('bk_fetch',0,'contento',0,0,5,0,3,0,0,0,
 (S,ft)=>{ if(ft<10){ S.look=1; S.eyes='wide'; S.mouth='o'; S.leaf='perk'; S.armR='up'; if(ft<3) S.ex.push(['bang',-6,15,false]) }
  else if(ft<34){ POSE.walk(S,ft,1); S.dx=Math.round(lerp(0,3,(ft-10)/23)); S.mouth='open'; S.eyes='happy'; S.ex.push(['speed',6,-3,false]) }
  else { S.sit=true; S.look=1; S.eyes='happy'; S.mouth='open'; S.cheeks='C'; S.leaf=alt(ft,4)?'up2':'up'; if(ft%12===6) S.ex.push(['twink',5,20,false]); floorBook(S) } },
 {});
A('bk_pat',0,'contento',0,0,3,240,2,0,0,0,
 (S,ft)=>{ S.look=1; S.armR=ft%10<5?'point':'up'; S.eyes='happy'; S.mouth='smile'; S.leaf=alt(ft,10)?'up':'up2'; if(ft%10===5) S.ex.push(['twink',7,20,false]); floorBook(S) },
 {amb:1,cd:240});
A('bk_sitnext',0,'contento',0,0,6,300,1,0,0,0,
 (S,ft)=>{ S.sit=true; S.look=ft%40<26?1:0; S.eyes=ft%36>33?'blink':'open'; S.mouth='smile'; S.leaf=alt(ft,15)?'perk':'up'; if(ft===34) S.ex.push(['twink',5,19,false]); floorBook(S) },
 {amb:1,cd:300});
A('bk_guard',0,'agotado',0,0,7,600,1,0,0,0,
 (S,ft)=>{ S.sit=true; S.leaf='wilt'; S.look=1; if(ft<14){ S.eyes='half'; S.mouth='small' } else { S.eyes='sleep'; S.mouth='small'; S.stretch=-1; snot(S,ft,14); zs(S,ft,15) } floorBook(S) },
 {amb:1,cd:600});

const deskAmb=(S,ft,n,act)=>{
  if(ft<5){ S.dx=Math.round(lerp(0,-7,ft/5)); S.look=1; S.armR='up'; return }
  S.sit=true; S.dx=-7; S.look=1;
  if(ft<8){ S.armR='up'; if(ft<7) S.ex.push(['puff',10,13,true],['puff',12,19,true]); else deskPut(S); return }
  const k=ft-(n-8);
  if(k>=0){ if(k<6) deskPut(S); if(k<2) lapPut(S,'lp_mid'); else if(k<4) lapPut(S,'lp_half'); else if(k<6) lapPut(S,'lp_closed'); else S.ex.push(['puff',10,13,true],['puff',12,19,true]); return }
  deskPut(S); act(S,ft-8) };
A('am_rope',0,'radiante',0,0,7,120,1,0,0,0,
 (S,ft)=>{ const q=ft%6; S.eyes=q===3||q===4?'happy':'open'; S.mouth=q>=2&&q<=4?'open':'smile'; S.armL='side'; S.armR='side'; S.dy=[0,0,-1,-2,-2,-1][q]; S.leaf=q<3?'up':'perk';
  if(ft<6){ S.dy=0; S.eyes='open' } else S.ex.push([['jrU','jrU','jrM','jrD','jrD','jrM'][q], q===0||q===1?-5:(q===2||q===5?10:9), -1, true]) });
A('am_cookies',0,'contento',0,0,8,120,1,0,0,0,
 (S,ft)=>{ S.sit=true; S.dx=-7; S.look=1; S.ex.push(['plate',11,11,true],['milk',10,21,true]); const q=ft%32;
  if(q<6){ S.armR='side'; S.eyes='open'; S.mouth='smile' }
  else { S.armR='up'; S.eyes=q>=14?'happy':'open'; const bite=q<14?0:q<20?1:q<26?2:3; S.mouth=alt(q,2)&&bite?'open':'small';
   if(bite<3) S.ex.push([['cookie1','cookie1','cookie2','cookie3'][bite],7,18,false]);
   if(q>=14&&q<30) S.ex.push(['crumb',13-((31-q)>>2),19,false]) } });
A('am_code',0,'contento',0,0,11,180,1,0,0,0,
 (S,ft)=>deskAmb(S,ft,110,(S,t)=>{ const stop=t%40>=28&&t%40<34; lapPut(S,'lp_code'+(stop?0:(t>>2)%12)); S.lookY=1; S.eyes=t%50<2?'blink':'open';
  if(stop){ S.armR='up'; S.mouth='o'; S.leaf=alt(t,3)?'up':'up2'; S.look=0 } else { S.mouth='small'; typing(S,t,1) } }));
A('am_pc',0,'contento',0,0,10,180,1,0,0,0,
 (S,ft)=>deskAmb(S,ft,100,(S,t)=>{ lapPut(S,'lp_pic'+(alt(t,6))); S.lookY=1; S.eyes='happy'; S.mouth=t%30>=18&&t%30<26?'open':'smile'; S.cheeks='C'; S.ex.push(['hand',ft%32<16?10:9,22,true]); if(t%30>=18&&t%30<26) S.dy=alt(t,2)?-1:0 }));
A('am_tea',0,'radiante',0,0,8,120,1,0,0,0,
 (S,ft)=>{ S.sit=true; S.ex.push(['puf',12,-3,true]); const q=ft%40, sip=q>=20&&q<30; S.armR=sip?'up':'side'; S.eyes=sip||q>=30?'happy':'open'; S.mouth=q>=30&&q<36?'o':'cat';
  S.ex.push(['cup',sip?4:(alt(ft,12)?7:8),sip?12:14,false]); if(!sip) S.ex.push([alt(ft,5)?'steam1':'steam2',alt(ft,12)?2:3,17,false]); if(q>=30&&q<36) S.ex.push(['puff',9+((q-30)>>1),-3,false]) },
 {v:'t_all'});
A('am_bed',0,'contento',0,0,10,300,1,0,0,0,
 (S,ft)=>{ if(ft<8){ S.eyes='half'; S.mouth=ft<5?'small':'yawn'; S.armL='up'; S.armR='up'; S.stretch=1 }
  else if(ft<12){ S.sit=true; S.eyes='sleep'; S.ex.push(['puff',12,-4,true],['puff',12,20,true]) }
  else if(ft<86){ S.sit=true; S.eyes='sleep'; S.cap=ft>16; S.mouth='small'; S.ex.push(['bed',4,-4,true]); zs(S,ft,15); S.stretch=alt(ft,12) }
  else if(ft<94){ S.sit=true; S.cap=false; S.eyes=ft<90?'half':'open'; S.mouth='yawn'; S.armL='up'; S.armR='up'; S.ex.push(['bed',4,-4,true]) }
  else { S.sit=ft<98; S.eyes='happy'; S.mouth='open'; S.ex.push(['puff',12,-4,true],['puff',12,20,true]) } });
A('bk_puf',0,'contento',0,0,7,0,2,0,0,0,
 (S,ft)=>{ if(ft<6){ S.look=-1; S.armR='up'; S.ex.push(['puff',13,-2,true],['puff',14,19,true]); return } S.ex.push(['puf',12,-3,true]); lap(S,true); scan(S,ft,4); S.eyes=ft%40>37?'blink':'open'; S.mouth='small'; S.dx=(ft>>4)%2; S.leaf=alt(ft,10)?'up':'up2'; flip(S,ft,22) },
 {v:'bk_read_up'});
const FIX=(id,o)=>{ const e=CAT.find(x=>x.id===id); if(!e) throw new Error('FIX '+id); Object.assign(e,o) };
const CONF=['to confirm'];

FIX('t_done',{});
FIX('att_1',{});
FIX('att_cap',{state:1});
FIX('att_rm',{});
FIX('c_needs',{});
FIX('c_perm',{});
FIX('t_stale',{att:0,cd:15});
FIX('c_answered',{});

['pc_type','pc_read','pc_search','pc_run','pc_think','pc_sleep'].forEach(i=>FIX(i,{v:'pc_out'}));
FIX('pc_out',{});
FIX('pc_think',{});
FIX('pc_sleep',{});

FIX('ag_dust',{v:'ag_walk'});FIX('ag_catch',{v:'ag_walk'});
FIX('ag_walk',{});
FIX('ag_bubbles',{});
FIX('ag_nod_off',{});
FIX('q_h5_wipe',{});
FIX('q_h5_fan',{});
FIX('ti_idle20',{});
FIX('w_tickle',{});
FIX('w_follow',{});
FIX('w_land',{dur:0.5});
FIX('t_streak',{});
FIX('tr_5h',{});
FIX('ti_night',{cd:14400});FIX('ti_weekend',{cd:86400});
['ti_morning','ti_idle5','ti_idle20','ti_return','sys_battery','ti_lunch','ti_evening','ti_night'].forEach(i=>FIX(i,{}));
['ti_lunch','ti_evening','ti_night'].forEach(i=>FIX(i,{}));
FIX('fl_walk',{});

['ag_walk','ag_bubbles','ag_nod_off','ag_catch','ag_dust','ag_lean','q_h5_wipe','q_h5_fan','q_watch','q_runout','t_all','am_rope','am_cookies','am_code','am_pc','am_tea','am_bed','c_wait','ti_morning','ti_lunch','ti_evening','ti_night','ti_idle5','ti_weekend','w_ignored','fl_walk','sf_top','sf_other','sf_sit','sf_peek','sf_fly','sf_ladder','sf_climb'].forEach(i=>FIX(i,{amb:1}));

FIX('sf_top',{cd:90});FIX('sf_other',{cd:90});FIX('sf_edge',{cd:60});FIX('sf_sit',{cd:120});FIX('sf_peek',{cd:120});FIX('sf_fly',{cd:300});FIX('sf_ladder',{cd:300});FIX('sf_climb',{cd:180});

['ag_lean', 'tr_week', 'q_watch', 'q_runout', 't_new', 't_done', 't_all', 't_stale', 'c_needs', 'c_perm', 'c_wait', 'att_1', 'att_2', 'att_3', 'att_cap', 'att_rm', 'pc_type', 'pc_read', 'pc_search', 'w_book_open', 'ti_morning', 'ti_evening', 'ti_night', 'sys_battery', 'fl_up', 'sf_ladder', 'sf_climb', 'sf_hang', 'bk_look', 'bk_read_upside', 'bk_read_aha'].forEach(i=>FIX(i,{}));

['att_1','att_2','att_3','att_cap','att_rm','c_needs','c_perm','t_done','t_new','bk_read_aha','bk_read_upside','fl_up','c_wait','t_all','ti_idle20','pc_out','pc_type','pc_read','pc_search','pc_run','pc_think','pc_away','pc_sleep'].forEach(i=>{ const e=CAT.find(x=>x.id===i); if(e) e.rev=0 });
['am_rope','am_cookies','am_code','am_pc','am_tea','am_bed','bk_puf'].forEach(i=>FIX(i,{}));

['att_1','att_2','att_3','att_cap','att_rm','c_needs','c_perm','bk_read_upside','bk_look','bk_present','bk_pickup','bk_read_up','bk_read_squint','bk_read_flip','bk_read_aha','bk_read_snooze','bk_drop','bk_putback','bk_dodge','bk_shoved','bk_fetch','bk_pat','bk_sitnext','bk_guard','t_all','am_tea','am_cookies','am_rope','ti_night','ti_weekend','sf_ladder','sf_climb','sf_hang'].forEach(i=>FIX(i,{}));
['bk_puf','q_h5_fan'].forEach(i=>FIX(i,{}));

['c_needs','c_perm','att_3','bk_read_upside','t_all','am_tea','am_bed','ti_idle20','bk_puf','q_h5_fan','sf_ladder','sf_climb','sf_hang','ag_grumble'].forEach(i=>FIX(i,{}));
['ag_annoyed','ag_angry','ag_flee','ag_calm','ag_return'].forEach(i=>FIX(i,{}));
FIX('ag_grumble',{});
FIX('ag_flee',{});
FIX('ag_calm',{});
FIX('ag_return',{});
FIX('ag_annoyed',{});
FIX('ag_angry',{});
root.PERSONALITY={CAT,POSE,CONF};
})(typeof globalThis!=='undefined'?globalThis:this);
