# Put BalloonPredictor v1.1 on GitHub

Keep this as its own repository, beside the ERA5 viewer repository. They are
separate applications with matching packaging and common weather inputs.

## Create a new repository

1. Extract the release and open a terminal in the `balloon-predictor` folder.
2. Create an **empty** repository named `balloon-predictor` in your GitHub
   account. Leave automatic README, licence and gitignore creation disabled:
   those files are already in this package.
3. Run the commands below, replacing `YOUR_USERNAME` with your GitHub username.

```bash
cd /data1/tim/balloon-predictor
git init -b main
git add .
git status --short
git commit -m "Release BalloonPredictor v1.1"
git remote add origin https://github.com/YOUR_USERNAME/balloon-predictor.git
git push -u origin main
git tag -a v1.1.0 -m "BalloonPredictor v1.1"
git push origin v1.1.0
```

GitHub may ask you to authenticate through your installed credential manager.
Do not put a token in the remote URL. Alternatively use your configured SSH
remote or GitHub Desktop to add this existing folder and publish it.

Before the first commit, `git status --short` should show source, documentation,
configuration templates and bundled local map assets. The included `.gitignore`
excludes weather data, generated results, environments, caches, credentials and
Node packages. Keep your ERA5/WRF volumes outside the repository. Personal
configuration files should stay outside the project or use `config.local.yml`,
which is ignored.

A Python test workflow in `.github/workflows/tests.yml` runs automatically on
pushes and pull requests. It uses synthetic fixtures and does not need CDS
credentials or your real weather files.

## Update an existing repository

If you already have the predictor in Git, work inside your existing checkout.
Copy this release's source files into it while preserving `.git`, personal
configuration, credentials and real data. Review before committing:

```bash
git status --short
git diff
# After checking the intended changes:
git add .
git commit -m "Upgrade BalloonPredictor to v1.1"
git push
git tag -a v1.1.0 -m "BalloonPredictor v1.1"
git push origin v1.1.0
```

Do not replace an existing `v1.1.0` tag: use a new patch version for subsequent
changes. If this is a shared repository, use a branch and a pull request.

## Optional GitHub Release

In your repository, choose **Releases → Draft a new release**, select tag
`v1.1.0`, title it **BalloonPredictor v1.1**, and paste the relevant section of
`CHANGELOG.md`. Attach the provided release ZIP if you want a ready-to-extract
download alongside GitHub's source archives.

No GitHub account or repository was connected for this task; the release is
prepared for you to upload using these steps.
