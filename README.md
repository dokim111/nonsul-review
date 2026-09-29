# nonsul-review

**수리논술 강사의 검수·확정을 돕는 Python 명령줄 도구.** 문항·루브릭과 텍스트 답안을 입력하면 항목별 판정, 답안 인용, 판정 이유, 피드백 초안을 만든다. 강사가 YAML에서 수정하고 확정하면 변경 기록을 함께 보관한다.

루브릭을 먼저 적용하는 방식과 전체 피드백을 먼저 쓰는 방식을 같은 입력·설정으로 실행하고, 사람의 최초 채점 및 합의 판정과 비교할 수 있다. 학생용 서비스나 점수 자동 확정 기능은 제공하지 않는다.

## 현재 상태

**버전 `0.1.0`의 소스 코드를 [GitHub](https://github.com/dokim111/nonsul-review)에 공개했으며, [v2 구현 커밋의 GitHub Actions CI](https://github.com/dokim111/nonsul-review/actions/runs/36540313464)를 통과했다. 2026-09-29에 Opus 5.5와 검수 절차 v2로 `ex-001`의 실제 API 실행 및 결과 대조 검토를 완료했다. GitHub pre-release 게시와 PyPI 배포는 별도 단계다.** [공개 실행 증거](examples/ex-001/live/evidence.json)에는 네 실행의 결과·raw와 코드·프롬프트·입력 해시를 연결해 두었다. dev 검증 결과와 남은 한계는 [릴리스 노트](docs/release-notes-v0.1.0.md)에 기록했다.

| 구분 | 이 코드베이스에 포함된 내용 |
| --- | --- |
| 입력 | YAML 문항·루브릭, Markdown/LaTeX 답안, 엄격한 스키마 검증 |
| 판정 | `rubric`, `free` 절차, Anthropic API 호출, 원본 응답·재시도 기록 |
| 실행 | 단일 실행, 디렉터리 배치, dev/test 선택, 덮어쓰기 방지 |
| 강사 검수 | 검수 YAML, 수정 후 확정, 원본과 확정본의 변경 기록 |
| 평가 | 최초 채점 간 일치도, 모델-평가자 일치도, 오류 탐지, 대안 풀이 오판 지적 |
| 배포 준비 | 설치 가능한 wheel/sdist, 테스트, CI, 수동 GitHub pre-release·PyPI 워크플로 |
| 공개 예제 | 자체 출제 문항 3개. `ex-001`은 형식 확인용 최소 예제, **사람이 작성한 데모 결과**와 별도 `live/`의 **실제 API 결과**, `ex-002`·`ex-003`은 수리논술 수준 문항과 정답·오답·대안 풀이 답안 |

다음은 이후 수행할 일이다.

- 독립적으로 얻은 평가자 최초 채점과 합의 판정으로 평가셋을 검증한다.
- 공개 증거를 포함한 최종 커밋의 CI와 릴리스 검사를 확인한 뒤, `v0.1.0`을 GitHub **pre-release**로 게시한다.
- PyPI의 프로젝트 이름·Trusted Publisher를 설정하고 배포를 진행한다. 현재 `pip install nonsul-review`의 성공을 보장하지 않는다.

`--demo`는 제공된 예제의 처리 흐름을 확인하는 기능이다. API를 부르지 않으며 결과에 `is_demo: true`를 기록한다. 데모 수치를 실제 모델 성능이나 평가자 일치도 연구 결과로 해석하면 안 된다. 실제 호출을 확인하는 공개 게이트는 데모 결과를 거부한다.

## 공개 예제

| 예제 | 내용 | 데모 결과 |
| --- | --- | --- |
| [ex-001](examples/ex-001/) | 도함수와 경계 조건. 입력 형식과 전체 흐름을 확인하는 최소 예제 | 있음 (사람이 작성) |
| [ex-002](examples/ex-002/) | 부품 배열의 점화식·일반항·홀짝·극한. 경우 분할과 두 단계 귀납의 근거를 봄 | 없음 |
| [ex-003](examples/ex-003/) | 뉴턴 방법 수열의 하한·단조성·수렴·닫힌꼴. 수렴 논증의 비약을 잡는지 봄 | 없음 |

`ex-002`와 `ex-003`은 저자가 직접 출제한 모의 문항을 공개용으로 재구성했다. 루브릭이 4항목이라 논증 단계별 판정의 차이가 드러나며, 실제 API 실행으로 모델 판정을 확인하는 데 쓴다. 각 폴더의 README에 작성자가 의도한 판정을 적었다.

## 설치

Python **3.10 이상**이 필요하다. 내려받은 저장소 또는 소스 압축을 푼 디렉터리에서 실행한다.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
nonsul-review --version
```

Windows PowerShell에서는 가상환경 활성화만 다음과 같이 바꾼다.

```powershell
.venv\Scripts\Activate.ps1
```

코드 수정·테스트 없이 사용할 때는 `python -m pip install .`로 설치해도 된다. 빌드된 wheel을 받은 경우 `python -m pip install 경로/nonsul_review-0.1.0-py3-none-any.whl`로 설치한다. 프롬프트와 데모 fixture는 wheel에도 포함된다. 아래 명령의 예제 입력은 소스 저장소의 `examples/`에 있다.

## 5분 데모: API 키 없이 실행

모든 예제는 저장소 루트에서 실행한다. 출력 폴더의 같은 파일을 덮어쓰지 않으므로 재실행할 때는 새로운 출력 폴더를 지정한다.

```bash
nonsul-review run \
  --problem examples/ex-001/problem.yaml \
  --answer examples/ex-001/answers/a01.md \
  --mode rubric --demo --out results/demo-rubric

nonsul-review run \
  --problem examples/ex-001/problem.yaml \
  --answer examples/ex-001/answers/a02.md \
  --mode free --demo --out results/demo-free
```

성공하면 다음 두 파일을 확인할 수 있다.

| 파일 | 내용 |
| --- | --- |
| `results/demo-rubric/ex-001-a01.rubric.json` | 공통 판정표 |
| `results/demo-rubric/ex-001-a01.rubric.raw.json` | 단계별 응답과 재시도 기록 |

데모는 등록된 예제 문항·답안에만 적용된다. 새 답안의 수학적 내용을 자동 판정하는 오프라인 엔진은 아니다.

## 실제 Anthropic API 실행

`.env.example`을 `.env`로 복사하고 값을 입력한다. 키는 환경변수 또는 `.env`에서만 읽는다. 명령줄 인수로 API 키를 받지 않는다.

```dotenv
ANTHROPIC_API_KEY=발급받은_키
NONSUL_MODEL=계정에서_사용할_정확한_모델_ID
```

```bash
nonsul-review run \
  --problem examples/ex-001/problem.yaml \
  --answer examples/ex-001/answers/a01.md \
  --mode rubric --out results/live-rubric \
  --temperature 1 --max-tokens 4096 --timeout 120
```

모델은 `--model` 또는 `NONSUL_MODEL`로 지정한다. 사용할 수 있는 정확한 ID는 자신의 Anthropic 계정에서 확인한다. 자동으로 다른 모델로 바꾸지 않는다. `--demo`를 빼면 문항·모범답안·루브릭과 답안 **본문**이 외부 API로 전송되며 호출 비용이 발생한다. 답안 앞부분의 `id`, `source`, `error_types`, `split` 등 메타데이터는 모델 입력에 들어가지 않는다.

기본 temperature는 `1`이다. 현재 일부 Claude 모델은 다른 값을 허용하지 않으므로, `--temperature 0`은 해당 값을 지원하는 모델에서만 사용한다. 사용자가 지정한 값을 임의로 다른 값으로 바꾸지 않는다. 이 설정은 [Anthropic의 API 매개변수 폐기 안내](https://platform.claude.com/docs/en/about-claude/model-deprecations#api-parameter-deprecations)를 반영한다.

공정한 비교를 위해 두 방식에 같은 모델 ID, temperature, max_tokens, timeout과 입력 파일을 지정한다. 결과 메타데이터의 입력·설정 해시도 확인한다. 같은 입력을 제공하더라도 두 방식은 호출 횟수와 이전 단계에서 생성된 중간 내용이 다르므로, 동일 비용이나 동일 토큰 수를 보장하는 실험은 아니다.

## 검수 절차 버전

`run`과 `batch`의 기본 절차는 **v2**다. v2는 v1 dev 검증에서 확인한 문제(학생용 피드백에 섞인 채점 메모, 피드백 속 보완 설명의 수학 오류, 강사 확인 요청이 문장으로만 전달되는 문제)를 반영했으며 API 호출 수는 v1과 같다. 마지막 단계는 강사 확인이 필요한 사항을 `review_flags`로 따로 남기고, 해당 항목을 `needs_review`로 표시한다. 이전 결과를 재현하려면 `--prompt-set v1`을 지정한다. 자세한 내용은 [검수 절차 v2](docs/review-procedure-v2.md)를 참고한다.

## 두 방식의 차이

| 방식 | 처리 순서 | 공통 입력 |
| --- | --- | --- |
| `rubric` | 루브릭 항목을 하나씩 판정 → 모인 판정으로 전체 피드백 작성 | 문항, 모범답안, 전체 루브릭, 허용 대안 풀이, 답안 본문 |
| `free` | 답안 전체 피드백 작성 → 그 뒤 공통 항목별 판정표 작성 | 위와 같음 |

두 방식은 같은 판정표 스키마를 사용한다. 자유형 방식에도 같은 루브릭을 제공한다. 자유형 피드백 뒤에 판정표를 작성하는 단계도 실제로 분리되어 있다.

## 입력 작성

### 문항 YAML

```yaml
id: my-001
title: 적분과 함수의 부호
prompt: |
  주어진 함수의 부호를 조사하고 정적분을 구하시오.
model_answer: |
  구간별 부호를 조사한 뒤 적절하게 적분한다.
rubric:
  - id: R1
    step: 구간별 부호 조사
    criteria: 모든 영점을 찾고 각 구간의 부호를 정확히 설명한다.
    partial: 영점은 찾았지만 일부 구간의 부호 설명이 빠졌다.
    points: 3
  - id: R2
    step: 적분 계산과 결론
    criteria: 구간 분할과 적분 계산을 통해 올바른 결론을 얻는다.
    partial: 적분식은 맞으나 계산 오류 또는 설명 누락이 있다.
alternatives: |
  대칭성으로 계산한 풀이도 논리가 충분하면 인정한다.
```

실제로 실행할 문항에는 완전한 발문과 모범답안을 넣는다. `rubric`의 `id`는 문항 내에서 중복될 수 없다. `points`는 선택 정보이며, 도구가 총점을 자동 확정하지 않는다.

### 답안 Markdown

```markdown
---
id: my-001-a01
problem: my-001
source: synthetic
error_types: [E2]
split: dev
---
학생 답안을 여기에 입력한다. 수식은 $f(x)$처럼 LaTeX로 쓴다.
```

`source`는 `synthetic` 또는 `learner`이다. `error_types`는 사람이 부여한 평가용 태그이며 모델에는 공개되지 않는다. `split`은 `dev` 또는 `test`로 정하고 최종 평가 답안으로 프롬프트를 고치지 않는다. 구체적인 예제와 오류 분류는 [examples/ex-001](examples/ex-001/)을 참고한다.

## 명령어

```text
nonsul-review --help
nonsul-review run --help
nonsul-review batch --help
nonsul-review review --help
nonsul-review finalize --help
nonsul-review evaluate --help
nonsul-review validate --help
```

| 명령 | 목적 |
| --- | --- |
| `run --problem P --answer A --mode rubric\|free --out DIR` | 답안 한 개의 판정표·원본 응답 저장 |
| `batch --problems DIR --answers DIR --mode rubric\|free --out DIR` | 여러 문항·답안을 ID로 연결해 실행 |
| `review --result FILE [--out FILE]` | 강사 검수 YAML 생성 |
| `finalize --review FILE [--out DIR]` | 수정·확정된 판정표와 변경 기록 저장 |
| `evaluate --pred DIR --raters DIR --gold DIR [--answers DIR] [--out FILE]` | 사람의 판정과 모델 결과 비교 |
| `validate --problem FILE [--answer FILE] [--result FILE]` | API 호출 없이 형식·ID 연결·답안 인용 확인 |

### 배치 실행

```bash
nonsul-review batch \
  --problems examples/ex-001 \
  --answers examples/ex-001/answers \
  --mode rubric --demo --out results/batch-rubric
```

실제 평가 자료는 공개 저장소 밖에 둔다. `--split dev` 또는 `--split test`를 추가하면 해당 답안만 선택하며, 이때 모든 입력 답안이 split을 명시해야 한다. dev와 test가 섞였는데 `--split`을 생략한 실행은 거부한다. 답안의 `problem` 값으로 문항을 연결하며, 문항·답안 ID 충돌이나 기존 출력과의 충돌은 실행 전에 확인한다. 일부 호출 실패는 `batch.rubric.json` 또는 `batch.free.json`에 남기고 종료 코드로 알린다.

### 강사 검수·확정

```bash
nonsul-review review \
  --result results/demo-rubric/ex-001-a01.rubric.json \
  --out results/demo-rubric/a01.review.yaml
```

생성된 YAML에는 원본을 보여 주는 `model_verdict`, `model_evidence`, `model_reason`, `model_feedback`과 수정할 `verdict`, `evidence`, `reason`, `feedback`이 나란히 있다. `edit_level`에는 `none`(그대로 사용 가능), `minor`(일부 수정), `major`(핵심 수정) 중 하나를 적을 수 있다. 비워 두어도 확정할 수 있다. `source`, `original`, `model_*`, 문항·답안·루브릭 ID는 보존한다. 파일을 옮길 때는 원본 JSON과의 상대 경로도 유지한다.

```bash
nonsul-review finalize \
  --review results/demo-rubric/a01.review.yaml \
  --out results/final
```

`finalize`는 강사가 검토한 파일을 명시적으로 확정하는 명령이다. `ex-001-a01.rubric.final.json`과 `ex-001-a01.rubric.changes.json`을 저장하고, 각 항목의 변경 전·후 판정, 피드백 수정 여부, 수정 수준을 변경 기록에 남긴다. `verdict: null`인 항목이 있으면 확정을 거부하므로 강사가 먼저 세 판정 중 하나를 정해야 한다. 데모로 시작한 결과는 확정하더라도 데모 표시가 유지된다.

### 평가

```bash
nonsul-review evaluate \
  --pred examples/ex-001/results \
  --raters examples/ex-001/evaluation/raters \
  --gold examples/ex-001/evaluation/gold \
  --answers examples/ex-001/answers \
  --allow-demo --out results/demo-evaluation.json
```

위 명령은 공개된 사람이 작성한 자료로 **계산 절차**를 확인한다. 실제 실험에서는 `--allow-demo`를 쓰지 않고, 처음 채점한 평가자 자료와 별도로 합의한 기준 판정을 사용한다. 평가자들이 서로 또는 모델의 결과를 보기 전 기록한 최초 채점을 나중에 합의 판정으로 덮어쓰면 안 된다.

| 질문 | 계산 |
| --- | --- |
| 평가자끼리 얼마나 일치하는가? | 평가자 쌍별 항목 일치율, 순서형 3단계의 선형 가중 Cohen κ |
| 모델이 각 평가자와 얼마나 일치하는가? | 방식·평가자 쌍별 같은 일치도 |
| 합의한 오류를 얼마나 찾는가? | gold가 `partial`/`not_met`인 항목을 양성으로 한 precision·recall |
| 올바른 대안 풀이를 잘못 지적하는가? | 대안 풀이의 gold `met` 항목에서 오류로 판정한 비율 |

Gold의 `unresolved: true` 항목은 오류 탐지 지표에서 제외하고 수를 보고한다. 분모가 0이거나 κ가 정의되지 않는 경우는 `null`을 표시한다. 모델이 판정을 보류한 항목과 누락된 대응 항목의 수·포함률도 함께 확인한다. `recall`은 실제 판정한 항목에서의 재현율이고, `end_to_end_recall`은 누락·보류도 포함한 기준 오류 전체에서의 재현율이다. 답안 또는 gold의 `is_alternative: true`로 표시한 경우에만 대안 풀이 지표에 들어간다. 오류 유형별 결과는 `--answers`의 메타데이터로 계산한다. 파일 형식·정의·분모는 [평가 문서](docs/evaluation.md)에 정리되어 있다. 자체 작성 예제의 수치는 실제 학습자 분포를 대표하지 않으며 결론은 실제 사용한 평가셋 범위에 한정한다.

## 출력·재현성·오류 처리

공통 판정표의 각 항목에는 `rubric_id`, `verdict`, `evidence`, `reason`, `feedback`을 저장한다. 정상 판정은 `met`, `partial`, `not_met`이다. 판정할 수 없는 상태는 명세의 모호한 부분을 보완하여 `verdict: null`, `needs_review: true`와 이유로 표현한다. `not_met`으로 바꾸지 않으며 최종 확정 전 강사가 해결해야 한다. `evidence`는 답안 본문에 실제로 존재하는 연속 문자열이어야 한다. 자세한 규칙은 [데이터 형식 문서](docs/data-format.md)를 참고한다.

결과에는 모델 ID, 프롬프트 버전·파일 해시, 입력·설정 해시, temperature, 생성 시각과 데모 여부를 기록한다. 입력 해시는 모델에 제공한 정보에 기반하므로 숨긴 오류 태그를 수정해도 모델 입력이 달라지지는 않는다.

스키마에 맞지 않는 응답은 **최초 시도 + 최대 두 번의 재시도**로 제한한다. 루브릭에 없는 ID, 항목 누락·중복, 잘못된 판정값은 실패로 처리한다. API 인증 실패 등 복구되지 않는 오류도 종료 코드로 알린다. 끝까지 실패한 경우 성공 판정표를 만들어 내지 않고 원본 응답과 `*.failure.json`을 남긴다. `*.raw.json`은 각 단계에서 실제로 받은 메시지 본문과 검증 결과를 기록한다.

재현성 기록은 어떤 설정으로 실행했는지 확인하게 해 준다. 같은 설정의 API 호출이 완전히 같은 문장을 돌려준다는 보장은 아니다. 모델·프롬프트·설정을 변경한 실험은 별도의 출력 디렉터리에 저장한다.

## 데이터 취급

API 키는 `.env` 또는 환경변수로만 관리한다. `.env.example`에는 값이 없으며, `.env`, 개인 키 파일, `data/private/`, 일반 실행 출력은 Git 추적에서 제외한다. `.gitignore`는 이미 추적한 파일을 자동 삭제하지 않으므로 공개 전 변경 목록을 확인한다.

원본 응답에는 답안 인용이 포함될 수 있다. 모델 결과·강사 검수본·원본 응답은 입력 답안과 같은 수준으로 관리한다. 학습자 답안과 평가자 최초 채점은 저장소 밖에 두고, 공개 예제에는 권리를 확인한 자체 작성 자료만 사용한다. 공개 전 `python scripts/release_check.py --dist dist`로 패키지·추적 파일의 금지 경로와 일반적인 자격증명 패턴을 점검할 수 있다. 이 검사는 모든 개인정보를 자동 판별하는 도구는 아니다.

## 개발·검증

```bash
python -m pip install -e ".[dev]"
ruff check .
ruff format --check .
pytest --cov=nonsul_review --cov-report=term-missing
python -m build
python -m twine check dist/*
python scripts/release_check.py --dist dist
```

테스트는 API를 모의 응답으로 대체한다. PR·일반 CI에서 유료 API 호출을 하지 않는다. CI 설정은 Linux의 Python 3.10~3.14와 Windows·macOS의 Python 3.12에서 설치·테스트하고, wheel 설치 후 패키지 리소스와 데모 실행을 확인한다. 실제 지원 상태는 자신의 저장소에서 CI를 실행한 결과로 확인한다.

실제 API 공개 예제는 다음과 같이 별도로 실행한다. 환경변수 또는 `.env`에 키와 모델 ID가 있어야 한다.

```bash
python scripts/live_smoke.py --model MODEL_ID --prompt-set v2
python scripts/release_check.py --dist dist --require-live
```

성공 결과와 근거 manifest가 없는 동안 `--require-live`는 실패한다. 이 실패를 데모 fixture나 임의로 작성한 성공 표식으로 우회하지 않는다. GitHub pre-release와 PyPI 게시 순서는 [릴리스 절차](docs/releasing.md), 개발 참여 방법은 [CONTRIBUTING.md](CONTRIBUTING.md)를 참고한다.

## 범위와 라이선스

이미지·손글씨 인식, 학생용 앱, 회원·결제, 여러 모델 순위 비교, 실제 수업 운영 시스템은 이 버전에 포함되지 않는다. UI 확장은 이후 과제다. 현재 출력은 강사가 검토할 초안이며, 프로그램이 학생의 최종 점수나 합격 여부를 정하지 않는다.

코드·프롬프트·저장소의 자체 작성 예제는 [MIT License](LICENSE)로 제공한다. 외부에서 추가한 문제·답안에는 해당 자료의 권리가 별도로 적용될 수 있다.
