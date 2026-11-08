# Secret layout (T008)

No secret is committed, and none exists inside the repository at rest. This directory holds
only the encrypted (sops/age) source of truth and its documentation.

| Location | Content | Mode |
| --- | --- | --- |
| `infra/age/*.sops.yaml` | age-encrypted source of truth, safe to commit | 0644 |
| `/run/atlas/<name>.env` | decrypted runtime files rendered at boot | 0600 |
| `/etc/atlas/age.key` | age private key, host only, never in git | 0600 |

`/run` is tmpfs, so decrypted material disappears on reboot and is re-rendered by
`scripts/render-credentials.sh` before any service starts.

## Templates

Copy one of these into `infra/age/<name>.sops.yaml` as plain YAML, fill in real values, run
`sops --encrypt --in-place infra/age/<name>.sops.yaml`, and commit only the ciphertext.

`infra/age/postgres.sops.yaml`:

```yaml
data:
  POSTGRES_DB: atlas
  POSTGRES_USER: atlas
  POSTGRES_PASSWORD: __REPLACE_WITH_A_GENERATED_PASSWORD__
```

`infra/age/redis.sops.yaml`:

```yaml
data:
  REDIS_PASSWORD: __REPLACE_WITH_A_GENERATED_PASSWORD__
```

`infra/age/execution.sops.yaml` — mounted into `services/execution` only (FR-064):

```yaml
data:
  # Trading permission only. Withdrawal permission must be absent; T078 asserts it.
  ATLAS_VENUE_API_KEY: __REPLACE__
  ATLAS_VENUE_API_SECRET: __REPLACE__
  ATLAS_VENUE_PASSPHRASE: __REPLACE_IF_THE_VENUE_HAS_ONE__
  # Shared HMAC key for signed, single-use risk verdicts (60 s lifetime).
  ATLAS_HMAC_KEY: __REPLACE_WITH_32_BYTES_BASE64__
```

The API key is created in the exchange console by the operator, bound to a withdrawal
whitelist address. ATLAS never creates exchange credentials and never enables live trading
by itself.
