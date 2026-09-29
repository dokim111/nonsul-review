# 릴리스 절차

이 문서를 정리한 2026-09-29의 초기 확인 상태는 다음과 같다. [GitHub 저장소](https://github.com/dokim111/nonsul-review)는 공개되어 있고 [초기 CI 실행](https://github.com/dokim111/nonsul-review/actions/runs/36523220236)은 통과했다. 이 시점에는 실제 API 예제 검증, GitHub pre-release 게시, PyPI 게시를 완료하지 않았다. 이후 완료 상태는 최신 README와 Releases에서 확인한다. 초기 CI 통과가 이후 변경이나 실제 API 검증까지 대신하지는 않는다.

API 키 설정부터 첫 호출까지는 [첫 실제 API 실행 안내](first-live-run.md)를 따른다. 아래 절차는 dev 실험을 검토한 뒤 최종 공개 근거를 만들고 게시하는 순서다.

이 문서의 명령은 저장소 루트에서 실행한다. `OWNER`나 `MODEL_ID`가 보이면 자신의 값으로 바꾼다. API 키를 Git 명령, 워크플로 입력, 릴리스 노트에 넣지 않는다.

## 0. 소스 ZIP에서 시작하는 경우

현재 공개 저장소의 릴리스를 진행할 때는 clone한 체크아웃에서 기존 이력을 이어 간다. 제공하는 소스 ZIP에는 `.git` 이력이 없으므로, ZIP에서 독립적인 새 저장소를 만드는 경우에만 다음을 실행한다.

```bash
git init -b main
git add -- README.md LICENSE pyproject.toml MANIFEST.in CHANGELOG.md CONTRIBUTING.md \
  .env.example .gitignore src tests scripts docs examples .github
git commit -m "Initial nonsul-review pre-release source"
```

공개 소스 ZIP에 포함된 `examples/`의 데모 raw·검수본은 `.gitignore`의 예외 규칙으로 함께 추적된다. 일반 실행 결과(`results/`, `examples/*/live/`)는 계속 제외되며, 사용자 실행 결과를 `examples/`의 데모 디렉터리에 섞지 않는다.

데모 파일은 실제 API 공개 기준을 채우지 않는다. 실제 실행 근거는 아래 2절에서 별도로 만든다.

## 1. 로컬 설치·검증

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
ruff check .
ruff format --check .
pytest --cov=nonsul_review --cov-report=term-missing
python -m build
python -m twine check dist/*
python scripts/release_check.py --dist dist
```

wheel과 sdist를 깨끗한 가상환경에 설치하고 저장소 밖에서 `nonsul-review --version`, 예제 `run --demo`를 실행한다. CI도 이 흐름을 구현한다. 코드·의존성·프롬프트를 바꾸면 관련 검증을 다시 실행한다.

`release_check.py`는 다음을 검사한다.

- Git이 초기화된 저장소에서는 추적 파일과 ignore되지 않은 신규 파일을 검사한다. 이미 추적된 `.env`나 `data/private/`도 탐지 대상이다.
- Git이 없는 소스 압축에서는 코드·예제·문서 등 명시한 공개 디렉터리를 검사한다.
- 배포 디렉터리는 wheel 하나와 sdist 하나를 포함해야 한다. 압축 내부의 경로·자격증명 패턴·프롬프트·데모 리소스를 확인한다.
- 의심스러운 자격증명을 발견해도 값을 출력하지 않고 파일명만 보고한다. 학습자 이름·개인정보를 모두 알아내는 검사는 아니므로 공개 파일은 사람이 읽는다.

검증용 임시 폴더와 실제 답안 결과는 공개 소스 묶음에 포함하지 않는다. `MANIFEST.in`은 wheel/sdist의 포함 범위를 정하고 실제 실행 raw·검수·개인 자료를 제외한다.

### dev 검토와 구현 동결

공개 예제와 비공개 dev 자료의 결과를 먼저 검토하고, 필요한 코드·프롬프트 변경을 적용한 뒤 관련 테스트와 dev 검증을 마친다. 비공개 입력·최초 채점·출력은 `data/private/`나 공개 저장소 밖에 보관한다. 공유 프롬프트를 수정했다면 그 영향을 받는 공개·비공개 dev 실험의 두 방식을 같은 입력과 모델·설정으로 다시 실행해 비교한다. 최종 test 자료로 프롬프트를 조정하지 않는다.

README나 gold 판정만 수정하여 모델 입력·코드·프롬프트·실행 설정이 바뀌지 않았다면 유료 API를 다시 부를 필요는 없다. gold를 수정한 경우에는 평가 계산을 다시 실행한다. dev 검토를 끝내고 코드·프롬프트·패키지 설정을 동결한 다음, 아래의 최종 공개 live 증거를 만든다.

## 2. 동결한 구현으로 공개 API 증거 생성

`examples/ex-001/`의 자체 작성 문항과 답안만 사용한다. `.env` 또는 환경변수에 `ANTHROPIC_API_KEY`, `NONSUL_MODEL`을 설정한다.

첫 실행 안내에서 현재 동결한 구현으로 최종 live 증거를 이미 만들었다면 아래의 유료 실행을 중복하지 않고 검증 명령부터 진행한다. 다음 실행 옵션은 첫 실행 안내의 설정과 같으며, 다른 모델을 사용할 때는 지원하는 설정을 확인한다.

```bash
python scripts/live_smoke.py --model MODEL_ID \
  --temperature 1 --max-tokens 16000 --timeout 180
python scripts/release_check.py --require-live
```

이 명령은 정답 a01·오답 a02를 두 방식으로 실제 실행한다. 루브릭이 3개인 현재 예제에서 각 답안의 `rubric`은 항목 3회와 전체 피드백 1회, `free`는 피드백·판정표 2회이므로, 재시도 없이도 총 **12회 API 요청**이 발생한다. 스키마 또는 일시 오류로 재시도하면 요청 수가 늘 수 있다. `--max-tokens`는 각 요청의 한도이며 전체 예산 한도가 아니다. 실패 시 부분 결과는 남지만 성공 manifest는 만들지 않는다.

현재 기본 temperature는 1이다. 값을 바꾸려면 선택한 모델이 지원하는지 확인한다. [Anthropic 공식 안내](https://platform.claude.com/docs/en/about-claude/model-deprecations#api-parameter-deprecations)는 최신 일부 모델의 비기본 sampling 값 제한과 Python SDK 변경을 설명한다. 도구는 사용자가 지정한 값을 조용히 변경하지 않는다.

모든 호출과 근거 검증이 성공하면 `examples/ex-001/live/evidence.json`이 생성된다. 릴리스 워크플로는 이 기본 경로를 검사한다. 기존 출력 디렉터리가 비어 있지 않으면 실행을 거부하므로, 재실행 시 이전 결과를 별도로 보관한 뒤 기본 경로를 비워 둔다. 이 디렉터리는 기본적으로 Git에서 제외된다. manifest는 다음을 기록하고 공개 게이트는 다시 검증한다.

| 기록 | 검사 목적 |
| --- | --- |
| 버전·코드/프롬프트 fingerprint | 실행 후 구현이 바뀌었는지 확인 |
| 문항·답안·판정표·raw 파일 해시 | 실행 근거 파일이 바뀌었는지 확인 |
| 모델·공통 설정·실제 응답 모델 | 두 방식의 조건이 일치하는지 확인 |
| API 단계와 최종 유효 응답 | 단계 누락·데모·실패를 성공으로 세지 않음 |
| 시각·선택적 Git commit | 실행 시점 기록; Git 미초기화 상태도 실행 가능 |

`source_sha256`은 `pyproject.toml`과 `src/nonsul_review/`의 코드·프롬프트·리소스를 검사한다. 예제 결과나 문서를 추가하는 새 commit 자체는 이전의 실행을 무효화하지 않는다. `git_commit`은 실행 시점의 참고 기록이며, 현재 commit과의 일치를 요구하지 않는다. 공개할 코드·프롬프트·패키지 설정을 변경했다면 최종 공개 예제를 다시 실행한다. 해시와 API 응답 로그는 감사 가능성을 높이지만, 제3자의 암호학적 호출 증명이나 수학적 정확도 인증은 아니다.

### 실제 실행 결과 검토와 Git 추가

모든 판정·인용·피드백과 raw 파일을 읽는다. 입력이 자체 작성 예제인지, 개인정보나 키가 없는지 확인한다. 실제 호출이 성공해도 판정의 정확성은 별도로 확인해야 한다. 결과를 수정해 모델이 옳게 판정한 것처럼 보이게 만들지 않는다. 잘못된 판정은 그대로 보관하고 알려진 한계로 기록한다.

확인한 9개 파일만 명시적으로 추적한다. `ex-001-a0*.json`처럼 넓은 패턴은 실패 파일이나 추가 결과도 포함할 수 있으므로 사용하지 않는다.

```bash
git add -f -- \
  examples/ex-001/live/evidence.json \
  examples/ex-001/live/ex-001-a01.rubric.json \
  examples/ex-001/live/ex-001-a01.rubric.raw.json \
  examples/ex-001/live/ex-001-a01.free.json \
  examples/ex-001/live/ex-001-a01.free.raw.json \
  examples/ex-001/live/ex-001-a02.rubric.json \
  examples/ex-001/live/ex-001-a02.rubric.raw.json \
  examples/ex-001/live/ex-001-a02.free.json \
  examples/ex-001/live/ex-001-a02.free.raw.json
```

데모 결과·임의의 성공 표식은 `--require-live`를 통과할 수 없다. 키가 없거나 실제 예제가 실패한 상태에서는 코드 검토본으로 보관하고 v0.1.0 공개 완료로 표시하지 않는다.

실제 공개 예제는 Git 저장소와 GitHub의 자동 소스 압축에 포함된다. wheel/sdist에는 공개 실행 raw를 포함하지 않으므로, API 로그가 필요한 검토자는 저장소의 `examples/ex-001/live/`를 확인한다.

### 문서 정리와 최종 빌드

실제 실행 결과를 검토한 뒤 README·CHANGELOG·릴리스 노트를 확인된 기록에 맞게 정리하고, 변경한 공개 파일만 이름을 지정해 stage한다. 릴리스 노트의 게시용 초안 표시도 실제 검증 상태에 맞게 갱신한다. 실행하지 않은 검증을 완료로 표시하지 않는다.

예를 들어 README와 릴리스 노트를 갱신했다면 다음처럼 추가한다. 다른 공개 파일도 바뀌었다면 그 경로를 별도로 확인해 추가한다.

```bash
git add -- README.md docs/release-notes-v0.1.0.md
```

README는 패키지 메타데이터에도 들어가므로 최종 문서 수정 뒤 다시 빌드한다. 이전 배포물은 별도로 보관하고 깨끗한 `dist/`에서 다음을 실행한다.

```bash
python -m build
python -m twine check dist/*
python scripts/release_check.py --dist dist --require-live --require-tracked
git diff --cached --name-only
git diff --cached --stat
```

stage된 목록에 키·비공개 입력·비공개 결과가 없는지 확인한다. `--require-tracked`는 Git index에 포함되었는지 검사하며 commit·push 완료를 보장하지 않는다. 최종 공개 파일을 commit하고 그 commit의 CI를 확인하는 단계가 남아 있다.

## 3. GitHub 저장소·CI

공개 원격 저장소가 이미 있으므로 새 저장소를 만들지 않고 기존 `origin`을 확인한다. 소스 ZIP에서 시작했다면 이 저장소의 기존 이력을 clone해 이어 가는 것을 권장한다.

```bash
gh auth status
git remote -v
git status --short
```

위에서 검토한 변경이 stage되어 있는 경우에만 commit한다. 이미 동일한 파일이 commit되어 있으면 추가 commit을 만들 필요가 없다. `main`에서 준비한 공개 변경을 push한다.

```bash
if ! git diff --cached --quiet; then
  git commit -m "Prepare nonsul-review v0.1.0 pre-release"
fi
git push origin main
```

`.github/workflows/ci.yml`은 Python 3.10~3.14 Linux 테스트와 Python 3.12 Windows·macOS 테스트, lint, build, 설치한 wheel 리소스·데모 실행을 수행한다. push 후 Actions → CI에서 방금 올린 commit의 실행이 통과했는지 확인한다. 초기 CI 실행이 아니라 최종 문서와 증거가 포함된 commit을 확인한 뒤 태그를 만든다.

API 키를 GitHub에 등록하지 않아도 CI를 실행할 수 있다. 일반 PR·push CI는 실제 API를 부르지 않는다.

## 4. GitHub v0.1.0 pre-release

GitHub 저장소 Settings → Environments에서 **`github-release`** 환경과 필요한 승인 규칙을 확인·설정한다. 환경이나 required reviewers가 이미 설정되어 있다고 가정하지 않는다. 워크플로 YAML만으로 외부 환경의 승인 규칙을 생성할 수는 없다. 아래 실행은 workflow ref가 `main`이므로 배포 브랜치를 제한한다면 **브랜치 `main`을 허용**해야 한다. `tag` 입력은 환경의 실행 ref를 바꾸지 않는다. [GitHub 환경 규칙](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)을 참고한다.

현재 코드의 버전과 태그는 `0.1.0`, `v0.1.0`이다. 검증한 commit에 태그를 만든다. 이미 사용한 태그를 이동하거나 기존 릴리스 자산을 덮어쓰지 않는다.

```bash
git tag -a v0.1.0 -m "nonsul-review v0.1.0 pre-release"
git push origin v0.1.0
gh workflow run github-release.yml --ref main -f tag=v0.1.0
```

웹 화면에서는 **Actions → GitHub pre-release → Run workflow → Branch: main → tag: v0.1.0 → Run workflow** 순서다. 실행 브랜치와 `tag` 입력은 별개다. 태그 push만으로 게시되지는 않는다. 워크플로가 기본 브랜치에 있어야 하며 실행자는 저장소 write 권한이 필요하다. [GitHub 수동 실행 안내](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)를 참고한다.

워크플로는 지정한 태그의 CI, 태그·패키지 버전 일치, 배포물 검사와 실제 예제 게이트를 다시 실행한 뒤 설정된 환경 규칙에 따라 게시한다. 승인이 필요한 환경이면 해당 승인도 완료되어야 한다. 게시 직전 원격 태그가 검증한 commit을 그대로 가리키는지도 확인한다. GitHub `pre-release` 표시를 켜고 `Latest`로 설정하지 않는다. `docs/release-notes-v0.1.0.md`가 게시할 노트다.

태그를 만든 뒤 문서나 구현을 고쳐야 한다면 그대로 게시하지 말고 변경 범위에 필요한 검증부터 다시 진행한다. 이미 사용한 태그를 이동해 검증 기록을 덮어쓰지 않는다.

수동으로 게시해야 한다면 같은 검증 이후 다음처럼 실행할 수 있다. 이 명령은 실제 외부 게시를 수행한다.

```bash
gh release create v0.1.0 dist/*.whl dist/*.tar.gz \
  --verify-tag --prerelease --latest=false \
  --title "nonsul-review v0.1.0 (pre-release)" \
  --notes-file docs/release-notes-v0.1.0.md
```

`--verify-tag`와 `--prerelease`의 동작은 [GitHub CLI 공식 문서](https://cli.github.com/manual/gh_release_create)에 설명되어 있다. 게시 후 웹 화면과 `gh release view v0.1.0`으로 태그·자산·사전 공개 표시를 확인한다.

## 5. PyPI 게시: 별도 작업

GitHub에서 `v0.1.0`을 pre-release로 표시해도 Python 패키지 버전 `0.1.0`은 PEP 440의 정규 버전이다. PyPI에서 사전 버전으로 취급하려면 `0.1.0rc1` 같은 버전을 별도로 선택하고 패키지 버전·코드 버전·태그를 일치시켜야 한다. 원 명세의 GitHub 태그를 따르기 위해 현재 버전은 `0.1.0`이다. 이 구분은 [Python 버전 명세](https://packaging.python.org/en/latest/specifications/version-specifiers/#pre-releases)를 따른다.

PyPI 이름 `nonsul-review`는 이 코드가 자동으로 확보한 이름이 아니다. 계정에서 사용 가능 여부를 확인하고, 이름이 충돌하면 패키지 이름·설치 안내를 먼저 조정한다.

1. PyPI 계정에서 pending 또는 기존 프로젝트의 Trusted Publisher를 등록한다.
2. repository owner/name은 실제 저장소와 일치시킨다.
3. workflow filename은 **`publish-pypi.yml`**, environment는 **`pypi`**로 정확히 입력한다.
4. GitHub에 `pypi` 환경을 만들고 required reviewers·허용 브랜치/태그를 설정한다.
5. 배포할 버전의 검사·실제 예제·GitHub CI가 통과한 뒤 `publish-pypi.yml`을 수동 실행한다.

```bash
gh workflow run publish-pypi.yml -f tag=v0.1.0
```

빌드 job은 읽기 권한으로 배포물을 만들고, 게시 job은 결과물을 내려받아 OIDC Trusted Publishing으로 업로드한다. 게시 job에만 `id-token: write`를 부여하며 PyPI 비밀번호·장기 API 토큰을 저장하지 않는다. 구성은 [PyPI Trusted Publishing 안내](https://docs.pypi.org/trusted-publishers/using-a-publisher/)와 [PyPA GitHub Actions 배포 안내](https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/)를 따른다.

GitHub pre-release를 만들었다고 PyPI 워크플로가 자동 실행되지는 않는다. PyPI 게시가 성공한 뒤 새 가상환경에서 `python -m pip install nonsul-review==0.1.0`과 설치된 명령을 확인하고 README의 게시 상태를 갱신한다. 이미 게시된 버전은 같은 이름으로 다시 업로드할 수 없으므로 수정본에는 새 버전을 사용한다.

## 워크플로 의존성

2026-09-29 기준 공식 릴리스와 사용 문서를 확인하여 아래 버전으로 작성했다. 이는 이 저장소의 실제 Actions 실행을 대신하지 않는다.

| Action | 고정 버전·확인 자료 |
| --- | --- |
| `actions/checkout` | [v7.0.1](https://github.com/actions/checkout/releases/tag/v7.0.1), commit `3d3c42e5aac5ba805825da76410c181273ba90b1` |
| `actions/setup-python` | [v7.0.0](https://github.com/actions/setup-python/releases/tag/v7.0.0), commit `5fda3b95a4ea91299a34e894583c3862153e4b97` |
| `actions/upload-artifact` | [v7.0.1](https://github.com/actions/upload-artifact/releases/tag/v7.0.1), commit `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a` |
| `actions/download-artifact` | v8.0.1, commit `3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c`; [GitHub CLI 공식 워크플로에서 사용하는 pin](https://github.com/cli/cli/blob/trunk/.github/workflows/deployment.yml) 대조 |
| `pypa/gh-action-pypi-publish` | [v1.14.2](https://github.com/pypa/gh-action-pypi-publish/releases/tag/v1.14.2) |

Python 버전 matrix와 Action 버전은 유지보수 시 함께 확인한다. 자체 호스팅 runner에는 각 Action의 최소 runner 요구사항도 적용된다.
