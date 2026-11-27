#!/usr/bin/env bash
# Create a stable sideloading/update key only if the GitHub repo has none.
# This is not a Google Play account or publishing step.
set -euo pipefail
if (($#)); then
    case "$1" in
        -h|--help) printf '%s\n' "Usage: bash setup-android-signing.sh" "Keeps an existing GitHub signing key, or creates one for direct APK installation and upgrades."; exit 0 ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; exit 1 ;;
    esac
fi
repo=tbanyard/balloon-predictor
command -v gh >/dev/null || { printf 'Install gh first.\n' >&2; exit 1; }
gh auth status --hostname github.com >/dev/null
names=$(gh secret list --repo "$repo" --json name --jq '.[].name')
found=false
store=false
keypass=false
while IFS= read -r name; do
    case "$name" in
        BP_ANDROID_KEYSTORE) found=true ;;
        BP_ANDROID_STORE_PASSWORD) store=true ;;
        BP_ANDROID_KEY_PASSWORD) keypass=true ;;
    esac
done <<< "$names"
if "$found"; then
    "$store" && "$keypass" || { printf 'Stopped: the repository has a signing key but a password secret is missing. Restore its matching password; this script will not replace your key.\n' >&2; exit 1; }
    printf 'The repository already has an Android signing key. Keeping it.\n'
    exit 0
fi
command -v openssl >/dev/null || { printf 'Install openssl first.\n' >&2; exit 1; }
umask 077
key_dir="${BP_SIGNING_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/balloon-predictor/android-signing}"
mkdir -p -- "$key_dir"
[[ ! -L "$key_dir" && -O "$key_dir" ]] || { printf 'The signing folder is not owned by this account.\n' >&2; exit 1; }
chmod 700 "$key_dir"
key="$key_dir/balloon-predictor.p12"
password="$key_dir/password"
if [[ ! -f "$key" ]]; then
    [[ ! -e "$password" ]] || { printf 'A partial signing setup already exists. Check %s before retrying.\n' "$key_dir" >&2; exit 1; }
    openssl rand -hex 32 > "$password"
    trap 'rm -f -- "$key_dir/private.pem" "$key_dir/certificate.pem"' EXIT
    openssl req -x509 -newkey rsa:3072 -sha256 -days 3650 -nodes \
        -subj /CN=BalloonPredictor -keyout "$key_dir/private.pem" -out "$key_dir/certificate.pem" >/dev/null 2>&1
    openssl pkcs12 -export -name balloon-predictor -inkey "$key_dir/private.pem" -in "$key_dir/certificate.pem" \
        -out "$key" -passout "file:$password"
fi
[[ -s "$password" ]] || { printf 'The existing key password is missing. Restore it from your backup.\n' >&2; exit 1; }
tr -d '\r\n' < "$password" | gh secret set BP_ANDROID_STORE_PASSWORD --repo "$repo"
tr -d '\r\n' < "$password" | gh secret set BP_ANDROID_KEY_PASSWORD --repo "$repo"
base64 < "$key" | tr -d '\n' | gh secret set BP_ANDROID_KEYSTORE --repo "$repo"
printf 'Saved the stable Android signing key in GitHub secrets.\nKeep a private backup of %s and its password.\nRun prepare-v1.6.6.sh next.\n' "$key"
