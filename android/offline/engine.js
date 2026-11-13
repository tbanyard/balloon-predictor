/* Offline counterpart of the server's regular-grid interpolation and midpoint flight solver. */
(function(root){
  "use strict";
  const R=6371008.8,RD=287.05,G=9.80665,RHO=101325/(RD*288.15),RAD=Math.PI/180;
  const names=["height","u","v","temperature","pressure","w","surface_u","surface_v","surface_temperature","surface_pressure","terrain"];
  const wrap=lon=>((lon+180)%360+360)%360-180;
  const near=(lon,ref)=>ref+wrap(lon-ref);
  function decode(buffer){
    if(buffer.byteLength<12||buffer.byteLength>32*1024**2+65544)throw Error("Forecast pack is empty or exceeds 32 MiB.");
    const bytes=new Uint8Array(buffer),view=new DataView(buffer);
    if(String.fromCharCode(...bytes.subarray(0,4))!=="BPF1")throw Error("Choose a BalloonPredictor .bpforecast file.");
    const n=view.getUint32(4,true);
    if(n>65536||n+8>bytes.length)throw Error("Invalid forecast header.");
    const info=JSON.parse(new TextDecoder("utf-8",{fatal:true}).decode(bytes.subarray(8,8+n)));
    if(info.format!=="BalloonPredictor offline forecast 1"||typeof info.label!=="string"||!info.setup||!Array.isArray(info.fields))
      throw Error("This forecast format is not supported.");
    for(const [axis,max] of [[info.lat,2048],[info.lon,2048],[info.time,400]]){
      if(!Array.isArray(axis)||axis.length<2||axis.length>max||!axis.every((v,i)=>Number.isFinite(v)&&(!i||v>axis[i-1])))throw Error("Invalid forecast grid or model times.");
    }
    const ny=info.lat.length,nx=info.lon.length,nl=info.levels,nt=info.time.length,fields=Object.create(null);
    if(!Number.isInteger(nl)||nl<2||nl>300||info.lat[0]<-90||info.lat.at(-1)>90||info.lon.at(-1)-info.lon[0]>360||
       !Number.isFinite(info.gap_seconds)||info.gap_seconds<=0)throw Error("Invalid forecast dimensions.");
    let offset=8+n;
    for(const spec of info.fields){
      const i=names.indexOf(spec.name);
      const shape=i<6?[nt,nl,ny,nx]:i<10?[nt,ny,nx]:[ny,nx];
      if(i<0||fields[spec.name]||String(spec.shape)!==String(shape))throw Error("Invalid forecast field.");
      const count=shape.reduce((a,b)=>a*b,1);
      if(offset+count*4>bytes.length)throw Error("Incomplete forecast data.");
      // The JSON header need not end on a four-byte boundary.
      const field=new Float32Array(count);
      for(let j=0;j<count;j++,offset+=4)field[j]=view.getFloat32(offset,true);
      fields[spec.name]=field;
    }
    if(offset!==bytes.length||names.slice(0,5).some(k=>!fields[k]))throw Error("Required forecast fields are missing.");
    return {info,fields,ny,nx,nl,nt};
  }

  function bracket(axis,value,what){
    if(value<axis[0]-1e-7||value>axis.at(-1)+1e-7)throw Error(what+" is outside the saved forecast. Use a wider or newer pack.");
    const v=Math.min(axis.at(-1),Math.max(axis[0],value));
    let lo=0,hi=axis.length-1;
    while(hi-lo>1){const mid=(lo+hi)>>1;if(axis[mid]<=v)lo=mid;else hi=mid;}
    const f=(v-axis[lo])/(axis[hi]-axis[lo]);
    return [[lo,1-f],[hi,f]].filter(p=>p[1]>1e-12);
  }
  function horizontal(pack,lat,lon){
    const {info}=pack,ref=(info.lon[0]+info.lon.at(-1))/2;
    return bracket(info.lat,lat,"Latitude").flatMap(([y,wy])=>bracket(info.lon,near(lon,ref),"Longitude").map(([x,wx])=>[y*pack.nx+x,wx*wy]));
  }
  function column(pack,ti,i,alt,policy){
    const {fields:f,nx,ny,nl}=pack,area=nx*ny,ground=f.terrain?.[i]??-Infinity,si=ti*area+i;
    const sp=f.surface_pressure?.[si],levels=[];
    for(let l=0;l<nl;l++){
      const j=(ti*nl+l)*area+i,h=f.height[j],u=f.u[j],v=f.v[j],t=f.temperature[j],p=f.pressure[j];
      if(h>ground+.1&&[h,u,v,t,p].every(Number.isFinite)&&t>0&&p>0&&!(sp>0&&p>sp))
        levels.push([h,u,v,t,p,f.w?.[j]??0]);
    }
    if(levels.length<2)throw Error("Fewer than two valid air levels in the saved forecast.");
    levels.sort((a,b)=>a[0]-b[0]);
    let points=levels.filter((p,j)=>!j||p[0]-levels[j-1][0]>1e-4);
    const surf=[ground+10,f.surface_u?.[si],f.surface_v?.[si],f.surface_temperature?.[si],sp,0];
    if(surf.every(Number.isFinite)&&surf[3]>0&&surf[4]>0&&points.some(p=>p[0]>surf[0]+.1))
      points=[surf,...points.filter(p=>p[0]>surf[0]+.1)];
    const anchor=points[0][0],depth=anchor-alt;
    if(alt>points.at(-1)[0]+.5)throw Error("The trajectory exceeds the top of the saved forecast.");
    if(depth>1e-6&&policy==="strict")throw Error("The trajectory is below the lowest valid model air.");
    if(depth>3000)throw Error("The near-surface estimate would extend more than 3 km below the model.");
    if(alt<=anchor){
      const result=points[0].slice(1);
      if(depth>1e-6){
        const t=result[2]+.0065*depth;
        result[3]*=(t/result[2])**(G/(RD*.0065));result[2]=t;
      }
      return result;
    }
    let lo=0,hi=points.length-1;
    while(hi-lo>1){const mid=(lo+hi)>>1;if(points[mid][0]<=alt)lo=mid;else hi=mid;}
    const a=points[lo],b=points[hi],weight=Math.min(1,(alt-a[0])/(b[0]-a[0]));
    return a.slice(1).map((v,j)=>j===3?Math.exp(Math.log(v)*(1-weight)+Math.log(b[j+1])*weight):
      weight===0?v:weight===1?b[j+1]:v*(1-weight)+b[j+1]*weight);
  }
  function sample(pack,lat,lon,alt,time,policy){
    const timeWeights=bracket(pack.info.time,time,"Time");
    if(timeWeights.length===2&&pack.info.time[timeWeights[1][0]]-pack.info.time[timeWeights[0][0]]>pack.info.gap_seconds)
      throw Error("The saved forecast has a gap at this time.");
    const result=[0,0,0,0,0];
    for(const [i,w] of horizontal(pack,lat,lon))for(const [t,wt] of timeWeights)
      column(pack,t,i,alt,policy).forEach((v,j)=>result[j]+=w*wt*v);
    return result;
  }
  function terrain(pack,s){
    if(!pack.fields.terrain)throw Error("This forecast has no model terrain. Choose a known fixed landing height.");
    const height=horizontal(pack,s[0],s[1]).reduce((sum,[i,w])=>sum+w*pack.fields.terrain[i],0);
    if(!Number.isFinite(height))throw Error("Model terrain is missing here. Choose a known fixed landing height.");
    return height;
  }
  function fly(pack,request){
    const q=request,stamp=Date.parse(q.launch_datetime)/1000;
    if(!Number.isFinite(stamp)||!Number.isFinite(q.launch_latitude)||Math.abs(q.launch_latitude)>85||
      !Number.isFinite(q.launch_longitude)||!Number.isFinite(q.launch_altitude)||q.launch_altitude<-500||
      !(q.burst_altitude>q.launch_altitude&&q.burst_altitude<=55000&&q.burst_altitude>0)||
      !(q.ascent_rate>0&&q.ascent_rate<=30&&q.descent_rate>0&&q.descent_rate<=50))throw Error("Check the launch coordinates, altitude and flight rates.");
    if(q.ground_source==="dem"||q.flight_mode==="full"&&q.landing_mode==="dem")throw Error("Physical DEM is available in server mode. Choose model terrain or a known fixed elevation offline.");
    if(q.use_vertical_wind&&!pack.fields.w)throw Error("The saved forecast has no vertical wind.");
    if(q.flight_mode==="full"&&q.landing_mode==="fixed"&&!(q.landing_altitude<q.burst_altitude))throw Error("Landing height must be below burst height.");
    const limit=Math.min(48,q.max_duration_hours||12)*3600,step=Math.max(1,Math.min(60,q.time_step||10));
    let s=[q.launch_latitude,q.launch_longitude,q.launch_altitude],t=0,phase="ascent",burst=null;
    const ground=s=>q.landing_mode==="fixed"?q.landing_altitude:terrain(pack,s);
    const launchGround=q.ground_source==="model"?terrain(pack,s):q.ground_altitude;
    if(launchGround!=null&&s[2]<launchGround-1)throw Error("Launch altitude is below the selected ground.");
    const point=(v,t)=>({latitude:v[0],longitude:wrap(v[1]),altitude:v[2],elapsed_seconds:t,datetime:new Date((stamp+t)*1000).toISOString()});
    const paths={ascent:[point(s,0)],descent:[]};
    function velocity(v,dt){
      if(Math.abs(v[0])>85)throw Error("Latitude is outside the supported range.");
      const [u,n,temp,pressure,w]=sample(pack,...v,stamp+dt,q.below_model_policy);
      const vertical=(phase==="ascent"?q.ascent_rate:-q.descent_rate*(q.descent_model==="density"?Math.sqrt(RHO/(pressure/(RD*temp))):1))+(q.use_vertical_wind?w:0);
      if(!Number.isFinite(vertical))throw Error("Vertical wind is missing here.");
      return [n/(R+v[2])/RAD,u/((R+v[2])*Math.cos(v[0]*RAD))/RAD,vertical];
    }
    function advance(s,t,dt){
      const first=velocity(s,t),middle=s.map((v,i)=>v+first[i]*dt/2),next=velocity(middle,t+dt/2);
      return s.map((v,i)=>v+next[i]*dt);
    }
    function finish(landing){
      return {weather:pack.info.label,launch:paths.ascent[0],burst,landing,request:q,
        prediction:Object.entries(paths).filter(([,p])=>p.length).map(([stage,trajectory])=>({stage,trajectory}))};
    }
    for(let iterations=0;t<limit-1e-6&&iterations<180000;iterations++){
      const speed=velocity(s,t)[2];let dt=Math.min(step,limit-t);
      if(phase==="ascent"){
        if(speed<=0)throw Error("Vertical wind prevents ascent.");
        dt=Math.min(dt,Math.max(1e-4,(q.burst_altitude-s[2])/speed));
      }else{
        if(speed>=0)throw Error("Vertical wind prevents descent.");
        const remaining=s[2]-ground(s);
        if(remaining>0)dt=Math.min(dt,Math.max(1e-4,1.5*remaining/-speed));
      }
      const crossed=v=>phase==="ascent"?v[2]>=q.burst_altitude:v[2]<=ground(v);
      let next=advance(s,t,dt);
      const event=phase==="ascent"?next[2]>=q.burst_altitude-1e-5:crossed(next);
      if(event){
        let lo=0,hi=dt;
        for(let i=0;i<30;i++){const mid=(lo+hi)/2;if(crossed(advance(s,t,mid)))hi=mid;else lo=mid;}
        dt=hi;next=advance(s,t,dt);next[2]=phase==="ascent"?q.burst_altitude:ground(next);
      }
      sample(pack,...next,stamp+t+dt,q.below_model_policy);
      t+=dt;s=next;paths[phase].push(point(s,t));
      if(event){
        if(phase==="descent")return finish(paths.descent.at(-1));
        burst=paths.ascent.at(-1);
        if(q.flight_mode==="ascent")return finish(null);
        phase="descent";paths.descent.push(burst);
      }
    }
    throw Error("The flight did not reach its endpoint within the saved duration.");
  }
  const api={decode,fly};
  if(typeof module!=="undefined"&&module.exports)module.exports=api;
  else root.BPOffline=api;
})(globalThis);
