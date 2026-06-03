# Changelog

## [0.4.0](https://github.com/apidepth-io/apidepth-python/compare/apidepth-v0.3.1...apidepth-v0.4.0) (2026-06-03)


### Features

* add model name extraction from AI vendor response bodies ([#22](https://github.com/apidepth-io/apidepth-python/issues/22)) ([e164481](https://github.com/apidepth-io/apidepth-python/commit/e164481e29d9b7bd54a26fd9d1625fa35c6c6026))

## [0.3.1](https://github.com/apidepth-io/apidepth-python/compare/apidepth-v0.3.0...apidepth-v0.3.1) (2026-05-30)


### Documentation

* add CLI section to README (setup and test subcommands) ([#12](https://github.com/apidepth-io/apidepth-python/issues/12)) ([1ba6a7e](https://github.com/apidepth-io/apidepth-python/commit/1ba6a7ecbf9c77ee66c6479c1e6f86e923cc3662))

## [0.3.0](https://github.com/apidepth-io/apidepth-python/compare/apidepth-v0.2.3...apidepth-v0.3.0) (2026-05-30)


### Features

* onboarding cluster — setup/test CLI, smart ignored host defaults, framework detection ([#10](https://github.com/apidepth-io/apidepth-python/issues/10)) ([87c492a](https://github.com/apidepth-io/apidepth-python/commit/87c492a404628d845926154a4e832d2806a1443e))

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
