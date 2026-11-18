# PPSSPP homebrew store

The curated homebrew store in PPSSPP (Homebrew & Demos tab → Homebrew store), served from
https://store.ppsspp.org/. No user uploads.

## Layout

`pub/` is exactly what's served:

- `index.json`: the listing. It's the only file the app fetches.
- `files/<file>.zip`: the downloads.
- `icons/`: icons referenced by `icon-url`.
- `index.html` and `robots.txt`: for anyone opening the site in a browser.

Tools:

- `check.py`: validates `index.json` against the files.
- `deploy.sh dev|prod`: runs the check and uploads.

## How the app uses it

See `UI/Store.cpp` in the ppsspp repo. Every app version ever released reads the same
`index.json`, so changes must be backwards compatible. Only add optional fields; never remove
fields or change their meaning.

- `file` is the folder the game installs to, in `PSP/GAME/<file>`. The app considers the game
  installed if that folder exists, and deletes it on uninstall. Never change it for an
  existing entry.
- The download is `files/<file>.zip`, or `download-url` if set. `download-url` must be an
  absolute URL, since old apps use it as is.
- The zip must contain `<file>/EBOOT.PBP` (plus the rest of that folder) and nothing else.
- `size` is the zip size in bytes. `check.py` verifies it.
- `author`: write `&` as `&&`. Old app versions drop a single `&`, and new ones turn `&&` back
  into `&`.
- `en_US.name` and `en_US.description` are required. Other languages (`sv_SE`, …) can override
  either.
- `content-rating` 100 or more hides the entry in the iOS App Store build.
- Don't use `hidden`. Current versions ignore it and old ones handle it badly. Remove the entry
  instead.
- `category` isn't used by the app yet.

## Adding or updating a game

1. Repack the zip as `<file>/...` with `EBOOT.PBP` directly inside, and put it in `pub/files/`.
2. Add an entry to `pub/index.json`. New entries go at the top, since the app lists them in
   order.
3. `./check.py`, then `./deploy.sh dev` and test in the app.
4. Commit, then `./deploy.sh prod`.

**Updating a game:** don't replace an existing zip. Cloudflare caches zips for 7 days, so
users would keep getting the old one, and `deploy.sh` refuses to do it. Add the new version
as e.g. `files/<file>-1.1.zip`, point `download-url` at
`https://store.ppsspp.org/files/<file>-1.1.zip`, and keep `file` unchanged.

## Deploying

```
./deploy.sh dev        # /srv/www/store.ppsspp.org/dev   (dev-store.ppsspp.org)
./deploy.sh prod       # /srv/www/store.ppsspp.org/pub   (store.ppsspp.org)
./deploy.sh prod -n    # dry run: show what would change
```

Nothing is ever deleted on the server, and `index.json` is uploaded last.

## History

Until November 2026, the store was edited by hand on the server, in
`/srv/www/store.ppsspp.org`. That folder is part of an old checkout of `hrydgard/web.git`,
which tracked `index.json` but not the zips. This repo was created from the live files on
2026-11-18.

Fixes made then:

- Corrected the `size` of flower and PSPRev_v03a, which didn't match their zips.

Left out of the repo, but still on the server:

- `index2.json` and `indexall.json` (broken JSON). No app version fetches either.
- `files/temp/`, `files/Ozone.iso` (a duplicate of `Ozone.zip`) and `files/flo.wer.zip`.
- `src/`, which has an unlisted `pinball.zip`.
