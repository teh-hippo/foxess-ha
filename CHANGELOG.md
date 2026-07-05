# CHANGELOG


## v1.2.2 (2026-07-05)

### Bug Fixes

- **sensor**: Redact API key from debug log
  ([`4176636`](https://github.com/teh-hippo/foxess-ha/commit/4176636e340a87e30833b634d5d07e98f2876881))

### Chores

- **deps**: Update teh-hippo/common-repo-configs digest to b3d0a78
  ([`0bb559d`](https://github.com/teh-hippo/foxess-ha/commit/0bb559d786590fc14cb3fa00ae36c10bc472fadb))

### Continuous Integration

- **hacs**: Skip licence check for unlicensed upstream fork
  ([`0551257`](https://github.com/teh-hippo/foxess-ha/commit/055125798e258f2ac5595d2b59feb79cfb82c67c))


## v1.2.1 (2026-06-22)

### Bug Fixes

- Call the FoxESS API via aiohttp and drop the internal rest dependency
  ([`46edcfd`](https://github.com/teh-hippo/foxess-ha/commit/46edcfdd38a06b4c4131bfa99e711a8f07b4461a))

### Chores

- **deps**: Update actions/checkout action to v7
  ([`e83083e`](https://github.com/teh-hippo/foxess-ha/commit/e83083e58d892efddcbe79b29c22242a2484bebc))

- **deps**: Update softprops/action-gh-release digest to 718ea10
  ([`21d35c4`](https://github.com/teh-hippo/foxess-ha/commit/21d35c47388f51723adefa0097238397128962ab))


## v1.2.0 (2026-06-21)

### Build System

- **deps**: Adopt shared Renovate preset
  ([`ad7a378`](https://github.com/teh-hippo/foxess-ha/commit/ad7a37885bfa7036fc63a288883b9cd905e63444))

- **deps**: Pin dependencies
  ([`ae5d1af`](https://github.com/teh-hippo/foxess-ha/commit/ae5d1af5d7deaccf5eee96eca83d128690ea6e6e))

- **deps**: Update astral-sh/setup-uv action to v8
  ([`e770acf`](https://github.com/teh-hippo/foxess-ha/commit/e770acf8ee246f83174eab1ab6de65fa29160c1c))

- **deps**: Update github/codeql-action digest to 7211b7c
  ([`c2f33be`](https://github.com/teh-hippo/foxess-ha/commit/c2f33be788fae79c0ceecfc70829d586018511ff))

- **deps**: Update softprops/action-gh-release action to v3
  ([`71e6e44`](https://github.com/teh-hippo/foxess-ha/commit/71e6e440904d2eb43cafa4a3ef8e5058e9e12e0d))

- **deps**: Upgrade
  ([`424b4e0`](https://github.com/teh-hippo/foxess-ha/commit/424b4e0f54c15a8d2442016112d30699b99974d4))

- **deps**: Upgrade
  ([`cb933d8`](https://github.com/teh-hippo/foxess-ha/commit/cb933d88e3c5959133279a55a985c3ca4b084c5d))

- **deps**: Upgrade
  ([`9234e00`](https://github.com/teh-hippo/foxess-ha/commit/9234e003834db90de90e997b47e593d5c48ffd16))

- **deps**: Upgrade
  ([`6e4ab88`](https://github.com/teh-hippo/foxess-ha/commit/6e4ab88a202bd2ffd015071e9cf478e7134786eb))

- **deps**: Upgrade
  ([`a1dab74`](https://github.com/teh-hippo/foxess-ha/commit/a1dab74ba40d75c19749666fe5e0203517b72fa0))

- **renovate**: Align config with canonical baseline
  ([`adb34bf`](https://github.com/teh-hippo/foxess-ha/commit/adb34bf4e2148300b8e56a63a5547b8774396619))

### Chores

- **deps**: Lock file maintenance
  ([`0a65935`](https://github.com/teh-hippo/foxess-ha/commit/0a65935d52e072eae59a739337755d2a9d1e343b))

- **deps**: Update actions/checkout digest to df4cb1c
  ([`91b886c`](https://github.com/teh-hippo/foxess-ha/commit/91b886c32781fcb9fa91fa9791e578667505b797))

- **deps**: Update astral-sh/setup-uv action to v8.2.0
  ([`884174a`](https://github.com/teh-hippo/foxess-ha/commit/884174ad4709790182c9b8a3bffa0369e8f68988))

### Continuous Integration

- Adopt shared CodeQL workflow
  ([`7fa9238`](https://github.com/teh-hippo/foxess-ha/commit/7fa92386c99759351bafe786cd3fd963c53c548a))

- Adopt uv sync --locked pattern
  ([`36b7e51`](https://github.com/teh-hippo/foxess-ha/commit/36b7e51ff2c508fc92547994813336270b21ac11))

- Stagger cron and pin floating action refs
  ([`3e114ee`](https://github.com/teh-hippo/foxess-ha/commit/3e114ee9d8d1ef85eec7c10b8145251c963b0857))

- **release**: Commit uv.lock from build_command via assets
  ([`2ae0bb5`](https://github.com/teh-hippo/foxess-ha/commit/2ae0bb515b28b14f219abaaa37c6696bc6a35324))

- **validate**: Drop daily cron and Dependabot/Copilot branch push triggers
  ([`f28c2fd`](https://github.com/teh-hippo/foxess-ha/commit/f28c2fd03ce78d3f3e4f6cfcf0279a24100f9b64))

### Features

- Sun-aware sleep/offline handling for PV-only inverters
  ([`56b66a0`](https://github.com/teh-hippo/foxess-ha/commit/56b66a0abfcc79101607383ac5c28b14fe557f84))


## v1.1.0 (2026-04-07)

### Build System

- Use RELEASE_TOKEN and align renovate config with other repos
  ([`0838d5d`](https://github.com/teh-hippo/foxess-ha/commit/0838d5dadf1946170fc77c561a1157f7f0dfa139))

- **deps**: Upgrade
  ([`028be90`](https://github.com/teh-hippo/foxess-ha/commit/028be90c3566e334ed0f754b0076cc53620adce6))

- **deps**: Upgrade
  ([`1fd05fc`](https://github.com/teh-hippo/foxess-ha/commit/1fd05fc928e7163118b82e9c53131886d39da84a))

- **deps**: Upgrade
  ([`4c9ce37`](https://github.com/teh-hippo/foxess-ha/commit/4c9ce37f1967b3865521851200360cc036ea9d6a))

### Continuous Integration

- Enable Renovate fork processing
  ([`fd79cbd`](https://github.com/teh-hippo/foxess-ha/commit/fd79cbd8cbdd999cebe8ef9f2d57d118cae8fe2d))

- Fix automerge config for all update types
  ([`f2c1123`](https://github.com/teh-hippo/foxess-ha/commit/f2c1123a142b74ed43ecd9f414a0d1ff777834a7))

- Fix build_command, add dependabot, remove lockfile-update workflow
  ([`083d3c7`](https://github.com/teh-hippo/foxess-ha/commit/083d3c723cb6d7a671bbe3cb7364d693756bc977))

- Migrate from Dependabot to Renovate
  ([`ab262aa`](https://github.com/teh-hippo/foxess-ha/commit/ab262aafdaa510d1279e3770eafe2790e90d306e))

### Features

- Raise Repairs issue when inverter data is stale
  ([`dd466d9`](https://github.com/teh-hippo/foxess-ha/commit/dd466d97af2d5bf699fb885fe0f4fab7d838b49c))


## v1.0.0 (2026-03-17)

- Initial Release
