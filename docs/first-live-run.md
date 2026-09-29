# 첫 실제 API 실행 가이드

설치와 무료 데모를 확인한 뒤 작은 실제 요청부터 시작하여 사용량과 판정 내용을
살펴보는 순서다. 이 문서가 있다는 사실이나 오프라인 데모 성공만으로 실제 API
연결·비용·수학적 판정 품질이 검증된 것은 아니다. 각 단계의 실행 결과를 확인한
뒤 다음 단계로 진행한다.

실제 실행은 다음 설정으로 통일한다.

| 설정 | 값 |
| --- | --- |
| 모델 | `claude-opus-5-5` |
| temperature | `1` |
| max_tokens | `16000` |
| 요청별 timeout | `180`초 |
| 개발용 답안 선택 | `--split dev` |

이 모델에 접근할 수 있는 계정에서 실행한다. 모델 접근 오류가 나면 계정의 사용
가능 모델을 확인하고 실행 계획을 다시 정한다. 비교 도중에 모델을 바꾸면 별도
실험 회차로 기록한다. 아래 명령은 저장소 루트에서 실행하는 Bash 형식이다.

## 1. 설치와 API 호출 없는 데모

Python 3.10 이상을 준비하고 내려받은 저장소의 루트로 이동한다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
nonsul-review --version
```

Windows의 가상환경 활성화 방법은 [README의 설치 안내](../README.md#설치)를
참고한다. 기존 가상환경이 있으면 그 환경을 활성화하여 설치 상태를 확인한다.

API 키를 설정하기 전에 다음 데모를 실행할 수 있다.

```bash
nonsul-review run \
  --problem examples/ex-001/problem.yaml \
  --answer examples/ex-001/answers/a01.md \
  --mode rubric \
  --demo \
  --out results/setup-demo-01
```

이 명령의 API 요청 수는 **0회**다. 제공된 예제용으로 작성해 둔 응답으로 처리
흐름을 확인하며, 결과에 `is_demo: true`가 남는다. 같은 출력 폴더에 다시 실행하면
덮어쓰기를 거부하므로 재실행에는 `results/setup-demo-02` 같은 새 경로를 쓴다.

**`free`는 ‘자유형 방식’이라는 이름이다. 무료 실행 옵션이 아니다.**
`--mode free`로 실행하면서 `--demo`를 생략하면 실제 API 요청과 비용이 발생한다.
API를 호출하지 않는 데모는 `--demo`를 명시해야 하며 제공된 `ex-001` 입력에만
사용할 수 있다.

## 2. Workspace, 예산, API 키 준비

[Claude Console](https://platform.claude.com/)에서 이번 실행용 Workspace와 API 키를
준비한다. Workspace의 키와 사용량을 구분하면 다른 작업과 섞이지 않은 비용을
확인하기 쉽다.

1. 전용 Workspace를 생성하거나 사용할 Workspace를 선택한다. 생성 권한이 없으면
   조직 관리자에게 요청한다.
2. 해당 Workspace의 지출 한도와 알림을 설정하고 조직 전체 한도도 확인한다.
   Workspace 한도는 조직 한도 이하로 설정하며, 기본 Workspace에는 별도 한도를
   설정할 수 없으므로 필요한 경우 전용 Workspace를 사용한다.
3. Billing에서 크레딧 잔액을 확인하고 필요한 금액만 충전한다. 자동 충전을 원하지
   않으면 Auto-reload를 Off로 둔다.
4. 그 Workspace에서 API 키를 발급한다.
5. Usage 또는 Cost 보고서에서 해당 Workspace를 선택하여 실행 전 사용량과
   실행 후 증가분을 확인할 준비를 한다.

설정 위치와 권한은 [공식 Workspace 안내](https://platform.claude.com/docs/en/manage-claude/workspaces)와
[공식 사용 한도 안내](https://platform.claude.com/docs/en/api/rate-limits)를 따른다.
예를 들어 월 한도 $10, 알림 $5·$8로 시작하고 실제 비용을 확인한 뒤 한도를
조정할 수 있다. 충전과 자동 충전 설정은
[공식 결제 안내](https://support.claude.com/en/articles/8977456-how-do-i-pay-for-my-claude-api-usage)를 참고한다.
지출 한도는 실행 계획의 예산과 구분하여 관리한다. **특정 충전 금액만으로 이
가이드 전체를 완료할 수 있다고 보장하지 않는다.** 실제 비용은 모델의 현재
단가, 입력·출력 토큰, 재시도와 반복 실행에 따라 달라진다. 먼저 작은 실행의
비용을 보고 다음 단계에 사용할 예산을 정한다.

저장소 루트의 `.env.example`을 참고하여 로컬 `.env`를 만들거나 기존 `.env`를
편집한다. 아래는 값이 비어 있는 형식 예시다.

```dotenv
ANTHROPIC_API_KEY=
NONSUL_MODEL=claude-opus-5-5
```

발급받은 키는 로컬 편집기에서 `ANTHROPIC_API_KEY=` 뒤에 입력한다. 기존 `.env`를
템플릿으로 덮어쓰지 않는다. 키는 환경변수 또는 로컬 `.env`로만 관리하고 채팅,
명령줄 인수, 스크린샷, Git 커밋, 공개 결과에 넣지 않는다. 환경변수에 이미 설정한
키가 있으면 `.env`보다 우선하므로 이번 Workspace의 키가 적용되는지 확인한다.
일반 GitHub CI에는 API 키가 필요하지 않다.

## 3. 첫 실제 호출: ex-001 정답을 rubric으로 실행

```bash
nonsul-review run \
  --problem examples/ex-001/problem.yaml \
  --answer examples/ex-001/answers/a01.md \
  --mode rubric \
  --model claude-opus-5-5 \
  --temperature 1 \
  --max-tokens 16000 \
  --timeout 180 \
  --out results/opus55-first-rubric-01
```

`ex-001`은 루브릭이 3개이므로 항목별 판정 3회와 전체 피드백 1회, **재시도 없이
4회 API 요청**을 보낸다. 이 실행은 연결과 기본 결과를 확인하는 첫 단계이며
최종 릴리스용 실행 근거는 뒤에서 최종 코드로 별도 생성한다.

성공하면 `results/opus55-first-rubric-01/`의 다음 파일을 확인한다.

- `ex-001-a01.rubric.json`: 공통 판정표
- `ex-001-a01.rubric.raw.json`: 단계별 응답과 검증 기록

확인할 내용은 다음과 같다.

- `meta.provider`가 `anthropic`이고 `meta.is_demo`가 `false`인지 확인한다.
- 모델·실제 응답 모델·temperature·프롬프트와 입력 해시를 확인한다.
- 각 인용이 실제 답안에 있고, 판정 이유가 수학적으로 타당한지 읽는다.
- Console에서 사용량과 비용 증가분을 확인하고 다음 단계의 예산을 결정한다.

오류가 발생하면 성공한 것으로 간주하지 않고 종료 메시지와 로컬 `*.failure.json`,
`*.raw.json`을 확인한다. 키·모델 접근·한도 문제를 해결한 다음 새 출력 폴더로
다시 실행한다. 인증 오류나 지출 한도 오류가 계속되는 상태에서 큰 배치를 반복하지
않는다.

## 4. ex-002를 두 방식으로 실행하고 비용 확인

`ex-002`의 3개 답안을 같은 설정으로 실행한다. 첫 명령이 성공했는지 확인한 뒤
두 번째 명령을 실행한다.

```bash
nonsul-review batch \
  --problems examples/ex-002 \
  --answers examples/ex-002/answers \
  --split dev \
  --mode rubric \
  --model claude-opus-5-5 \
  --temperature 1 \
  --max-tokens 16000 \
  --timeout 180 \
  --out results/dev-ex002-r01

nonsul-review batch \
  --problems examples/ex-002 \
  --answers examples/ex-002/answers \
  --split dev \
  --mode free \
  --model claude-opus-5-5 \
  --temperature 1 \
  --max-tokens 16000 \
  --timeout 180 \
  --out results/dev-ex002-r01
```

루브릭 4항목인 답안 하나당 루브릭 방식은 5회, 자유형은 2회를 요청한다.
따라서 답안 3개는 재시도 없이 **rubric 15회 + free 6회 = 21회**다.

같은 문항 묶음의 두 방식은 출력 이름에 `rubric` 또는 `free`가 들어가므로 같은
`--out`을 사용할 수 있다. `batch.rubric.json`과 `batch.free.json`에서 각각
`selected: 3`, `succeeded: 3`, `failed: 0`인지 확인하고 실제 결과를 읽는다.
배치는 한 답안이 실패해도 다음 답안으로 계속 진행하므로 최종 집계를 확인한다.

여기서 **비용을 다시 확인하고 진행 여부를 정한다.** 첫 소규모 예제보다 문항·답안과
생성 내용이 길 수 있으므로 첫 4회 요청의 비용을 요청 수에 단순 비례시키지 않는다.
Console 사용량, 실제 재시도 수, 남은 지출 한도를 함께 보고 다음 단계로 진행한다.

## 5. ex-003 실행과 선택적 개인 자료

예산과 결과를 확인한 뒤 `ex-003`의 두 방식을 실행한다.

```bash
nonsul-review batch \
  --problems examples/ex-003 \
  --answers examples/ex-003/answers \
  --split dev \
  --mode rubric \
  --model claude-opus-5-5 \
  --temperature 1 \
  --max-tokens 16000 \
  --timeout 180 \
  --out results/dev-ex003-r01

nonsul-review batch \
  --problems examples/ex-003 \
  --answers examples/ex-003/answers \
  --split dev \
  --mode free \
  --model claude-opus-5-5 \
  --temperature 1 \
  --max-tokens 16000 \
  --timeout 180 \
  --out results/dev-ex003-r01
```

이 문항도 답안 3개, 루브릭 4항목이므로 **15 + 6 = 21회**다. ex-002와 ex-003은
출력 폴더를 분리한다. 서로 다른 문항 배치를 같은 폴더에 저장하면 이미 만들어진
`batch.rubric.json` 또는 `batch.free.json`과 충돌한다.

개인 자료는 선택적으로 추가한다. 다음 일반 규칙을 적용한다.

- 입력, 작성자 기준 판정, 실제 결과, raw, 검수본을 모두 `data/private/` 아래 또는
  공개 저장소 밖에 둔다. 공개 문항 디렉터리로 옮기지 않는다.
- 발문과 모범답안, 루브릭, 답안 파일을 먼저 검토하고 개발용 답안에는 `split: dev`를
  명시한다. `--answers`는 답안 Markdown만 있는 폴더를 가리킨다.
- 같은 문항 묶음의 두 방식은 같은 설정과 새 출력 폴더를 사용한다.
- 문항 2개에 각각 답안 3개가 있고 모든 문항의 루브릭이 4개라면, 답안 6개에 대해
  `6 × (5 + 2) = 42회`가 추가된다. 자료 구성이 다르면 아래 식으로 다시 계산한다.
- 공개 문서에 개인 문항·답안·인용을 넣지 않는다. 검토한 집계와 실행 조건을 공유할
  때도 작성자 의도 판정과의 비교인지 독립 평가자의 판정과의 비교인지 구분한다.

## 6. dev 결과 검토, 필요한 수정, 최종 상태 확정

공개 예제 README의 의도 판정은 검토의 출발점이다. 모델과 기준이 다르면 해당
논증을 확인하여 어느 쪽을 고쳐야 하는지 판단하고 이유를 기록한다.

| 검토 필드 | 확인할 내용 |
| --- | --- |
| 실행 회차·문항·답안·항목·방식 | 어느 결과를 검토했는지 |
| 모델 판정 / 기준 판정 / 강사 판단 | 판정 불일치와 최종 판단 |
| 인용 원문 | 답안에 실제 존재하고 판정을 뒷받침하는지 |
| 이유의 수학적 정확성 | 계산, 논리, 경계 조건, 정리 적용이 타당한지 |
| 논증 누락 탐지 | 필요한 단계의 생략을 찾았는지 |
| 대안 풀이 오판 | 올바른 다른 풀이를 잘못 지적했는지 |
| 보류·수정 수준 | `needs_review`, `null` 판정, `edit_level` |
| 수정 내용과 근거 | 판정·피드백·루브릭·프롬프트 등 무엇을 왜 바꿨는지 |

공개 예제의 대안 풀이 답안은 `ex-002-a03`, `ex-003-a03`이다. 작성자 기준이나
데모 자료를 독립 평가자의 최초 채점이라고 표현하지 않는다. 정식 평가용 test
답안으로 프롬프트를 조정하지 않는다.

프로그램의 `review` 명령은 편집 가능한 YAML을 만든다. 예를 들면 다음과 같다.

```bash
nonsul-review review \
  --result results/dev-ex002-r01/ex-002-a02.rubric.json
```

지원되는 YAML 필드만 편집하고, 위 표의 추가 감사 필드는 별도의 검토표에 기록한다.
`finalize` 사용법과 최초 채점·합의 판정의 구분은
[README의 검수·평가 안내](../README.md#강사-검수확정)를 참고한다.

수정과 재검증은 다음과 같이 관리한다.

1. 기존 결과를 보존하고 새 회차에는 `results/dev-ex002-r02`처럼 새 출력 폴더를
   사용한다. 같은 입력·설정의 반복 결과를 하나의 평가 디렉터리에 모두 섞으면
   중복 예측으로 거부될 수 있으므로 평가 회차를 분리한다.
2. 공유 프롬프트를 고쳤다면 영향을 받는 두 방식과 자료를 같은 모델·설정으로
   다시 검증한다. 한쪽은 수정 전 결과, 다른 쪽은 수정 후 결과로 비교하지 않는다.
3. 문항·루브릭·답안 본문을 고쳤다면 해당 입력의 두 방식 결과를 다시 확인한다.
   의도 판정이나 gold만 수정하고 모델 입력이 그대로라면 평가를 다시 계산한다.
4. 코드·프롬프트·입력과 배포할 패키지 버전을 최종 확정한 뒤 릴리스 근거를 만든다.
   공개 예제에 필요한 수정이 남아 있으면 dev 검토를 마친 다음 릴리스를 진행한다.

## 7. 최종 코드로 공개 live 근거 생성

최종 상태에서 다음을 실행한다.

```bash
python scripts/live_smoke.py \
  --model claude-opus-5-5 \
  --temperature 1 \
  --max-tokens 16000 \
  --timeout 180

python scripts/release_check.py --require-live
```

이 스크립트는 `ex-001`의 정답 a01·오답 a02를 두 방식으로 실행한다. 실행 결과는
4개지만 실제 요청 수는 `2 × (4 + 2) = 12회`이며 재시도 시 늘어난다. 기본 출력은
`examples/ex-001/live/`이다. 첫 연결 점검과 별개로 최종 배포 상태의 근거를 만드는
단계다.

실행이 끝나면 실제 판정표와 raw를 읽고, 성공 manifest가 생성되었는지와
`--require-live` 통과 여부를 확인한다. 실패한 실행이나 데모로 실제 호출 완료를
표시하지 않는다. 이미 기본 출력 폴더가 있으면 덮어쓰지 않으므로 기존 결과를
보존한 채 [릴리스 절차](releasing.md)에 따라 재실행 경로를 정한다.

`source_sha256`는 `pyproject.toml`과 패키지의 코드·프롬프트·리소스를 확인한다.
이들을 바꾸면 최종 상태에서 live 근거를 다시 생성해야 한다. 예제 입력 파일의
변경도 해당 근거의 파일 해시와 입력 해시에 영향을 준다. 기존 응답에 새 해시만
기입해서 새 코드의 실행 근거처럼 만들지 않는다. ex-001용 공개 게이트가 ex-002,
ex-003이나 개인 자료의 판정 품질까지 검증하는 것은 아니다.

## 요청 수와 재시도 조건

루브릭 수가 `K`인 답안 하나의 기본 요청 수는 `rubric: K+1`, `free: 2`이다.
양쪽을 모두 실행하면 `K+3`회다. `batch`는 이 개별 요청들을 순차 처리하는 CLI
명령이며 요청 수를 하나로 합쳐 주는 API가 아니다.

| 단계 | 재시도 없는 기본 요청 수 |
| --- | ---: |
| 오프라인 `--demo` | 0 |
| 첫 ex-001 정답 rubric 실행 | 4 |
| ex-002 답안 3개, 두 방식 | 21 |
| ex-003 답안 3개, 두 방식 | 21 |
| 최종 ex-001 공개 live 근거 | 12 |
| 기본 순서 합계 | **58** |
| 선택 개인 자료: 답안 6개, 모두 K=4 | **추가 42** |
| 위 선택 자료까지 포함한 합계 | **100** |

이 표는 모든 단계가 최초 시도에 성공하고 실험을 반복하지 않는 경우다. 모델
응답의 형식·인용 검증 실패나 일부 일시적 API 오류에는 **단계별 최초 시도와 최대
두 번의 재시도**가 적용되어 같은 단계가 최대 3회 호출될 수 있다. 실패하면 뒤
단계에 도달하지 못할 수도 있다. 재실행한 배치는 표의 요청 수가 다시 추가되며,
실제 요청 수는 결과의 `meta.calls`와 raw의 각 단계 `attempts`를 확인한다.

`16000`은 각 응답의 최대 토큰 수이고 `180`초는 요청별 timeout이다. 둘 다 전체
비용이나 전체 완료 시간의 상한을 뜻하지 않는다. 다음 묶음을 실행하기 전에 실제
사용량과 비용을 확인하는 순서를 유지한다.

## 8. 배포로 넘어가기

최종 코드·프롬프트와 검토한 실제 근거가 준비되면
[릴리스 절차](releasing.md)의 빌드, 배포 파일 검사, 추적 파일 확인, 태그,
GitHub pre-release 게시 단계로 이어간다. PyPI 배포는 별도로 진행한다.

main에 소스가 올라간 상태, GitHub pre-release, PyPI 게시를 각각 확인한다.
아직 버전 릴리스를 발행하지 않았다면 dev 수정을 마친 뒤 첫 `v0.1.0`을 만든다.
이미 발행한 버전의 코드를 수정해 다시 배포하려면 새 버전을 사용하고, 그 버전에
맞는 실행 근거를 준비한다.
