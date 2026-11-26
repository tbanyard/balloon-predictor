package io.github.tbanyard.balloonpredictor;

import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.concurrent.*;
import org.json.*;

/** A stable loopback origin, independent of each reconnecting SSH transport. */
final class RecoveryGateway implements AutoCloseable {
    static final String CONTROL="/__bp_connection/";
    final String origin;
    final ServerSession.Prompts prompts;
    final ServerSocket listener;
    final String host,user;final int sshPort,webPort;
    final ExecutorService workers=Executors.newCachedThreadPool();
    final File history;
    volatile ServerSession transport;
    volatile String state="reconnecting",password;
    volatile boolean interactive=true,closed=false,needsLogin=false,usedPassword=false,wake=false;
    RecoveryGateway(String host,int sshPort,String user,int webPort,int port,ServerSession.Prompts ui,File directory) throws Exception {
        this.host=host;this.user=user;this.sshPort=sshPort;this.webPort=webPort;
        ServerSocket socket;
        try{socket=new ServerSocket();socket.bind(new InetSocketAddress("127.0.0.1",port));}
        catch(IOException error){if(port==0)throw error;socket=new ServerSocket();socket.bind(new InetSocketAddress("127.0.0.1",0));}
        listener=socket;origin="http://127.0.0.1:"+socket.getLocalPort();
        String key=java.util.Base64.getUrlEncoder().withoutPadding().encodeToString(MessageDigest.getInstance("SHA-256").digest((host+":"+sshPort+":"+user+":"+webPort).getBytes(StandardCharsets.UTF_8)));
        history=new File(directory,"flights-"+key+".json");
        prompts=new ServerSession.Prompts(){
            public String pin(String endpoint){return ui.pin(endpoint);}
            public void savePin(String endpoint,String fingerprint){ui.savePin(endpoint,fingerprint);}
            public boolean trust(String endpoint,String fingerprint){if(!interactive){needsLogin=true;return false;}return ui.trust(endpoint,fingerprint);}
            public String[] credentials(String text,String[] labels,boolean[] echo){
                boolean reusable=labels.length==1&&!echo[0]&&labels[0].toLowerCase(Locale.ROOT).contains("password")&&!labels[0].toLowerCase(Locale.ROOT).matches(".*(code|token|otp|verification).*");
                if(reusable&&password!=null&&!usedPassword){usedPassword=true;return new String[]{password};}
                if(usedPassword)password=null;
                if(!interactive){needsLogin=true;return null;}
                String[] answer=ui.credentials(text,labels,echo);
                if(reusable&&answer!=null&&answer.length==1){password=answer[0];usedPassword=true;}
                if(answer==null)needsLogin=true;
                return answer;
            }
        };
        workers.execute(()->{while(!closed)try{Socket client=listener.accept();workers.execute(()->handle(client));}catch(IOException ignored){if(!closed)close();}});
    }
    void attach(ServerSession value){transport=value;state="connected";interactive=false;workers.execute(this::monitor);}
    void monitor(){int delay=1;
        while(!closed){
            try{
                ServerSession current=transport;
                if(current!=null&&current.connected()){Thread.sleep(1500);continue;}
                if("signin_required".equals(state)&&!wake){Thread.sleep(400);continue;}
                wake=false;state="reconnecting";needsLogin=false;usedPassword=false;
                if(current!=null)current.close();transport=null;
                try{ServerSession next=ServerSession.transport(host,sshPort,user,webPort,0,prompts);if(closed){next.close();return;}transport=next;state="connected";delay=1;}
                catch(Exception error){state=needsLogin||error.getMessage()!=null&&error.getMessage().contains("fingerprint")?"signin_required":"reconnecting";}
                finally{interactive=false;}
                if(!"connected".equals(state)){for(int n=0;n<delay*5&&!wake&&!closed;n++)Thread.sleep(200);delay=Math.min(30,delay*2);}
            }catch(InterruptedException ignored){return;}
        }
    }
    void recover(){if(!"connected".equals(state)){interactive=true;state="reconnecting";wake=true;}}
    private void reply(OutputStream output,int code,JSONObject value,boolean offline) throws IOException {
        byte[] body=value.toString().getBytes(StandardCharsets.UTF_8);
        output.write(("HTTP/1.1 "+code+" Response\r\nContent-Type: application/json\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\nConnection: close\r\nContent-Length: "+body.length+"\r\n"+(offline?"X-Balloon-Reconnect: yes\r\n":"")+"\r\n").getBytes(StandardCharsets.US_ASCII));output.write(body);output.flush();
    }
    private void handle(Socket client){
        try(Socket local=client){local.setSoTimeout(20000);InputStream input=local.getInputStream();OutputStream output=local.getOutputStream();
            ByteArrayOutputStream header=new ByteArrayOutputStream();int previous=0,matched=0;
            while(header.size()<32768){int b=input.read();if(b<0)return;header.write(b);matched=b=="\r\n\r\n".charAt(matched)?matched+1:(b==13?1:0);if(matched==4)break;}
            if(matched!=4)return;
            String[] lines=header.toString("ISO-8859-1").split("\r\n"),request=lines[0].split(" ");if(request.length!=3)return;
            String method=request[0],path=request[1];Map<String,String> fields=new HashMap<>();
            for(int i=1;i<lines.length;i++){int colon=lines[i].indexOf(':');if(colon>0)fields.put(lines[i].substring(0,colon).toLowerCase(Locale.ROOT),lines[i].substring(colon+1).trim());}
            if(!URI.create(origin).getRawAuthority().equals(fields.get("host"))||!path.startsWith("/")||path.startsWith("//")){reply(output,403,new JSONObject(),false);return;}
            if(path.startsWith(CONTROL)){
                String action=path.substring(CONTROL.length());
                if("GET".equals(method)&&"state".equals(action)){reply(output,200,new JSONObject().put("native_ssh",true).put("status",state),false);return;}
                if("GET".equals(method)&&"flights".equals(action)){synchronized(history){reply(output,200,history.isFile()?new JSONObject(new String(Files.readAllBytes(history.toPath()),StandardCharsets.UTF_8)):new JSONObject(),false);}return;}
                if(!"POST".equals(method)||!origin.equals(fields.get("origin"))||!fields.getOrDefault("content-type","").split(";")[0].equals("application/json")){reply(output,403,new JSONObject(),false);return;}
                int length=Integer.parseInt(fields.getOrDefault("content-length","0")),maximum="flights".equals(action)?32*1024*1024:16384;if(length<0||length>maximum)return;
                byte[] body=new byte[length];int at=0,n;while(at<length&&(n=input.read(body,at,length-at))!=-1)at+=n;if(at!=length)return;JSONObject data=length==0?new JSONObject():new JSONObject(new String(body,StandardCharsets.UTF_8));
                if("recover".equals(action)){recover();reply(output,200,new JSONObject(),false);}
                else if("identity".equals(action)){ServerSession current=transport;if(current==null)throw new IOException();String token=current.renewIdentity();reply(output,200,new JSONObject().put("token",token==null?JSONObject.NULL:token),false);}
                else if("cancel".equals(action)){interactive=false;reply(output,200,new JSONObject(),false);}
                else if("flights".equals(action)){if(!(data.opt("recent") instanceof JSONArray))return;synchronized(history){File temp=new File(history.getPath()+".tmp");try(FileOutputStream file=new FileOutputStream(temp)){file.write(body);}Files.move(temp.toPath(),history.toPath(),StandardCopyOption.REPLACE_EXISTING);}reply(output,200,new JSONObject().put("saved",true),false);}
                else reply(output,404,new JSONObject(),false);
                return;
            }
            ServerSession current=transport;
            if(current==null||!current.connected()){state="reconnecting";reply(output,503,new JSONObject().put("detail","Reconnecting. Your flights remain open."),true);return;}
            URI target=URI.create(current.origin);
            try(Socket remote=new Socket()){
                remote.connect(new InetSocketAddress("127.0.0.1",target.getPort()),8000);remote.setSoTimeout(90000);
                StringBuilder clean=new StringBuilder(lines[0]+"\r\n");for(int i=1;i<lines.length;i++)if(!lines[i].toLowerCase(Locale.ROOT).startsWith("connection:")&&!lines[i].toLowerCase(Locale.ROOT).startsWith("proxy-connection:"))clean.append(lines[i]).append("\r\n");clean.append("Connection: close\r\n\r\n");
                OutputStream toServer=remote.getOutputStream();toServer.write(clean.toString().getBytes(StandardCharsets.ISO_8859_1));
                long remaining=Long.parseLong(fields.getOrDefault("content-length","0"));byte[] buffer=new byte[65536];int n;
                if(fields.containsKey("transfer-encoding"))return;
                while(remaining>0&&(n=input.read(buffer,0,(int)Math.min(buffer.length,remaining)))!=-1){toServer.write(buffer,0,n);remaining-=n;}toServer.flush();
                InputStream response=remote.getInputStream();while((n=response.read(buffer))!=-1)output.write(buffer,0,n);output.flush();
            }
        }catch(Exception ignored){/* An interrupted mutation is not blindly replayed. */}
    }
    @Override public void close(){if(closed)return;closed=true;password=null;try{listener.close();}catch(IOException ignored){}ServerSession old=transport;transport=null;if(old!=null)old.close();workers.shutdownNow();}
}
