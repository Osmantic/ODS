# ODS-maintained Source 4.3.42 candidate

This is an ODS-maintained candidate for the narrow Source proposal publication
fix at functional checkpoint `2341f406ee4b461e6f36e595632dac72fb1872b1`.
The compatibility row's date records the scoped functional checks below. It
is not supported-release promotion, physical Mac qualification, owner-account
acceptance, provider authorization or a fleet-readiness claim.

## Change and reason for the version

Calendar and Gmail proposal writers now open a new file exclusively, set its
intended `0640` mode through that same open descriptor before writing the body,
and close the descriptor in `finally`. The gateway's private `0077` umask stays
in force. Setting the proposal's file mode adds no execution authority; existing
direct Calendar and owner-review policy remain unchanged.

The version advances from 4.3.41 because the existing ordinary Linux/WSL
source-upgrade gate requires a strictly newer version for a different exact
source ref. That gate and all source custody checks are preserved. Version
metadata is regenerated through the unchanged release generator. The
manifest-pinned OpenClaw runtime, dependency versions and hashes, image
references and hashes, tool argument schemas and definitions, signing keys
and trust policy are unchanged.

## Scoped evidence at the functional checkpoint

- Both actual exported proposal tools reproduced `0600` under umask `0077`
  before the fix, and produce `0640` with the fix; a private `0600` control
  remains private. The focused Source suite passed 25 cases with one existing
  optional pinned-SDK case skipped because its SDK variable was not supplied.
- A Linux DAC probe used the real exported Calendar queue path and an existing
  immutable local sandbox image. UID 1000:GID 1000 read the proposal and private
  control; UID 65534:GID 1000 read the fixed proposal but was denied the private
  control in the same group-searchable directory. Both exact owned containers
  were removed. No real account, provider, model or installed settings were used.
- Existing strict-version, prepared-client preservation, deterministic native
  bundle and native-updater ordering controls passed 21 cases. These native
  Mac component fixtures ran on Linux and do not qualify launchd or Darwin ACLs.

## Remaining qualification

The full public-export release-contract check remains blocked by the missing
frozen historical `LIVE-AUDIT-4.0.0.md`, reproduced on the preceding 4.3.41
source too. Its fixed historical hash check is unchanged. Passing generated
metadata and focused current-version controls does not pass that full gate.

The ODS vendor provenance outside this Source payload records the final bundle
identities, deterministic bundle/source/Git-mode verification and paired
installer/client/bootstrap checks. Those artifact checks do not qualify ordinary
installed Windows/WSL adoption, actual direct-broker/provider behavior or native
Mac installation/update; these remain separate gates. The old 4.3.41
compatibility row and previous source receipts remain historical evidence,
not results for this newly packaged candidate. No release signature or upstream
release is claimed.
