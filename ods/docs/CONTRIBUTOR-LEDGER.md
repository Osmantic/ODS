# Beta contributor ledger

This ledger is derived from raw Git author names, including merge commits.
It credits recorded authors without asserting legal identity, account ownership,
work quality, or a one-to-one mapping between a name and a person. Identical
names are grouped; different names remain separate even when they may be aliases.
Personal emails and local workstation hostnames are intentionally not repeated.

- Comparison base: `21f4b3a64dd2a2fac1163f446806091c25b6b814`.
- Audited beta: `0e01537095a46f312aa0b7cc392c6d8f0d730f2c` (1822 commits after the base).
- Remediation input: `4099cffc874f80b45f589504ebb29d4dbdcbd5c6` (1856 commits after the base).

| Recorded author name | Audited range commits | Remediation input commits | Example author record |
| --- | ---: | ---: | --- |
| Mike Bradley | 550 | 571 | [commit](https://github.com/Osmantic/ODS/commit/1ad675277346ac00471d61ea77b618043f00cf80) |
| Gabriel Madureira | 346 | 352 | [commit](https://github.com/Osmantic/ODS/commit/4099cffc874f80b45f589504ebb29d4dbdcbd5c6) |
| gabsprogrammer | 262 | 263 | [commit](https://github.com/Osmantic/ODS/commit/7ccc3911c84395f7c55e82e84ab62891a737339c) |
| 0xacee | 185 | 185 | [commit](https://github.com/Osmantic/ODS/commit/64607288891d573d90e862db4732c3640169f7c4) |
| Codex ODS Maintainer | 160 | 160 | [commit](https://github.com/Osmantic/ODS/commit/bef2245ad05ca4d4ea457b841f7773a994258d9b) |
| Tang Vu | 147 | 147 | [commit](https://github.com/Osmantic/ODS/commit/f2024d2e61cbf2216d9e471f25f01c4f0c83eede) |
| Roshan Kumar Gupta | 53 | 53 | [commit](https://github.com/Osmantic/ODS/commit/47a6e12b8b803751ee5011afbc3aec7484fadda9) |
| Gabriel Santana | 40 | 46 | [commit](https://github.com/Osmantic/ODS/commit/74f39f055f58761d20ff6c73642e07639e402675) |
| vaibhavsrv | 38 | 38 | [commit](https://github.com/Osmantic/ODS/commit/d6bfd82352adf9ab96a980297d255bd31d897224) |
| patil2001 | 21 | 21 | [commit](https://github.com/Osmantic/ODS/commit/65867d082053cfe2fe9f3189d6001e85b5e1f8b0) |
| IronicRayquaza | 12 | 12 | [commit](https://github.com/Osmantic/ODS/commit/afe5989a312fb25c70a3f24c0a67c7c80c532c0e) |
| Niks2801 | 3 | 3 | [commit](https://github.com/Osmantic/ODS/commit/929a70ba848c20819e2249c9d138ccb189fe68b8) |
| root | 3 | 3 | [commit](https://github.com/Osmantic/ODS/commit/db8df4870808a4eb20e9ed9ca1e0eb05f5f157d4) |
| Vishaaallll | 2 | 2 | [commit](https://github.com/Osmantic/ODS/commit/583274a316ff06a94b12a40ca7af23460ae21b5a) |

Reproduce the counts locally with `git log --format=%an BASE..REVISION` and group
identical lines. Counts describe this frozen range and are not current lifetime
rankings or merged-PR counts. The full repository history credits earlier
contributors; [the contributor page](../../CONTRIBUTORS.md) keeps narrative
acknowledgements.

## Identity reconciliation

The audit identified 224 commits in its beta range using placeholder addresses
or local workstation identities. The original history is preserved. No
`.mailmap` aliases are added here: matching names, shared emails, or similar
handles are insufficient to establish a preferred public identity.
Maintainers should request contributor-confirmed mappings and record that
confirmation before applying presentation-only `.mailmap` entries. Unconfirmed
automation and placeholder records must not be reassigned to a human by guesswork.

New commits should use an account-verified email or an account-provided
`noreply` address. See [contributing](../CONTRIBUTING.md#license). Rewriting
published history is outside this documentation correction.
