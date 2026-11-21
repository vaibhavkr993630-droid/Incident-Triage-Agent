# Expired or invalid TLS certificates

Owner: platform team.

## When to use
Clients fail with certificate errors (expired, unknown authority, hostname mismatch), or
HTTPS calls to a dependency fail right after a certificate rotation.

## How to confirm
Inspect the certificate's validity dates and chain with openssl s_client.

## Mitigation
Renew or redeploy the certificate; check the renewal automation.
