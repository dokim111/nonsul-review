# Contributing

이 도구는 수리논술 강사의 판단과 실험 기록을 돕는다. 기능 변경 시 판정의 근거, 강사 검수 이력, 평가 분모를 확인할 수 있어야 한다.

## 개발 환경

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
ruff check .
ruff format --check .
pytest
```

Python 3.10 이상과 `src/` 패키지 구조를 사용한다. Windows PowerShell은 `.venv\Scripts\Activate.ps1`로 활성화한다. 프롬프트는 `src/nonsul_review/prompts/`에서 버전별 파일로 관리하고, 설치된 wheel에서도 읽을 수 있어야 한다.

## 변경할 때 지킬 계약

- 답안 front matter의 `error_types`, `source`, `split`, `is_alternative` 등 평가 태그를 모델 입력에 넣지 않는다.
- 두 방식의 원래 정보와 공통 설정을 유지한다. 절차나 비용이 달라지면 실험 문서에 명시한다.
- 판정 불가를 오답으로 처리하지 않는다. `null`·검수 필요 표시를 유지하고 확정 시 해결하도록 한다.
- 루브릭 ID의 누락·중복·추가를 거부한다. `evidence`가 실제 본문에서 복사한 문자열인지 확인한다.
- API 실패와 형식 실패는 모의 응답으로 테스트한다. ordinary CI에 실제 API 키를 넣거나 유료 호출을 추가하지 않는다.
- 강사 확정본을 최초 평가자 채점으로 재사용하지 않는다. 평가 시 누락·보류·미합의 항목과 분모를 함께 보고한다.
- 새로운 파일은 UTF-8로 저장한다. 실제 답안·채점·결과는 저장소 밖에 둔다.

프롬프트 변경은 별도의 실험으로 취급한다. dev에서 수정하고 test는 최종 확인에 사용한다. test의 결과를 보고 프롬프트를 변경했다면 그 test를 더 이상 미사용 최종 평가셋으로 표시할 수 없다.

## 의미 있는 테스트

입력 검증, 모델 정보 누출 방지, 재시도 한계, 두 방식의 실행 순서, 인용 검증, 출력 보존, 확정 이력, 지표의 분모·경계 사례를 테스트한다. 스키마를 완화할 때는 거부하던 실제 사례와 새로 허용할 범위를 설명한다. 데모 fixture가 통과한다는 이유만으로 새 문항이나 실제 모델에 대한 정확성을 주장하지 않는다.

```bash
pytest tests/test_review.py
python -m build
python -m twine check dist/*
python scripts/release_check.py --dist dist
```

전체 릴리스 검사와 실제 API 확인은 [릴리스 절차](docs/releasing.md)를 따른다. 스크립트가 검증한 범위를 문서에 정확히 적는다.

## 이슈·PR 작성

이슈에는 재현 명령, 기대 결과, 실제 결과, Python/패키지 버전, 최소한의 익명화한 자체 작성 예제를 포함한다. API 키나 학습자 답안 원문을 공개 이슈에 붙이지 않는다. raw 파일을 공유해야 할 때는 본문과 인용도 포함될 수 있음을 확인한다.

PR 설명에는 문제, 변경 후 동작, 검증한 핵심 사례를 적는다. 평가 지표가 달라지면 어떤 분모·제외 규칙·그룹 구성이 바뀌는지 명시한다. 사용자 출력 형식이 바뀌면 README·데이터 형식·예제도 함께 수정한다.

기여하는 코드와 직접 작성한 예제는 저장소의 MIT 라이선스로 제공할 권리가 있는 자료여야 한다.
