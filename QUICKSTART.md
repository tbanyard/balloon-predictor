# BalloonPredictor v1.1

Unzip this release into `/data1/tim`. The ZIP contains the `balloon-predictor`
folder, so the resulting project is `/data1/tim/balloon-predictor`.

```bash
cd /data1/tim/balloon-predictor
bash start.sh
```

Open **http://127.0.0.1:8000**. Python 3.12+ is required. First-time setup needs
internet access; subsequent runs reuse the environment. An activated Conda or
virtual environment is reused when it already has the dependencies.

1. **Weather dataset:** ERA5 is selected initially. It reads the same files as
   ERA5 viewer v1.2 from `/data3/ERA5`, including matching `era5_topo.nc`.
2. Choose an **Available date** and enter your launch time in **UTC**. Use
   **Use ground** to set the launch altitude just above the model terrain.
3. Choose **Burst, descend & land** or **Stop at target / burst altitude**.
4. **Add launch point** or **Duplicate** makes another setup. Tabs in
   **03 / Flight Profile** switch between saved launch points.
5. Expand **Repeated launches**, choose the window and interval, then run the
   current point or all saved points. A 24 h window at 1 h intervals gives 24
   launches. A 12 h window at 3 h intervals gives four launches.
6. Use the map menus to show both stages or ascent only, and compare **Landing
   positions** or **Target / burst positions**.
7. **Open Skew-T & u/v alongside** shows the model sounding beside the map.
   Switch between the predicted ascent and the fixed launch column, or open a
   separate window for SVG, CSV and print/PDF exports.

**Your WRF files:** put standard native `wrfout_d01_…` / `wrfout_d02_…` files in
`/data1/tim/SkySightWRF`, preferably in one subfolder per run. Open **Manage
weather → Rescan local data**, then select the run/domain. Files around 10 GB
remain on disk; only required columns are read. Actual support requires the
variables listed in [docs/WRF.md](docs/WRF.md).

Only the two project ZIPs were provided for this release. The WRF text/header
mentioned in the original brief was not attached, so compatibility with that
specific file has not been verified. Standard WRF fixtures have been tested.

The existing **Download & add dataset** window remains available for ERA5,
ERA5T and IFS. Existing cropped CLI imports also remain available.

Change `era5_root` / `wrf_root` in `config.yml` for different local data paths.
For a shared installation, keep your own YAML configuration and run:

```bash
bash start.sh --config /path/to/my-balloon-config.yml --port 8001
```

Run ERA5 viewer and BalloonPredictor independently; they use different ports
and share only the source weather files. See [docs/GIT.md](docs/GIT.md) for
uploading the source to GitHub and tagging v1.1.
