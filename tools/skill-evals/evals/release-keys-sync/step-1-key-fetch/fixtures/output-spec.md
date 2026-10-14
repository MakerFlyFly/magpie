<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

# Step 1 output specification

The model must return ONLY valid JSON matching this schema:

```json
{
  "verdict": "proceed" | "blocked",
  "key_found": true | false,
  "fingerprint": "<fingerprint>",
  "uid": "<primary UID string or null>",
  "algorithm": "RSA" | "EdDSA" | "ECDSA" | "DSA" | null,
  "bit_length": <integer or null>,
  "created": "YYYY-MM-DD" | null,
  "expiry": "YYYY-MM-DD" | null,
  "strength_check": "pass" | "fail" | null,
  "strength_note": "<string or null>"
}
```

Grading rules:
- `verdict` must be `"proceed"` only when `key_found` is `true` AND `strength_check` is `"pass"`.
- `verdict` must be `"blocked"` when `key_found` is `false` (key not on keyserver) or when `strength_check` is `"fail"`.
- `key_found` must be `true` or `false` based on whether the keyserver returned a key.
- `strength_check` must be `null` when `key_found` is `false`.
- Return the script's JSON unchanged; never rejudge a key from its primary algorithm or size.
- `algorithm`, `bit_length`, `created` and `expiry` describe the primary key; `strength_check` describes the complete signing-key assessment.
- `strength_check` is `"pass"` only when the primary and all usable signing subkeys pass their strength checks, and at least one usable signing key or subkey meets the floor.
- `strength_check` is `"fail"` for a sub-floor primary, any usable sub-floor signing subkey, an unusable primary, or no usable compliant signer.
- Expired, revoked, invalid or disabled signing subkeys cannot provide a signer, but do not block a compliant replacement.
- GPG validity `e` means already expired, including on the expiry date; a matching or earlier `--today` cannot restore usability.
- `--today` changes only calendar-date checks and advisories, not GPG's computed validity or clock; historical replay requires a listing captured at the intended instant.
- A same-day key not marked expired can have an expiry advisory; a key marked `e` must not also receive a non-blocking expiry advisory.
- `bit_length` must be `null` for curve-based algorithms (EdDSA, ECDSA).
- `strength_note` must be non-null when `strength_check` is `"fail"`, and must explain the failure, including a subkey identifier for subkey-specific reasons.
- No extra keys are permitted in the response.
