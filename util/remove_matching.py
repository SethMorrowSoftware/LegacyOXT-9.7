#!/usr/bin/env python

# This script removes lines from a file that match any lines from a second file.
# Note that this will not preserve the order of lines, and will remove duplicates.

#Usage: remove_matching <file1> <file2> [<file3>]

# Output is written to stdout, or to a third file if specified

# Runs under both Python 2.7 and Python 3.

import sys

if len(sys.argv) < 3 or len(sys.argv) > 4:
	print "ERROR: incorrect number of arguments"
	sys.exit(1)

if sys.version_info[0] >= 3:
	# Use Latin-1 so that each byte is one character: any input can be read,
	# the output bytes are the input bytes and lines sort in byte order, as
	# they do with Python 2's byte strings.
	def open_text(path, mode="r"):
		return open(path, mode, encoding="latin-1")
else:
	open_text = open
	
file1 = set(open_text(sys.argv[1]).readlines())
file2 = set(open_text(sys.argv[2]).readlines())

if len(sys.argv) == 4:
	outfile = open_text(sys.argv[3], mode="w")
else:
	outfile = sys.stdout
	if hasattr(outfile, "reconfigure"):
		# Python 3.7 and later: write the Latin-1 text as the original bytes
		outfile.reconfigure(encoding="latin-1")

diff = list(file1.difference(file2))
diff.sort()
outfile.writelines(diff)

if outfile is not sys.stdout:
	outfile.close()
