# Heading after a byte order mark

The mark is a real character in the source and must survive as one. Decoding
with utf-8-sig would swallow it and hide a defect.
