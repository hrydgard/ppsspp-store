#!/usr/bin/env python3
"""Validates pub/index.json against the files in pub/. Run by deploy.sh; exits 1 on errors.

Rules come from how PPSSPP reads the store (UI/Store.cpp in the ppsspp repo). Every version
of the app that's out there reads this file, so the format must stay backwards compatible:
never remove fields or change what they mean, only add new optional ones.
"""
import json, os, re, sys, zipfile

from metadata import png_problem

ROOT = os.path.dirname(os.path.abspath(__file__))
PUB = os.path.join(ROOT, "pub")
STORE_URL = "https://store.ppsspp.org/"

# Fields the app reads, plus some informational ones that are already in use.
KNOWN_KEYS = {"file", "author", "size", "download-url", "icon-url", "pic1-url", "content-rating",
              "website-url", "license", "category", "hidden", "version", "last-updated"}
LANG_RE = re.compile(r"^[a-z]{2}_[A-Z]{2}$")

errors, warnings = [], []


def err(msg):
    errors.append(msg)


def warn(msg):
    warnings.append(msg)


def no_duplicate_keys(pairs):
    keys = [k for k, _ in pairs]
    dups = {k for k in keys if keys.count(k) > 1}
    if dups:
        # json.load would silently keep only the last one.
        err(f"duplicate keys in an object: {sorted(dups)}")
    return dict(pairs)


def local_path(url):
    """Path in pub/ for an URL on the store itself, else None."""
    if url.startswith(STORE_URL):
        url = "/" + url[len(STORE_URL):]
    elif url.startswith("http://store.ppsspp.org/"):
        url = "/" + url[len("http://store.ppsspp.org/"):]
    if url.startswith("/"):
        return os.path.join(PUB, url.lstrip("/").split("?")[0])
    return None


def check_zip(path, name, where):
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        err(f"{where}: {path} is not a valid zip")
        return
    bad = z.testzip()
    if bad:
        err(f"{where}: corrupt file in zip: {bad}")
    names = z.namelist()
    if f"{name}/EBOOT.PBP" not in names:
        err(f"{where}: zip must contain {name}/EBOOT.PBP (the folder name must equal \"file\", "
            "that's how the app tells if it's installed)")
    stray = sorted({n for n in names if not n.startswith(name + "/")})
    if stray:
        warn(f"{where}: files outside {name}/ in zip: {stray}")


def check_lang_block(block, where):
    if not isinstance(block, dict):
        err(f"{where}: must be an object")
        return
    for k, v in block.items():
        if k not in ("name", "description"):
            warn(f"{where}: unknown key {k!r}")
        if not isinstance(v, str):
            err(f"{where}.{k}: must be a string")


def check_entry(e, i, categories, seen_files, referenced):
    if not isinstance(e, dict):
        err(f"entries[{i}]: must be an object")
        return
    f = e.get("file")
    where = f"entries[{i}] ({f})"
    if not isinstance(f, str) or not f:
        err(f"{where}: \"file\" missing (the app skips the entry)")
        return
    # The app rejects these (IsSafePathComponent), and the folder is deleted by this name on uninstall.
    if f in (".", "..") or re.search(r'[/\\:*?"<>|]', f) or f.startswith("."):
        err(f"{where}: \"file\" must be a plain folder name")
    if f in seen_files:
        err(f"{where}: \"file\" {f!r} used twice")
    seen_files.add(f)

    en = e.get("en_US")
    if not isinstance(en, dict) or not isinstance(en.get("name"), str) or not en["name"]:
        err(f"{where}: needs en_US.name")
    for k, v in e.items():
        if LANG_RE.match(k):
            check_lang_block(v, f"{where}.{k}")
        elif k not in KNOWN_KEYS:
            warn(f"{where}: unknown key {k!r} (fine if intentional; old apps ignore it)")

    author = e.get("author")
    if not isinstance(author, str):
        err(f"{where}: \"author\" missing")
    elif re.search(r"(?<!&)&(?!&)", author.replace("&&", "")):
        err(f"{where}: write & as && in author (old app versions eat a single &)")

    for k in ("website-url", "license", "version", "last-updated"):
        if k in e and not isinstance(e[k], str):
            err(f"{where}: {k} must be a string")
    if "content-rating" in e and not isinstance(e["content-rating"], int):
        err(f"{where}: content-rating must be an integer")
    if "hidden" in e and e["hidden"]:
        warn(f"{where}: \"hidden\" is ignored by current app versions and broken in old ones; "
             "remove the entry to hide it")
    cat = e.get("category")
    if cat is not None and cat not in categories:
        warn(f"{where}: category {cat!r} not defined in \"categories\" (the app doesn't use categories yet)")

    # The download.
    url = e.get("download-url")
    if url:
        if not re.match(r"^https?://", url):
            err(f"{where}: download-url must be absolute (old apps use it as is)")
        zpath = local_path(url)
    else:
        zpath = os.path.join(PUB, "files", f + ".zip")
    size = e.get("size")
    if not isinstance(size, int):
        err(f"{where}: \"size\" must be an integer (bytes)")
    if zpath:
        referenced.add(os.path.normpath(zpath))
        if not os.path.exists(zpath):
            err(f"{where}: {os.path.relpath(zpath, ROOT)} missing")
        else:
            if isinstance(size, int) and size != os.path.getsize(zpath):
                err(f"{where}: size is {size}, but the zip is {os.path.getsize(zpath)} bytes")
            check_zip(zpath, f, where)

    # icon-url is shown by the app. pic1-url (the PSP's PIC1 background) isn't used yet.
    for field in ("icon-url", "pic1-url"):
        url = e.get(field)
        if not url:
            continue
        ipath = local_path(url)
        if not ipath:
            continue
        if not os.path.exists(ipath):
            err(f"{where}: {field} {url} missing")
        else:
            prob = png_problem(open(ipath, "rb").read())
            if prob:
                err(f"{where}: {field} {url} is broken: {prob}")


def main():
    path = os.path.join(PUB, "index.json")
    try:
        with open(path, encoding="utf-8") as fp:
            root = json.load(fp, object_pairs_hook=no_duplicate_keys)
    except json.JSONDecodeError as ex:
        print(f"ERROR: pub/index.json is not valid JSON: {ex}")
        sys.exit(1)

    if root.get("type") != "PPSSPP Store":
        err('top level: "type" must be "PPSSPP Store"')
    categories = root.get("categories", {})
    if not isinstance(categories, dict):
        err('top level: "categories" must be an object')
        categories = {}
    entries = root.get("entries")
    if not isinstance(entries, list):
        err('top level: "entries" must be an array')
        entries = []

    seen, referenced = set(), set()
    for i, e in enumerate(entries):
        check_entry(e, i, categories, seen, referenced)

    zips = os.path.join(PUB, "files")
    for f in sorted(os.listdir(zips)):
        if os.path.normpath(os.path.join(zips, f)) not in referenced:
            warn(f"pub/files/{f} isn't used by any entry")

    for w in warnings:
        print("warning:", w)
    for e in errors:
        print("ERROR:", e)
    print(f"{len(entries)} entries, {len(errors)} errors, {len(warnings)} warnings")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
