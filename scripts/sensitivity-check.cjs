const assert=require("node:assert/strict"),{roundedEnvelope,longitudeNear}=require("../frontend/sensitivity.js");
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
