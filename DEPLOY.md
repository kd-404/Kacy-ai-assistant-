# Deploying Kacy to Azure

One always-on Azure Container App serves the API and the web UI. Chats and approvals live in a
SQLite file on an Azure Files share. Kacy signs in to Foundry with its managed identity (no key).

## Before the first deploy

1. **Rotate any key that was ever pasted into a chat or committed**: Content Safety, Language,
   Search, and the Google client secret. Put the new values in `backend/.env` (it is git-ignored).
2. Make the container image public so Azure can pull it without a registry bill:
   push to `main` (the `build-and-deploy` workflow publishes `ghcr.io/<you>/kacy`), then on GitHub
   open *Packages -> kacy -> Package settings -> Change visibility -> Public*.
3. Have the names ready: your Foundry resource name and its resource group.

## Deploy

```bash
az login
RESOURCE_GROUP=kacy-rg LOCATION=centralindia \
FOUNDRY_ACCOUNT=<foundry resource name> FOUNDRY_RG=<its resource group> \
IMAGE=ghcr.io/kd-404/kacy:latest \
bash infra/deploy.sh
```

It prints the live URL. Then, in Google Cloud Console -> your OAuth client, add the printed
redirect URI and JavaScript origin, and keep your own email in the test users.

## Check it

- Open the URL, sign in as a demo person, ask something.
- Ask "what is our parental leave policy" and confirm it cites the handbook. If it does not, the
  Foundry agent lost its portal-configured search tool; re-add it and republish the agent.

## Protecting the bill

Demo visitors are limited by `app/limits.py`: 15 messages/hour and 40/day per visitor, and 300 a day
across everyone. Change them with `KACY_DEMO_PER_HOUR`, `KACY_DEMO_PER_DAY`, `KACY_DEMO_DAILY_CAP`.
**Emergency stop:** `az containerapp update -n kacy-app -g kacy-rg --set-env-vars KACY_DEMO_PAUSED=1`
(your Google sign-in keeps working). Also create a budget alert in *Cost Management -> Budgets*.

## Limits of this setup (on purpose, for a demo)

- One replica only. Sign-in sessions and rate-limit counters are in memory, so a restart signs
  everyone out. Do not raise max replicas.
- SQLite on a file share is demo-grade. Real traffic needs Postgres.
- Demo personas are shared by all visitors, so anyone signed in as "alice" sees the same chats.
  Do not type anything private as a demo person.

## Automatic deploys (optional)

Create an Entra app registration with a federated credential for this repo, then add secrets
`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` and variables `AZURE_DEPLOY=true`,
`AZURE_RESOURCE_GROUP`. Every push to `main` then tests, builds and rolls out.
