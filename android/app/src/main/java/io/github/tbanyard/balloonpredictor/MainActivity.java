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
    Spinner mode;
    LinearLayout toolbar;
    private TextView offlineHint;
    private Button vpn;
    EditText address,host,username,sshPort,webPort;
    CheckBox allowHttp;
    TextView status;
    WebView web;
    private LinearLayout webFields,sshFields;
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
        TextView title=text("BalloonPredictor",18);title.setTypeface(null,android.graphics.Typeface.BOLD);title.setGravity(Gravity.CENTER_VERTICAL);
        toolbar.addView(title,new LinearLayout.LayoutParams(0,dp(44),1));
        serverButton=button("Server",false);toolbar.addView(serverButton);layout.addView(toolbar);
        content=new FrameLayout(this);layout.addView(content,new LinearLayout.LayoutParams(-1,0,1));setContentView(layout);
        buildForm();
        serverButton.setOnClickListener(v->{if(form.getVisibility()==View.VISIBLE&&web!=null)showFlight();else showForm();});
        splash();
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
        LinearLayout card=column();card.setPadding(dp(22),dp(18),dp(22),dp(28));card.setBackgroundColor(0xffffffff);
        TextView title=text("Connect to BalloonPredictor",23);title.setTypeface(null,android.graphics.Typeface.BOLD);card.addView(title);
        card.addView(text("Connect to a computer or server running BalloonPredictor. Turn on your VPN first if needed.",14));
        label(card,"Connection");mode=new Spinner(this);mode.setContentDescription("Connection method");
        ArrayAdapter<String> adapter=new ArrayAdapter<>(this,android.R.layout.simple_spinner_item,new String[]{"Web server","SSH tunnel","Run locally · offline forecast"});
        adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);mode.setAdapter(adapter);
        card.addView(mode,new LinearLayout.LayoutParams(-1,dp(44)));
        int uri=InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_URI;
        webFields=column();card.addView(webFields);address=field(webFields,"Server address","https://server.example.org",prefs.getString("address",""),uri);
        allowHttp=new CheckBox(this);allowHttp.setText("Allow HTTP on a trusted network or VPN");allowHttp.setChecked(prefs.getBoolean("http",false));
        webFields.addView(allowHttp);
        sshFields=column();card.addView(sshFields);
        host=field(sshFields,"SSH host","server.example.org",prefs.getString("host",""),uri);
        username=field(sshFields,"SSH username","Username",prefs.getString("username",""),InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
        LinearLayout ports=new LinearLayout(this);sshFields.addView(ports);LinearLayout a=column(),b=column();
        ports.addView(a,new LinearLayout.LayoutParams(0,-2,1));ports.addView(b,new LinearLayout.LayoutParams(0,-2,1));
        sshPort=field(a,"SSH port","22",prefs.getString("ssh_port","22"),InputType.TYPE_CLASS_NUMBER);
        webPort=field(b,"BalloonPredictor port","8001",prefs.getString("web_port","8001"),InputType.TYPE_CLASS_NUMBER);
        sshFields.addView(text("The server asks for a password or sign-in code when required. Passwords aren’t saved.",14));
        offlineHint=text("Open a saved .bpforecast file to calculate a flight on this phone. You can prepare a pack on the server before you lose signal.",14);
        card.addView(offlineHint);
        mode.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener(){
            public void onItemSelected(AdapterView<?> parent,View v,int i,long id){updateMode();}
            public void onNothingSelected(AdapterView<?> parent){}
        });
        mode.setSelection(prefs.getInt("mode",0));updateMode();
        connectButton=button("Connect",true);card.addView(connectButton);connectButton.setOnClickListener(v->connect(signIn));
        returnButton=button("Return to flight",false);card.addView(returnButton);returnButton.setOnClickListener(v->showFlight());
        vpn=button("VPN settings",false);card.addView(vpn);vpn.setOnClickListener(v->{
            try{startActivity(new Intent(Settings.ACTION_VPN_SETTINGS));}catch(Exception ignored){toast("Open VPN settings in Android Settings.");}
        });
        status=text("",14);status.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);card.addView(status);
        form.addView(card);content.addView(form,new FrameLayout.LayoutParams(-1,-1));updateMode();
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
        if(vpn!=null)vpn.setVisibility(modeId==2?View.GONE:View.VISIBLE);
        if(connectButton!=null)connectButton.setText(modeId==2?"Open offline workspace":web==null?"Connect":"Connect again");
    }
    private void hideKeyboard(){((InputMethodManager)getSystemService(INPUT_METHOD_SERVICE)).hideSoftInputFromWindow(form.getWindowToken(),0);}
    private void showForm(){
        serverButton.setVisibility(View.VISIBLE);if(web!=null)web.setVisibility(View.GONE);
        form.setVisibility(View.VISIBLE);returnButton.setVisibility(web==null?View.GONE:View.VISIBLE);
        updateMode();
    }
    private void showFlight(){if(web!=null){hideKeyboard();form.setVisibility(View.GONE);web.setVisibility(View.VISIBLE);}}
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
                ServerSession next=selected==0?ServerSession.direct(url,http):ServerSession.ssh(hostname,Integer.parseInt(sp),user,Integer.parseInt(wp),prefs.getInt("local_port",0),credentials);
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
        view.loadUrl(candidate.origin+(candidate.offline?"/offline/index.html":"/"));
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
            view.evaluateJavascript("(async()=>{const b=await(await fetch("+JSONObject.quote(url)+")).blob();if(b.size>4000000)return;const f=new FileReader();f.onload=()=>window.__bpExport=f.result;f.readAsDataURL(b)})()",null);
            readBlob(view,item,25);
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
                    if(!data.startsWith("data:")||data.length()>5500000)throw new IllegalArgumentException();
                    item.data=Base64.decode(data.substring(data.indexOf(',')+1),Base64.DEFAULT);
                    if(item.name.startsWith("download"))item.name=item.mime.equals("text/csv")?"offline-balloon-flight.csv":
                        item.mime.contains("geo+json")?"offline-balloon-flight.geojson":"balloon-launch-setups.json";
                    chooseSave(item);
                }catch(Exception e){toast("Could not read the setup export.");}
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
