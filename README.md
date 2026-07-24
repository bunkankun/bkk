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
## Bootstrapping the install

This requires the github `gh` tool to be installed and authenticated to work best.

Here are the commands to get a complete local copy running:
```bash
bkk repo diff --download-missing
bkk index merge
bkk index catalog
```
This will clone all texts from @bkkbooks and index them.
There are currently more than 12000 texts, so this will take a while.  The corpus wide index is more than 100 GB in size.
Once this has completed, and the server has been installed, the server can be started
```bash
bkk serve
```
The default is to serve from port 8000 on localhost.  
All `bkk` commands can be run with `--help` to learn more about subcommands, options and purpose. 

See [module/README.md](module/README.md) for full CLI and API documentation.
