# BalloonPredictor for macOS

Use **BalloonPredictor-1.4.0-macOS-Universal.dmg** on an Apple silicon or
Intel Mac running **macOS 15 Sequoia or newer**. The same disk image works
for both processor types. It includes Python and the forecast libraries.

1. Open the disk image and drag **BalloonPredictor** to **Applications**.
2. Open **BalloonPredictor** from Applications. It has its own window.
3. Click **Calculate flight** with **Demo · synthetic winds** selected
   to try it. Choose **Online · ECMWF operational IFS** or **Online · NOAA
   GFS** in the weather menu to download a forecast for a real flight.

The test app has an ad hoc signature and has not been notarized by
Apple. Verify the disk image against the SHA-256 in your own GitHub
Actions run before testing it. If macOS blocks the verified test app,
Apple documents how to allow that individual app under **System Settings
→ Privacy & Security → Open Anyway**, where your Mac's policy allows it.
Do not change your Mac's global security settings. Use Developer ID
signing and Apple notarization before distributing it publicly.

The macOS minimum comes from the included native GRIB and terrain
libraries. Macs on macOS 14 or earlier need a separate compatibility
build. Check **Apple menu → About This Mac** for the system version.

Forecasts, terrain and results are in
**~/Library/Application Support/BalloonPredictor/data**. You can paste
that path in Finder's **Go → Go to Folder**. **Manage weather** shows
the actual data location and offers cleanup. Launch setups and panel
layouts are kept in the app's local window data.

The optional **~/Library/Application Support/BalloonPredictor/config.yml**
can point to local ERA5 or WRF files. The Mac app does not assume Unix
server paths exist. CDS downloads still require your own CDS setup.
Internet is needed for operational weather and online map tiles.
If startup fails, look for **BalloonPredictor.log** in the
**~/Library/Application Support/BalloonPredictor** folder.

To uninstall, quit the app and drag it from Applications to the Trash.
Your weather files and settings stay in your account.

## Building and testing

Push to **macos-installer-test** to start **macOS installer (test)**.
The job builds one disk image with a universal launcher and private,
native Python runtimes for both processor types. Two separate macOS 15
jobs mount the same disk image, copy the app, then test the installed
engine, a Demo flight in the WebKit window, GRIB, GeoTIFF and both NetCDF
exports. They also save a model box from the window. The workflow
uploads **BalloonPredictor-macOS-Universal-test** only after both tests
pass. It does not publish a release.

Test the same disk image on your own Mac before sharing it. Try a Demo
flight, a small live IFS and GFS forecast, both NetCDF downloads, and
closing and reopening to confirm the setup is retained.

In v1.5 choose **Run locally**, **Web server**, or **SSH tunnel**. Use **BalloonPredictor → Connection** to reopen the chooser. Change local storage in Manage Weather.
