#!/usr/bin/env python3
"""Extracts metadata from the games' zips: the title from PARAM.SFO, and ICON0.PNG and
PIC1.PNG into pub/icons/<file>_icon0.png and <file>_pic1.png.

Everything is read from <file>/EBOOT.PBP in the zip, or from loose <file>/ICON0.PNG,
PIC1.PNG and PARAM.SFO files if the PBP doesn't have them. Some EBOOT.PBPs are bare ELF
executables, which have no metadata at all. Every PNG is fully validated (chunk CRCs, the
compressed image data and its size), since some old hand-extracted ones were truncated.

  ./metadata.py                report for all entries in pub/index.json, change nothing
  ./metadata.py Ozone bg3      only these entries ("file" names)
  ./metadata.py some.zip       look inside a zip that isn't in the store yet
  ./metadata.py --write        write pub/icons/<file>_icon0.png and _pic1.png
  ./metadata.py --set-urls     also set icon-url and pic1-url in index.json where missing
                               (implies --write)

An image is only written if it's valid and differs from what's there.
"""
import json, os, struct, sys, zipfile, zlib

ROOT = os.path.dirname(os.path.abspath(__file__))
PUB = os.path.join(ROOT, "pub")
ICONS = os.path.join(PUB, "icons")

PBP_MAGIC = b"\x00PBP"
ELF_MAGIC = b"\x7fELF"
# The PBP header is magic, version, then offsets of these sections, in this order.
PBP_SECTIONS = ["PARAM.SFO", "ICON0.PNG", "ICON1.PMF", "PIC0.PNG", "PIC1.PNG", "SND0.AT3",
                "DATA.PSP", "DATA.PSAR"]
# Image section -> (suffix of the file in pub/icons, index.json field).
IMAGES = {"ICON0.PNG": ("icon0", "icon-url"), "PIC1.PNG": ("pic1", "pic1-url")}
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def pbp_sections(data):
    """Returns {section name: bytes} for the non-empty sections of a PBP file."""
    if len(data) < 40 or data[:4] != PBP_MAGIC:
        raise ValueError("not a PBP file")
    offsets = list(struct.unpack("<8I", data[8:40])) + [len(data)]
    out = {}
    for i, name in enumerate(PBP_SECTIONS):
        start, end = offsets[i], offsets[i + 1]
        if not (40 <= start <= end <= len(data)):
            raise ValueError(f"bad offset for {name}")
        if end > start:
            out[name] = data[start:end]
    return out


def parse_sfo(data):
    """Returns the PARAM.SFO key/values as a dict (strings and ints)."""
    if data[:4] != b"\x00PSF":
        raise ValueError("not a PARAM.SFO")
    key_start, data_start, count = struct.unpack("<III", data[8:20])
    out = {}
    for i in range(count):
        key_off, fmt, length, _, data_off = struct.unpack("<HHIII", data[20 + 16 * i:36 + 16 * i])
        k = data[key_start + key_off:data.index(b"\0", key_start + key_off)].decode("utf-8", "replace")
        v = data[data_start + data_off:data_start + data_off + length]
        if fmt == 0x0404:
            out[k] = struct.unpack("<I", v[:4])[0]
        else:
            out[k] = v.split(b"\0")[0].decode("utf-8", "replace")
    return out


def png_problem(data):
    """Returns None if data is a complete, valid PNG, else what's wrong with it."""
    if data[:8] != PNG_MAGIC:
        return "not a PNG"
    p, idat, ihdr = 8, b"", None
    while True:
        if p + 8 > len(data):
            return "truncated (no IEND)"
        n, ctype = struct.unpack(">I4s", data[p:p + 8])
        if p + 12 + n > len(data):
            return f"truncated in {ctype.decode('latin1')} chunk"
        body = data[p + 8:p + 8 + n]
        if zlib.crc32(ctype + body) != struct.unpack(">I", data[p + 8 + n:p + 12 + n])[0]:
            return f"CRC error in {ctype.decode('latin1')} chunk"
        if ctype == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body)
        elif ctype == b"IDAT":
            idat += body
        p += 12 + n
        if ctype == b"IEND":
            break
    if not ihdr:
        return "no IHDR"
    try:
        raw = zlib.decompress(idat)
    except zlib.error as ex:
        return f"image data corrupt ({ex})"
    w, h, depth, color, _, _, interlace = ihdr
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color)
    if channels is None:
        return f"bad color type {color}"
    if not interlace:
        expected = h * (1 + (w * channels * depth + 7) // 8)
        if len(raw) != expected:
            return f"image data is {len(raw)} bytes, expected {expected}"
    return None


def png_size(data):
    return struct.unpack(">II", data[16:24])


def read_game_zip(zip_path, name=None):
    """Reads metadata from a game zip. name is the game folder in the zip; if None, it's the
    shallowest folder containing EBOOT.PBP. Returns a dict with:
      name, sfo (dict or None), images ({"ICON0.PNG": (bytes, source)}), notes ([str])."""
    res = {"name": name, "sfo": None, "images": {}, "notes": []}
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        if name is None:
            eboots = sorted((n for n in names if n.upper().endswith("EBOOT.PBP") and "/" in n),
                            key=lambda n: n.count("/"))
            if not eboots:
                res["notes"].append("no EBOOT.PBP in a folder in the zip")
                return res
            name = res["name"] = eboots[0].rsplit("/", 1)[0]
        lower = {n.lower(): n for n in names}
        sections = {}
        eboot = lower.get(f"{name}/EBOOT.PBP".lower())
        if not eboot:
            res["notes"].append(f"no {name}/EBOOT.PBP")
        else:
            data = z.read(eboot)
            if data[:4] == ELF_MAGIC:
                res["notes"].append("EBOOT.PBP is a bare ELF executable, it has no title or images")
            else:
                try:
                    sections = pbp_sections(data)
                except ValueError as ex:
                    res["notes"].append(f"{eboot}: {ex}")
        # Loose files, used if the PBP doesn't have a valid one.
        for sect in ["PARAM.SFO"] + list(IMAGES):
            loose = lower.get(f"{name}/{sect}".lower())
            if loose and (sect not in sections or (sect in IMAGES and png_problem(sections[sect]))):
                sections[sect] = z.read(loose)
                src = loose
            else:
                src = f"{eboot}:{sect}"
            if sect in IMAGES and sect in sections:
                res["images"][sect] = (sections[sect], src)
        if "PARAM.SFO" in sections:
            try:
                res["sfo"] = parse_sfo(sections["PARAM.SFO"])
            except (ValueError, struct.error) as ex:
                res["notes"].append(f"PARAM.SFO: {ex}")
    return res


def set_urls(changes):
    """changes: {file name: {field: url}}. Adds or fills in those fields in index.json,
    editing it as text to keep its formatting, and verifies that nothing else changed."""
    path = os.path.join(PUB, "index.json")
    text = open(path, encoding="utf-8").read()
    before = json.loads(text)
    lines = text.split("\n")

    def indent_of(line):
        return len(line) - len(line.lstrip())

    for name, fields in changes.items():
        for field, url in fields.items():
            idx = next(i for i, l in enumerate(lines) if l.strip().rstrip(",") == f'"file": "{name}"')
            ind = indent_of(lines[idx])
            # The entry's own lines: everything around "file" indented at least as much.
            lo = idx
            while lo > 0 and (not lines[lo - 1].strip() or indent_of(lines[lo - 1]) >= ind):
                lo -= 1
            hi = idx
            while hi + 1 < len(lines) and (not lines[hi + 1].strip() or indent_of(lines[hi + 1]) >= ind):
                hi += 1
            existing = [i for i in range(lo, hi + 1)
                        if indent_of(lines[i]) == ind and lines[i].strip().startswith(f'"{field}"')]
            if existing:
                i = existing[0]
                comma = "," if lines[i].rstrip().endswith(",") else ""
                lines[i] = " " * ind + f'"{field}": "{url}"{comma}'
            else:
                # Before "file", which keeps the commas right whether or not "file" is last.
                lines.insert(idx, " " * ind + f'"{field}": "{url}",')
    new_text = "\n".join(lines)
    after = json.loads(new_text)
    for e in before["entries"] + after["entries"]:
        for field in changes.get(e.get("file"), {}):
            e.pop(field, None)
    if before != after:
        sys.exit("set_urls: unexpected change to index.json, not writing")
    open(path, "w", encoding="utf-8").write(new_text)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    do_urls = "--set-urls" in sys.argv
    write = "--write" in sys.argv or do_urls
    index = json.load(open(os.path.join(PUB, "index.json"), encoding="utf-8"))
    by_file = {e["file"]: e for e in index["entries"]}

    jobs = []  # (label, zip path, folder name or None, entry or None)
    for a in args or list(by_file):
        if a.endswith(".zip"):
            jobs.append((a, a, None, None))
        elif a in by_file:
            url = by_file[a].get("download-url", "")
            zpath = (os.path.join(PUB, url.split("store.ppsspp.org/", 1)[1]) if "store.ppsspp.org/" in url
                     else os.path.join(PUB, "files", a + ".zip"))
            jobs.append((a, zpath, a, by_file[a]))
        else:
            sys.exit(f"{a}: not an entry in index.json or a .zip")

    url_changes, problems = {}, 0
    for label, zpath, name, entry in jobs:
        res = read_game_zip(zpath, name)
        name = res["name"]
        print(f"== {label}" + (f" (game folder: {name})" if entry is None and name else ""))
        for n in res["notes"]:
            print(f"   note: {n}")
        if res["sfo"]:
            sfo = res["sfo"]
            print(f"   PARAM.SFO: title {sfo.get('TITLE')!r}, disc ID {sfo.get('DISC_ID')!r}, "
                  f"version {sfo.get('APP_VER') or sfo.get('DISC_VERSION')!r}")
        for sect, (suffix, field) in IMAGES.items():
            src_data = res["images"].get(sect)
            if entry is None:
                # A zip that isn't in the store: just report.
                if src_data:
                    prob = png_problem(src_data[0])
                    print(f"   {suffix}: {src_data[1]} " + (f"BROKEN: {prob}" if prob else "%dx%d" % png_size(src_data[0])))
                else:
                    print(f"   {suffix}: none")
                continue
            out = os.path.join(ICONS, f"{name}_{suffix}.png")
            old = open(out, "rb").read() if os.path.exists(out) else None
            old_prob = None if old is None else png_problem(old)
            if not src_data:
                status = "none in zip" + ("" if old is None else f"; existing file {'BROKEN: ' + old_prob if old_prob else 'ok'}")
                if old_prob:
                    problems += 1
            else:
                data, src = src_data
                prob = png_problem(data)
                if prob:
                    problems += 1
                    status = f"BROKEN in zip ({src}): {prob}"
                elif old == data:
                    status = "up to date"
                else:
                    what = "new" if old is None else ("replaces BROKEN file" if old_prob else "differs from file")
                    status = f"{what} ({src}, %dx%d)" % png_size(data)
                    if write:
                        open(out, "wb").write(data)
                        status += ", written"
            # Link it from index.json if the file exists (now) and is valid.
            if os.path.exists(out) and not png_problem(open(out, "rb").read()) and not entry.get(field):
                url_changes.setdefault(name, {})[field] = f"/icons/{name}_{suffix}.png"
                status += f"; {field} not set" + ("" if do_urls else " (--set-urls)")
            print(f"   {suffix}: {status}")

    if do_urls and url_changes:
        set_urls(url_changes)
        print("Updated index.json: " + ", ".join(f"{n} ({', '.join(f)})" for n, f in url_changes.items()))
    if not write:
        print("(report only; --write to update pub/icons, --set-urls to also link them)")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
