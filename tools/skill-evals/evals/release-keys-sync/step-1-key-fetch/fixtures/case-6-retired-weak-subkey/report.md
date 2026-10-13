<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

Pre-flight: PASS (verdict: proceed)
Fetching fingerprint A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6ABCD from keys.openpgp.org

The keyserver returned an RSA 4096-bit certify-only primary key, an RSA 1024-bit signing subkey that expired on 2020-01-01, and a current Ed25519 signing subkey.
Today's date: 2026-06-19.

Output of `python3 <skill-dir>/scripts/check_key.py --key-file fetched-key.asc --fingerprint A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6ABCD`:

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
