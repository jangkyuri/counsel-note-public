# 5A Connect Log 서버 이관

이 묶음은 현재 사용 중인 원본 Streamlit 앱이다. `app.py`, 실행 모듈, 프롬프트는 원본과 동일하다. 별도로 다시 만든 Apps Script 화면을 포함하지 않는다. 학생 명단, Docs 연결 파일, 녹음, 전사, 운영 로그, 모델과 인증 정보도 포함하지 않는다.

1. 서버의 기존 `C:\server`와 별도 폴더에 풀고, Python 3.12 가상 환경에서 `requirements.txt`를 설치한다. 기존 Windows 의존성 참고 목록은 `requirements-validated.txt`에 있다. API 방식으로 맞추기 전에는 이 묶음의 전사와 정리 기능이 기존 로컬 런타임을 요구한다.
2. 첫 실행은 테스트 설정으로만 한다. `data/students/google_docs_map.csv`에는 `student_name,doc_id,doc_url` 헤더만 두고 `data/students/google_apps_script_webapp_url.txt`는 빈 파일로 둔다. `data/students/admin_api_base_url.txt`에는 `http://127.0.0.1:1`을, `data/students/recording_auto_cleanup_enabled.txt`에는 `false`를 넣는다. 운영 계정의 다운로드나 녹음기 폴더를 읽지 않도록 별도 실행 계정을 사용한다.
3. `.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1 --server.port 8510 --server.headless true` 명령을 사용한다. 포트 8510이 사용 중이면 별도 미사용 포트를 선택한다. 기존 8003 서비스, 바인딩, 방화벽은 변경하지 않는다.
4. 화면과 CSV 처리 흐름부터 가상 데이터로 확인한다. 운영용 학생-Docs 매핑과 현재 Docs 저장 주소는 승인된 서버 비공개 설정으로만 연결한다. 실학생 주소를 사용해 시험 저장하지 않는다.
5. 외부 접속이 필요하면 검증된 학원 계정 전용 접속 경로 뒤에서만 Streamlit을 제공한다. Streamlit 포트와 8003은 직접 공개하지 않는다. 학원 계정 로그인, 비인가 접속 거절, 가상 Docs 저장과 중복 방지가 확인된 뒤에만 기존 링크를 전환한다.

기존 Apps Script 주소는 이 원본 Python 앱을 직접 실행하는 서버가 아니다. 현재 운영 링크나 기존 로컬 앱은 이 묶음으로 자동 교체되지 않는다.
