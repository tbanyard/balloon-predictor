#!/usr/bin/env bash
# Publish the extracted BalloonPredictor v1.1 package as one GitHub release.
# Usage: bash upload-to-github.sh [--public|--private] [--directory PATH]
# GitHub CLI documentation: https://cli.github.com/manual/gh_repo_create
# Release documentation: https://cli.github.com/manual/gh_release_create
set -Eeuo pipefail

project_dir=../balloon-predictor
visibility=public
version=1.1.0
tag="v${version}"
title='BalloonPredictor v1.1'
temporary_dir=''

fail() { printf '\n%s\n' "Stopped: $*" >&2; exit 1; }
cleanup() { if [[ -n "$temporary_dir" ]]; then rm -rf -- "$temporary_dir"; fi; }
trap cleanup EXIT
trap 'printf "\nUpload stopped at line %s. Fix the error above, then rerun this script.\n" "$LINENO" >&2' ERR

while (( $# )); do
  case "$1" in
    --public) visibility=public; shift ;;
    --private) visibility=private; shift ;;
    --directory)
      [[ $# -ge 2 && -n "$2" ]] || fail '--directory needs a folder path.'
      project_dir="$2"; shift 2 ;;
    -h|--help)
      printf '%s\n' \
        'Usage: bash upload-to-github.sh [--public|--private] [--directory PATH]' \
        'Default: public repository, ../balloon-predictor, release v1.1.0.'
      exit 0 ;;
    *) fail "Unknown argument: $1" ;;
  esac
done

for dependency in git gh awk mktemp; do
  command -v "$dependency" >/dev/null || fail "Install $dependency first. GitHub CLI: https://cli.github.com/"
done
cd -- "$project_dir" || fail "Cannot open $project_dir."
project_dir=$(pwd -P)
[[ -f pyproject.toml && -f .gitignore && -f CHANGELOG.md && -f start.sh && -d balloon_predictor && -d frontend ]] \
  || fail 'This folder is not the extracted BalloonPredictor package.'
awk '$1 == "version" && $2 == "=" && $3 == "\"1.1.0\"" {found=1} END {exit !found}' pyproject.toml \
  || fail 'This script is for BalloonPredictor 1.1.0.'

# Reuse the current GitHub login, or start the normal browser/device-code flow.
if ! auth_status=$(gh auth status --hostname github.com 2>&1); then
  gh auth login --hostname github.com --git-protocol https --web --scopes workflow
elif [[ "$auth_status" == *'Token scopes:'* && "$auth_status" != *"'workflow'"* ]]; then
  # The package includes .github/workflows/tests.yml.
  gh auth refresh --hostname github.com --scopes workflow
fi
owner=$(gh api --hostname github.com user --jq '.login')
[[ "$owner" =~ ^[A-Za-z0-9-]+$ ]] || fail 'Could not identify your GitHub account.'
repository="$owner/balloon-predictor"
remote_url="https://github.com/$repository.git"
printf '\nPublishing %s to %s (%s).\n' "$title" "$repository" "$visibility"

# Never include an older checkout's history or alter another repository.
if [[ ! -e .git ]]; then
  git init -b main
fi
[[ "$(git rev-parse --show-toplevel)" == "$project_dir" ]] || fail 'The Git checkout belongs to a different folder.'
if git rev-parse --verify HEAD >/dev/null 2>&1; then
  [[ "$(git rev-list --count HEAD)" == 1 ]] \
    || fail 'This checkout has existing history. Use a fresh extraction for a single v1.1 release.'
  [[ "$(git branch --show-current)" == main ]] || fail 'This checkout must be on main.'
else
  git symbolic-ref HEAD refs/heads/main
fi

existing_remote=$(git config --get remote.origin.url || true)
if [[ -n "$existing_remote" && "$existing_remote" != "$remote_url" && "$existing_remote" != "git@github.com:$repository.git" ]]; then
  fail 'origin points to another repository. This script will not change it.'
fi

# Local upload exclusions supplement the package's .gitignore.
# Templates remain included; weather files and runtime folders stay local.
exclude_file=$(git rev-parse --git-path info/exclude)
if ! exclude_marker=$(awk '$0 == "# BalloonPredictor release upload exclusions" {print "present"}' "$exclude_file" 2>/dev/null); then
  exclude_marker=''
fi
if [[ -z "$exclude_marker" ]]; then
  cat >> "$exclude_file" <<'EXCLUSIONS'

# BalloonPredictor release upload exclusions
.env.*
!.env.example
venv/
env/
*.nc
*.nc4
*.grib
*.grib2
*.grb
*.grb2
wrfout_*
EXCLUSIONS
fi

# Only fill missing commit identity, and only for this checkout.
if ! git var GIT_AUTHOR_IDENT >/dev/null 2>&1; then
  [[ -n "$(git config user.name || true)" ]] || git config user.name "$owner"
  [[ -n "$(git config user.email || true)" ]] || \
    git config user.email "$(gh api --hostname github.com user --jq '"\(.id)+\(.login)@users.noreply.github.com"')"
fi

temporary_dir=$(mktemp -d)
git add -- .
git ls-files -z > "$temporary_dir/tracked-files"

# Ignore rules do not hide files already tracked by a previous attempt.
while IFS= read -r -d '' file; do
  case "$file" in
    .env.example|*/.env.example|.cdsapirc.example|*/.cdsapirc.example|data/*/.gitkeep) ;;
    .env|*/.env|.env.*|*/.env.*|.cdsapirc|*/.cdsapirc|config.local.yml|config.local.yaml|data/*|.venv/*|venv/*|env/*|node_modules/*|*.nc|*.nc4|*.grib|*.grib2|*.grb|*.grb2|wrfout_*|*/wrfout_*)
      fail "Local data or credentials are tracked: $file. Use a fresh extraction with the included .gitignore." ;;
  esac
done < "$temporary_dir/tracked-files"

if git rev-parse --verify HEAD >/dev/null 2>&1; then
  git diff --cached --quiet || fail 'The saved v1.1 snapshot differs from these files. This script does not replace an existing release.'
else
  git commit -m 'Release BalloonPredictor v1.1'
fi
commit=$(git rev-parse HEAD)
if git show-ref --verify --quiet "refs/tags/$tag"; then
  [[ "$(git rev-parse "$tag^{commit}")" == "$commit" ]] || fail "$tag points to another commit."
else
  git tag -a "$tag" -m "$title"
fi

asset="$temporary_dir/balloon-predictor-v1.1.zip"
notes="$temporary_dir/release-notes.md"
git archive --format=zip --prefix=balloon-predictor/ --output="$asset" "$tag"
awk '
  /^## 1\.1\.0[[:space:]]*$/ {active=1; next}
  active && /^## / {exit}
  active {print}
' CHANGELOG.md > "$notes"
[[ -s "$notes" ]] || printf '%s\n' "$title" > "$notes"

# Repo creation fails safely if this account cannot create or access the repo.
if existing_private=$(gh repo view "$repository" --json isPrivate --jq '.isPrivate' 2>/dev/null); then
  if [[ "$visibility" == private && "$existing_private" != true ]] || \
     [[ "$visibility" == public && "$existing_private" != false ]]; then
    fail "The existing repository has different visibility. Choose the matching --public or --private option."
  fi
else
  gh repo create "$repository" "--$visibility" \
    --description 'Local balloon trajectory predictions with ERA5 and WRF'
fi
if [[ -z "$existing_remote" ]]; then
  git remote add origin "$remote_url"
fi
gh auth setup-git --hostname github.com

# Only an empty remote or this exact previous attempt can be used.
git ls-remote origin > "$temporary_dir/remote-refs"
while read -r remote_sha remote_ref; do
  case "$remote_ref" in
    HEAD|refs/heads/main)
      [[ "$remote_sha" == "$commit" ]] || fail 'GitHub already contains a different commit. No files have been overwritten.' ;;
    "refs/tags/$tag")
      [[ "$remote_sha" == "$(git rev-parse "refs/tags/$tag")" ]] || fail "GitHub already has a different $tag tag." ;;
    "refs/tags/$tag^{}")
      [[ "$remote_sha" == "$commit" ]] || fail "GitHub already has a different $tag commit." ;;
    *) fail "GitHub already contains another branch or tag: $remote_ref" ;;
  esac
done < "$temporary_dir/remote-refs"

git push -u origin main
git push origin "refs/tags/$tag"
if gh release view "$tag" --repo "$repository" >/dev/null 2>&1; then
  asset_names=$(gh release view "$tag" --repo "$repository" --json assets --jq '.assets[].name')
  if ! awk '$0 == "balloon-predictor-v1.1.zip" {found=1} END {exit !found}' <<< "$asset_names"; then
    gh release upload "$tag" "$asset" --repo "$repository"
  fi
  if [[ "$(gh release view "$tag" --repo "$repository" --json isDraft --jq '.isDraft')" == true ]]; then
    gh release edit "$tag" --repo "$repository" --draft=false
  fi
  printf '\n%s\n' 'This release already exists; no duplicate was created.'
else
  gh release create "$tag" "$asset" --repo "$repository" --verify-tag \
    --title "$title" --notes-file "$notes"
fi
printf '\n%s\n%s\n' 'Upload complete.' "https://github.com/$repository/releases/tag/$tag"
