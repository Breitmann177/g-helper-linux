"""Minimaler ICC-v2-Generator für Matrix/TRC-Displayprofile (ohne externe Bibliotheken)."""
import struct
import time

D50 = (0.9642, 1.0, 0.8249)

PRIMARIES = {
    #              R            G             B             Weiß
    "sRGB":       ((0.640, 0.330), (0.300, 0.600), (0.150, 0.060), (0.3127, 0.3290)),
    "DCI-P3":     ((0.680, 0.320), (0.265, 0.690), (0.150, 0.060), (0.3140, 0.3510)),
    "Display P3": ((0.680, 0.320), (0.265, 0.690), (0.150, 0.060), (0.3127, 0.3290)),
}

BRADFORD = ((0.8951, 0.2664, -0.1614), (-0.7502, 1.7135, 0.0367), (0.0389, -0.0685, 1.0296))


def _mul(a, b):
    return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3))


def _vec(m, v):
    return tuple(sum(m[i][k] * v[k] for k in range(3)) for i in range(3))


def _inv(m):
    (a, b, c), (d, e, f), (g, h, i) = m
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    return ((( e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det),
            (( f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det),
            (( d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det))


def _xyz(xy):
    x, y = xy
    return (x / y, 1.0, (1 - x - y) / y)


def _rgb_to_xyz_d50(prims):
    r, g, b, w = prims
    cols = [_xyz(r), _xyz(g), _xyz(b)]
    m = tuple(tuple(cols[j][i] for j in range(3)) for i in range(3))
    s = _vec(_inv(m), _xyz(w))
    m = tuple(tuple(m[i][j] * s[j] for j in range(3)) for i in range(3))
    # Bradford-Adaption Quellweiß → D50
    src, dst = _vec(BRADFORD, _xyz(w)), _vec(BRADFORD, D50)
    diag = ((dst[0] / src[0], 0, 0), (0, dst[1] / src[1], 0), (0, 0, dst[2] / src[2]))
    adapt = _mul(_inv(BRADFORD), _mul(diag, BRADFORD))
    return _mul(adapt, m)


def _s15(v):
    return struct.pack(">i", int(round(v * 65536)))


def _xyz_tag(xyz):
    return b"XYZ " + b"\0" * 4 + b"".join(_s15(c) for c in xyz)


def _curv(gamma):
    return b"curv" + b"\0" * 4 + struct.pack(">I", 1) + struct.pack(">H", int(round(gamma * 256))) + b"\0\0"


def _desc(text):
    a = text.encode("ascii", "replace") + b"\0"
    return (b"desc" + b"\0" * 4 + struct.pack(">I", len(a)) + a
            + struct.pack(">II", 0, 0) + struct.pack(">HB", 0, 0) + b"\0" * 67)


def _text(text):
    return b"text" + b"\0" * 4 + text.encode("ascii", "replace") + b"\0"


def display_profile(name, prims, gamma=2.2):
    m = _rgb_to_xyz_d50(prims)
    tags = [
        (b"desc", _desc(name)),
        (b"cprt", _text("No copyright, use freely (G-Helper Linux)")),
        (b"wtpt", _xyz_tag(D50)),
        (b"rXYZ", _xyz_tag((m[0][0], m[1][0], m[2][0]))),
        (b"gXYZ", _xyz_tag((m[0][1], m[1][1], m[2][1]))),
        (b"bXYZ", _xyz_tag((m[0][2], m[1][2], m[2][2]))),
        (b"rTRC", _curv(gamma)),
        (b"gTRC", _curv(gamma)),
        (b"bTRC", _curv(gamma)),
    ]
    offset = 128 + 4 + 12 * len(tags)
    table, data = b"", b""
    for sig, body in tags:
        while (offset + len(data)) % 4:
            data += b"\0"
        table += sig + struct.pack(">II", offset + len(data), len(body))
        data += body
    while len(data) % 4:
        data += b"\0"
    size = offset + len(data)
    t = time.gmtime()
    header = (struct.pack(">I", size) + b"lcms" + struct.pack(">I", 0x02100000) + b"mntrRGB XYZ "
              + struct.pack(">6H", t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec)
              + b"acsp" + b"APPL" + struct.pack(">I", 0) + b"\0" * 8 + b"\0" * 8 + struct.pack(">I", 0)
              + b"".join(_s15(c) for c in D50) + b"ghlp" + b"\0" * 16 + b"\0" * 28)
    assert len(header) == 128, len(header)
    return header + struct.pack(">I", len(tags)) + table + data
