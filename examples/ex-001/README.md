# 직접 만든 예제: 도함수와 경계 조건

문제·모범답안·루브릭·학생 역할 답안은 이 프로젝트를 위해 직접 작성했다.
대학 기출이나 실제 학습자 답안을 복제하지 않았다.

| 답안 | 구성 | 작성 시 의도한 판정 |
| --- | --- | --- |
| a01 | 적분상수로 풀고 두 조건을 검증 | met / met / met |
| a02 | $1+C=3$에서 $C=3$이라고 계산 | met / partial / not_met |
| a03 | 정적분을 이용한 올바른 대안 풀이 | met / met / met |

이 표와 `results/`의 데모 판정은 **수작업으로 작성한 동작 예시**이다.
두 방식의 정확도나 성능을 실험하여 얻은 결과가 아니다.
`--demo`는 이 문항과 동일한 답안 본문에만 사용할 수 있으며,
실제 LLM 요청과 같은 단계 흐름을 미리 작성된 응답으로 실행한다.

```bash
nonsul-review batch --problems examples/ex-001 \
  --answers examples/ex-001/answers --mode rubric --demo --out demo-rubric/
nonsul-review batch --problems examples/ex-001 \
  --answers examples/ex-001/answers --mode free --demo --out demo-free/
```

`evaluation/`의 rater-a·rater-b·gold 역시 평가 코드 사용법을 설명하는
가상 채점 데이터다. 실제 평가자의 독립 채점이나 합의 기록을 의미하지 않는다.
가상 rater-b는 a02의 R2를 not_met으로 판정하도록 만들었다. 이 불일치를
이용해 사람 간 일치도와 합의 판정 대비 오류 탐지를 분리하는 동작을 확인한다.

```bash
nonsul-review evaluate --pred examples/ex-001/results \
  --raters examples/ex-001/evaluation/raters --gold examples/ex-001/evaluation/gold \
  --answers examples/ex-001/answers --allow-demo --out demo-evaluation.json
```

실제 평가에서는 별도 데이터에 독립 평가자가 최초 채점을 하고, 그 원본을
유지한 채 합의한 판정을 `gold/`에 작성한다. 공개 예제는 전부 `split: dev`이며
최종 test 평가셋으로 사용할 수 없다.
