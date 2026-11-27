/* Behavioural checks for geographic sampling and earth-relative animation. */
const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict'),path=require('path');
const context={window:{}};vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../frontend/weather-overlay.js'),'utf8'),context);const w=context.window.BalloonWeather;
const data={grid:{latitude:[0,1],longitude:[179,181],u:[[0,10],[20,30]],v:[[1,null],[3,4]]}};
assert.equal(w.sampleGrid(data,.5,-180,['u'])[0],15);assert.equal(w.sampleGrid(data,.5,180,['u'])[0],15);assert.equal(w.sampleGrid(data,.5,540,['u'])[0],15);
assert.equal(w.sampleGrid(data,0,179,['v'])[0],1);assert.equal(w.sampleGrid(data,.5,180,['v']),null);assert.equal(w.sampleGrid(data,-.01,180,['u']),null);assert.equal(w.sampleGrid(data,.5,178,['u']),null);
for(const lat of [-85,-60,0,45,85])assert.ok(Math.abs(w.inverseMercator(w.mercator(lat))-lat)<1e-10);
const a=w.step(0,0,10,0,1),b=w.step(60,0,10,0,1),c=w.step(0,0,0,-10,1),d=w.step(0,0,-10,0,1);
assert.ok(Math.abs(a[1]*Math.PI/180*6371008.8-10)<1e-9);assert.ok(Math.abs(b[1]/a[1]-2)<1e-12);assert.ok(c[0]<0&&c[1]===0&&d[1]<0&&d[0]===0);
const palette=[[0,10,20],[100,110,120],[200,210,220]];assert.deepEqual(Array.from(w.colour(palette,0)),palette[0]);assert.deepEqual(Array.from(w.colour(palette,.5)),palette[1]);assert.deepEqual(Array.from(w.colour(palette,1)),palette[2]);
const values=Array.from({length:100},(_,i)=>i).concat([100000]);assert.equal(w.quantile(values,.98),98);assert.equal(w.quantile([0,10],.5),5);
const range=w.niceRange(-6.2,12.3);assert.ok(range[0]<=-6.2&&range[1]>=12.3);const signed=w.niceRange(-3,8,true);assert.equal(signed[0],-signed[1]);
const fake={getBounds:()=>({getNorth:()=>60,getSouth:()=>50,getWest:()=>170,getEast:()=>190}),getCenter:()=>({lng:180})},buffer=w.bufferedBounds(fake);assert.ok(w.containsView(buffer,fake));assert.ok(buffer[0]>60&&buffer[2]<50&&Math.abs(buffer[3]-buffer[1]-36)<1e-6);
const world={getBounds:()=>({getNorth:()=>90,getSouth:()=>-90,getWest:()=>-400,getEast:()=>400}),getCenter:()=>({lng:0})};const all=w.bufferedBounds(world);assert.equal(all[0],85);assert.equal(all[2],-85);assert.ok(all[3]-all[1]<=360+1e-8&&w.containsView(all,world));
assert.equal(w.regional({area:[55,-6,51,-2]}),true);assert.equal(w.regional({area:[85,-180,-85,180]}),false);
console.log('Weather geometry: PASS · geographic/date-line interpolation, missing-cell masks, exact boundary samples, Mercator coordinates, physical wind direction/distance and colour scale.');

assert.equal(w.animationFactor(10),1);assert.equal(w.animationFactor(9),1);assert(w.animationFactor(7)>2&&w.animationFactor(7)<4);assert.equal(w.animationFactor(0),8);for(let z=1;z<9;z++)assert(w.animationFactor(z)>=w.animationFactor(z+1));
console.log('Wind animation zoom boost: bounded, smooth and stronger at wide zoom.');
