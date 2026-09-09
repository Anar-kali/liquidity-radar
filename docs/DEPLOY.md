# Deploying the site

The site is static — HTML, CSS, JS and JSON files, no server. Everything that
updates it already runs:

```
cron-job.org  →  GitHub Actions (radar.yml)
                   → main.py          updates radar.db
                   → export_site.py   writes site/public/data/*.json
                   → bot commits and pushes to main
                          ↓
                   host rebuilds  →  live site
```

There is nothing to keep alive. A domain is only a name; it needs a host to
point at, and the host below is free.

## Cloudflare — settings

Cloudflare's dashboard now routes new projects through **Workers** rather than
Pages, so this deploys as a Worker serving static assets. There is no Worker
script: `assets.directory` in `site/wrangler.jsonc` alone makes it a pure
static site, which is all this needs.

Workers & Pages → Create → connect `Anar-kali/liquidity-radar`, then:

| Setting | Value |
|---|---|
| Project name | `liquidity-radar` |
| Build command | `npm ci && npm run build` |
| Deploy command | `npx wrangler deploy` |
| **Root directory** (Advanced settings) | `site` |

**Root directory is the one that is easy to miss** — it is collapsed under
*Advanced settings*. Without it the build runs at the repo root, where there is
no `package.json`, and `npm ci` fails immediately.

Node version comes from `site/.node-version`, pinned to **22**. Wrangler
requires Node 22 or newer; Vite is happy on anything ≥20. Pinning lower breaks
the deploy step rather than the build, so the failure arrives late.

Under **Settings → Build → Build watch paths**, set *Include paths* to:

```
site/*
```

This matters. Three workflows push to `main` (`radar.yml`, `blockdeals.yml`,
`refresh-tickers.yml`) but only `radar.yml` touches the site. Without a watch
path every unrelated `radar.db` commit spends a build.

## Deploying from GitHub Actions (recommended)

`.github/workflows/deploy-site.yml` builds the site and uploads it to
Cloudflare on every push that touches `site/**`. This is preferable to letting
Cloudflare build from the Git connection, for one concrete reason: this repo is
public, so GitHub Actions minutes are unlimited, and building here spends none
of Cloudflare's 500-build monthly allowance (see below).

Two repository secrets are required — **Settings → Secrets and variables →
Actions → New repository secret**:

| Secret | Where to get it |
|---|---|
| `CLOUDFLARE_API_TOKEN` | Cloudflare → My Profile → API Tokens → Create Token → Custom. Permission: **Account / Workers Scripts / Edit**. Scope it to this account only. |
| `CLOUDFLARE_ACCOUNT_ID` | Right-hand sidebar of the Cloudflare dashboard. |

Neither value is ever printed by the workflow, and secrets are masked in logs.

**If you also connect the repo inside Cloudflare's dashboard, disconnect one of
them.** Both will deploy on every push and race each other.

The workflow refuses to ship a build with no `index.html`, no `_headers`, or an
empty feed — a site that looks broken with nothing explaining why is worse than
a failed deploy.

## The build-quota constraint

Cloudflare's free plan allows **500 builds/month**, 1 concurrent, 20-minute
timeout. The pipeline pushes **~14 times a day ≈ 420/month**, and nearly all of
those change site data, so a build fires each time.

That fits, but with roughly 15% headroom and no room to raise the pipeline's
cadence. If it becomes a problem, in order of preference:

1. **Export less often.** Have `radar.yml` commit `site/public/data` on a subset
   of runs (say hourly rather than every sweep). The feed is a reading tool;
   15-minute freshness is not load-bearing.
2. **Pro plan** — $20/month, 5,000 builds.

None of this applies if you deploy from GitHub Actions as above, which is why
that is the recommended path.

Free-plan ceilings this deployment sits well inside: 20,000 files (we ship
~620) and 25 MiB per file (largest is ~330 KB). Static-asset requests are not
billed as Worker invocations.

## Caching

`site/public/_headers` ships with the build:

- `/assets/*` — immutable, one year. Vite fingerprints these, so the filename
  changes whenever the content does.
- `/data/*` — 5 minutes, must-revalidate. Rewritten every pipeline run.
- `/index.html` — never cached, or a deploy won't reach anyone holding an old
  copy.

## Custom domain

Buy the domain from any registrar. In the project → **Settings → Domains &
Routes → Add**, enter it, and Cloudflare shows the record to add:

- Domain registered *at* Cloudflare, or using Cloudflare nameservers → the
  record is created automatically.
- Domain elsewhere (Hostinger, GoDaddy) → add the `CNAME` Cloudflare gives you
  at the registrar's DNS panel. For an apex domain (`example.com` with no
  `www`), use the registrar's ALIAS/ANAME record if offered, or move
  nameservers to Cloudflare.

HTTPS is issued automatically; allow a few minutes after DNS propagates.

## Local

```bash
npm --prefix site ci
npm --prefix site run dev     # http://localhost:5174
npm --prefix site run build   # -> site/dist
```

`npm run dev` serves `site/public/data` at `/data/*`, so the dev server reads
the same files the deployed site does. Re-run `python export_site.py` to
refresh them from `radar.db`.
