# BalloonPredictor for Windows

Open **BalloonPredictor** from the Start menu. It opens in its own window.
Setup includes Python and the forecast libraries. No Python, Conda or
server command is needed.

The first launch selects **Demo · synthetic winds**. Click **Calculate
flight** to try the app. For real weather, choose **Online · ECMWF
operational IFS** or **Online · NOAA GFS** in the weather dataset menu and
download the forecast for your launch. Model downloads and online map
tiles need internet access. If WebView2 is missing, Setup needs internet
access to install it from Microsoft.

Your forecasts, terrain and results are under
**%LOCALAPPDATA%\BalloonPredictor\data**. Paste that path into File
Explorer. **Manage weather** shows the actual data location and offers
cleanup. Launch setups and panel layouts are kept in the desktop window
profile. Windows uninstall leaves these files in place.

The optional **%LOCALAPPDATA%\BalloonPredictor\config.yml** can point
to a local ERA5 or WRF archive. The app does not assume the Unix server
paths exist. CDS downloads still require your own CDS account and setup.
The startup log is **%LOCALAPPDATA%\BalloonPredictor\BalloonPredictor.log**.

## Building and testing

The Windows installer workflow runs on a push to **windows-installer-test**.
It builds a per-user x64 installer for Windows 11 and Windows 10 22H2.
The workflow installs it into a path with spaces, launches the installed
app with its bundled Python and WebView2, exercises synthetic GRIB, terrain
and both NetCDF exports, then uninstalls it. The installer, SHA-256 and
smoke test report are available as an Actions artifact after the checks
pass. No release is published by the workflow.

Before distributing, run the test installer on a clean Windows x64 PC or
VM as a standard user. Test live IFS and GFS downloads separately.
The automated check uses offline fixtures and does not depend on
availability of those forecast services. The test installer is unsigned.

Application and Leaflet notices are in the installed app folder. Python
and wheel notices are retained with the runtime and packages. Microsoft
Visual C++ redistributable DLLs are deployed with the private runtime;
WebView2 is Microsoft's separately updated browser runtime.

In v1.5 choose **Run locally**, **Web server**, or **SSH tunnel**. Use **Connection → Choose server** to reopen the chooser. Change local storage in Manage Weather.
