# Krul review — 2026-10-03

## Findings and scope

No new correctness defects found in the GPIO category change and its surrounding
discovery, enum decoding, filtering, polling, write and response-update paths.
Reviewed the current worktree diff, including the existing special_test additions;
those additions were preserved. This is a focused review of the current change,
not a new exhaustive audit of every serialization/runtime path.

The category field is application metadata, not a new Krul type or transport
version. Starset only uses a nonempty string category for output placement.
Missing, empty and invalid categories retain the old flat layout; mixed lists
retain uncategorized outputs in a fallback group. Input placement is unchanged.
Grouping does not rewrite wire IDs or requests, and response updates do not
require categories. Filtering hides empty category boxes without restricting
global output actions. Board naming rules stay in BKU, outside shared Krul.

The existing special_test enum is appended, preserving older enum values;
discovery maps it to a widget hint and the generic fallback remains available.

## Comparison with the 2025-08-28 baseline

| Historical item | Current status and evidence |
| --- | --- |
| Linear command/field lookup | Still present in `command-interface/src/krul.c`; documented design constraint, not a new defect. |
| Single response buffer | Still present; dispatch/release contract and native tests remain in place. |
| Undecided CONSOLE_STRING status | Changed: retained public type with documented string APIs in `command-interface/inc/krul.h`; no informal removal note. |
| Duplicate severity mapping | Still present in `krul.c` and `krul_discovery.c`; maintenance duplication. |
| Informal public API comments | Fixed for the cited comments: searches for the historical phrases did not find them in `krul.h`. |
| Undecided constraint/value-ref APIs | Changed: documented validator accessors and an optional application constraint callback in `krul.h`. |
| JSON token sizing not documented | Fixed: `serialization/README.md` documents key/value token costs and application-owned capacity. |
| Writer depth 12 vs result depth 8 | Still a valid stricter application-layer limit; category adds no nesting. |
| GUI monolith | Changed: transport, wire codec, widgets, theme and configuration are separate modules; MainWindow remains substantial. |
| README points to main.py | Fixed: canonical entry point is Starset.py. |
| Client timeout code outside wire protocol | Changed/clarified: `Starset.py::_expire_requests` uses CLIENT_ERROR_TIMEOUT and `source: client`. |
| Unknown response IDs discarded silently | Fixed: `Starset.py::_on_message` warns for unmatched IDs. |
| Serial replacement decoding / missing frame cap | Fixed: transport uses strict decode through krul_wire and FrameParser enforces 10 KiB. |
| GrathPlot spelling | Still present, intentionally retained. |
| Generic Callable hints in callbacks | Still present; no behavior regression caused by categories. |
| Simulator duplicates discovery | Still present intentionally; new metadata stays application-owned rather than adding BKU commands to the shared simulator. |
| Missing CI/root build/presets | Fixed: root CMake and presets and `.github/workflows/ci.yml` exist. |
| Missing root README / stale Libs/docs paths | Fixed: root README exists and module documentation refers to docs. |
| Missing LICENSE / fuzz target | Still present: no repository license or fuzz target found. |
| Missing replay protection | Still a protocol integration concern; this change neither creates nor expands request semantics. |
| Custom native test harness / missing Python packaging marker | Still present; category verification uses the existing test infrastructure. |

## Verification

- `python -m pytest -q tests -p no:cacheprovider` from Python GUI:
  **104 passed**, including 18 new category/legacy compatibility cases.
- BKU native MSVC build and `ctest --preset Host-Debug`: **7/7 passed**,
  including the shared JSON/BSON/CBOR, transport and Krul host tests.
- BKU `cmake --build --preset CM7-Debug`: successful; updated CVM links,
  KNUD/KTPP builds remain successful.
- Framed native simulator to real Python codec and IOPanel, after DESCRIBE:
  JSON/BSON/CBOR full snapshots of CVM/KNUD/KTPP all succeed. All 58/72/68 pins
  survive decoding, all output families have categories, and all output cards
  appear in their grids. Maximum response payload: **5393 bytes** (BSON KNUD).
- Category filtering, single output writes, global writes, numeric wire IDs,
  old string target IDs, old type fields and updates without categories are
  covered by regression tests.
- No hardware flashing or physical-board tests performed. The compatibility
  matrix still needs hardware verification with old CVM and with new CVM plus
  old KNUD/KTPP firmware.

The initial pytest run emitted an environment-only cache permission warning;
the complete run disabled the cache provider and passed without that warning.
Unrelated existing BKU whitespace findings were left untouched.
