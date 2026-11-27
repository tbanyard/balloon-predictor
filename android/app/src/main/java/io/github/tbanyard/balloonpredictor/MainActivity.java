package io.github.tbanyard.balloonpredictor;

import android.app.*;
import android.content.*;
import android.net.Uri;
import android.os.*;
import android.provider.Settings;
import android.text.InputType;
import android.util.Base64;
import android.view.*;
import android.view.animation.*;
import android.view.inputmethod.InputMethodManager;
import android.webkit.*;
import android.widget.*;
import java.io.*;
import java.net.*;
import java.util.concurrent.*;
import org.json.JSONArray;
import org.json.JSONObject;

public final class MainActivity extends Activity {
    private static final int PAPER=0xffeff3eb,INK=0xff203b32,ORANGE=0xffbb4823;
    private static final int SAVE=110,PICK=111;
    private final Handler ui=new Handler(Looper.getMainLooper());
    private final ExecutorService network=Executors.newCachedThreadPool();
    FrameLayout content;
    ScrollView form;
    Button serverButton,connectButton,returnButton;
    Button localChoice,serverChoice,weatherButton;
    Spinner mode;
    LinearLayout toolbar;
    private TextView offlineHint;
    private Button vpn;
    EditText address,host,username,sshPort,webPort;
    CheckBox allowHttp;
    TextView status,connectionDot;
    WebView web;
    private LinearLayout webFields,sshFields;
    private LinearLayout connectionOptions;
    private WebView pendingWeb;
    private ServerSession session,pendingSession;
    private SharedPreferences prefs,pins;
    private boolean connecting,closed;
    private int attempt;
    private SaveRequest saving;
    private ValueCallback<Uri[]> picking;
    private CompletableFuture<Object> prompt;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        prefs=getSharedPreferences("connection",MODE_PRIVATE);pins=getSharedPreferences("ssh-hosts",MODE_PRIVATE);
        LinearLayout layout=column();layout.setBackgroundColor(PAPER);
        if(Build.VERSION.SDK_INT>=35)layout.setOnApplyWindowInsetsListener((v,insets)->{
            android.graphics.Insets e=insets.getInsets(WindowInsets.Type.systemBars()|WindowInsets.Type.displayCutout());
            v.setPadding(e.left,e.top,e.right,e.bottom);return insets;
        });
        toolbar=new LinearLayout(this);toolbar.setGravity(Gravity.CENTER_VERTICAL);toolbar.setPadding(dp(12),0,dp(8),0);
        ImageView logo=new ImageView(this);logo.setImageResource(R.drawable.balloon);logo.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        LinearLayout.LayoutParams logoSize=new LinearLayout.LayoutParams(dp(27),dp(32));logoSize.setMarginEnd(dp(6));toolbar.addView(logo,logoSize);
        TextView title=text("BalloonPredictor",16);title.setTypeface(null,android.graphics.Typeface.BOLD);title.setGravity(Gravity.CENTER_VERTICAL);
        title.setSingleLine(true);title.setEllipsize(android.text.TextUtils.TruncateAt.END);
        toolbar.addView(title,new LinearLayout.LayoutParams(0,dp(44),1));
        String version="1.6.3";try{version=getPackageManager().getPackageInfo(getPackageName(),0).versionName;}catch(Exception ignored){}
        TextView versionLabel=text("v"+version,11);versionLabel.setPadding(0,0,dp(6),0);toolbar.addView(versionLabel);
        weatherButton=button("Weather",false);weatherButton.setTextSize(12);weatherButton.setVisibility(View.GONE);toolbar.addView(weatherButton);
        weatherButton.setOnClickListener(v->{if(web!=null)web.evaluateJavascript("document.querySelector('#weather-open')?.click()",null);});
        serverButton=button("Server",false);serverButton.setTextSize(12);toolbar.addView(serverButton);
        connectionDot=text("●",13);connectionDot.setPadding(dp(5),0,dp(4),0);connectionDot.setVisibility(View.GONE);toolbar.addView(connectionDot);layout.addView(toolbar);
        for(Button action:new Button[]{weatherButton,serverButton}){
            action.setMinWidth(0);action.setMinimumWidth(0);action.setPadding(dp(8),0,dp(8),0);
        }
        content=new FrameLayout(this);layout.addView(content,new LinearLayout.LayoutParams(-1,0,1));setContentView(layout);
        buildForm();
        serverButton.setOnClickListener(v->{if(form.getVisibility()==View.VISIBLE&&web!=null)showFlight();else showForm();});
        splash();
        ui.postDelayed(this::updateConnectionDot,2000);
    }

    private void updateConnectionDot(){
        if(closed)return;
        if(web==null||session==null||session.offline)connectionDot.setVisibility(View.GONE);
        else{connectionDot.setVisibility(View.VISIBLE);web.evaluateJavascript("window.BalloonNetwork?.state||'reconnecting'",answer->{
            boolean ready="\"connected\"".equals(answer),signin="\"signin_required\"".equals(answer);
            connectionDot.setTextColor(ready?0xff2a8266:signin?0xffc24736:0xffdf8a2c);
            String label=ready?"Connected":signin?"Sign-in required":"Reconnecting…";connectionDot.setTooltipText(label);connectionDot.setContentDescription(label);
        });}
        ui.postDelayed(this::updateConnectionDot,2000);
    }

    private void splash(){
        form.setVisibility(View.GONE);toolbar.setVisibility(View.GONE);
        LinearLayout splash=column();splash.setGravity(Gravity.CENTER);splash.setBackgroundColor(PAPER);
        ImageView balloon=new ImageView(this);balloon.setImageResource(R.drawable.balloon);
        splash.addView(balloon,new LinearLayout.LayoutParams(dp(114),dp(114)));
        TextView label=text("BalloonPredictor",24);
        label.setGravity(Gravity.CENTER);label.setTypeface(null,android.graphics.Typeface.BOLD);
        LinearLayout.LayoutParams caption=new LinearLayout.LayoutParams(-1,-2);caption.topMargin=dp(16);
        splash.addView(label,caption);content.addView(splash,new FrameLayout.LayoutParams(-1,-1));
        TranslateAnimation rise=new TranslateAnimation(0,0,dp(8),-dp(9));rise.setDuration(650);
        rise.setRepeatMode(Animation.REVERSE);rise.setRepeatCount(Animation.INFINITE);
        if(android.animation.ValueAnimator.areAnimatorsEnabled())balloon.startAnimation(rise);
        splash.post(()->ui.postDelayed(()->{balloon.clearAnimation();content.removeView(splash);toolbar.setVisibility(View.VISIBLE);showForm();},2000));
    }

    private void buildForm(){
        form=new ScrollView(this);form.setFillViewport(true);
        LinearLayout card=column();card.setPadding(dp(22),dp(22),dp(22),dp(28));card.setBackgroundColor(PAPER);
        TextView eyebrow=text("SETUP",11);eyebrow.setLetterSpacing(.16f);eyebrow.setPadding(0,0,0,dp(10));card.addView(eyebrow);
        TextView title=text("Where would you like to work?",23);title.setTypeface(null,android.graphics.Typeface.BOLD);card.addView(title);
        card.addView(text("Choose this device or a server. Your current flight stays open while you choose.",14));
        LinearLayout choices=new LinearLayout(this);choices.setBaselineAligned(false);choices.setClipChildren(false);choices.setClipToPadding(false);choices.setGravity(Gravity.TOP);choices.setPadding(0,dp(24),0,dp(24));card.addView(choices);
        localChoice=choice("Local","On this phone",R.drawable.device);serverChoice=choice("Server","Connect securely",R.drawable.server);
        LinearLayout.LayoutParams left=new LinearLayout.LayoutParams(0,-1,1);left.setMarginEnd(dp(14));
        choices.addView(localChoice,left);choices.addView(serverChoice,new LinearLayout.LayoutParams(0,-1,1));
        connectionOptions=column();card.addView(connectionOptions);connectionOptions.setVisibility(View.GONE);
        label(connectionOptions,"Connection");mode=new Spinner(this);mode.setContentDescription("Connection method");
        ArrayAdapter<String> adapter=new ArrayAdapter<>(this,android.R.layout.simple_spinner_item,new String[]{"Web server","SSH tunnel","Run locally · offline forecast"});
        adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);mode.setAdapter(adapter);
        connectionOptions.addView(mode,new LinearLayout.LayoutParams(-1,dp(44)));
        int uri=InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_URI;
        webFields=column();connectionOptions.addView(webFields);address=field(webFields,"Server address","https://server.example.org",prefs.getString("address",""),uri);
        allowHttp=new CheckBox(this);allowHttp.setText("Allow HTTP on a trusted network or VPN");allowHttp.setChecked(prefs.getBoolean("http",false));
        webFields.addView(allowHttp);
        sshFields=column();connectionOptions.addView(sshFields);
        host=field(sshFields,"SSH host","server.example.org",prefs.getString("host",""),uri);
        username=field(sshFields,"SSH username","Username",prefs.getString("username",""),InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
        LinearLayout ports=new LinearLayout(this);sshFields.addView(ports);LinearLayout a=column(),b=column();
        ports.addView(a,new LinearLayout.LayoutParams(0,-2,1));ports.addView(b,new LinearLayout.LayoutParams(0,-2,1));
        sshPort=field(a,"SSH port","22",prefs.getString("ssh_port","22"),InputType.TYPE_CLASS_NUMBER);
        webPort=field(b,"BalloonPredictor port","8011",prefs.getString("web_port","8011"),InputType.TYPE_CLASS_NUMBER);
        sshFields.addView(text("The server asks for a password or sign-in code when required. Passwords aren’t saved.",14));
        offlineHint=text("Open a saved .bpforecast file to calculate a flight on this phone. You can prepare a pack on the server before you lose signal.",14);
        connectionOptions.addView(offlineHint);
        mode.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener(){
            public void onItemSelected(AdapterView<?> parent,View v,int i,long id){updateMode();}
            public void onNothingSelected(AdapterView<?> parent){}
        });
        mode.setSelection(prefs.getInt("mode",1));updateMode();
        connectButton=button("Connect",true);connectionOptions.addView(connectButton);connectButton.setOnClickListener(v->connect(signIn));
        returnButton=button("Return to flight",false);card.addView(returnButton);returnButton.setOnClickListener(v->showFlight());
        vpn=button("VPN settings",false);connectionOptions.addView(vpn);vpn.setOnClickListener(v->{
            try{startActivity(new Intent(Settings.ACTION_VPN_SETTINGS));}catch(Exception ignored){toast("Open VPN settings in Android Settings.");}
        });
        status=text("",14);status.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);card.addView(status);
        localChoice.setOnClickListener(v->chooseMode(true));serverChoice.setOnClickListener(v->chooseMode(false));
        form.addView(card);content.addView(form,new FrameLayout.LayoutParams(-1,-1));updateMode();
    }

    private Button choice(String name,String detail,int icon){
        int accent=icon==R.drawable.device?ORANGE:0xff216f63;
        Button result=new ChoiceButton(accent);result.setAllCaps(false);result.setTextColor(INK);result.setTextSize(13);result.setGravity(Gravity.CENTER);
        android.text.SpannableString caption=new android.text.SpannableString(name+"\n"+detail);
        caption.setSpan(new android.text.style.StyleSpan(android.graphics.Typeface.BOLD),0,name.length(),0);
        caption.setSpan(new android.text.style.RelativeSizeSpan(1.6f),0,name.length(),0);result.setText(caption);
        result.setMinHeight(dp(225));result.setMinimumHeight(dp(225));result.setMinWidth(0);result.setMinimumWidth(0);
        result.setPadding(dp(8),dp(30),dp(8),dp(26));result.setIncludeFontPadding(false);result.setCompoundDrawablePadding(dp(30));
        android.graphics.drawable.StateListDrawable states=new android.graphics.drawable.StateListDrawable();
        for(boolean selected:new boolean[]{true,false}){android.graphics.drawable.GradientDrawable shape=new android.graphics.drawable.GradientDrawable();shape.setColor(0xffffffff);shape.setCornerRadius(dp(25));shape.setStroke(dp(selected?2:1),selected?accent:0xffd7e1db);states.addState(selected?new int[]{android.R.attr.state_selected}:new int[]{},shape);}
        result.setBackgroundTintList(null);result.setBackground(states);result.setStateListAnimator(null);result.setElevation(dp(2));
        result.setOutlineProvider(android.view.ViewOutlineProvider.BACKGROUND);
        if(android.os.Build.VERSION.SDK_INT>=28){result.setOutlineAmbientShadowColor(0xff52645b);result.setOutlineSpotShadowColor(0xff52645b);}
        android.graphics.drawable.GradientDrawable badge=new android.graphics.drawable.GradientDrawable();badge.setColor(icon==R.drawable.device?0xfffbece3:0xffe0f0ec);badge.setCornerRadius(dp(28));
        android.graphics.drawable.LayerDrawable image=new android.graphics.drawable.LayerDrawable(new android.graphics.drawable.Drawable[]{badge,getDrawable(icon)});
        image.setLayerInset(1,dp(22),dp(22),dp(22),dp(22));image.setBounds(0,0,dp(88),dp(88));
        result.setCompoundDrawables(null,image,null,null);result.setContentDescription(name+" · "+detail);return result;
    }
    private final class ChoiceButton extends Button {
        final int accent;
        ChoiceButton(int color){super(MainActivity.this);accent=color;}
        @Override protected void onDraw(android.graphics.Canvas canvas){super.onDraw(canvas);if(!isSelected())return;
            android.graphics.Paint paint=new android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG);paint.setColor(accent);
            float x=getWidth()-dp(22),y=dp(22);canvas.drawCircle(x,y,dp(10),paint);
            paint.setColor(0xffffffff);paint.setStrokeWidth(dp(2));paint.setStyle(android.graphics.Paint.Style.STROKE);paint.setStrokeCap(android.graphics.Paint.Cap.ROUND);paint.setStrokeJoin(android.graphics.Paint.Join.ROUND);
            android.graphics.Path tick=new android.graphics.Path();tick.moveTo(x-dp(4),y);tick.lineTo(x-dp(1),y+dp(3));tick.lineTo(x+dp(5),y-dp(4));canvas.drawPath(tick,paint);
        }
    }
    private void chooseMode(boolean local){
        int saved=prefs.getInt("mode",1);mode.setSelection(local?2:saved==0?0:1);
        connectionOptions.setVisibility(View.VISIBLE);updateMode();
        localChoice.setSelected(local);serverChoice.setSelected(!local);
    }

    private int dp(int n){return Math.round(n*getResources().getDisplayMetrics().density);}
    private LinearLayout column(){LinearLayout v=new LinearLayout(this);v.setOrientation(LinearLayout.VERTICAL);return v;}
    private TextView text(String s,int size){TextView v=new TextView(this);v.setTextColor(INK);v.setTextSize(size);v.setText(s);return v;}
    private Button button(String s,boolean primary){Button b=new Button(this);b.setAllCaps(false);b.setText(s);b.setTextColor(primary?0xffffffff:INK);
        b.setBackgroundTintList(android.content.res.ColorStateList.valueOf(primary?ORANGE:0xffe8ede6));return b;}
    private TextView label(LinearLayout group,String name){TextView v=text(name,14);v.setPadding(0,dp(8),0,0);group.addView(v);return v;}
    private EditText field(LinearLayout group,String name,String hint,String value,int type){
        TextView l=label(group,name);EditText v=new EditText(this);v.setId(View.generateViewId());l.setLabelFor(v.getId());
        v.setSingleLine();v.setInputType(type);v.setTextSize(16);v.setHint(hint);v.setText(value);v.setSaveEnabled(false);
        group.addView(v,new LinearLayout.LayoutParams(-1,dp(48)));return v;
    }
    private void updateMode(){
        int modeId=mode.getSelectedItemPosition();
        sshFields.setVisibility(modeId==1?View.VISIBLE:View.GONE);webFields.setVisibility(modeId==0?View.VISIBLE:View.GONE);
        offlineHint.setVisibility(modeId==2?View.VISIBLE:View.GONE);
        mode.setVisibility(modeId==2?View.GONE:View.VISIBLE);
        if(vpn!=null)vpn.setVisibility(modeId==2?View.GONE:View.VISIBLE);
        if(connectButton!=null)connectButton.setText(modeId==2?"Open offline workspace":web==null?"Connect":"Connect again");
    }
    private void hideKeyboard(){((InputMethodManager)getSystemService(INPUT_METHOD_SERVICE)).hideSoftInputFromWindow(form.getWindowToken(),0);}
    private void showForm(){
        weatherButton.setVisibility(View.GONE);
        serverButton.setVisibility(View.VISIBLE);if(web!=null)web.setVisibility(View.GONE);
        connectionOptions.setVisibility(View.GONE);localChoice.setSelected(false);serverChoice.setSelected(false);
        form.setVisibility(View.VISIBLE);returnButton.setVisibility(web==null?View.GONE:View.VISIBLE);
        updateMode();
    }
    private void showFlight(){if(web!=null){hideKeyboard();form.setVisibility(View.GONE);web.setVisibility(View.VISIBLE);weatherButton.setVisibility(session!=null&&!session.offline?View.VISIBLE:View.GONE);}}
    @Override public void onBackPressed(){
        if(form.getVisibility()==View.VISIBLE&&web!=null)showFlight();
        else if(web!=null&&web.canGoBack())web.goBack();
        else if(web!=null)showForm();
        else super.onBackPressed();
    }

    private static String value(EditText input){return input.getText().toString().trim();}
    void connect(ServerSession.Prompts credentials){
        if(connecting)return;
        int selected=mode.getSelectedItemPosition(), token=++attempt;
        String url=value(address), hostname=value(host), user=value(username), sp=value(sshPort), wp=value(webPort);
        boolean http=allowHttp.isChecked();
        connecting=true;connectButton.setEnabled(false);status.setText("Connecting…");hideKeyboard();
        if(selected==2){
            open(ServerSession.offline(),token,()->prefs.edit().putInt("mode",2).apply());
            return;
        }
        network.execute(()->{
            try{
                ServerSession next=selected==0?ServerSession.direct(url,http):ServerSession.ssh(hostname,Integer.parseInt(sp),user,Integer.parseInt(wp),prefs.getInt("local_port",0),credentials,getFilesDir());
                runOnUiThread(()->{
                    if(closed||token!=attempt){next.close();return;}
                    open(next,token,()->{
                        SharedPreferences.Editor edit=prefs.edit().putInt("mode",selected).putString("address",url).putBoolean("http",http)
                          .putString("host",hostname).putString("username",user).putString("ssh_port",sp).putString("web_port",wp);
                        if(selected==1)edit.putInt("local_port",URI.create(next.origin).getPort());
                        edit.apply();
                    });
                });
            }catch(NumberFormatException e){runOnUiThread(()->failed(token,"Enter SSH and BalloonPredictor ports between 1 and 65535."));}
             catch(IllegalArgumentException e){runOnUiThread(()->failed(token,e.getMessage()));}
             catch(Exception e){runOnUiThread(()->failed(token,"Could not connect. Check the VPN and server settings."));}
        });
    }
    private void failed(int token,String message){
        if(closed||token!=attempt)return;
        ++attempt;connecting=false;connectButton.setEnabled(true);status.setText(message);
        if(pendingWeb!=null){content.removeView(pendingWeb);pendingWeb.destroy();pendingWeb=null;}
        if(pendingSession!=null){ServerSession old=pendingSession;pendingSession=null;network.execute(old::close);}
        if(form.getVisibility()!=View.VISIBLE)toast(message);
    }

    private void open(ServerSession candidate,int token,Runnable remember){
        status.setText("Opening flight workspace…");
        WebView view=new WebView(this);pendingWeb=view;pendingSession=candidate;
        view.getSettings().setJavaScriptEnabled(true);view.getSettings().setDomStorageEnabled(true);
        String appVersion="1.6.3";try{appVersion=getPackageManager().getPackageInfo(getPackageName(),0).versionName;}catch(Exception ignored){}
        view.getSettings().setUserAgentString(view.getSettings().getUserAgentString()+" BalloonPredictor/"+appVersion+" (+https://github.com/tbanyard/balloon-predictor)");
        view.getSettings().setAllowFileAccess(false);
        // Offline HTML and scripts are bundled; it can read a forecast the
        // user chose with the system document picker.
        view.getSettings().setAllowContentAccess(candidate.offline);
        view.getSettings().setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        view.setVisibility(View.INVISIBLE);content.addView(view,0,new FrameLayout.LayoutParams(-1,-1));
        view.setWebViewClient(new WebViewClient(){
            @Override public WebResourceResponse shouldInterceptRequest(WebView v,WebResourceRequest request){
                return candidate.offline?offlineResource(request):null;
            }
            boolean invalid=false;
            void error(String text){invalid=true;ui.post(()->{if(pendingWeb==view)failed(token,text);else if(web==view)toast(text);});}
            public boolean shouldOverrideUrlLoading(WebView v,WebResourceRequest r){
                Uri uri=r.getUrl();
                if(sameOrigin(uri.toString(),candidate.origin))return false;
                if(r.isForMainFrame()&&r.hasGesture()&&"https".equals(uri.getScheme())){
                    try{startActivity(new Intent(Intent.ACTION_VIEW,uri));}catch(Exception ignored){}
                }
                return true;
            }
            public void onReceivedSslError(WebView v,SslErrorHandler h,android.net.http.SslError e){h.cancel();error("Could not verify the server’s HTTPS certificate.");}
            public void onReceivedError(WebView v,WebResourceRequest r,WebResourceError e){if(r.isForMainFrame())error("The flight page could not load. Check the VPN or reconnect from Server.");}
            public void onReceivedHttpError(WebView v,WebResourceRequest r,WebResourceResponse e){if(r.isForMainFrame())error("The server returned HTTP "+e.getStatusCode()+".");}
            public void onPageFinished(WebView v,String url){
                if(sameOrigin(url,candidate.origin)&&!candidate.offline)
                    v.evaluateJavascript("document.documentElement.dataset.host='android';window.dispatchEvent(new Event('resize'))",null);
                if(candidate.signin!=null&&url.contains("/signin"))return;
                if(invalid||pendingWeb!=v||token!=attempt||!sameOrigin(url,candidate.origin))return;
                if(web!=null){content.removeView(web);web.destroy();}
                ServerSession old=session;session=candidate;web=v;pendingWeb=null;pendingSession=null;
                connecting=false;connectButton.setEnabled(true);status.setText(candidate.offline?"Offline workspace ready":"Connected");remember.run();showFlight();
                if(old!=null)network.execute(old::close);
            }
        });
        view.setDownloadListener((url,agent,disposition,mime,length)->download(view,candidate.origin,url,agent,disposition,mime));
        view.setWebChromeClient(new WebChromeClient(){
            public boolean onShowFileChooser(WebView v,ValueCallback<Uri[]> callback,FileChooserParams params){
                if(picking!=null)picking.onReceiveValue(null);picking=callback;
                try{startActivityForResult(new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*"),PICK);}
                catch(Exception e){picking=null;callback.onReceiveValue(null);}return true;
            }
        });
        view.loadUrl(candidate.origin+(candidate.offline?"/offline/index.html":candidate.signin!=null?candidate.signin:"/"));
        ui.postDelayed(()->{if(pendingWeb==view)failed(token,"The flight page did not load in time.");},20000);
    }

    // Serve only packaged files on a synthetic HTTPS origin. This WebView
    // never falls back to the network for an offline resource.
    private WebResourceResponse offlineResource(WebResourceRequest request){
        String url=request.getUrl().toString(),path=request.getUrl().getPath();
        if(sameOrigin(url,ServerSession.OFFLINE_ORIGIN)&&"GET".equals(request.getMethod())&&path!=null&&
           !path.contains("..")&&path.matches("/[a-zA-Z0-9/_.-]+")){
            String asset=path.startsWith("/offline/")?path.substring(1):
                path.startsWith("/assets/")?"offline-assets/"+path.substring(8):null;
            if(asset!=null){
                String mime=path.endsWith(".html")?"text/html":path.endsWith(".js")?"text/javascript":
                    path.endsWith(".css")?"text/css":path.endsWith(".png")?"image/png":"application/octet-stream";
                try{return new WebResourceResponse(mime,path.endsWith(".png")?null:"UTF-8",getAssets().open(asset));}
                catch(IOException ignored){}
            }
        }
        return new WebResourceResponse("text/plain","UTF-8",404,"Not found",java.util.Collections.emptyMap(),
            new ByteArrayInputStream(new byte[0]));
    }

    private final ServerSession.Prompts signIn=new ServerSession.Prompts(){
        public String pin(String endpoint){return pins.getString(endpoint,null);}
        public void savePin(String endpoint,String fingerprint){pins.edit().putString(endpoint,fingerprint).apply();}
        public boolean trust(String endpoint,String fingerprint){
            return Boolean.TRUE.equals(ask("Verify the SSH server",endpoint+" offers "+fingerprint+
                ". Compare it with the fingerprint supplied by your server operator.","Trust fingerprint",null,null));
        }
        public String[] credentials(String message,String[] labels,boolean[] echo){Object value=ask("SSH sign-in",message,"Continue",labels,echo);return value instanceof String[]?(String[])value:null;}
    };

    private Object ask(String title,String message,String accept,String[] labels,boolean[] echo){
        CompletableFuture<Object> result=new CompletableFuture<>();
        runOnUiThread(()->{
            if(closed){result.complete(null);return;}prompt=result;
            EditText[] inputs=new EditText[labels==null?0:labels.length];
            AlertDialog.Builder box=new AlertDialog.Builder(this).setTitle(title).setMessage(message)
                .setNegativeButton("Cancel",(d,w)->result.complete(null));
            if(inputs.length>0){
                LinearLayout fields=column();fields.setPadding(dp(20),0,dp(20),0);
                for(int i=0;i<inputs.length;i++)inputs[i]=field(fields,labels[i],"","",InputType.TYPE_CLASS_TEXT|
                    (echo[i]?InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS:InputType.TYPE_TEXT_VARIATION_PASSWORD));
                box.setView(fields);
            }
            box.setPositiveButton(accept,(d,w)->{
                if(labels==null){result.complete(Boolean.TRUE);return;}
                String[] answer=new String[inputs.length];
                for(int i=0;i<inputs.length;i++){answer[i]=inputs[i].getText().toString();inputs[i].setText("");}
                result.complete(answer);
            });
            AlertDialog dialog=box.create();
            dialog.setOnDismissListener(d->{result.complete(null);prompt=null;});
            if(labels!=null)dialog.getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
            dialog.show();ui.postDelayed(()->{if(!result.isDone())dialog.dismiss();},90000);
        });
        try{return result.get(95,TimeUnit.SECONDS);}catch(Exception ignored){return null;}
    }

    private static boolean sameOrigin(String url,String target){
        try{URI a=URI.create(url),b=URI.create(target);int port="https".equals(b.getScheme())?443:80;
            return b.getScheme().equals(a.getScheme())&&b.getHost().equalsIgnoreCase(a.getHost())&&a.getRawUserInfo()==null
                &&(a.getPort()<0?port:a.getPort())==(b.getPort()<0?port:b.getPort());
        }catch(Exception ignored){return false;}
    }
    private static final class SaveRequest {String url,agent,cookies,mime,name;byte[] data;}
    private void download(WebView view,String origin,String url,String agent,String disposition,String mime){
        if(view!=web||saving!=null)return;
        SaveRequest item=new SaveRequest();item.mime=mime==null?"application/octet-stream":mime;
        item.name=URLUtil.guessFileName(url,disposition,item.mime).replace('/','_').replace((char)92,'_');
        if(url.startsWith("blob:"+origin+"/")){
            view.evaluateJavascript("(async()=>{const b=await(await fetch("+JSONObject.quote(url)+")).blob();if(b.size>33554432)return;const f=new FileReader();f.onload=()=>window.__bpExport=f.result;f.readAsDataURL(b)})()",null);
            readBlob(view,item,100);
        }else if(sameOrigin(url,origin)){
            item.url=url;item.agent=agent;item.cookies=android.webkit.CookieManager.getInstance().getCookie(url);chooseSave(item);
        }
    }
    private void readBlob(WebView view,SaveRequest item,int tries){
        if(tries==0||view!=web){toast("Could not read the setup export.");return;}
        ui.postDelayed(()->{
            if(view!=web)return;
            view.evaluateJavascript("(()=>{const v=window.__bpExport||null;delete window.__bpExport;return v})()",answer->{
                try{
                    if(answer==null||"null".equals(answer)){readBlob(view,item,tries-1);return;}
                    String data=new JSONArray("["+answer+"]").getString(0);
                    if(!data.startsWith("data:")||data.length()>46000000)throw new IllegalArgumentException();
                    item.data=Base64.decode(data.substring(data.indexOf(',')+1),Base64.DEFAULT);
                    if(item.name.startsWith("download"))item.name=item.mime.equals("text/csv")?"offline-balloon-flight.csv":
                        item.mime.contains("geo+json")?"offline-balloon-flight.geojson":"balloon-launch-setups.json";
                    view.evaluateJavascript("(()=>{const name=window.__bpExportFilename||null;delete window.__bpExportFilename;return name})()",filename->{
                        try{if(filename!=null&&!"null".equals(filename)){String name=new JSONArray("["+filename+"]").getString(0);if(name.length()<=240&&!name.contains("/")&&!name.contains("\\"))item.name=name;}}catch(Exception ignored){}
                        chooseSave(item);
                    });
                }catch(Exception e){toast("Could not read the saved flight export.");}
            });
        },160);
    }
    private void chooseSave(SaveRequest item){
        if(saving!=null||closed)return;saving=item;
        try{startActivityForResult(new Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE)
            .setType(item.mime).putExtra(Intent.EXTRA_TITLE,item.name),SAVE);}
        catch(Exception e){saving=null;toast("No file picker is available.");}
    }
    @Override protected void onActivityResult(int code,int result,Intent intent){
        super.onActivityResult(code,result,intent);
        Uri uri=result==RESULT_OK&&intent!=null?intent.getData():null;
        if(code==PICK&&picking!=null){picking.onReceiveValue(uri==null?null:new Uri[]{uri});picking=null;}
        if(code==SAVE){SaveRequest item=saving;saving=null;if(item!=null&&uri!=null)network.execute(()->save(uri,item));}
    }
    private void save(Uri uri,SaveRequest item){
        HttpURLConnection conn=null;
        try{
            InputStream download=null;
            if(item.data==null){
                conn=(HttpURLConnection)URI.create(item.url).toURL().openConnection(Proxy.NO_PROXY);
                conn.setConnectTimeout(12000);conn.setReadTimeout(30000);conn.setInstanceFollowRedirects(false);
                if(item.agent!=null)conn.setRequestProperty("User-Agent",item.agent);
                if(item.cookies!=null)conn.setRequestProperty("Cookie",item.cookies);
                if(conn.getResponseCode()!=200)throw new IOException("Export unavailable");
                download=conn.getInputStream();
            }
            try(InputStream source=download;OutputStream out=getContentResolver().openOutputStream(uri)){
                if(out==null)throw new IOException("File not writable");
                if(item.data!=null)out.write(item.data);
                else{byte[] buf=new byte[65536];int n;while((n=source.read(buf))!=-1)out.write(buf,0,n);}
            }
            runOnUiThread(()->toast("Export saved"));
        }catch(Exception e){runOnUiThread(()->toast("Could not save the export. Check the connection and destination."));}
        finally{if(conn!=null)conn.disconnect();}
    }
    private void toast(String message){if(!closed)Toast.makeText(this,message,Toast.LENGTH_LONG).show();}
    @Override protected void onDestroy(){
        closed=true;++attempt;ui.removeCallbacksAndMessages(null);if(prompt!=null)prompt.complete(null);
        if(picking!=null)picking.onReceiveValue(null);
        if(web!=null){content.removeView(web);web.destroy();}
        if(pendingWeb!=null){content.removeView(pendingWeb);pendingWeb.destroy();}
        if(session!=null)network.execute(session::close);if(pendingSession!=null)network.execute(pendingSession::close);
        network.shutdown();super.onDestroy();
    }
}
