# Wake recovery, 2026-11-21

The VPS kernel retained the home's previous IPv4 endpoint after the FRITZ!Box's
MyFRITZ DNS record changed. Refreshing the endpoint restored a handshake and home
router connectivity immediately. WireGuard's roaming updates require an incoming
authenticated packet; DNS changes alone do not update an existing kernel peer.

`wireguard_endpoint.py` re-resolves the configured endpoint for the single home
peer after three minutes without a handshake. The systemd timer runs about once
a minute. Recent roaming endpoints are preserved; DNS failures leave the current
endpoint intact. No private or preshared keys are printed. Five unit tests pass.

The PC controller was also inactive after boot: its enabled user unit was a
symlink into the Data disk, unavailable when the user manager loaded units.
`fix_controller_boot.py` installs a regular copy on the home filesystem, keeping
the Data source unchanged. The existing restart-on-failure policy handles the
service's runtime dependency on Data and network availability. The user unit was
re-enabled and started; no desktop or model profile changes were made manually.

Profile selection and native process startup also compared the model's persisted
Linux device number. It changed at reboot while the inode, length and timestamps
remained identical. The complete model SHA-256 was rechecked successfully before
patching either check. Both now retain the stable file attributes and the existing
Data filesystem UUID guard. The model selector and runtime source pins were updated
in `profile-registry.json`, with backups retained. `prepare_native_boot.py` and
`prepare_native_runtime.py` recreate the source-pinned patches. Nine regression
tests cover endpoint refresh and both artifact checks, including rejection of
actual file identity/source changes. No full PC reboot was performed for testing.
