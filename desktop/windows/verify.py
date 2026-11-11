"""Install, exercise and uninstall the exact Setup.exe that Actions will upload."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory


def show_log(path):
    if path.is_file():
        print(f"--- {path.name} ---")
        print(path.read_text(encoding="utf-8-sig", errors="replace")[-12000:])


def verify(output):
    [installer] = output.glob("BalloonPredictor-*-Windows-x64-Setup.exe")
    with TemporaryDirectory(prefix="BalloonPredictor Clean Install ") as temp:
        root = Path(temp)
        program = root / "Installed App"
        data = root / "User Data"
        setup_log = root / "setup.log"
        try:
            subprocess.run(
                [str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                 "/SP-", f"/DIR={program}", f"/LOG={setup_log}"],
                cwd=root, check=True, timeout=300,
            )
            # Run the installed shortcut's actual executable outside the checkout,
            # without the build interpreter or its dependencies on the PATH.
            system = Path(os.environ["SystemRoot"])
            env = {
                **{k: v for k, v in os.environ.items() if k.upper() != "PATH"},
                "PATH": os.pathsep.join((str(system / "System32"), str(system))),
                "PYTHONHOME": str(root / "Nonexistent Python"),
                "PYTHONPATH": str(root / "Nonexistent Packages"),
            }
            subprocess.run(
                [str(program / "runtime/pythonw.exe"), str(program / "app/desktop/launcher.py"),
                 "--smoke-test", str(data), "--check-window"],
                env=env, cwd=root, check=True, timeout=180,
            )
            result = data / "smoke-test-passed.txt"
            if not result.is_file():
                raise RuntimeError("The installed app did not write its smoke test result.")
            [uninstaller] = program.glob("unins*.exe")
            subprocess.run(
                [str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                cwd=root, check=True, timeout=180,
            )
            if (program / "runtime/pythonw.exe").exists() or not result.is_file():
                raise RuntimeError("Uninstall did not remove the program and preserve user data.")
            shutil.copy2(result, output / result.name)
            with (output / result.name).open("a", encoding="utf-8") as report:
                report.write("Silent install and uninstall passed.\n")
            print((output / result.name).read_text(encoding="utf-8"))
        except Exception:
            show_log(setup_log)
            show_log(data / "BalloonPredictor.log")
            raise


if __name__ == "__main__":
    verify(Path(sys.argv[1]).resolve())
