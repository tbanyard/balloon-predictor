#!/usr/bin/env bash
set -euo pipefail
dist=$1
tests=$2
work=$(mktemp -d)
web=18087
ssh=18088
api_pid=
ssh_pid=
cleanup() {
    [[ -z $ssh_pid ]] || kill "$ssh_pid" 2>/dev/null || true
    [[ -z $api_pid ]] || kill "$api_pid" 2>/dev/null || true
    rm -rf -- "$work"
}
trap cleanup EXIT
printf 'data_dir: %s/data\ndefault_dataset: demo\n' "$work" > "$work/config.yml"
python server.py --config "$work/config.yml" --port "$web" > "$work/server.log" 2>&1 &
api_pid=$!
python scripts/ssh-test-fixture.py "$work/ssh.json" "$web" "$ssh" > "$work/ssh.log" 2>&1 &
ssh_pid=$!
for ((i=0;i<100;i++)); do
    if [[ -f "$work/ssh.json" ]] && curl -fsS "http://127.0.0.1:$web/api/health" > /dev/null 2>&1; then break; fi
    sleep .2
done
fingerprint=$(python -c 'import json,sys;print(json.load(open(sys.argv[1]))["fingerprint"])' "$work/ssh.json")
adb install -r "$dist/BalloonPredictor-1.5.0-Android.apk"
adb install -r "$tests/BalloonPredictor-instrumentation.apk"
adb shell am instrument -w -e ssh_port "$ssh" -e web_port "$web" -e fingerprint "$fingerprint" \
  io.github.tbanyard.balloonpredictor.test/io.github.tbanyard.balloonpredictor.FlightTest | tee "$work/report"
if ! grep -q 'BP_ANDROID_PASS:' "$work/report"; then
    cat "$work/server.log" "$work/ssh.log"
    adb logcat -d -s AndroidRuntime
    exit 1
fi
cp "$work/report" "$dist/android-smoke.txt"
