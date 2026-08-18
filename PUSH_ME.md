# How to get this onto GitHub

Everything is already committed on `main` in this folder, on top of the repo's
existing history. From this folder:

    git push origin main

If `origin` is not set (you extracted this without the `.git` folder):

    git init -b main
    git remote add origin https://github.com/imklubb/no100f-poptracker.git
    git fetch origin main
    git reset --mixed FETCH_HEAD
    git add -A
    git commit -m "Add full pack, regenerate logic, enable auto-update"
    git push origin main

## Then cut the 1.0.1 release

    git tag v1.0.1
    git push origin v1.0.1

The `Release pack` workflow builds `ap_n100f_1.0.1.zip`, writes its sha256 into
`versions.json` on `main`, and attaches the zip to a GitHub release. PopTracker
reads `versions.json` from `main`, so once that lands, existing installs update
themselves.

If you would rather not use the workflow, `dist/ap_n100f_1.0.1.zip` is already
built and `versions.json` already has its sha256 - just attach that exact zip to
a release tagged `v1.0.1` by hand.
