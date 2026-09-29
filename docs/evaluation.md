# 평가 지표와 데이터 계약

`evaluate`는 **최초 모델 판정**, **평가자의 독립적인 최초 채점**, **합의 후 기준 판정(gold)**을 서로 구분한다. 강사가 수정한 확정본을 모델의 최초 성능으로 계산하지 않는다. 결과는 자동 채점 점수가 아니라 항목별 판정에 대한 기술 통계다.

```bash
nonsul-review evaluate \
  --pred results/test \
  --raters data/private/test/raters \
  --gold data/private/test/gold \
  --answers data/private/test/answers \
  --out results/test/evaluation.json
```

Python에서는 파일을 쓰지 않고 보고서 사전을 받을 수 있다.

```python
from pathlib import Path
from nonsul_review.evaluate import evaluate_directories

report = evaluate_directories(
    Path("results/test"),
    Path("data/private/test/raters"),
    Path("data/private/test/gold"),
    Path("data/private/test/answers"),
)
```

## 1. 파일 배치와 최초 채점 보존

```text
raters/
  teacher-a/
    ex-001-a01.yaml
  teacher-b/
    ex-001-a01.yaml
gold/
  ex-001-a01.yaml
answers/
  ex-001-a01.md
```

각 평가자의 YAML과 gold의 기본 구조는 같다. `answer_id`, `problem_id`, `items`는 필수다. 파일명보다 파일 안의 ID를 기준으로 결합한다.

```yaml
answer_id: ex-001-a01
problem_id: ex-001
items:
  - rubric_id: R1
    verdict: met
  - rubric_id: R2
    verdict: partial
  - rubric_id: R3
    verdict: null
```

평가자는 서로의 최초 채점과 모델 출력을 보기 전에 독립적으로 판정해야 한다. 합의를 위해 모인 뒤에도 `raters/<평가자>/`의 최초 파일을 수정하지 않고, 합의 결과만 `gold/`에 기록한다. 프로그램은 파일의 출처나 실제 블라인드 절차까지 확인할 수 없으므로 이 구분은 평가 운영 과정에서 지켜야 한다.

합의하지 못한 gold 항목에는 `unresolved: true`를 쓴다.

```yaml
answer_id: ex-001-a01
problem_id: ex-001
is_alternative: true
items:
  - rubric_id: R1
    verdict: met
  - rubric_id: R2
    verdict: partial
    unresolved: true
```

위의 R2는 잠정 판정이 `partial`이어도 오류 탐지에서 제외된다. `verdict: null`인 gold도 기준 판정으로 사용할 수 없어 별도로 제외한다. `unresolved: true`인 항목은 `verdict`를 생략해도 된다. 그 외에는 `verdict` 필드가 있어야 한다.

## 2. 계산 단위와 판정 불가 처리

계산 단위는 `(answer_id, rubric_id)`다. 같은 답안의 여러 항목을 각각 하나의 관측으로 취급한다. 따라서 이 보고서의 항목별 수치가 독립적인 학생 수나 답안 수를 뜻하지는 않는다.

판정 순서는 다음과 같다.

```text
not_met < partial < met
```

모델이 판정할 수 없으면 공통 판정표에 `verdict: null, needs_review: true`가 있어야 한다. 이를 `not_met` 또는 `met`으로 바꾸어 계산하지 않는다. 사람이 판정하지 못한 `null`과 미합의 표시도 비교에서 제외한다.

비교에서 빠진 항목을 숨기지 않도록 보고서는 다음 값을 함께 제공한다.

| 필드 | 의미 |
| --- | --- |
| `compared_items` | 양쪽에 있고 실제 판정 비교가 가능한 항목 수 |
| `missing_prediction_items` | 기준에는 있으나 해당 모델·설정의 예측에는 없는 항목 수 |
| `unmatched_prediction_items` | 예측에는 있으나 비교 대상의 파일에는 없는 항목 수 |
| `prediction_abstentions` | 유효한 기준 항목과 연결되지만 모델 판정이 `null`인 수 |
| `coverage` | 비교 가능한 항목 수 ÷ 전체 유효 기준 항목 수 |
| `matched_coverage` | 예측 레코드가 있는 유효 기준 항목 수 ÷ 전체 유효 기준 항목 수 |
| `decision_coverage` | 비교 가능한 항목 수 ÷ 예측 레코드가 있는 유효 기준 항목 수 |

기준과 연결되지 않은 예측은 정답·오답으로 판단할 수 없어 성능 지표에서는 제외하고 별도로 센다. 특정 답안의 예측 파일이 통째로 없으면 `missing_prediction_answers`에도 반영한다.

## 3. 정확 일치율과 선형 가중 코헨 카파

평가자 간 일치도는 모든 평가자 쌍에 대해 계산한다. 모델과 사람 사이의 일치도는 각 모델·설정 그룹과 각 평가자 사이에서 따로 계산한다. 합의 후 gold를 평가자의 최초 채점 대신 사용하지 않는다.

비교 가능한 N개 항목 중 판정이 정확히 같은 항목 수를 M이라고 하면 정확 일치율은 `M / N`이다.

선형 가중 코헨 카파는 `not_met=0`, `partial=1`, `met=2`로 놓고 두 판정 간 불일치 가중치를 `|i-j| / 2`로 정의한다. 따라서 한 단계 차이는 0.5, 두 단계 차이는 1의 불일치다. 관측 불일치 평균을 `D_o`, 두 평가의 주변분포가 독립이라고 가정했을 때의 불일치 기댓값을 `D_e`라 하면 다음과 같다.

```text
κ = 1 − D_o / D_e
```

`linear_weighted_kappa`에 이 값을 기록한다. **제곱 가중치가 아니라 선형 가중치**를 쓴다. `confusion_matrix`의 행은 왼쪽 평가, 열은 오른쪽 평가다. 모델-평가자 비교에서는 왼쪽이 모델, 오른쪽이 사람이다.

카파가 정의되지 않는 경우를 0이나 1로 채우지 않는다. 비교 항목이 없거나, 양쪽이 모든 항목에 동일한 하나의 범주만 사용해 `D_e=0`이면 `null`을 기록한다. 이때 정확 일치율은 1일 수 있다. 분모가 0인 다른 비율도 모두 JSON `null`이다.

손계산으로 확인 가능한 예:

| 항목 | 평가 A | 평가 B |
| --- | --- | --- |
| 1 | met | met |
| 2 | met | partial |
| 3 | partial | partial |
| 4 | partial | not_met |
| 5 | not_met | not_met |
| 6 | not_met | met |

이 예의 정확 일치율은 `3/6=0.5`, 관측 가중 불일치는 `1/3`, 기대 가중 불일치는 `4/9`, 카파는 `0.25`다. 이 값은 외부 통계 패키지에 의존하지 않는 독립적인 단위 테스트로 검증한다.

평가자끼리의 `union_coverage`는 비교 가능한 수를 두 평가의 항목 합집합 크기로 나눈다. 모델-평가자의 `coverage`는 해당 사람의 **전체 유효 최초 채점**을 분모로 쓴다. `end_to_end_exact_agreement`는 정확히 맞은 수를 이 전체 분모로 나누므로 모델의 누락·판정 불가도 표시된다.

## 4. 기준 판정에 대한 오류 탐지

미합의가 아니며 판정이 존재하는 gold 항목만 유효 기준으로 사용한다. gold의 `partial` 또는 `not_met`을 오류 양성, `met`을 오류 음성으로 정의한다. 모델 판정도 같은 방식으로 이진화한다.

| 필드 | 정의 |
| --- | --- |
| `true_positives` (TP) | gold가 오류이며 모델도 오류를 지적 |
| `false_positives` (FP) | gold는 `met`인데 모델이 오류를 지적 |
| `false_negatives` (FN) | gold는 오류인데 모델이 `met`으로 판정 |
| `true_negatives` (TN) | gold도 모델도 `met` |
| `precision` | `TP / (TP + FP)` |
| `recall` | `TP / (TP + FN)` |
| `f1` | `2TP / (2TP + FP + FN)` |
| `end_to_end_recall` | `TP / 전체 유효 gold 오류 항목 수` |

`precision`, `recall`, `f1`은 모델이 실제 판정을 한 공통 항목의 지표다. `end_to_end_recall`은 **모델이 판정하지 못한 오류와 예측 파일이 없는 오류까지 분모에 포함**한다. 모델의 판정 보류가 늘었을 때 선택된 항목의 재현율만 좋아 보이는 문제를 막기 위해 두 재현율과 coverage를 함께 읽어야 한다.

예를 들어 유효 gold 오류가 4개이고, 모델이 1개를 탐지하고 1개를 `met`으로 오판했으며 나머지는 판정 불가 1개·파일 누락 1개라면, 선택된 항목의 `recall`은 `1/2`, `end_to_end_recall`은 `1/4`다. 판정 불가와 누락을 FN의 가짜 판정으로 기록하지 않고 `missed_errors_due_to_abstention`, `missed_errors_due_to_missing_prediction`에 각각 1을 기록한다.

`unresolved_gold_items`는 잠정 판정 유무와 관계없이 미합의 항목 전체를 센다. 미합의 표시는 없지만 `verdict: null`인 gold는 `unassessable_gold_items`에 기록한다. 두 수는 중복되지 않는다.

## 5. 대안 풀이에 대한 잘못된 오류 지적

대안 풀이 여부는 gold 파일의 답안 수준 `is_alternative: true` 또는 답안 Markdown의 같은 메타데이터로 표시한다. 두 곳에서 명시적으로 다른 값을 주면 평가를 거부한다. 어느 곳에도 표시하지 않았으면 대안 풀이 여부를 모르는 것으로 취급한다.

분모 후보는 **대안 풀이로 표시된 답안 중, 유효 gold가 `met`인 항목**이다. 같은 답안에 실제 오류인 다른 항목이 있더라도 그 오류 항목은 잘못된 오류 지적의 분모에 넣지 않는다. 판정 불가와 누락이 수치를 가리지 않도록 두 비율을 제공한다.

| 필드 | 의미 |
| --- | --- |
| `false_accusation_rate` | 잘못 오류를 지적한 수 ÷ 모델이 실제 판정한 분모 후보 수 |
| `full_set_false_accusation_rate` | 잘못 오류를 지적한 수 ÷ 전체 분모 후보 수 |
| `coverage` | 실제 판정한 분모 후보 수 ÷ 전체 분모 후보 수 |

판정 불가가 많은 모델에서는 두 번째 비율이 낮아질 수 있으므로 coverage를 함께 봐야 한다. 대안 풀이 표시가 전혀 없으면 `status: no_alternative_labels`이며 비율은 `null`이다. 기본값을 근거로 모든 답안을 일반 풀이로 간주하지 않는다.

## 6. 오류 유형, dev/test, 원문 연결

`--answers`를 주면 답안 Markdown의 YAML 메타데이터에서 오류 유형을 읽는다.

```markdown
---
id: ex-001-a01
problem: ex-001
source: synthetic
error_types: [E2, E4]
is_alternative: true
split: test
---
답안 본문
```

모델 결과나 gold에 들어 있는 `error_types`는 슬라이스 생성에 사용하지 않는다. 오류 유형은 **답안 수준** 라벨이므로 E2 슬라이스에는 E2로 표시한 답안의 모든 관련 루브릭 항목이 포함된다. 복수 라벨 답안은 여러 슬라이스에 들어가므로 슬라이스 수를 단순히 합치면 중복 계산된다. 라벨이 빈 배열이라고 해서 실제 오류가 없음을 입증하는 것은 아니다.

슬라이스마다 모델-평가자 일치도, 오류 탐지, 대안 풀이 오판 지적을 계산한다. 평가자끼리의 슬라이스는 `inter_rater_by_error_type`에 있다. `--answers`를 생략하면 오류 유형 결과가 `unavailable`이며 안내 경고를 남긴다. 일부 답안만 메타데이터가 있으면 `partial`로 표시한다.

평가에 실제로 등장하는 답안 중 `split: dev`와 `split: test`가 섞여 있으면 평가를 거부한다. 파일을 나누고 각각 평가해야 한다. `--answers` 아래에 있어도 이번 예측·평가자·gold에 등장하지 않는 파일은 split 검사에 넣지 않는다. split 표기가 없는 답안 수는 `dataset.split_counts.unknown`에 기록하고, 전체 분리를 확인할 수 없다는 경고를 제공한다. 이 검사는 과거에 test를 보면서 프롬프트를 바꾸었는지까지 검증하지는 못한다.

현재 버전이 생성한 예측에는 파서가 읽은 답안 본문의 `answer_body_sha256`가 있다. `--answers`를 주면 이 해시를 다시 계산해 같은 ID로 본문이 바뀌지 않았는지 검사하며 불일치하면 거부한다. 오류 유형 등 평가 메타데이터는 본문 해시에 포함되지 않는다. 오래된 결과에 해시가 없으면 계산은 가능하지만 원문 연결을 검증하지 못한 개수와 경고를 남긴다. 문항 전체가 포함된 `input_sha256`는 평가 명령에서 재계산하지 않고 두 방식의 동일 입력 확인에 사용한다.

## 7. 설정별 그룹과 두 방식의 비교 가능성

동일한 모델 별칭이라도 실제 응답 모델이나 프롬프트 파일이 달라질 수 있다. 결과는 다음 정보로 그룹을 나눈다.

- `mode`, `provider`, 요청 `model`, 실제 `response_models`
- `prompt_version`과 `prompt_sha256`에 기록된 전체 프롬프트 파일 해시
- `temperature`, `max_tokens`, `timeout`, `max_retries`
- `config_sha256`와, 구형 결과에 `meta.config`가 있으면 그 설정의 해시
- 데모 여부

생성 일시, 답안별 입력 해시, 실제 호출 수와 재시도 횟수는 그룹 경계로 쓰지 않는다. 같은 그룹 안에서 동일 `answer_id`가 두 번 나타나면 덮어쓰거나 평균내지 않고 거부한다. 여러 번 실행한 실험은 별도 디렉터리로 나누어 평가한다.

`paired_modes`는 루브릭 방식과 자유형 방식의 각 그룹 쌍에 대해 공통 답안 수, 한쪽에만 있는 답안 수, 입력 해시 일치·불일치·미검증 수를 기록한다. 두 방식의 절차별 프롬프트는 다르므로 그 전체 해시의 동일성을 요구하지 않는다. 대신 **공통 프롬프트 `common-v1.txt`의 해시**를 따로 확인한다.

`comparable_on_shared_answers: true`가 되려면 공통 답안이 있고, 기록된 모델·설정이 같으며, 동일한 단일 실제 응답 모델·공통 프롬프트·설정 해시·공통 답안 입력 해시가 모두 검증되어야 한다. `fully_paired: true`는 여기에 답안 집합까지 완전히 같다는 뜻이다. 필요한 메타데이터가 없는 구형 결과는 성능 수치를 계산할 수 있지만 검증된 비교 가능성을 주장하지 않는다.

이 표시는 실험의 기본적인 일관성을 검사할 뿐, 오류 유형의 대표성·통계적 유의성·실제 수업 효과를 보장하지 않는다.

## 8. 제외 파일과 검증 실패

예측 디렉터리는 재귀적으로 읽는다. `*.raw.json`, `*.failure.json`, `*.changes.json`, `*.final.json`은 제외한다. 알려진 평가 보고서 파일, `report_type: nonsul-review-evaluation`인 보고서, `kind: batch_manifest`인 일괄 실행 명세도 제외한다. 파일 이름을 바꾼 확정본도 `status: finalized`, `finalized_at`, 확정본의 review 해시 출처로 식별해 제외한다.

일반 JSON을 조용히 무시하지는 않는다. 제외 대상이 아닌 JSON은 공통 판정표 스키마에 맞아야 한다. 잘못된 파일, 중복 JSON/YAML 키, 같은 자료군 안의 중복 답안 ID, 중복 루브릭 ID, 서로 다른 문항을 가리키는 동일 답안 ID는 오류다. 모든 입력은 공통 IO의 크기 제한과 안전한 YAML 파서를 사용한다. 오류 메시지에 답안 본문이나 파서의 원문 인용을 넣지 않는다.

데모 판정표는 기본적으로 거부한다. `--allow-demo`는 공개 예제의 실행 흐름을 확인하는 용도이며 보고서에도 **실제 LLM 성능의 근거가 아니다**라는 경고를 기록한다. 데모의 평가자·gold 예제 또한 실제 강사의 독립 채점 자료라고 해석해서는 안 된다.

예측이 하나도 없거나 gold가 비어 있으면 성능 0 또는 100%를 만들어내지 않고 비어 있는 결과와 경고를 반환한다.

## 9. 보고서 읽는 순서

1. `dataset`에서 실제 파일·답안·평가자 수, 제외 수, 오류 유형 자료와 split의 완전성을 확인한다.
2. `inter_rater_agreement`에서 최초 채점 자체의 일치도와 비교 가능 항목 수를 확인한다.
3. `groups[].settings`와 `paired_modes`에서 같은 실험 조건인지 확인한다.
4. 각 그룹의 `model_rater_agreement`와 `error_detection`을 coverage·누락·판정 불가와 함께 읽는다.
5. 대안 풀이 오판 지적과 오류 유형별 결과를 보되, 분모가 작거나 슬라이스가 겹친다는 점을 고려한다.

보고서는 **본 평가셋에서 관측된 결과**만을 설명한다. 이 버전은 답안 단위 부트스트랩 신뢰구간, 유의성 검정, 사전 등록, 평가자 블라인드 운영을 자동으로 수행하지 않는다. 자체 작성 답안에서 얻은 결과를 실제 학습자 답안 전체로 일반화하려면 별도의 표본과 검증이 필요하다.
