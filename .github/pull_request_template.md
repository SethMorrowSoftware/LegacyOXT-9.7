## Summary

<!-- What does this change do, and why? -->

## Pristine check

This repository keeps Tom Perry's OpenXTalk Lite 1.15 as it is (his two
working copies, merged at the tag `tom-perry-1.15-merged`). Tick one:

- [ ] Only this repository's own files change (`.github/`, `tools/ci/`, `tools/oxt/`, `tests/`, `Installer/openxtalk-lite/`, `Installer/linux/`, documentation)
- [ ] It changes Tom Perry's files **only because the build needs it** on a current compiler, SDK or system, and adds a section to `CHANGES-FROM-TOM.md` naming each such file, what changes in it and why; the commit message says what fails to build without it

Fixes of bugs and new features do not belong here; they belong in
[OXT-Beyond](https://github.com/SethMorrowSoftware/OpenXTalk-Beyond). A test
that fails because of Tom Perry's code goes into the baselines
(`tools/ci/*-baseline*.txt`) with a comment saying why.

## How was it tested?

<!-- The pull request's checks build and test Windows, macOS and Linux and
run the pristine guard; say what else you tried. -->

## Checklist

- [ ] I agree that this contribution is licensed under the GPLv3, like the rest of the repository (see `LICENSE`)
- [ ] No build output or generated files are included (`build-win-x86_64/`, `win-x86_64-bin/`, `build-linux-*/`, `linux-*-bin/`, `build-mac/`, `_build/`, `prebuilt/fetched/`, `prebuilt/unpacked/`, `prebuilt/packaged/`, `dist/`)
