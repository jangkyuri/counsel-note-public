# Connect Log Recording Backup

## 녹음 백업 안내

Connect Log Recording Backup은 개인 PC에서 사용하는 녹음 백업 기능입니다.
사용자가 녹음 처리를 실행하면, 선택한 녹음과 미리 지정한 녹음 수신 폴더의
음성 파일을 사용자가 연결한 Google Drive 폴더에 백업합니다.
PC가 켜져 있다는 이유만으로 계속 실행하는 상시 백업 서비스는 아닙니다.

백업 전 파일 내용의 해시와 크기를 비교해 같은 내용의 파일을 다시 올리지
않습니다. 업로드 뒤에도 Drive에 저장된 파일의 해시와 크기를 확인합니다.
백업 기능은 로컬 원본을 삭제하거나 기존 Drive 파일을 삭제하지 않으며,
Drive의 공유 설정을 변경하지 않습니다.

## Google 연결

Google 계정의 소유자가 직접 동의한 연결을 사용합니다. 현재 사용하는
rclone Drive 연결은 Drive 파일의 조회·생성·수정·삭제가 가능한 넓은 권한을
요청합니다. 이 백업 기능의 실제 작업은 지정된 폴더의 목록 조회, 중복 확인,
새 녹음 업로드와 업로드 결과 확인으로 제한합니다.

인증 정보는 사용자 PC의 rclone 설정에 보관합니다. 이 안내 페이지에는
인증 정보, 학생 명단, 실제 녹음, 전사본 또는 운영 기록을 올리지 않습니다.

## 연결 해제와 문의

Google 계정의 연결된 앱 관리 화면에서 이 앱의 접근 권한을 해제할 수
있습니다. 접근 권한을 해제해도 이미 Drive에 저장된 파일은 지워지지 않습니다.

- [개인정보 안내](RECORDING_BACKUP_PRIVACY.md)
- [Google 계정의 연결된 앱 관리](https://myaccount.google.com/connections)
- [공개 문의](https://github.com/jangkyuri/counsel-note-public/issues)

공개 문의에는 녹음, 학생정보, 인증 정보나 비공개 로그를 첨부하지 마세요.

최종 수정: 2026-10-25
