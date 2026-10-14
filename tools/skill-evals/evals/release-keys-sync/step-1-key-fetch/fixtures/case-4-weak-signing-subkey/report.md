<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

Pre-flight: PASS (verdict: proceed)
Fetching fingerprint A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6ABCD from keys.openpgp.org

The keyserver returned an RSA 4096-bit certify-only primary key and one active RSA 1024-bit signing subkey, SIGNSUB.
Today's date: 2026-06-19.

Output of `python3 <skill-dir>/scripts/check_key.py --key-file fetched-key.asc --fingerprint A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6ABCD`:

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
  "strength_note": "signing subkey SIGNSUB: RSA 1024-bit key is below the ASF floor of 2048 bits; generate a new key that meets the floor; no usable signing key or subkey meets the ASF floor; add a signing subkey that does"
}
```
