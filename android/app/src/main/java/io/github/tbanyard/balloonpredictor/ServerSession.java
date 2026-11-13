package io.github.tbanyard.balloonpredictor;

import com.jcraft.jsch.HostKey;
import com.jcraft.jsch.HostKeyRepository;
import com.jcraft.jsch.JSch;
import com.jcraft.jsch.Session;
import com.jcraft.jsch.UIKeyboardInteractive;
import com.jcraft.jsch.UserInfo;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.Proxy;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Base64;

final class ServerSession implements AutoCloseable {
    interface Prompts {
        String pin(String endpoint);
        boolean trust(String endpoint, String fingerprint);
        void savePin(String endpoint, String fingerprint);
        String[] credentials(String text, String[] prompts, boolean[] echo);
    }
    static final String OFFLINE_ORIGIN="https://appassets.androidplatform.net";
    final String origin;
    final boolean offline;
    private final Session ssh;

    private ServerSession(String origin, Session ssh) { this.origin=origin; this.ssh=ssh; this.offline=OFFLINE_ORIGIN.equals(origin); }
    static ServerSession offline(){return new ServerSession(OFFLINE_ORIGIN,null);}

    static ServerSession direct(String address, boolean allowHttp) {
        URI url;
        try {url=URI.create(address);} catch (IllegalArgumentException error) {throw new IllegalArgumentException("Enter a valid server URL.");}
        String scheme=url.getScheme(),host=url.getHost();
        int port=url.getPort(), standard="https".equals(scheme)?443:80;
        if (!("http".equals(scheme)||"https".equals(scheme))||host==null||url.getRawUserInfo()!=null||
            url.getRawQuery()!=null||url.getRawFragment()!=null||!("".equals(url.getRawPath())||"/".equals(url.getRawPath()))||port==0||port>65535)
            throw new IllegalArgumentException("Enter a server URL such as https://server.example.org.");
        if ("http".equals(scheme)&&!allowHttp) throw new IllegalArgumentException("Use HTTPS or allow HTTP on your trusted VPN or local network.");
        String origin=scheme+"://"+host.toLowerCase(java.util.Locale.ROOT)+(port>0&&port!=standard?":"+port:"");
        if (OFFLINE_ORIGIN.equals(origin)) throw new IllegalArgumentException("Choose Run locally for the offline workspace.");
        if (!healthy(origin)) throw new IllegalArgumentException("BalloonPredictor did not answer. Check the VPN, address and port.");
        return new ServerSession(origin,null);
    }

    static ServerSession ssh(String host,int sshPort,String user,int webPort,int localPort,Prompts ui) throws Exception {
        if (!host.matches("[a-zA-Z0-9._:-]+")||user.isEmpty()||user.length()>128||sshPort<1||sshPort>65535||webPort<1||webPort>65535)
            throw new IllegalArgumentException("Enter the SSH host, username and valid port numbers.");
        String endpoint=host+":"+sshPort;
        JSch client=new JSch();
        KeyPin keys=new KeyPin(endpoint,ui);
        client.setHostKeyRepository(keys);
        Session session=client.getSession(user,host,sshPort);
        session.setConfig("StrictHostKeyChecking","yes");
        session.setConfig("PreferredAuthentications","keyboard-interactive,password");
        session.setConfig("ssh-ed25519","com.jcraft.jsch.bc.SignatureEd25519");
        session.setConfig("xdh","com.jcraft.jsch.bc.XDH");
        session.setConfig("cipher.c2s","aes128-ctr,aes256-ctr,aes128-gcm@openssh.com,aes256-gcm@openssh.com");
        session.setConfig("cipher.s2c",session.getConfig("cipher.c2s"));
        session.setServerAliveInterval(25000);
        session.setServerAliveCountMax(3);
        session.setUserInfo(new Authentication(endpoint,ui));
        try {
            try {session.connect(15000);} catch (Exception error) {
                if(keys.changed)throw new IllegalArgumentException("The SSH host fingerprint changed. Check with the server operator.");
                throw new IllegalArgumentException("SSH could not connect or sign in. Check the VPN, port, username and credentials.");
            }
            int assigned;
            try {assigned=session.setPortForwardingL("127.0.0.1",localPort,"127.0.0.1",webPort);}
            catch (com.jcraft.jsch.JSchException error) {
                if(localPort==0)throw error;
                assigned=session.setPortForwardingL("127.0.0.1",0,"127.0.0.1",webPort);
            }
            String origin="http://127.0.0.1:"+assigned;
            if(!healthy(origin))throw new IllegalArgumentException("SSH signed in, but BalloonPredictor did not answer at server port "+webPort+".");
            return new ServerSession(origin,session);
        } catch (Exception error) {
            session.disconnect();
            throw error;
        }
    }

    static boolean healthy(String origin) {
        HttpURLConnection connection=null;
        try {
            connection=(HttpURLConnection)URI.create(origin+"/api/health").toURL().openConnection(Proxy.NO_PROXY);
            connection.setConnectTimeout(8000);connection.setReadTimeout(8000);connection.setInstanceFollowRedirects(false);
            if(connection.getResponseCode()!=200)return false;
            try(InputStream source=connection.getInputStream()){
                byte[] buffer=new byte[8192];int total=0,n;
                while(total<buffer.length&&(n=source.read(buffer,total,buffer.length-total))!=-1)total+=n;
                String result=new String(buffer,0,total,StandardCharsets.UTF_8).replaceAll("\\s","");
                return result.contains("\"status\":\"ok\"")&&result.contains("\"time_reference\":\"UTC\"");
            }
        } catch(Exception ignored){return false;}
        finally {if(connection!=null)connection.disconnect();}
    }
    @Override public void close(){if(ssh!=null)ssh.disconnect();}

    private static final class KeyPin implements HostKeyRepository {
        final String endpoint;final Prompts ui;boolean changed;
        KeyPin(String endpoint,Prompts ui){this.endpoint=endpoint;this.ui=ui;}
        public int check(String host,byte[] key){
            try{
                String fingerprint="SHA256:"+Base64.getEncoder().withoutPadding().encodeToString(MessageDigest.getInstance("SHA-256").digest(key));
                String saved=ui.pin(endpoint);
                if(saved!=null){changed=!saved.equals(fingerprint);return changed?CHANGED:OK;}
                if(!ui.trust(endpoint,fingerprint))return NOT_INCLUDED;
                ui.savePin(endpoint,fingerprint);return OK;
            }catch(Exception refused){return NOT_INCLUDED;}
        }
        public String getKnownHostsRepositoryID(){return "BalloonPredictor verified hosts";}
        public HostKey[] getHostKey(){return new HostKey[0];}
        public HostKey[] getHostKey(String host,String type){return new HostKey[0];}
        public void add(HostKey key,UserInfo user){}
        public void remove(String host,String type){}
        public void remove(String host,String type,byte[] key){}
    }

    private static final class Authentication implements UserInfo,UIKeyboardInteractive {
        final String endpoint;final Prompts ui;String password;int attempts;
        Authentication(String endpoint,Prompts ui){this.endpoint=endpoint;this.ui=ui;}
        public boolean promptPassword(String text){
            if(++attempts>3)return false;
            String[] response=ui.credentials("SSH password for "+endpoint,new String[]{"Password"},new boolean[]{false});
            password=response==null?null:response[0];return password!=null;
        }
        public String getPassword(){String answer=password;password=null;return answer;}
        public String[] promptKeyboardInteractive(String dest,String name,String instruction,String[] prompt,boolean[] echo){
            return ++attempts>5?null:ui.credentials(instruction.isEmpty()?"SSH sign-in for "+endpoint:instruction,prompt,echo);
        }
        public String getPassphrase(){return null;}
        public boolean promptPassphrase(String message){return false;}
        public boolean promptYesNo(String message){return false;}
        public void showMessage(String message){}
    }
}
