---
title: Deployment Guide
version: 2
---

# Deployment Guide

The **Celurion** server requires Go 1.22 and a running instance of `postgres`.
Install dependencies with `go mod download`, then run the migration.

## Configuration

Set the `DATABASE_URL` variable before starting. See [the reference](https://example.com/docs) for details.

| Option | Default | Description |
| --- | --- | --- |
| `port` | 8080 | Listening port for the HTTP server |
| `workers` | 4 | Number of background workers |

1. Clone the repository from GitHub Actions.
2. Run `make build` to produce the binary.
3. Deploy to production using the {{deploy_target}} pipeline.

> Warning: never commit secrets to the repository.

```python
def hello():
    print("do not translate me")
```

The formula $E = mc^2$ should survive untouched.
