<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

Pre-flight: PASS (verdict: proceed)
Fetching fingerprint A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6ABCD from keys.openpgp.org

The RSA 4096-bit certify-only primary has only one Ed25519 signing subkey, SIGNSUB, which expired today at 06:00 UTC.
GPG computed the listing at 12:00 UTC on 2026-06-19 and marked that subkey with validity `e`.
The helper's `--today` is 2026-06-19; there is no usable signer and no non-blocking expiry advisory.

Output of `python3 <skill-dir>/scripts/check_key.py --key-file fetched-key.asc --fingerprint A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6ABCD --today 2026-06-19`:

```json
{
  "verdict": "blocked",
  "key_found": true,
  "fingerprint": "A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6ABCD",
  "uid": "Example Release Manager <rm@example.invalid>",
  "algorithm": "RSA",
  "bit_length": 4096,
  "created": "2022-03-15",
  "expiry": null,
  "strength_check": "fail",
  "strength_note": "signing subkey SIGNSUB: key is expired (gpg marked it expired); extend its expiry or generate a new key; no usable signing key or subkey meets the ASF floor; add a signing subkey that does"
}
```
