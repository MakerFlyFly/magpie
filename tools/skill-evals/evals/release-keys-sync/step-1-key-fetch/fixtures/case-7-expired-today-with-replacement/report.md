<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

Pre-flight: PASS (verdict: proceed)
Fetching fingerprint A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6ABCD from keys.openpgp.org

The RSA 4096-bit certify-only primary has an RSA 1024-bit signing subkey that expired today at 06:00 UTC and an unexpired Ed25519 replacement.
GPG computed the listing at 12:00 UTC on 2026-06-19 and marked the weak signing subkey with validity `e`.
The helper's `--today` is 2026-06-19; the calendar date must not make the already-expired weak subkey active again.

Output of `python3 <skill-dir>/scripts/check_key.py --key-file fetched-key.asc --fingerprint A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6ABCD --today 2026-06-19`:

```json
{
  "verdict": "proceed",
  "key_found": true,
  "fingerprint": "A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6ABCD",
  "uid": "Example Release Manager <rm@example.invalid>",
  "algorithm": "RSA",
  "bit_length": 4096,
  "created": "2022-03-15",
  "expiry": null,
  "strength_check": "pass",
  "strength_note": null
}
```
