import csv, sys

path = "/var/log/asterisk/cel-custom/cel_trace.csv"
want = sys.argv[1] if len(sys.argv) > 1 else None
rows = list(csv.reader(open(path)))

for r in (rows if want else rows[-60:]):
    time, kind, chan, uniqueid, linkedid = r[0][11:19], r[1], r[2], r[8], r[9]
    if want and want not in (uniqueid, linkedid):
        continue
    print(time, kind.ljust(13), chan.ljust(32), uniqueid.ljust(38), linkedid)
