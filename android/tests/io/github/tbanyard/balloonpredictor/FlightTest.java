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
            AtomicInteger trusted=new AtomicInteger(),authenticated=new AtomicInteger();
            runOnMainSync(()->{
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
            waitFor(()->"true".equals(js(original,"Boolean(window.L && document.querySelector('#predict-button')?.disabled===false)")),"flight controls");
            js(original,"(()=>{document.querySelector('#flight_name').value='Keep this flight';document.querySelector('#flight_mode').value='ascent';document.querySelector('#burst_altitude').value='1000';document.querySelector('#ascent_rate').value='5';document.querySelector('#predict-button').click()})()");
            waitFor(()->"true".equals(js(original,"document.querySelector('#flight-status').textContent.startsWith('Flight complete')")),"flight");
            runOnMainSync(()->app.serverButton.performClick());
            check(onMain(()->app.form.getVisibility()==View.VISIBLE&&app.returnButton.getVisibility()==View.VISIBLE),"Missing Return to flight");
            runOnMainSync(()->app.returnButton.performClick());
            check(onMain(()->app.web==original&&original.getVisibility()==View.VISIBLE),"Return replaced WebView");
            check("true".equals(js(original,"document.querySelector('#flight_name').value==='Keep this flight' && !document.querySelector('#flight-result').hidden")),"Return lost the flight");
            runOnMainSync(()->{app.serverButton.performClick();app.onBackPressed();});
            check(onMain(()->app.web==original&&original.getVisibility()==View.VISIBLE),"Back did not return to active flight");
            result.putString("stream","\nBP_ANDROID_PASS: verified SSH login, flight, and both return paths.\n");
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
