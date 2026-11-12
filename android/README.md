# Android client

See [v1.5 connection and installation notes](../docs/V1.5.md). Build with JDK
17, Android SDK platform 36 and build-tools 35.0.0:

```sh
python3 android/build.py dist/android
```

For an updateable APK, supply `--keystore /path/to/balloon-predictor.p12` and
the `BP_APK_STORE_PASS` and `BP_APK_KEY_PASS` environment variables. GitHub
uses the encrypted `BP_ANDROID_KEYSTORE`, `BP_ANDROID_STORE_PASSWORD`, and
`BP_ANDROID_KEY_PASSWORD` secrets. The emulator test uses real SSH against a
localhost fixture and checks that Server settings preserve the calculated flight.
