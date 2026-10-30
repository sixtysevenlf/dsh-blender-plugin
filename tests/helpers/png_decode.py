
import sys, zlib, struct
for p in sys.argv[1:]:
    d = open(p, 'rb').read()
    pos = 8; idat = b''; ihdr = None
    while pos < len(d):
        ln = struct.unpack('>I', d[pos:pos+4])[0]; typ = d[pos+4:pos+8]
        body = d[pos+8:pos+8+ln]
        if typ == b'IHDR': ihdr = struct.unpack('>IIBBBBB', body)
        elif typ == b'IDAT': idat += body
        pos += 12 + ln
    raw = zlib.decompress(idat)
    print(p.split('/')[-1], 'size', ihdr[0], ihdr[1], 'bd', ihdr[2], 'ct', ihdr[3], 'rawlen', len(raw), 'filter0', raw[0], 'px', list(raw[1:5]), 'delta_px', list(raw[1+ihdr[0]*4:1+ihdr[0]*4+4]))
