package io.github.tbanyard.balloonpredictor;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Intent;
import android.os.Bundle;
import android.os.SystemClock;
import android.view.View;
import android.webkit.WebView;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.BooleanSupplier;
import java.util.function.Supplier;

public final class FlightTest extends Instrumentation {
    Bundle args;
    @Override public void onCreate(Bundle options){super.onCreate(options);args=options;start();}
    @Override public void onStart(){
        Bundle result=new Bundle();
        try{
            MainActivity app=(MainActivity)startActivitySync(new Intent(Intent.ACTION_MAIN)
                .setClassName(getTargetContext(),MainActivity.class.getName()).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            waitFor(()->onMain(()->app.form.getVisibility()==View.VISIBLE),"connection screen");
            check(onMain(()->app.localChoice.getVisibility()==View.VISIBLE&&app.serverChoice.getVisibility()==View.VISIBLE),"Missing large Local/Server choices");
            check(onMain(()->app.localChoice.getTop()==app.serverChoice.getTop()&&app.localChoice.getHeight()==app.serverChoice.getHeight()),"Local/Server cards are offset or unequal");
            check(onMain(()->app.localChoice.getCompoundDrawables()[1].getBounds().height()==app.serverChoice.getCompoundDrawables()[1].getBounds().height()),"Connection icons are unequal");
            check(onMain(()->app.localChoice.getStateListAnimator()==null&&app.serverChoice.getStateListAnimator()==null),"Choice elevation animation causes inconsistent shadows");
            check(onMain(()->app.localChoice.getElevation()==app.serverChoice.getElevation()),"Choice shadows differ");
            AtomicInteger trusted=new AtomicInteger(),authenticated=new AtomicInteger();
            runOnMainSync(()->{
                app.serverChoice.performClick();
                app.mode.setSelection(1);app.host.setText("10.0.2.2");app.username.setText("fixture");
                app.sshPort.setText(args.getString("ssh_port"));app.webPort.setText(args.getString("web_port"));
                app.connect(new ServerSession.Prompts(){
                    public String pin(String e){return null;}
                    public void savePin(String e,String p){}
                    public boolean trust(String e,String p){trusted.incrementAndGet();return p.equals(args.getString("fingerprint"));}
                    public String[] credentials(String m,String[] p,boolean[] echo){
                        authenticated.incrementAndGet();return p.length==1&&!echo[0]?new String[]{"fixture-password"}:null;
                    }
                });
            });
            waitFor(()->onMain(()->app.web!=null),"SSH WebView");
            check(trusted.get()==1&&authenticated.get()>=1,"SSH did not verify the key and ask for a password");
            WebView original=onMain(()->app.web);
            check("true".equals(js(original,"navigator.userAgent.includes('BalloonPredictor/')")),"Missing app User-Agent identification");
            waitFor(()->"true".equals(js(original,"Boolean(window.L && document.querySelector('#predict-button')?.disabled===false)")),"flight controls");
            check("true".equals(js(original,"document.documentElement.dataset.host==='android' && getComputedStyle(document.querySelector('.topbar')).display==='none'")),"Duplicate Android header is visible");
            check(onMain(()->app.weatherButton.getVisibility()==View.VISIBLE),"Missing native Weather action");
            runOnMainSync(()->app.weatherButton.performClick());
            waitFor(()->"true".equals(js(original,"document.querySelector('#weather-dialog').open")),"native Weather action");
            js(original,"document.querySelector('#rescan-weather').click()");
            waitFor(()->"true".equals(js(original,"document.querySelector('#rescan-status').textContent.startsWith('Server data index refreshed')")),"rescan through SSH");
            js(original,"document.querySelector('#weather-close').click()");
            check("true".equals(js(original,"document.querySelector('#launch_datetime-date').type==='text' && document.querySelector('#launch_datetime-time').type==='text' && !!document.querySelector('#launch_datetime-calendar')")),"Separate editable date/time controls are missing");
            String expected=args.getString("workspace_user");
            js(original,"fetch('/api/storage').then(r=>r.json()).then(v=>window.__workspaceUser=v.workspace_user)");
            waitFor(()->("\""+expected+"\"").equals(js(original,"window.__workspaceUser||''")),"verified personal workspace");
            js(original,"(()=>{document.querySelector('#flight_name').value='Keep this flight';document.querySelector('#flight_mode').value='ascent';document.querySelector('#burst_altitude').value='1000';document.querySelector('#ascent_rate').value='5';document.querySelector('#predict-button').click()})()");
            waitFor(()->"true".equals(js(original,"document.querySelector('#flight-status').textContent.startsWith('Flight complete')")),"flight");
            runOnMainSync(()->app.serverButton.performClick());
            check(onMain(()->app.form.getVisibility()==View.VISIBLE&&app.returnButton.getVisibility()==View.VISIBLE),"Missing Return to flight");
            runOnMainSync(()->app.returnButton.performClick());
            check(onMain(()->app.web==original&&original.getVisibility()==View.VISIBLE),"Return replaced WebView");
            check("true".equals(js(original,"document.querySelector('#flight_name').value==='Keep this flight' && !document.querySelector('#flight-result').hidden")),"Return lost the flight");
            runOnMainSync(()->{app.serverButton.performClick();app.onBackPressed();});
            check(onMain(()->app.web==original&&original.getVisibility()==View.VISIBLE),"Back did not return to active flight");
            runOnMainSync(()->{
                app.serverButton.performClick();app.localChoice.performClick();app.connect(null);
            });
            waitFor(()->onMain(()->app.web!=null&&app.web!=original),"offline WebView");
            WebView offline=onMain(()->app.web);
            waitFor(()->"true".equals(js(offline,"Boolean(document.querySelector('#forecast') && window.L)")),"offline assets");
            check("true".equals(js(offline,"document.querySelector('#launch_datetime-date').type==='text' && !!document.querySelector('#launch_datetime-calendar')")),"Offline calendar assets are missing");
            String fixture=args.getString("offline_fixture");
            js(offline,"(()=>{const raw=Uint8Array.from(atob('"+fixture+"'),c=>c.charCodeAt(0));"
                +"const transfer=new DataTransfer();transfer.items.add(new File([raw],'fixture.bpforecast'));"
                +"const input=document.querySelector('#forecast');input.files=transfer.files;input.dispatchEvent(new Event('change'));})()");
            waitFor(()->"true".equals(js(offline,"document.querySelector('#flight').hidden===false")),"decoded offline forecast");
            js(offline,"document.querySelector('#calculate').click()");
            waitFor(()->"true".equals(js(offline,"document.querySelector('#result').hidden===false")),"locally calculated flight");
            check("true".equals(js(offline,"document.querySelector('#status').textContent.includes('on this phone')")),"Offline solver was not used");
            result.putString("stream","\nBP_ANDROID_PASS: personal SSH workspace, rescan, one header, Local/Server choices, both return paths, and an offline forecast flight.\n");
            runOnMainSync(app::finish);finish(Activity.RESULT_OK,result);
        }catch(Throwable error){
            result.putString("stream","\nBP_ANDROID_FAIL: "+android.util.Log.getStackTraceString(error));
            finish(Activity.RESULT_CANCELED,result);
        }
    }
    private <T>T onMain(Supplier<T> value){AtomicReference<T> ref=new AtomicReference<>();runOnMainSync(()->ref.set(value.get()));return ref.get();}
    private String js(WebView view,String script){
        CompletableFuture<String> result=new CompletableFuture<>();runOnMainSync(()->view.evaluateJavascript(script,result::complete));
        try{return result.get(8,TimeUnit.SECONDS);}catch(Exception e){throw new AssertionError(e);}
    }
    private static void waitFor(BooleanSupplier predicate,String detail){
        long end=SystemClock.uptimeMillis()+35000;do{if(predicate.getAsBoolean())return;SystemClock.sleep(160);}while(SystemClock.uptimeMillis()<end);
        throw new AssertionError("Timed out waiting for "+detail);
    }
    private static void check(boolean ok,String message){if(!ok)throw new AssertionError(message);}
}
