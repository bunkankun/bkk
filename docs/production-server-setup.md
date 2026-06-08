# Production server setup (Apache + systemd on Ubuntu)

This guide deploys the BKK FastAPI server behind Apache on Ubuntu. The simplest
split is **Apache = TLS terminator + reverse proxy, uvicorn = serves both API
and built SPA** (via `--web-dist`). That way you don't have to enumerate API
path prefixes in the vhost.

## 1. System packages

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip apache2
sudo a2enmod proxy proxy_http headers ssl rewrite
```

## 2. Create a service user + venv

```bash
sudo useradd --system --create-home --home-dir /var/lib/bkk --shell /usr/sbin/nologin bkk
sudo -u bkk python3 -m venv /var/lib/bkk/venv
sudo -u bkk /var/lib/bkk/venv/bin/pip install --upgrade pip
sudo -u bkk /var/lib/bkk/venv/bin/pip install -e '/path/to/bkk/module[serve]'
```

Build the SPA once (on a machine with node) and copy `module/web/dist` to e.g.
`/var/lib/bkk/web-dist` (or point `--web-dist` at the repo's dist directory if
the bkk user can read it).

## 3. `.bkkrc` for the service

Put a `.bkkrc` at `/var/lib/bkk/.bkkrc` with at minimum `global.corpus`, plus
any `core`/`annotations` roots. The systemd unit below runs from
`/var/lib/bkk` so `load_rc()` will pick it up.

Optional: add a `serve.welcome` key pointing at a markdown file (e.g.
`/var/lib/bkk/welcome.md`). It is shown in the empty workspace on first load
and when a user clicks the logo. The file is read on each request, so edits
take effect without a restart:

```yaml
serve:
  welcome: /var/lib/bkk/welcome.md
```

## 4. Systemd unit — `/etc/systemd/system/bkk.service`

```ini
[Unit]
Description=BKK (Bunkankun) FastAPI server
After=network.target

[Service]
Type=simple
User=bkk
Group=bkk
WorkingDirectory=/var/lib/bkk
# Required for /admin/* and the admin web UI:
Environment=BKK_GITHUB_CLIENT_ID=...
Environment=BKK_GITHUB_CLIENT_SECRET=...
Environment=BKK_GITHUB_CALLBACK_URL=https://bunkankun.org/auth/github/callback
# Override the default admin team (bunkankun/bkk-admin) if needed:
# Environment=BKK_ADMIN_TEAM=your-org/your-team
# Required for the in-UI "Update" + "Restart server" buttons:
Environment=BKK_SOURCE_ROOT=/path/to/bkk
# Environment=BKK_SOURCE_BRANCH=master
ExecStart=/var/lib/bkk/venv/bin/bkk-serve \
    --host 127.0.0.1 --port 8000 \
    --web-dist /var/lib/bkk/web-dist
Restart=always
RestartSec=3
# Restart=always (not on-failure) so the in-UI "Restart server" button
# respawns the process on its clean SIGTERM exit. Use `systemctl stop bkk`
# to take the service offline without it bouncing back.
# Hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
# Allow read access to corpus/core/annotations roots if they live under /home:
ReadWritePaths=/var/lib/bkk
# Add ReadOnlyPaths=... for any external corpus directories the service needs.

[Install]
WantedBy=multi-user.target
```

Enable:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now bkk
sudo systemctl status bkk
journalctl -u bkk -f
```

## 5. Apache vhost — `/etc/apache2/sites-available/bkk.conf`

```apache
<VirtualHost *:80>
    ServerName bkk.example.org
    RewriteEngine On
    RewriteRule ^/(.*)$ https://%{HTTP_HOST}/$1 [R=301,L]
</VirtualHost>

<VirtualHost *:443>
    ServerName bkk.example.org

    SSLEngine on
    SSLCertificateFile      /etc/letsencrypt/live/bkk.example.org/fullchain.pem
    SSLCertificateKeyFile   /etc/letsencrypt/live/bkk.example.org/privkey.pem

    ProxyPreserveHost On
    ProxyRequests Off
    RequestHeader set X-Forwarded-Proto "https"

    # Everything to uvicorn (API + SPA static via --web-dist).
    ProxyPass        / http://127.0.0.1:8000/
    ProxyPassReverse / http://127.0.0.1:8000/

    # Generous timeout for index builds / large bundle responses.
    ProxyTimeout 120

    ErrorLog  ${APACHE_LOG_DIR}/bkk-error.log
    CustomLog ${APACHE_LOG_DIR}/bkk-access.log combined
</VirtualHost>
```

Enable and reload:

```bash
sudo a2ensite bkk
sudo apache2ctl configtest
sudo systemctl reload apache2
```

For TLS, use Let's Encrypt:

```bash
sudo apt install certbot python3-certbot-apache
sudo certbot --apache -d bkk.example.org
```

## 6. Verify

```bash
curl -sS http://127.0.0.1:8000/healthz        # local uvicorn
curl -sS https://bkk.example.org/healthz      # through Apache
```

## Notes / decisions worth flagging

- **Why proxy the SPA through uvicorn instead of serving `dist/` from Apache
  directly?** The app has many router prefixes (`/admin`, `/auth`, `/core`,
  `/annotations`, `/recipes`, `/bundles`, `/texts`, `/catalog`, `/search`,
  `/workspace`, plus redirects and SPA fallback). Letting uvicorn handle the
  SPA mount keeps Apache config trivial and avoids drift when routers are
  added. If you later want Apache to serve static assets for speed, add a
  `<Location /assets/>` block with `ProxyPass !` + `DocumentRoot`/`Alias`.
- **Admin access**: `/admin/*` and the admin UI require GitHub OAuth login plus
  active membership of the team in `BKK_ADMIN_TEAM` (default
  `bunkankun/bkk-admin`). The OAuth app must request the `read:org` scope.
  Membership is checked at login and cached for the session lifetime
  (30 days); removing a user from the team only takes effect once their
  session expires or they log out.
- **In-UI update/restart**: the admin panel exposes `Update (git pull + pip
  install)` and `Restart server` buttons (see `docs/web.md`). For these to
  work, set `BKK_SOURCE_ROOT` (or `serve.source_root` in `.bkkrc`) to the git
  checkout that the bkk venv was editable-installed from. The service user
  needs write access to that path. Restart sends SIGTERM and relies on
  systemd's `Restart=on-failure` to respawn — make sure that line is in the
  unit, or restart will simply stop the server.
- **OAuth callback**: if you use GitHub login, the callback URL must match
  what's registered with the GitHub app *and* what Apache exposes
  (`https://bkk.example.org/auth/github/callback`).
- **Corpus paths outside `/home/bkk`**: systemd's `ProtectHome=read-only`
  blocks `/home/*`. Either move corpus to `/var/lib/bkk/...`, or drop
  `ProtectHome` and add explicit `ReadOnlyPaths=`/`ReadWritePaths=` entries.

## 7. Updating

### Python server — from the admin UI

Once `BKK_SOURCE_ROOT` is set and the service user owns the checkout, an admin
team member can update without SSH:

1. Open the Admin activity → Operations tab → Server group.
2. Click *Update (git pull + pip install)* and wait for the `success` badge.
   The job runs `git fetch && git merge --ff-only origin/<branch>` followed by
   `pip install -e <source_root>/module`; pip stdout/stderr is captured.
3. Click *Restart server*. The UI sends SIGTERM, polls `/server-info`, and
   shows `online` once systemd has respawned the process.

This is intentionally a two-click workflow: read the update job's output
before bouncing. There is no rollback button — if the new build crashes on
boot, systemd will loop and shell access is required to `git reset`.

### Python server — from the shell

```bash
# 1. Pull latest code (as your normal user, wherever the repo lives)
cd /path/to/bkk
git pull

# 2. If dependencies changed in pyproject.toml, reinstall:
sudo -u bkk /var/lib/bkk/venv/bin/pip install -e '/path/to/bkk/module[serve]'

# 3. Restart the service
sudo systemctl restart bkk
sudo systemctl status bkk
journalctl -u bkk -n 50
```

Because the install is editable (`-e`), step 2 is only needed when
`pyproject.toml` changes (new deps, version bumps). Pure Python edits just
need the restart.

### SPA

```bash
# On a machine with node (or the server if node is installed):
cd /path/to/bkk/module/web
npm install        # only if package.json changed
npm run build

# Deploy the new dist/ to where --web-dist points:
sudo rsync -a --delete /path/to/bkk/module/web/dist/ /var/lib/bkk/web-dist/
sudo chown -R bkk:bkk /var/lib/bkk/web-dist
```

The SPA is served by uvicorn's static mount, so no restart is strictly
required — but a `systemctl restart bkk` is harmless and ensures any cached
`index.html` is re-read.

### Verify

```bash
curl -sS https://bkk.example.org/healthz
curl -sS https://bkk.example.org/server-info     # should reflect new version
```

### Rollback

```bash
cd /path/to/bkk && git checkout <previous-sha>
sudo systemctl restart bkk
```
