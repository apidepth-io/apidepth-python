# Changelog

## [0.2.3](https://github.com/apidepth-io/apidepth-python/compare/apidepth-v0.2.2...apidepth-v0.2.3) (2026-05-29)


### Bug Fixes

* detect cold starts via per-process host registry ([#8](https://github.com/apidepth-io/apidepth-python/issues/8)) ([6ff9982](https://github.com/apidepth-io/apidepth-python/commit/6ff99821107df4c81f53eed28e0a9bb348243756))

## [0.2.2](https://github.com/apidepth-io/apidepth-python/compare/apidepth-v0.2.1...apidepth-v0.2.2) (2026-05-29)


### Bug Fixes

* normalize request headers to lowercase before rate-limit extraction ([#6](https://github.com/apidepth-io/apidepth-python/issues/6)) ([2f248a7](https://github.com/apidepth-io/apidepth-python/commit/2f248a7a54cce75b1c7fe3e67ee5193ee6adaa75))

## [0.2.1](https://github.com/apidepth-io/apidepth-python/compare/apidepth-v0.2.0...apidepth-v0.2.1) (2026-05-27)


### Documentation

* Getting started section, How it works, consistent What gets captured table ([#2](https://github.com/apidepth-io/apidepth-python/issues/2)) ([75e322a](https://github.com/apidepth-io/apidepth-python/commit/75e322abf5e2b65ffd276f9becde5d2df0ccffae))

## [0.2.0](https://github.com/apidepth-io/apidepth-python/compare/apidepth-v0.1.1...apidepth-v0.2.0) (2026-05-25)


### Features

* bidirectional vendor registry sync (mirrors Ruby f2882dd + 2161fd0) ([38b08b7](https://github.com/apidepth-io/apidepth-python/commit/38b08b78e757d477e209f52215f342618fc85bed))
* close two Ruby parity gaps — app server detection + fork safety ([2a5744a](https://github.com/apidepth-io/apidepth-python/commit/2a5744a121ebed6255cbecf0c70630c9e5b50425))


### Bug Fixes

* SDK hardening — frozenset ignored_hosts, fork safety in instrument(), kwargs patch ([ee099e0](https://github.com/apidepth-io/apidepth-python/commit/ee099e0ba82a730d53c44650febaccad4d56c906))
* thread-safety and config validation issues (PY-001, PY-002, PY-003) ([5194ac3](https://github.com/apidepth-io/apidepth-python/commit/5194ac3ddc69b1764d6a156aca08e013ed3cde4f))


### Documentation

* document cold_start divergence from Ruby gem ([9c051a6](https://github.com/apidepth-io/apidepth-python/commit/9c051a6dd6f2543009e94b9631507edd918515e7))
