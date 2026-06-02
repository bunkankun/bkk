# Bunkankun (BKK)

Consolidates premodern Chinese texts from Kanripo (KRP), TLS/HXWD, and CBETA into a unified YAML-based archival format. See `bunkankun.md` for a full format description.

## Installation

Requires Python 3.10+. From the `module/` directory:

```bash
pip install .
```

To include the web server:

```bash
pip install ".[serve]"
```

This installs the `bkk` CLI entry point.

## Configuration

Copy `module/.bkkrc.sample` to `~/.bkkrc` and set paths for your machine:

```yaml
global:
  corpus: /path/to/corpus
  tls_root: /path/to/tls
  krp_root: /path/to/krp
```

See [module/README.md](module/README.md) for full CLI and API documentation.
