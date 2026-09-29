# 검수·확정 동작 예제

이 폴더는 오답 a02의 **데모 판정**을 YAML로 내보내고, R2 피드백을
조금 더 구체적으로 고친 뒤 확정한 예제다. 실제 강사의 실험 자료는 아니다.

- `ex-001-a02.rubric.review.yaml`: 모델 역할의 원본과 편집한 내용을 함께 표시한다.
- `ex-001-a02.rubric.final.json`: 수정 수준과 확정 표시를 포함한 판정표다.
- `ex-001-a02.rubric.changes.json`: R2 피드백의 변경 전·후와 수정 수준을 기록한다.

원래 판정 `met / partial / not_met`은 제공한 루브릭에 맞으므로 유지했다.
R2의 피드백에는 상수 계산을 고친 뒤 최종 함수에도 반영하라는 안내를 덧붙였고,
`edit_level: minor`로 표시했다. 나머지 항목은 `none`이다.

모든 파일은 `is_demo: true`라는 출처를 유지한다. 확정본은 최초 모델 성능을
평가하는 `evaluate`의 입력에서 제외된다. 검수 YAML의 `source.path`는
`../results/ex-001-a02.rubric.json`이므로, 이동할 때 원본과의 상대 위치를 유지한다.
