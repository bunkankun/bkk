# CLAUDE.md

This is the main folder for the Bunkankun project. The project consolidates premodern Chinese texts from various sources, especially Kanripo (KRP) and HXWD/TLS and CBETA to a new YAML based format.

The basics of the project, including a discussion of this format is in `bunkankun.md`.  Some important facts are recorded in `MEMORy.md`. 

## BKK module

There is a python module `bkk` with the source code sitting in `module/`, this handles the interaction with the text bundles. A web frontend, also called bunkankun is sitting at `module/web`. The UI has been prototyped with Claude Design, the handoff files are in `module/specs`.

Input of krp and tls shape used for developing and testing is in `module/input`, generated output bundles are in `module/output`. Export back into the original format shape is in `module/export`.  These folders should not be added to git. 

`module/README.md` gives the basic instructions, further documentation for the submodules is in `docs/`
