# Artifacts

`artifacts/mock_validation/`은 FR-01 mock validation을 실행할 때마다 생성되는 검증 결과 보관 폴더입니다.

각 실행은 고유한 run ID 폴더에 요청·응답, Scene 전후 상태, 요약, 보고서 및 실패 정보를 기록합니다. 이 파일들은 로컬 확인과 디버깅을 위한 실행 산출물이므로 Git에서는 추적하지 않습니다. 빈 디렉터리 구조를 유지하기 위한 `.gitkeep`만 추적합니다.

검증 실행 방법은 [`tools/mock_validation/README.md`](../tools/mock_validation/README.md)를 참고하세요.
