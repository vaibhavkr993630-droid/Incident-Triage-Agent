# Disk space exhausted

Owner: platform team. Applies to: database hosts, log volumes.

## When to use
Writes fail with "no space left on device", postgres refuses connections after a crash, or
a disk usage alert fires.

## How to confirm
Check filesystem usage on the host and the size of the database and WAL directories.

## Mitigation
Free space by removing old logs or archives, extend the volume, then investigate growth.
