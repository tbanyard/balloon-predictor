importScripts("engine.js");
let forecast;
self.onmessage=event=>{
  const {id,action,data}=event.data;
  try{
    if(action==="load"){forecast=BPOffline.decode(data);postMessage({id,info:forecast.info});}
    else if(action==="fly"&&forecast)postMessage({id,flight:BPOffline.fly(forecast,data)});
    else throw Error("Open a forecast pack first.");
  }catch(error){postMessage({id,error:error.message});}
};
