# Deployment Guide — V0.1

Everything in this repo runs from one Docker image (`Dockerfile`) plus Postgres and Redis (`docker-compose.yml`), so it deploys the same way on **any** host that can run Docker — a VPS, a home server, or a cloud free tier. This document picks two concrete free options and gives the exact steps for this repo; swap the host and the rest still applies.

## Read this first: why "free web hosting" alone is not enough

Most free application-hosting tiers (Render's free web service, Vercel, Fly.io's scale-to-zero apps, etc.) are **request-driven**: the process sleeps when idle and wakes up on the next HTTP request. That is fine for the dashboard/API (`uvicorn app.main:app`, §2 below) — a personal dashboard sitting idle between checks is an acceptable trade-off.

It is **not** fine for the Scanner Loop (`app/services/scanner_loop.py`, run via `app/scripts/run_live_scanner.py`). That process must stay connected to IBKR and keep processing candles continuously during market hours — it is not driven by incoming HTTP requests at all, so a platform that sleeps it on "inactivity" will silently stop Paper Trading (docs/PAPER_TRADING_PLAN_V0.1.md) without any request ever coming in to wake it back up.

So: the scanner needs an **always-on** compute option. The dashboard/API does not, strictly, but running everything on the same always-on host is simpler and is what this guide does.

## Option A (recommended): Oracle Cloud "Always Free" VM

Oracle Cloud's Always Free tier includes always-on compute instances (as of writing: 2 AMD-based VMs, or an ARM-based Ampere instance with up to 4 OCPUs/24GB RAM split across instances) that are not billed and do not sleep. This is the most genuinely "free forever, always-on" option available for a small always-running workload like this one. (Free-tier terms change over time — verify current limits on Oracle's own pricing page before relying on this long-term.)

1. Create an Oracle Cloud account (requires a card for identity verification; the Always Free resources themselves are not charged).
2. Console → Compute → Instances → Create Instance. Pick an "Always Free eligible" shape (the Ampere A1 or one of the AMD micro shapes). Choose Ubuntu as the image.
3. Add your SSH public key during creation; note the instance's public IP.
4. Open the needed ports: Console → Networking → Virtual Cloud Networks → (your VCN) → Security Lists → Add Ingress Rules for TCP port 8000 (dashboard/API) — and 22 (SSH) if not already open. **Do not** open 5432 (Postgres) or 6379 (Redis) — those stay internal to the Docker network.
5. SSH in and install Docker:
   ```bash
   ssh ubuntu@<instance-ip>
   sudo apt update && sudo apt install -y docker.io docker-compose-v2 git
   sudo usermod -aG docker $USER && newgrp docker
   ```
6. Get the code onto the VM. Since this session could not push to GitHub (see the repo's own known issue), transfer the delivered zip instead:
   ```bash
   scp options-trading-system.zip ubuntu@<instance-ip>:~/
   ssh ubuntu@<instance-ip>
   unzip options-trading-system.zip -d options-trading-system && cd options-trading-system
   ```
   (Once GitHub access is restored, `git clone` your repo instead — the workflow from here on is identical.)
7. Configure and start:
   ```bash
   cp .env.example .env
   nano .env   # set POSTGRES_PASSWORD, DATABASE_URL, REDIS_URL, IBKR_HOST/PORT, SCANNER_SYMBOLS
   docker compose up -d --build
   ```
8. Confirm: `curl http://localhost:8000/system/health` from the VM, or `http://<instance-ip>:8000/dashboard/` from your browser.
9. Once IBKR connectivity is sorted (§3 below), uncomment the `scanner` service in `docker-compose.yml` and `docker compose up -d` again.

The dashboard is now internet-reachable at `http://<instance-ip>:8000/dashboard/` with **no authentication** — see the security note in §4 before leaving it running unattended.

## Option B (quicker, but read the caveat above): Render free web service

If you only want the dashboard/API reachable somewhere quickly, and are running the Scanner Loop separately (e.g. on your own always-on machine) or not yet at all:

1. Push this repo to GitHub (once access is restored) or connect Render directly to a zip-imported repo.
2. Render dashboard → New → Web Service → connect the repo. Render detects the `Dockerfile` automatically.
3. Add a free Render Postgres instance (Render's free Postgres databases expire after a set period — check Render's current terms; this is a real limitation for a system meant to run for the 4-6 week Paper Trading window) and a Redis instance (Render doesn't offer free Redis directly — use a free tier from Upstash instead and set `REDIS_URL` to its connection string).
4. Set the same environment variables as `.env.example` in Render's dashboard.
5. Deploy. The free web service will sleep after 15 minutes of no HTTP traffic and cold-start on the next request — acceptable for occasionally checking the dashboard, not for the scanner (see above).

## IBKR connectivity — the piece only you can complete

Neither option above includes a working IBKR connection out of the box. You need TWS or IB Gateway logged into your **paper** account and reachable from wherever the `scanner` service runs. Two paths:

- **Run TWS/IB Gateway on your own machine** (it needs a GUI, so this is the simplest path) and point `IBKR_HOST` at that machine's address, reachable from the cloud VM over a VPN or SSH tunnel — plain internet exposure of the TWS API port is a real security risk and not recommended.
- **Run IB Gateway headlessly on the same VM** via the community `gnzsnz/ib-gateway-docker` project (referenced, commented-out, in `docker-compose.yml`). This handles headless login/2FA automation but is a third-party project outside this codebase's scope — read its own documentation and accept its own terms before using it with real (even paper) credentials.

Either way, this is an operational step this codebase cannot complete on your behalf — it needs your own IBKR account and a decision about where TWS/Gateway physically runs.

## Security notes for either option

- This dashboard has **no authentication** (V0.1 explicitly single-user, per `docs/OPERATIONAL_SPEC_V0.1.md` §7) and shows real position-sizing/account data. Do not leave port 8000 open to the whole internet unattended — put it behind a reverse proxy with HTTP basic auth (nginx/Caddy in front of the compose stack), or restrict the security list/firewall rule to your own IP, or tunnel over SSH/VPN instead of exposing it directly.
- Never commit a real `.env` — it's already git-ignored; keep it that way.
- Postgres and Redis should never be exposed outside the Docker network (the compose file already only publishes port 8000).

## Everyday operations

```bash
docker compose logs -f app          # dashboard/API logs
docker compose logs -f scanner      # scanner loop logs (once enabled)
docker compose exec app python -m app.scripts.run_paper_trading_report --days 7
docker compose exec app python -m app.scripts.run_outcome_tracker
docker compose down                 # stop everything (data volumes persist)
```
