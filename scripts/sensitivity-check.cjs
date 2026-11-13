const assert=require("node:assert/strict"),{roundedEnvelope,longitudeNear,endpointRegion,elapsedTicks}=require("../frontend/sensitivity.js");
const cross=(a,b,p)=>(b[0]-a[0])*(p[1]-a[1])-(b[1]-a[1])*(p[0]-a[0]);
for(const centers of [[[0,0],[60,0]],[[0,0],[60,0],[22,40]],[[0,0],[60,0],[60,40],[0,40]],[[0,0],[0,0]]]){
  const shape=roundedEnvelope(centers,8);
  assert(shape.length>=32);
  for(const [x,y] of centers)for(let d=0;d<360;d++){
    const r=d*Math.PI/180,p=[x+8*Math.cos(r),y+8*Math.sin(r)];
    assert(shape.every((q,i)=>cross(q,shape[(i+1)%shape.length],p)>=-1e-8));
  }
}
assert.deepEqual(roundedEnvelope([[1,1]],8),[]);
assert.equal(longitudeNear(-179,179),181);
console.log("Sensitivity outline encloses two, three, four and coincident member circles.");

const ring=endpointRegion({latitude:52,longitude:179.95},[{latitude:52.04,longitude:-179.99},{latitude:52.01,longitude:-179.98}]);
assert.ok(Math.max(...ring.map(p=>p[1]))-Math.min(...ring.map(p=>p[1]))<.2);
assert.ok(ring.every((a,i)=>cross([a[1],a[0]],[ring[(i+1)%ring.length][1],ring[(i+1)%ring.length][0]],[179.95,52])>=-1e-9));
assert.deepEqual(elapsedTicks(83*60),[0,900,1800,2700,3600,4500]);
assert.deepEqual(elapsedTicks(28*60),[0,300,600,900,1200,1500]);
console.log("Fixed region contains the nominal endpoint at the date line; elapsed ticks use round intervals.");
