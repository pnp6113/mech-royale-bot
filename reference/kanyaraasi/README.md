# Team Kanyaraasi — Mech Royale

This package adapts [justnau/giddygiddyboy-mech-royale](https://github.com/justnau/giddygiddyboy-mech-royale)
at pinned commit `345c0de2f43f5f27a4ecf74c1c25c789d00fbc49`, licensed MIT, copyright 2026 giddygiddyboy.
The complete upstream notice is in `THIRD_PARTY_LICENSES`; Team Kanyaraasi's
packaging license is `LICENSE`. Both complete notices are included in the image.

Three `mech/*.go` files and `go.mod` are unchanged from the attributed
control. `cmd/bot/main.go` retains map/panic diagnostics. The measured `Lethal`
release policy and the measured conservative practice drop helper remain
enabled, including the isolated any-damage drop quiet timer. The only additional
policy port adds a zero-
default `Finish` parameter, the upstream guarded temporary fight-range branch,
and `Finish = 14` in the squad profile. The guard requires target HP below
38%, own HP above 42%, and crowd at most two. It temporarily changes preferred
ranges for the existing local movement evaluator and restores the entire
parameter bundle immediately afterward. Original weapon-dependent preferred-
range floors still apply: 14 m is not a universal destination or firing range.
Default/Opening/FFA Finish remains zero. Aim, firing, geometry, detector,
Opening, original squad spacing, and other policy parameters are retained.
The target guard is a fraction of its own maximum HP. A grown or upgraded foe
can qualify while retaining more actual HP than our wounded mech; "finisher"
does not guarantee a quick kill. Pursuit can abandon cover or increase enemy
exposure. No placement improvement is guaranteed. The flat-map and recovered/coasting
fallbacks remain; strict validation rejects either.

The compiler is the upstream pinned Go 1.26.1 image; the final runtime is scratch,
uid 65534, with no third-party modules or runtime dependencies. Toolchain/module
fetching and telemetry are disabled during compilation. Build for linux/amd64;
run under the organizer's sandbox with its read-only arena at `/arena` and
64 MB writable `/tmp`. HOME and TMPDIR point to `/tmp`.
This extends selected P040, public commit `c6f6ad821e712d5b12f807deee2e1107f25a11d2`, which measured
5.81 in one official practice aggregate. That measurement belongs to the
control. The additional Finish field/branch/assignment are adopted from the
same author's MIT revision `64724a2ad873f9a740295d1984fcf2dc4cab5d4b`. Its NanoUp additions, Opening
change, new LaserFirst default, 120 m squad spacing, and other defaults are
excluded. This isolated pursuit adaptation has no measured placement result.

Runtime source SHA-256 values:
- `cmd/bot/main.go`: `a4259e0c2f9cf8de66a04844fc7a4d0ca4bc2070b987905c14723895939b7f93`
- `go.mod`: `51496525f2844bdd830c5d1e202b54bd97d9dfad96381782fea13fa51e6fc3fb`
- `mech/brain.go`: `c2e084f035b941fda044bae6ab7390be50d63fb397e5e1e503e9f8c59d4c4d08`
- `mech/export.go`: `3f0384222b15eddf9f516b74e89beefe44e65a10fef294735424f042d982967b`
- `mech/types.go`: `ab43e63ca989678290d180a5cdb54f0de04d73bf136a7558ad40fc039e76ed71`
- `mech/world.go`: `a2cf879007fc3bc49cfc0bb90e6e99e8cff5a9413f85499c6b165bfee6d27ec4`
