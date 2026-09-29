# 데이터 형식

YAML·JSON은 UTF-8로 저장한다. 필드 이름은 대소문자를 구분한다. 입력·판정 스키마는 알 수 없는 필드, 중복 YAML 키, 비정상 타입을 거부한다. 파일명으로 사용하는 ID는 ASCII 영문·숫자로 시작하고 영문·숫자·점·밑줄·하이픈으로 구성한 1~128자다. 경로 구분자, `..`, 끝의 점, Windows 예약 파일명은 허용하지 않는다.

## 문항: `problem.yaml`

| 필드 | 타입 | 규칙 |
| --- | --- | --- |
| `id` | 문자열 | 고유 문항 ID |
| `title` | 문자열 | 비어 있지 않은 제목 |
| `prompt` | 문자열 | 제시문·발문 전체 |
| `model_answer` | 문자열 | 완전한 모범답안 |
| `rubric` | 배열 | 1~100개, 항목 ID 중복 불가 |
| `alternatives` | 문자열 또는 null | 허용할 다른 풀이 방향, 선택 |

각 루브릭은 `id`, `step`, `criteria`가 필수다. `partial`은 부분충족 기준인 문자열 또는 null이며 생략 가능하다. `points`는 0 이상인 수 또는 null이며 생략 가능하다. 숫자 점수는 문항 설명으로 보관하며 프로그램에서 총점을 확정하지 않는다.

## 답안: `answers/<파일명>.md`

Markdown 첫 줄부터 `---`로 감싼 YAML front matter를 둔다. 그 아래가 답안 본문이다. 본문은 별도의 `body` 메타데이터 필드에 넣지 않는다.

```markdown
---
id: ex-001-a03
problem: ex-001
source: synthetic
error_types: []
split: dev
is_alternative: true
---
$F(x)$의 대칭성을 이용하면 다음과 같이 계산할 수 있다.
```

| 메타데이터 | 규칙 | 모델 전달 |
| --- | --- | --- |
| `id` | 고유 답안 ID, 필수 | 제외 |
| `problem` | 연결되는 문항 ID, 필수 | 제외 |
| `source` | `synthetic` 또는 `learner`, 필수 | 제외 |
| `error_types` | 중복 없는 오류 코드 문자열 배열, 기본 `[]` | 제외 |
| `split` | `dev`, `test`, null 중 하나, 기본 null | 제외 |
| `is_alternative` | 올바른 대안 풀이 답안을 따로 평가하기 위한 boolean, 기본 false | 제외 |
| 본문 | 비어 있지 않은 텍스트·LaTeX | 포함 |

`--split`을 사용하는 배치는 모든 답안에 split이 있어야 한다. dev/test 답안이 섞인 배치는 선택 없이 실행할 수 없다. 오류 코드는 [오류 분류표](error-types.md)를 참고한다. `is_alternative`는 인정되는 대안 풀이인지 사람이 판단한 평가 태그이며, 답안의 정오를 모델에 미리 알려 주지 않는다.

## 공통 판정표: `<answer_id>.<mode>.json`

최상위 필드는 `answer_id`, `problem_id`, `mode`, `items`, `overall_feedback`, `meta`다. `mode`는 `rubric` 또는 `free`다. `items`는 문항의 루브릭 ID를 누락·추가·중복 없이 모두 포함해야 한다.

```json
{
  "rubric_id": "R2",
  "verdict": "partial",
  "evidence": "상수는 a=2이다.",
  "reason": "상수를 정하는 논증이 생략되어 있다.",
  "feedback": "주어진 경계 조건을 대입하는 과정을 쓰세요.",
  "needs_review": false
}
```

`verdict`의 정상 값은 `met`, `partial`, `not_met`다. `evidence`는 답안 본문에 존재하는 연속 문자열을 그대로 복사한다. `'답안 3행: ...'`처럼 모델이 덧붙인 위치 설명은 인용 문자열에 넣지 않는다. `met`·`partial`에는 비어 있지 않은 인용이 필수다. 아예 쓰지 않은 논증을 지적하는 `not_met`은 인용을 빈 문자열로 두고 `reason`에서 누락을 설명할 수 있다. `reason`은 비울 수 없다.

### 판정 불가 표현

원 명세는 세 판정만 허용하면서 판정 불가를 `not_met`으로 처리하지 않도록 요구한다. 이를 일관되게 구현하기 위해 다음 표현을 추가했다.

```json
{
  "rubric_id": "R2",
  "verdict": null,
  "evidence": "",
  "reason": "답안에서 기호 a가 두 의미로 쓰여 논증을 확인할 수 없다.",
  "feedback": "기호의 뜻을 명확하게 작성해 주세요.",
  "needs_review": true
}
```

`null`이면 `needs_review: true`가 필수다. 이 항목을 오답으로 변환하지 않는다. 평가에서는 보류 수·포함률로 보고하고, 강사 `finalize`에서는 미해결 null을 거부한다. 세 판정 중 하나여도 특별히 강사 확인이 필요한 경우 `needs_review: true`를 유지할 수 있다.

### 실행 메타데이터

| 필드 | 의미 |
| --- | --- |
| `model`, `response_models` | 요청한 모델 ID와 실제 응답에 기록된 모델 ID |
| `provider`, `is_demo` | `anthropic`/`demo`, 실제 호출과 fixture 구분 |
| `prompt_version`, `prompt_sha256` | 절차 버전과 사용한 프롬프트 파일별 해시 |
| `temperature`, `max_tokens`, `timeout`, `max_retries` | 명시한 실행 설정 |
| `input_sha256` | 모델에 전달한 문항 객체와 답안 본문의 정규 JSON 해시 |
| `answer_body_sha256` | 답안 본문 UTF-8 해시 |
| `config_sha256` | 방식·ID·시각을 제외한 공통 모델 설정 해시 |
| `created_at` | 시간대가 포함된 ISO 8601 시각 |
| `calls`, `retry_count` | 총 시도 횟수와 재시도 횟수 |

`input_sha256`에는 hidden metadata가 들어가지 않는다. 파일 자체 해시와 의미가 다르다. 결과의 meta 객체는 확정·평가 이력을 확장할 수 있지만, 최상위 판정표와 루브릭 항목의 알 수 없는 필드는 거부한다.

## 원본·실패 기록

`<answer_id>.<mode>.raw.json`은 `schema_version`, `status`, `provider`, `model`, `is_demo`, `mode`, 프롬프트·입력·설정 정보 및 `stages`를 담는다. 각 단계의 `attempts`에 응답 메시지 본문, 형식 검증 성공 여부, 간결한 오류 분류를 기록한다. HTTP 인증 헤더와 API 키를 저장하지 않는다. 응답 본문에는 답안 인용이 들어갈 수 있다.

실패 시 원본과 `<answer_id>.<mode>.failure.json`을 남기며 성공 판정표는 쓰지 않는다. 배치의 `batch.<mode>.json`에는 선택·성공·실패 건수 및 각 실행 경로가 있다. 평가 모듈은 이 파일을 판정 결과로 세지 않는다.

## 검수 YAML과 확정

`review --result <stem>.json`은 기본 `<stem>.review.yaml`을 만든다. 원본 JSON의 경로와 해시, 원본 판정, 항목별 `model_*` 비교 열을 포함한다. 강사는 `verdict`, `evidence`, `reason`, `feedback`, 전체 피드백과 선택적 `edit_level`을 수정한다. `edit_level`은 `none`, `minor`, `major`, null 중 하나다.

원본·식별 정보·비교 열을 변조하면 `finalize`가 거부한다. 항목 순서는 바꿀 수 있으나 루브릭 ID는 정확하게 유지해야 한다. 확정은 `<원본 stem>.final.json`, 변경 기록은 `<원본 stem>.changes.json`이다. 원본 모델 판정과 강사가 확정한 판정을 각각 보관한다.

## 평가자·기준 판정

최초 채점은 `raters/<평가자 ID>/<answer_id>.yaml`, 합의 판정은 `gold/<answer_id>.yaml`에 별도로 둔다. 각각 `answer_id`, `problem_id`, `items`를 사용한다.

```yaml
answer_id: ex-001-a01
problem_id: ex-001
items:
  - rubric_id: R1
    verdict: met
  - rubric_id: R2
    verdict: null
    unresolved: true
```

Gold의 미합의 항목은 `unresolved: true`로 명시한다. 평가자 파일을 나중에 합의 결과로 덮어쓰지 않는다. 자세한 예제·수식·포함률은 [평가 문서](evaluation.md)에 있다.

## 형식 검증 명령

```bash
nonsul-review validate --problem examples/ex-001/problem.yaml
nonsul-review validate \
  --problem examples/ex-001/problem.yaml \
  --answer examples/ex-001/answers/a01.md \
  --result examples/ex-001/results/ex-001-a01.rubric.json
```

결과 검증에는 문항과 답안도 필요하다. `validate`는 형식·ID 대응과 실제 인용 여부를 확인하며, 수학적 판정이 옳다고 인증하지 않는다.
