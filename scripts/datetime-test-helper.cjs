exports.fillDateTime=async(page,id,value)=>{
 const [date,time='']=value.split('T');
 await page.fill('#'+id+'-date',date);await page.fill('#'+id+'-time',time);
 await page.locator('#'+id+'-time').dispatchEvent('change');
};
