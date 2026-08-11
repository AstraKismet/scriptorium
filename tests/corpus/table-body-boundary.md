# Where a table body ends

A GFM table body does not end at the last line holding a pipe. The line below
the last row renders as a cell, and CommonMark with no table rule reads the
whole run as one paragraph and that line as its lazy continuation, so the line
is prose under both readings and belongs to the model, not to the skeleton.

| Header A | Header B |
| -------- | -------- |
| value    | other    |
[x]: /url

| Header A | Header B |
| -------- | -------- |
| value    | other    |
[x]: /url "and a title"

| Header A | Header B |
| -------- | -------- |
| value    | other    |
Plain prose directly below the last row.

| Header A | Header B |
| -------- | -------- |
| value    | other    |
# A heading below the last row

| Header A | Header B |
| -------- | -------- |
| value    | other    |
> A blockquote below the last row.

| Header A | Header B |
| -------- | -------- |
| value    | other    |
- A list item below the last row.

| Header A | Header B |
| -------- | -------- |
| value    | other    |
| another  | row      |

| Header A | Header B |
| -------- | -------- |
| value    | other    |
    An indented line below the last row is prose to CommonMark and code to GFM.

| Header A | Header B |
| -------- | -------- |
| value    | other    |
```
A fenced block below the last row is a fence under both readings.
```

| Header A | Header B |
| -------- | -------- |
| value    | other    |
===

The table branch is tested above the list branch, so a table whose first line
carries a list marker is read here and the item's content column is recorded
here or nowhere.

- | Header A | Header B |
  | -------- | -------- |
  | value    | other    |
[x]: /url
===
    Four columns is two past this item's content column, so this line is the
    item's prose and not an indented code block.

Text after the tables.
