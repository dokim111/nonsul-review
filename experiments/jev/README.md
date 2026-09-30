# Jev 예비 시험 (실험)

TypeSafe AI의 Jev가 수리논술 루브릭 항목 판정을 **공개해도 될 만큼 확신 있게 맞히는지**,
특히 그럴듯한 오답을 높은 확신으로 `met`이라고 하지 않는지를 확인하는 독립 실험이다.

- `nonsul-review` 본체(엔진, 프롬프트, 결과 형식)는 바꾸지 않는다. 이번 학기의 루브릭 방식
  대 자유형 방식 비교와 섞지 않는다.
- 이 실험의 결론은 "무료 예상 점수에 쓸 후보인가"에 대한 1차 판단이다. 공개 기준값의 확정은
  이후의 학생 비공개 병행 운영에서 한다.

## 구성

| 파일 | 역할 |
| --- | --- |
| `jev_run.py` | 요청 생성·전송, 원본 응답 저장 (재시작 가능, 요청 상한, 재시도) |
| `jev_analyze.py` | gold·Opus 결과와 비교, 표본별 지표와 확신도 곡선, 사전 기준 판정 |
| `jev_common.py` | 입력 읽기, 세 가지 질문 설계, 응답 해석, 요구사항 집계 |
| `criteria.yaml` | **실행 전에 정한** 채택 기준 |
| `gold/` | 공개 예제 `ex-002`·`ex-003`의 작성자 기준 판정 |
| `requirements/` | 변형 C용 공개 문항 요구사항 분해 |
| `samples.example.yaml` | 표본 구분 목록의 예시 |

비공개 문항용 요구사항 분해는 `data/private/jev/requirements/`에 둔다.

## 질문 설계 (변형)

| 변형 | 방식 | 확인하려는 것 |
| --- | --- | --- |
| A | 루브릭 항목마다 Choice (`met`/`partial`/`not_met`) | 기본 성능 |
| B | 항목마다 Score (not_met < partial < met) | 순서 정보가 도움이 되는지 |
| C | 항목을 예/아니오 요구사항으로 분해해 Noul로 묻고 규칙으로 집계 | 분해가 함정 판정을 개선하는지 |

- 상태(state)에는 Opus 절차와 같은 정보(문제, 모범답안, 인정 대안, 루브릭 전체, 학생 답안)를 넣는다.
- 변형 C의 집계: 모든 요구사항이 예이면 `met`, `base` 요구사항이 모두 예이면 `partial`,
  하나라도 `base`가 아니오이면 `not_met`. 항목 확신도는 요구사항 판정 중 가장 약한
  `max(p, 1-p)`이다.
- 지시문 언어는 `--lang ko|en`으로 바꿔 비교한다.

## 준비 (기본: OpenCode Zen 무료 모델)

기본 제공자는 OpenCode Zen이고 모델은 기간 한정 무료 `jev-1.13-free`다. 요청 형식은 TypeSafe
공식 API와 같다.

```bash
export OPENCODE_API_KEY=...   # OpenCode Zen 콘솔에서 만든 키. 저장소의 .env에는 넣지 않는다
```

- 무료 목록에 없는 모델(`jev-1.13` 등)은 `--allow-paid` 없이는 보내지 않는다.
- 무료 모델이 404나 410을 돌려주면(무료 기간 종료) 유료 모델로 넘어가지 않고 즉시 멈춘다.
- 응답의 `usage`와 `cost`를 결과 파일의 `meta`에 기록한다. 무료 모델에서 양수 비용이 오면
  그 응답을 저장하고 **다음 요청 전에 멈춘다**. 비용이 보고되지 않으면 `unknown`으로 남긴다.
- `--max-requests`는 재시도를 포함한 **실제 전송 횟수**의 상한이다.
- TypeSafe 직접 호출은 `--provider typesafe --allow-paid`와 `TYPESAFE_API_KEY`로 쓴다.

## 실행 순서

원칙: **질문·입력·모델이 바뀌면 새 출력 폴더**를 쓴다(`...-v1`, `...-v2`). 같은 폴더에 다른
요청의 결과가 있으면 실행기가 멈춘다(종료 코드 5). 재개는 같은 요청임이 해시로 확인될 때만 한다.

종료 코드: 0 정상, 1 실패 응답 있음, 2 요청 상한 도달, 3 무료 기간 종료(404/410),
4 무료 모델에서 비용 발생, 5 출력 폴더에 다른 요청의 결과가 있음.

```bash
OUT=results/jev-zen-free-design-v1
P=(--problems examples/ex-002 examples/ex-003)
A=(--answers examples/ex-002/answers examples/ex-003/answers)
REQ=(--requirements experiments/jev/requirements)

# 1) 전송 없이 요청 확인
python experiments/jev/jev_run.py --variant A --dry-run "${P[@]}" "${A[@]}" --out "$OUT"

# 2) A·B·C 한 건씩 probe (3요청): 형식, 모든 항목 반환, cost_status 확인
for v in A B C; do
  python experiments/jev/jev_run.py --variant $v --probe "${REQ[@]}" \
    --problems examples/ex-003 --answers examples/ex-003/answers --out "$OUT" || break
done

# 3) 공개 설계 표본 전체를 1회씩 (6개 답안 × 3변형 × 2언어 = 36요청)
for v in A B C; do for lang in ko en; do
  python experiments/jev/jev_run.py --variant $v --lang $lang --repeat 1 --retries 0 \
    --max-requests 20 "${REQ[@]}" "${P[@]}" "${A[@]}" --out "$OUT" || break 2
done; done
```

3단계 결과로 분석을 돌려 `undecided`, 응답 문제(Response issues), 비용 상태를 확인한 뒤에만
반복을 채운다. 설정을 바꾸지 않았다면 같은 폴더에서 `--repeat 3`으로 다시 실행하면 이미 받은
결과는 건너뛰고 나머지만 보낸다. 질문 문구를 고쳤다면 `-v2` 폴더에서 처음부터 한다.

비공개 문항(`data/private/...`)은 **Zen·TypeSafe의 데이터 보관 조건을 확인한 뒤에** 같은
방식으로 추가한다(`--requirements data/private/jev/requirements` 포함).

```bash
# 분석
python experiments/jev/jev_analyze.py --runs "$OUT" "${P[@]}" "${A[@]}" \
  --gold experiments/jev/gold "${REQ[@]}" \
  --opus results/v2-ex002-r01 results/v2-ex003-r01 \
  --criteria experiments/jev/criteria.yaml --out "$OUT-report"
```

분석기는 다음을 따로 보고한다.

- 요청 결과: 완전(complete), 일부 누락·형식 오류(incomplete), 실패(failed)
- 미판정 항목(undecided): 누락·오류·실패 항목은 **분모에 남는다**
- 입력이 바뀐 결과(stale)는 제외하고 목록으로 남긴다
- Opus 비교는 **같은 답안 본문 해시**로 만든 결과끼리만 한다
- 비용: `zero`, `charged`, `unknown`(보고되지 않음 — 0원으로 확인된 것이 아님)

해석할 때 주의할 점:

- `--lang en`은 **지시문 언어**만 바꾼다. 문제·루브릭·답안과 변형 C의 요구사항 질문은 한국어다.
- 변형 A·B의 확신도는 제공자의 통계량이고, C는 요구사항 판정의 `min(max(p, 1-p))`다. 같은
  0.9를 같은 정답률로 읽지 말고, 실제 정답률과 자동 채택 비율을 함께 본다.
- 반복 실행은 정확도 분모에 더하지 않는다. 성능은 첫 실행, 반복은 안정성으로만 본다.

## 표본과 판정 원칙

| 표본 | 내용 | 해석 |
| --- | --- | --- |
| `design` | 질문 문구를 다듬는 데 쓴 답안 (현재 dev 12개) | 진단용. 성능 주장에 쓰지 않는다 |
| `risk` | 기존 함정의 **구조를 유지한 새 답안** | 높은 확신의 거짓 `met` 측정 |
| `alternative` | 모범답안과 다른 올바른 풀이 | 부당 감점 측정 |
| `normal` | 일반적인 답안 | 자동 공개율과 정확도 |

- 표본은 **절대 합산해 보고하지 않는다.** 위험 사례를 일부러 많이 넣으면 실제 제출 분포와
  달라지기 때문이다.
- `design`에서 발견한 실패를 고친 뒤에는 **같은 오류 구조의 새 답안**으로 다시 확인한다.
  고친 답안에 다시 맞았다는 것만으로 일반화하지 않는다.
- Opus와 다른 판정이 나오면 Jev 오류로 단정하지 않고 gold로 확인한다.
- `confidence`는 확률분포의 집중도를 요약한 값이다. 실제 정답률로 읽지 않는다.
- 새 표본 답안은 문항당 5–8개씩 작성하고, 답안 머리말의 `split`을 `test`로,
  `error_types`와 `is_alternative`를 정확히 적는다(표본 자동 분류에 쓰인다).
  명시적으로 나누려면 `--sets`로 목록 파일을 준다.

## 결과에 따른 쓰임

- **사전 기준 통과:** 무료 예상 점수 후보. 먼저 학생에게 보이지 않는 병행 운영을 하고, 무료
  사례의 무작위 표본을 사람이 판정해 기준값을 다시 확인한다.
- **함정에서만 실패:** 점수 대신 "정밀 분석이 필요한 답안"을 고르는 분류 용도로 쓴다.
- **전반적으로 실패:** 무료 단계는 Jev 없이 설계한다.

## 데이터 원칙

- Jev는 외부 처리 경로다. `source: learner` 답안은 기본적으로 전송을 거부한다
  (`--allow-learner-answers`는 동의와 TypeSafe 계약의 보관·삭제 조건을 확인한 뒤에만 쓴다).
- 모든 요청과 응답은 원본 그대로 저장하며, 요청·문항·답안·요구사항 파일의 해시를 함께 남긴다.
